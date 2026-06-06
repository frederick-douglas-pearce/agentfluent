# Agent Portfolio Analysis — v0.9 dogfood run

**Date:** 2026-06-05
**Author:** Claude (Opus 4.8, 1M context) with Fred Pearce
**Source:** `uv run agentfluent analyze --project agentfluent --diagnostics --git --github --json`
**Sessions analyzed:** 46
**agentfluent version:** v0.9.0 (published 2026-06-06 02:11 UTC; analyzed from editable install at the v0.9.0 release commit `8422ec3`)
**Baseline:** [`2026-05-30-v08-dogfood/analysis.md`](../2026-05-30-v08-dogfood/analysis.md)
**Raw outputs:** [`analyze.json`](analyze.json), [`analyze.table.txt`](analyze.table.txt)

## Purpose

Fourth dogfood run, first one with the v0.9 "Count Every Turn" surface live. Four goals:

1. **Validate v0.9 ships clean on real data** — model-turn counts populate at every level, the three Advanced Tool Use signals fire (or stay correctly silent), no parse-time exceptions, `tier3_degraded: false`.
2. **Pressure-test the marquee metric (model turns).** Does the new turn-level denominator produce a *diagnostic* on the corpus, or just a number? Does `avg_tool_calls_per_turn` finger the serialization anti-pattern the release was built to expose?
3. **Calibrate the Advanced Tool Use signals in production** — `PARAMETER_RETRY` (#405), `TOOL_ORCHESTRATION_CHAIN` (#406/#407), `TOOL_INVENTORY_OVERSIZED` (#404/#372). These shipped with thin or zero corpus validation; this is their first real-data exposure.
4. **Re-check the v0.8 follow-ups that landed in v0.9** — architect prompt tightening (#479), `active_duration` in the summary table (#480), `cleanupPeriodDays` warning (#481), tester removal (#477).

## Headline — delta across three baselines

| Metric | v0.7 (05-17, 25 sess) | v0.8 (05-30, 32 sess) | **v0.9 (06-05, 46 sess)** | Δ vs v0.8 |
|---|---|---|---|---|
| Total cost | $1,729 | $1,271 | **$1,286** | +$15 (+1%) |
| Total tokens | 2.52B | 1.71B | **1.92B** | +12% |
| API calls | 8,116 | 6,287 | **7,533** | +20% |
| Cache efficiency | 97.8% | 97.3% | **97.2%** | −0.1pp |
| Total agent invocations | 377 | 255 | **301** | +18% |
| Agent token % | 0.6% | 0.80% | **0.90%** | +0.1pp |
| Subagent traces parsed | — | 242 | **286** | +18% |
| **Parent-session model turns** | — | — | **7,533** | (new) |
| **Subagent model turns** | — | — | **3,413** | (new) |
| **`<synthetic>` messages** | — | — | **25** | (new) |
| Cost per session | $69 | $40 | **$28** | −$12 |

**The corpus rolled again, and bigger.** 46 sessions is the largest window yet — Claude Code's `cleanupPeriodDays` is now `3650` globally (the #481 fix), so sessions stopped aging out of `~/.claude/projects/` mid-cycle. This is *not* a strict superset of the v0.8 32; it's a wider, less-truncated snapshot of recent activity. **Cost per session fell again to $28** — the post-v0.8 work was the v0.9 feature batch (model turns + 3 signals + dogfood fixes), shipped as many small PRs (#485–#509) rather than a few large features. The +12% token / +20% API-call rise is volume (14 more sessions), not per-unit inflation: cost/session kept dropping.

**Cache efficiency held at 97.2%.** Four release cycles, four readings within 0.6pp. The parent-thread cache economics that make naive offloading a losing trade (see below) are stable.

## The marquee: model turns produce a real diagnostic on first contact

This is the result the v0.9 theme was built to produce, and it landed.

`total_model_turns` (parent session) is **7,533** — and equals `api_call_count` exactly. The 25 `<synthetic>` ghost responses (Claude Code's locally-fabricated filler, no API round-trip) are tallied separately in `total_synthetic_messages` and excluded from the turn count, per #507/#509. **One thing to verify (see follow-ups):** `model_turns == api_call_count` to the unit is either by-construction or a coincidence worth confirming — and a user (Fred) raised exactly this mid-development (a `user_correction` signal fired in-thread: *"does api calls include synthetic messages... I thought both model turns and api calls..."*). The definitions should be documented as deliberately-equal or deliberately-distinct so the next reader isn't left guessing.

### Per-agent turn efficiency — the headline finding

`avg_tool_calls_per_turn` is the v0.9 ratio that matters: high = the agent batches tool calls within a turn (efficient); low (~1.0) = it serializes one tool call per round-trip (the waste the release targets).

| Agent | Calls | Turns/inv | **Tool calls/turn** | Tokens/turn | $/turn |
|---|---|---|---|---|---|
| claude-code-guide (builtin) | 6 | 8.5 | **2.18** | 5,359 | $0.0051 |
| pm (custom) | 35 | 17.3 | **1.86** | 6,684 | $0.0064 |
| architect (custom) | 72 | 11.4 | **1.78** | 5,842 | $0.0043 |
| anthropic-research (custom) | 4 | 7.3 | 1.64 | 7,714 | $0.0080 |
| Explore (builtin) | 29 | 13.2 | 1.29 | 3,913 | $0.0007 |
| candidate-promoter (custom) | 3 | 7.0 | 1.14 | 5,314 | $0.0055 |
| **general-purpose (builtin)** | **150** | 11.0 | **0.96** | 3,938 | $0.0028 |

**The dominant agent is the least turn-efficient.** `general-purpose` — 150 invocations, the single biggest token consumer (6.5M) — runs at **0.96 tool calls per turn**. That's the serialized anti-pattern stated as a number: it does roughly one tool call, takes a model turn, does the next, takes another turn. The custom agents (`architect`, `pm`) batch at ~1.8 — nearly double the tool-calls-per-turn. On its very first dogfood, the model-turn metric independently fingered the exact agent the prior three dogfoods flagged for retry-loops and tool-error-sequences — but now with a crisp efficiency framing instead of a raw error count.

**The lever is unchanged, the evidence is sharper.** `general-purpose` is built-in; its prompt isn't user-editable. The fix is the one Fred's been executing for three releases — wrap narrow tasks in custom subagents — and the turn metric now *quantifies why it works*: the custom agents he's introduced (`architect`, `pm`, `candidate-verifier`) batch tool calls more tightly than the built-in `general-purpose` they displace. "Count turns, cut waste" is no longer a slogan; it's a column that ranks the portfolio.

## Advanced Tool Use signals: one fires loud, two stay silent

### `PARAMETER_RETRY` (#405) — works, but practically inert on this corpus

**25 fires.** The headline feature — extracting a paste-ready `input_examples` entry from the observed successful call — **works exactly as designed**. Example output:

> *"Subagent 'Explore' retried tool 'Read' 3 times with different parameter shapes before succeeding... Suggested `input_examples` entry for tool 'Read' based on the observed successful call: `{"file_path": "/tmp/simplify_481.diff", "offset": 219, "limit": 60}`"*

The dominant true positive is a textbook one: an agent passed `Read`'s `offset` as an array `[447, 525]` instead of a number, got `InputValidationError: The parameter offset type is expected as number but provided as array`, then retried correctly. 23 of 25 fires reference a real `is_error: true` first attempt; spot-checks confirm genuine validation retries.

**But the corpus exposes a scoping limit: 23 of 25 fires are on the built-in `Read` tool — where the recommendation is not user-actionable.** You cannot add an `input_examples` array to Claude Code's built-in `Read`; the tool definition isn't yours to edit. The signal's value proposition (paste-ready examples that lift complex-parameter accuracy 72%→90%) only *pays off for custom SDK/MCP tool definitions*. On this single-developer, built-in-tool-heavy corpus, the signal is technically correct but practically inert — it's correctly detecting a pattern whose fix the user can't apply. The signal will earn its keep on a corpus with custom MCP tools that have fiddly parameter shapes; this corpus isn't that.

**Two clear false positives (2/25 = 8%).** Both fired with *zero* `is_error` calls in the sequence:
- `architect` "retried `Read` 8 times" — actually reading eight different line-ranges of a notebook-builder script in sequence (legitimate paging, every call `is_error: false`).
- `claude-code-guide` "retried `WebSearch` 2 times" — actually refining a search query (`hooks reference` → `"duration_ms" PostToolUse`), not recovering from a validation error.

In both, the rendered message still reads *"First attempt failed with: '<successful output>'"* — quoting file content or search results as if it were an error string. That's a message-template correctness bug independent of the calibration question: a no-error sequence should not be described as "failed with."

**Recommendation:** file a v0.9.x calibration issue — (a) require at least the first attempt to be `is_error: true` before firing (kills the paging/query-refinement FPs), and (b) when scoring/sorting, deprioritize fires on built-in tools whose definitions the user can't edit, or annotate them as "informational — built-in tool, not user-tunable." The headline `input_examples` extraction is good; it just needs an actionability gate.

### `TOOL_ORCHESTRATION_CHAIN` (#406/#407) — silent-by-design, behaving as shipped

**4 fires, all INFO, all carrying the low-confidence caveat** (D043, #498). Each is the metadata-only proxy flagging a high token-per-tool-call agent (`architect`: 1,244 tool calls / 4.19M tokens; `general-purpose`: 1,072 / 3.48M). As documented at release, this corpus has **no genuine orchestration agents** (no Programmatic-Tool-Calling chains), so these are the expected token-heavy-reasoning false positives the caveat warns about — *not* true positives. The signal is doing exactly what #498 said it would: firing at INFO with an explicit "verify against the agent" hedge. Trace-level precision remains tracked as **#499**; nothing here changes that plan. This matches the `[[project-orchestration-signal-kept-live]]` decision — ship live-with-caveat, fix precision in Tier B.

### `TOOL_INVENTORY_OVERSIZED` (#404/#372) — zero fires, correct silence

**0 fires.** No agent in the portfolio declares >30 tools while exercising under half. The custom agents are tightly scoped (architect, pm, the research agents all have curated tool lists); the built-ins that *do* have broad tool access (`general-purpose`) actually exercise a wide spread. This is **healthy silence** — the same shape as v0.8's Tier 3 signals: the pattern the signal targets doesn't exist in this well-pruned config, so zero is the right answer, not a missing-data symptom. It will fire the day someone points AgentFluent at an agent with a 40-tool MCP bundle it barely touches.

## v0.8 follow-ups that shipped in v0.9 — did they land?

| Issue | Shipped | Dogfood verdict |
|---|---|---|
| **#477** remove `tester` | Yes | ✅ Gone. `tester` no longer appears in the agent table or `unused_agent` signals. (`marketer` is the lone `unused_agent` fire now — user-scope, cross-project, not actionable here, same as v0.8.) |
| **#480** `active_duration` in summary table | Yes | ✅ Present in `analyze.table.txt`. The 49-min/call `pm` framing trap from v0.8 is mitigated — wall-clock and active duration now sit side by side. |
| **#481** `cleanupPeriodDays` warning | Yes | ✅ Correctly *silent* — `cleanupPeriodDays: 3650`, `warnings: []`. The warning path didn't fire because the setting is safe; the corpus growth to 46 sessions is the downstream proof the underlying trap is closed. |
| **#479** architect prompt tightening | Yes (closed) | ⚠️ **No measurable effect yet.** architect→`Read` retries are **29** (was 30 in v0.8); architect→`mcp__github__get_issue` retries are **3** (was 3). Essentially flat. **Caveat:** the corpus is a rolling window spanning *before and after* #479 merged, so most of these 46 sessions predate the fix. This is not a clean post-fix measurement. Re-evaluate at v0.10.0 when the window is fully post-#479 — same corpus-rolling caveat the v0.8 report made. |

Note: the get_issue retry hot spot **migrated** — in v0.8 it was architect (3); in v0.9 the heavier offender is **`pm`→`mcp__github__get_issue` (7 retries)**. The v0.7/v0.8 "confirm the issue number before retrying" prompt fix was applied to architect (#479) but pm carries the same un-tightened pattern. Cheap parallel fix candidate.

## Calibration signals holding steady

- **`ERROR_PATTERN`: 1 fire** — the same lone true positive as v0.8: `anthropic-research` output contains "not found" (self-bootstrap registration string). #333's per-invocation trace gating continues to hold precision at the floor. No drift across two release cycles.
- **`FEAT_FIX_PROXIMITY`: 31 fires** — up from 24 (corpus is larger). Spot-checks reference real commit pairs from the v0.9 feature-then-fix wave (#498, #509 fixing #491/#465). The #402 2-file threshold continues to suppress trivial coincidences. No re-calibration needed.
- **`reviewer_caught`: 62** (was 54), all on `architect`. The v0.6 quality signal firing as designed — architect reviews catching findings on PRs. Healthy and growing with PR volume.

## File rework — README is the documentation-thrash signal the v0.8 report predicted

`file_rework: 31 fires`. Top targets by edit count:

| File | Edits |
|---|---|
| **README.md** | **64** |
| table.py | 53 |
| pipeline.py | 50 |
| models.py | 49 |
| analyze.py | 45 |
| aggregation.py | 44 |
| terms.yaml | 38 |
| quality_signals.py | 38 |

**The v0.8 report called this shot:** *"if a future dogfood shows README.md near the top again, that's a documentation-thrash signal worth investigating."* It's now **#1, at 64 edits** — ahead of every source file. This is the v0.9 docs catch-up (#483/#506) plus the README roadmap churn across the release. It's not a correctness red flag (docs *should* change at a release), but two consecutive dogfoods with README at/near the top suggests the README is absorbing release-note prose that might live more durably in `CHANGELOG.md` or `docs/`. Worth a glance at *what* keeps rewriting the same README sections — if it's the roadmap table re-edited every release, that's a structural fix (link out, don't inline).

## Critical-severity recommendations — same shape, built-in-bound

Four `critical` aggregated recommendations, all `tool_error_sequence`-driven, all on the heavy agents:

| Agent | tool_error_seq | retry_loops (Read) | Actionable? |
|---|---|---|---|
| general-purpose | 14 | 46 | Built-in — wrap-in-subagent only |
| architect | 4 | 29 | Custom — #479 landed, effect TBD (corpus caveat) |
| candidate-verifier | 2 | (low vol) | Custom — 1 invocation, 2 error-seqs in a 33-turn run |
| candidate-promoter | 2 | (low vol) | Skill-converted residual |

`general-purpose`→`Read` (46 retries) remains the largest single (agent, tool) retry pair in the corpus, exactly as in v0.8 — and the new turn metric reframes *why*: at 0.96 tool-calls/turn, general-purpose's serialized Read-then-reason loop is structurally retry-prone. No new built-in fix lever; the wrap-in-custom-subagent path is the answer the engine keeps (correctly) recommending.

## Offload candidates — negative-savings, third cycle running

10 offload candidates, **all ≤ $0 savings** (largest: `architect-pm` −$86, `wave-bash` −$74, `id-task` −$28, `pm-claude` −$27; the rest $0). **Third consecutive dogfood with the same verdict:** naive offloading loses to parent-thread cache efficiency (97.2%) on this workload. The default behavior correctly hides these; "no actionable offloads" is the right answer, not a missing-data symptom. This is now a load-tested invariant of the workload, not a finding.

## v0.10.0 candidate work, ranked by dogfood evidence

All five filed against the **v0.10.0** milestone after this analysis was drafted. Listed in priority order with issue numbers for traceability.

1. **[#510](https://github.com/frederick-douglas-pearce/agentfluent/issues/510) — `PARAMETER_RETRY` actionability gate + message fix (HIGH).** (a) Require ≥1 `is_error: true` attempt before firing — kills the 8% paging/query-refinement false positives. (b) Fix the "First attempt failed with: '<output>'" message when no attempt errored. (c) Deprioritize or annotate fires on built-in tools (`Read`, `WebSearch`) whose definitions the user can't edit — 23/25 fires this cycle were practically inert for that reason. The `input_examples` extraction itself is good; it needs a relevance filter.
2. **[#511](https://github.com/frederick-douglas-pearce/agentfluent/issues/511) — Document `model_turns` vs `api_call_count` relationship (MEDIUM).** They're equal to the unit (7,533) on this corpus. Document whether that's by-construction (both count merged assistant messages) or coincidental, and where `<synthetic>` exclusion applies to each. A user already hit this confusion mid-development; the docs should pre-empt it.
3. **[#512](https://github.com/frederick-douglas-pearce/agentfluent/issues/512) — Apply the architect get_issue/Read prompt tightening to `pm` (LOW-MED).** pm now owns the heaviest `mcp__github__get_issue` retry pair (7), the exact pattern #479 fixed for architect. Cheap, durable, parallel to a fix that already exists.
4. **[#513](https://github.com/frederick-douglas-pearce/agentfluent/issues/513) — Re-measure #479 at v0.10.0 on a fully-post-fix window (LOW).** architect Read/get_issue retries are flat (29/3), but the corpus straddles the #479 merge. Not a clean read. Defer judgment one cycle rather than concluding the prompt fix failed.
5. **[#514](https://github.com/frederick-douglas-pearce/agentfluent/issues/514) — README documentation-thrash investigation (LOW).** Two dogfoods with README as a top `file_rework` target. Check whether release-note/roadmap prose inlined in README should link out to CHANGELOG/docs instead.

**Not filed (inline observation only):**
- `TOOL_ORCHESTRATION_CHAIN` 4 INFO fires — expected corpus artifact, already tracked as #499. No new issue.
- `TOOL_INVENTORY_OVERSIZED` 0 fires — correct silence. No action.
- Offload negative-savings — correct, not a bug. Third confirmation.

## What v0.9 proved

The release shipped clean: model turns populate at every level, the three Advanced Tool Use signals either fired with their documented caveats or stayed correctly silent, `tier3_degraded: false`, no parse exceptions, and every v0.8 follow-up that targeted a reporting gap (active_duration, cleanup warning, tester removal) is verifiably in place. **The marquee metric earned its theme on first contact** — `avg_tool_calls_per_turn` independently surfaced `general-purpose` as the serialization hotspot, turning three releases of raw retry-counts into a single efficiency ranking.

The one genuinely actionable calibration finding is `PARAMETER_RETRY`'s actionability problem: it works, it extracts good examples, but on a built-in-tool-heavy single-dev corpus, 92% of its fires recommend a fix the user can't apply, and 8% fire on non-errors. That's a v0.9.x tune, not a release blocker — exactly the kind of "land the calibration before the *next* cut" the dogfood ritual exists to catch.

The next dogfood will be at v0.10.0 cut. Items 1–3 above should land before then so they show up in the v0.10.0 changelog rather than as backfilled notes.
