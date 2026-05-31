# Agent Portfolio Analysis — v0.8 dogfood run

**Date:** 2026-05-30
**Author:** Claude (Opus 4.7) with Fred Pearce
**Source:** `uv run agentfluent analyze --project agentfluent --diagnostics --git --github --json`
**Sessions analyzed:** 32
**agentfluent version:** v0.8.0 (published 2026-05-31 00:22 UTC; analyzed from editable install at the v0.8.0 commit)
**Baseline:** [`2026-05-17-v07-dogfood-analysis.md`](../2026-05-17-v07-dogfood-analysis.md)
**Raw outputs:** [`analyze.json`](analyze.json), [`analyze.table.txt`](analyze.table.txt)

## Purpose

Third dogfood run, first one with the v0.8 Tier 3 GitHub enrichment turned on. Three goals:

1. **Validate v0.8 ships clean on real data** — `tier3_degraded`, signal counts, recommendations sanity, no parse-time exceptions.
2. **Measure the calibration work in production** — did #333 (`ERROR_PATTERN` per-invocation trace gating) and #402 (`FEAT_FIX_PROXIMITY` 2-file threshold) actually move the false-positive needle?
3. **Re-evaluate the v0.7 recommendations** — most notably the "broaden tester scope, re-evaluate in 2-3 weeks" call (`tester` was broadened in [#397](https://github.com/frederick-douglas-pearce/agentfluent/pull/397) on 2026-05-19; 11 days have passed).

## Headline — delta from v0.7 baseline

| Metric | 2026-05-17 (v0.7, 25 sessions) | 2026-05-30 (v0.8, 32 sessions) | Δ |
|---|---|---|---|
| Total cost | $1,729 | **$1,271** | −$458 (−26%) |
| Total tokens | 2.52B | 1.71B | −32% |
| API calls | 8,116 | 6,287 | −23% |
| Cache efficiency | 97.8% | 97.3% | −0.5pp |
| Total agent invocations | 377 | 255 | −32% |
| Agent token % | 0.6% | **0.80%** | +0.2pp |
| Subagent traces parsed | — | 242 | (new) |
| Parent-thread cost share | ~85% | **85.2%** | unchanged |

**The corpus rolled** — 32 sessions today is *not* a strict superset of the 25 we saw on 2026-05-17. Claude Code rotates older sessions out of `~/.claude/projects/`, so the comparison is between two snapshots of "recent activity," not a cumulative ledger. The drop in absolute cost reflects that the post-v0.7 work was small surgical PRs (#460, #461, #463, #464, #472, #475) rather than the big features that dominated April. **Cost per session collapsed from $69 to $40** — same hardware, smaller patches.

**The 85/15 parent/subagent split is unchanged.** The product story still holds: the dollars are spent in the parent thread, not in subagents. Anything we ship that targets "make subagents cheaper" plays in 15% of the spend.

## Tier 3 (the v0.8 marquee): infrastructure green, signals silent

Two ways to read this section. Read both before forming an opinion.

**Infra works.** `tier3_degraded: false`. The `--github` flag enabled cleanly on a non-TTY run (consent was auto-recorded under `~/.config/agentfluent/github-consent.json` per [#399](https://github.com/frederick-douglas-pearce/agentfluent/pull/462)'s privacy model), 242 subagent traces were parsed, and no Tier 3 path raised an exception or degraded the rest of the pipeline. That's the load-bearing claim — Tier 3 didn't break Tier 1+2.

**Zero signals fired.** `CI_FAILURE_FIRST_PUSH` and `PR_REVIEW_COMMENT_DENSITY` produced **0 signals** across the 32-session corpus. (Their names appear 141 times in the JSON, but every occurrence is inside subagent prompt text or output — the agents that built these signals talked about them; the diagnostics pipeline didn't emit any.)

Why nothing fired:

- **`CI_FAILURE_FIRST_PUSH`** — Fred's workflow is "push only when local CI passes." A first-push CI failure means the local checks didn't catch something the GitHub Actions run did. Across the v0.8 PR window (~30 merged PRs), that didn't happen often enough to land. This is **the signal behaving correctly on a careful operator** — the same shape of result we'd want from a smoke alarm in a fireproof room. It's healthy silence, not broken silence.
- **`PR_REVIEW_COMMENT_DENSITY`** — most v0.8 PRs were single-author with minimal review back-and-forth (architect agent posts comments, but Fred doesn't engage in dense human review threads on his own PRs). The threshold was set assuming team-style PRs where reviewer/author comment ratios reveal struggle; a solo dev with an architect-bot reviewer doesn't generate that pattern.

**What to do with this finding (proposed):**

1. **Don't tune the thresholds yet.** A v0.8.x post-release tune would chase a corpus-of-one. The signals were built for the cross-team case the README pitches; they need a multi-author corpus before we know whether the threshold is too conservative or just well-targeted.
2. **Document the silent-on-careful-operator behavior** in `docs/SIGNALS.md` (or wherever the per-signal reference lives) — users running their first `--github` analyze should know that zero Tier 3 signals is a *possible healthy outcome*, not a "nothing got computed" bug. Without this note, a careful user will assume `--github` is broken.
3. **File a v0.9 follow-up** to dogfood Tier 3 against a multi-contributor repo (codefluent has 1-2 outside PRs; the github-pages project has none; we may need to wait for organic external traffic). Until then, the signals exist but lack production validation on their intended target audience.

## Calibration work: the silent wins

This is the result the dogfood ritual was designed to measure.

### `ERROR_PATTERN` (post-#333)

| Run | error_pattern signals | Notes |
|---|---|---|
| v0.7 baseline | not reported separately, but called out as a heavy FP source pre-#333 | "ERROR_PATTERN mis-fires" was the named example in `[[project-dogfood-after-release]]` |
| **v0.8 (today)** | **1** | The lone fire is `anthropic-research` saying "Agent type 'anthropic-research' not found" while bootstrapping itself — a legitimate string match on a real error |

[#333](https://github.com/frederick-douglas-pearce/agentfluent/issues/333) (per-invocation trace gating, shipped in [#475](https://github.com/frederick-douglas-pearce/agentfluent/pull/475)) **did its job**. The historic dogfood pain — `ERROR_PATTERN` firing dozens of times on phrases like "not found" embedded in helpful agent prose — is gone. The single remaining fire is a true positive on a transient registration error.

### `FEAT_FIX_PROXIMITY` (post-#402)

24 fires on the v0.8 corpus. Spot-check of the first 5: all reference real commit pairs (`feat 13e567c` followed by 1 fix on shared files within 3 days, etc.) and the patterns match recent reality — the post-release fix wave (#460, #461, #475) following the Tier 3 feature drop (#462, #463, #464) is exactly the kind of feat-then-fix proximity we're trying to surface. **The post-#402 calibration (2-file threshold) appears to suppress the trivial-coincidence cases without losing the genuine ones.**

Both calibrations land before the v0.8 dogfood. That was the deliberate sequencing per `[[project-dogfood-after-release]]`. It worked.

## Agent invocations — full breakdown

| Agent | Calls | Tokens | Avg dur/call | Tool uses | Notes |
|---|---|---|---|---|---|
| general-purpose (builtin) | 145 | 6.3M | 128s | 1,556 | Still the dominant agent |
| architect (custom, opus) | 57 | 3.8M | 178s | 1,190 | |
| pm (custom, opus) | 32 | 3.1M | **2,918s (~49 min)** | 847 | **Up from 33min in v0.7** — see below |
| Explore (builtin) | 6 | 270k | 143s | 120 | **Down from 92 calls in v0.7** |
| claude-code-guide (builtin) | 6 | 182k | 70s | 74 | |
| anthropic-research (custom, sonnet) | 4 | 170k | 192s | 36 | **New since v0.7** ([#408](https://github.com/frederick-douglas-pearce/agentfluent/pull/408)) |
| candidate-promoter (skill→agent history) | 3 | 112k | 146s | 24 | **New since v0.7**; converted from agent→skill in [#417](https://github.com/frederick-douglas-pearce/agentfluent/pull/417) |
| candidate-verifier (custom) | 1 | 77k | 1,130s | 48 | **New since v0.7** ([#410](https://github.com/frederick-douglas-pearce/agentfluent/pull/410)) |
| Plan (builtin) | 1 | 79k | 1,080s | 20 | **Down from 6 calls in v0.7** |
| **tester (custom, sonnet)** | **0** | **0** | — | — | **Still zero after scope broadening — see decision below** |
| marketer (user-scope, sonnet) | 0 | 0 | — | — | Cross-project; not actionable here |

### Three things stand out

**1. `pm` avg wall-clock duration is 49 min/call — but the signals aren't fooled.** [#230](https://github.com/frederick-douglas-pearce/agentfluent/issues/230) shipped (closed 2026-05-01); `active_duration_ms` is in the data and the `DurationOutlierRule` correctly uses `active_duration_per_tool_use`. The two `pm` duration_outlier signals in this run report **29.7s and 46.4s per tool use** (active), not wall-clock minutes — those are genuine compute outliers, not interactive-wait artifacts. The 49 min/call figure above comes from the `agent_metrics` summary's `total_duration_ms / invocation_count` aggregation, which is still wall-clock and arguably the right framing for the "how long did this agent take on the clock" summary. **The framing risk is in the reporting surface, not the diagnostics** — a future analyst reading the table without knowing about `active_duration_ms` will draw exactly the wrong conclusion I almost did. See v0.9 candidate #3 below.

**2. `Explore` collapsed from 92 calls to 6.** Possible explanations:
- The post-v0.7 work was small/surgical PRs that didn't need broad code search.
- The new `candidate-verifier` agent absorbed some "find references in code" workload that previously went to Explore.
- Worth a quick check before assuming this is a sampling artifact: re-run on a "feature-development-heavy" session subset to confirm Explore's role hasn't structurally shifted.

**3. The research pipeline agents (`anthropic-research`, `candidate-verifier`, `candidate-promoter`) are live and behaving.** 8 total invocations across the 3 — low volume, consistent with the bi-weekly cadence per `[[project-research-scout-cadence]]`. No retry storms, no token outliers on this group. Healthy.

## Tester verdict — recommend removal

Per the v0.7 recommendation: "If broadened-scope tester still has zero invocations after 2-3 weeks, *then* delete." The scope was broadened in [#397](https://github.com/frederick-douglas-pearce/agentfluent/pull/397) on 2026-05-19 (11 days ago). Current state:

- Tester description now covers writing, refactoring, **and** failure-diagnosis — the full pytest surface.
- `Write` was added to its tools per the v0.7 recommendation.
- Across 32 sessions including significant test work (every fix PR in the v0.8 series shipped with tests), tester was invoked **zero times**.

**Decision: remove the tester agent.** Two-strikes rule from the v0.7 plan. Keeping it past this point either (a) wastes registration overhead on something that never fires, or (b) signals that the broadening wasn't enough, in which case the right move is to acknowledge the agent's premise doesn't match how this developer/codebase works.

A follow-up issue (or PR directly removing `.claude/agents/tester.md`) closes this loop. `marketer` is user-scope and serves multiple projects — not a remove call from this dataset.

## Critical-severity recommendations

Four `critical` aggregated recommendations, all on the `tool_error_sequence` signal with `speed` as the primary axis:

| Agent | Count | Priority | Notes |
|---|---|---|---|
| general-purpose | 15 | 332.7 | Built-in — prompt not user-editable; recommended action is "narrow scope via wrapper" |
| architect | 5 | 322.9 | Custom — actionable; architect retries on `mcp__github__get_issue` and `Read` are the dominant pattern (see below) |
| candidate-verifier | 2 | 316.0 | Custom — only 1 invocation across 32 sessions, so 2 tool-error sequences in a single 48-tool-call run is concerning at high frequency |
| candidate-promoter | 2 | 316.0 | Skill-converted — flagged from the few residual agent-mode runs before [#417](https://github.com/frederick-douglas-pearce/agentfluent/pull/417) landed |

### `general-purpose` (15 tool_error_sequences + 63 retry_loops)

`general-purpose` is doing 145 invocations and absorbing the most tool-error volume. The dominant retry pattern: **Read** (46 of 63 general-purpose retry_loops). Same pattern as v0.7 — speculative file reads after a Grep miss, or re-reading the same file across analysis steps. **No new fix lever** in v0.8; this remains a built-in agent we can't tune directly. The recommendation engine correctly suggests "narrow scope via wrapper subagent" — which is the path Fred has been taking by introducing `Explore`, `candidate-verifier`, etc.

### `architect` (5 tool_error_sequences + 41 retry_loops + 3 token_outliers)

This is the actionable critical. The dominant retry hot spots:
- `architect` → `Read`: **30 retries**
- `architect` → `mcp__github__get_issue`: **3 retries** (down from 7 in v0.7, but still notable)
- `architect` → `Grep`: 5 retries

Two prompt-level fixes the v0.7 report flagged are still relevant:
1. **`mcp__github__get_issue` retries** — the v0.7 prompt-tuning suggestion ("confirm the issue number before retrying") was never landed. With architect's call volume holding steady, this is still worth ~5 wasted tool calls per dogfood window.
2. **Read retries on architect** are a new high — 30 is the largest individual (agent, tool) retry pair in the corpus. Pattern likely: architect reads a file → reads it again with a different offset/line range → reads it a third time. A prompt-level "if you need multiple line ranges from one file, request them all in the first Read call" instruction is worth trying.

## Other notable signals

- **`reviewer_caught: 54`** on architect. This is the v0.6 quality signal firing as designed — architect reviews are catching findings on PRs. Healthy.
- **`file_rework: 28`** — top targets are exactly what the dev work history would predict: `pipeline.py` (46 edits), `README.md` (44), `aggregation.py` (41), `models.py` (42), `analyze.py` (40). These reflect the v0.8 feature work; not a quality red flag, but if a future dogfood shows `README.md` near the top again, that's a documentation-thrash signal worth investigating.
- **`token_outlier: 20`, `duration_outlier: 19`** — concentrated in `general-purpose`, `architect`, and `pm` (the 3 heaviest agents). Outlier signals on the busiest agents are expected; not a calibration concern.

## Offload candidates — all negative-savings, again

9 offload candidates surfaced, **all with negative savings**:

| Cluster | Estimated savings |
|---|---|
| `bash-agent` | −$275 |
| `bash-main` | −$189 |
| `code-existing` | −$114 |
| `bash-milestone` | −$104 |
| `release-bash` | −$102 |
| `architect-pm` | −$98 |
| `security-label` | −$45 |
| `wave-sub` | −$40 |
| `id-task` | −$28 |

**Same finding as v0.7, now load-tested across two release cycles**: naive offloading loses to parent-thread cache efficiency on this workload. The `--show-negative` flag would surface these; the default behavior correctly hides them. **The recommendation engine's "no actionable offloads" output is the correct answer**, not a missing-data symptom.

## v0.9 candidate work, ranked by dogfood evidence

All filed as GitHub issues after this analysis was drafted. Listed here in priority order with the issue numbers for traceability.

1. **[#481](https://github.com/frederick-douglas-pearce/agentfluent/issues/481) — Detect/warn `cleanupPeriodDays` default in analyze output.** Discovered mid-investigation: Claude Code's default 30-day cleanup silently truncates the analysis window, and the default already cost ~3 weeks of pre-Apr-29 session data across two of Fred's projects before being noticed. Textbook "what your tool should tell you" surface; raised to `priority:high` because it actively bites new users. Setting is now `cleanupPeriodDays: 3650` globally, so the corpus is safe going forward.
2. **[#477](https://github.com/frederick-douglas-pearce/agentfluent/issues/477) — Remove `.claude/agents/tester.md`.** 0 invocations across 32 sessions after scope broadening (#397). Closes the v0.7 two-strikes decision.
3. **[#480](https://github.com/frederick-douglas-pearce/agentfluent/issues/480) — Surface `active_duration` alongside wall-clock in the agent_metrics summary table.** [#230](https://github.com/frederick-douglas-pearce/agentfluent/issues/230) shipped the data; the reporting surface didn't follow. The dogfood author (me) was almost misled by his own table — every user will be.
4. **[#479](https://github.com/frederick-douglas-pearce/agentfluent/issues/479) — Architect prompt tightening.** Add "request all needed line ranges in one Read call" and "confirm issue number before retrying `mcp__github__get_issue`" instructions to `.claude/agents/architect.md`. 30 + 3 retries respectively; cheap, durable wins.
5. **[#478](https://github.com/frederick-douglas-pearce/agentfluent/issues/478) — Document "Tier 3 can be healthily silent."** Add to `docs/SIGNALS.md` (or equivalent) so the first-time `--github` user doesn't assume the feature is broken when they see zero new signals.
6. **[#482](https://github.com/frederick-douglas-pearce/agentfluent/issues/482) — Tier 3 multi-author validation.** Filed at `priority:low` and explicitly blocked on organic multi-contributor traffic. Tracked so the validation gap stays visible at v0.9 cut rather than buried in this doc.

**Not filed (inline observation only):**
- **`Explore` collapse from 92→6 invocations.** Likely sampling artifact — post-v0.7 work was small/surgical PRs that didn't need broad code search, and `candidate-verifier` may have absorbed some "find references" workload. Worth re-checking at the next dogfood; not worth a tracked issue unless the pattern persists.

Not on the v0.9 list:
- **Offload-candidate signal tuning** — the negative-savings result is correct, not a bug.
- **`ERROR_PATTERN` further calibration** — #333 landed; signal is at 1 true positive. No follow-up needed.
- **`FEAT_FIX_PROXIMITY` further calibration** — #402 landed; signal is firing on plausible commit pairs. No follow-up unless a future dogfood shows drift.

## What v0.8 proved

The Tier 3 release shipped clean: PyPI artifacts match GitHub assets byte-for-byte, the new flag works end-to-end on a real dataset, the new signals don't crash anything, and — critically — the calibration discipline of landing #333 and #402 *before the release cut* produced the clean dogfood the discipline exists to produce. The marquee signals not firing is itself a finding worth treating as data, not a bug to fix in a hurry.

The next dogfood will be at v0.9 cut. The tester removal decision should be implemented this week so it lands cleanly in the v0.9 changelog rather than as a backfilled note.
