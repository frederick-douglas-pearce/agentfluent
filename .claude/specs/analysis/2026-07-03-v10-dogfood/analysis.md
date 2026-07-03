# Agent Portfolio Analysis — v0.10 dogfood run

**Date:** 2026-07-03
**Author:** Claude (Opus 4.8, 1M context) with Fred Pearce
**Source:** `uv run agentfluent analyze --project agentfluent --diagnostics --git --github --json`
**Sessions analyzed:** 70
**agentfluent version:** v0.10.0 (published 2026-07-03 07:31 UTC; analyzed from editable install at the v0.10.0 release commit `27b9acb`)
**Baseline:** [`2026-06-05-v09-dogfood/analysis.md`](../2026-06-05-v09-dogfood/analysis.md)
**Raw outputs:** [`analyze.json`](analyze.json), [`analyze.table.txt`](analyze.table.txt)

## Purpose

Fifth dogfood run, first at the v0.10.0 cut. Five goals:

1. **Validate v0.10 ships clean on real data** — no parse exceptions, `tier3_degraded: false`, `warnings: []`, model-turn counts populate at every level.
2. **Check the three v0.9-follow-up fixes that shipped in v0.10** — `PARAMETER_RETRY` actionability gate (#510/#555), `model_turns`-vs-`api_call_count` docs (#511), hook-coverage recommendations (#424/#425/#426).
3. **First real-data exposure of the hook_inspector surface** — does `DurationOutlierRule` now emit `target: hooks` recommendations, and are they sound?
4. **Re-check the deferred v0.9 candidates** — pm `get_issue` retries (#512), architect #479 re-measurement (#513), README documentation-thrash (#514) — all deferred past v0.10.
5. **First populated delegation-suggestions surface** — the corpus finally crossed the clustering threshold; are the suggestions actionable?

## Headline — delta across four cuts

| Metric | v0.7 (25) | v0.8 (32) | v0.9 (46) | **v0.10 (70)** | Δ vs v0.9 |
|---|---|---|---|---|---|
| Total cost | $1,729 | $1,271 | $1,286 | **$1,847** ⚠️ | +$561 (see caveat) |
| Total tokens | 2.52B | 1.71B | 1.92B | **2.17B** | +13% |
| API calls | 8,116 | 6,287 | 7,533 | **9,415** | +25% |
| Cache efficiency | 97.8% | 97.3% | 97.2% | **97.0%** | −0.2pp |
| Total agent invocations | 377 | 255 | 301 | **367** | +22% |
| Agent token % | 0.6% | 0.80% | 0.90% | **0.8%** | −0.1pp |
| Subagent traces parsed | — | 242 | 286 | **352** | +23% |
| Parent-session model turns | — | — | 7,533 | **9,415** | +25% |
| Subagent model turns | — | — | 3,413 | **3,885** | +14% |
| `<synthetic>` messages | — | — | 25 | **40** | +60% |
| Cost per session | $69 | $40 | $28 | **$26** | −$2 |

**⚠️ The absolute cost is not comparable across the v0.10 boundary.** [#534](https://github.com/frederick-douglas-pearce/agentfluent/issues/534) fixed a pricing bug where 1-hour cache writes were billed at the 5-minute rate — so v0.10 reports cache-write cost *more accurately (higher)* than every prior dogfood. The +$561 total is part corpus growth (70 vs 46 sessions) and part the pricing correction; the two can't be separated from this table alone. **The comparable number is cost-per-session, which held at $26** (v0.9: $28) — real per-session spend kept its downward trend *even after* the pricing fix inflated each session's measured cost. Don't read "+44% total cost" as a spend regression; it's a measurement made honest plus 24 more sessions.

**The corpus rolled again to 70 sessions** — the largest window yet, up from 46. With `cleanupPeriodDays: 3650` global (the v0.9 #481 fix), sessions no longer age out, so each dogfood is a wider, less-truncated snapshot. **Cache efficiency held at 97.0%** — five cuts, all within 0.8pp. The parent-thread cache economics that make naive offloading a losing trade (below) are a load-tested invariant now.

**`total_model_turns == api_call_count` again, exactly (9,415 == 9,415)**, with `<synthetic>` messages (40) tallied and excluded separately — the [#511](https://github.com/frederick-douglas-pearce/agentfluent/issues/511) documentation now covers this equality, so it's expected, not a coincidence to re-investigate.

## The marquee: PARAMETER_RETRY's #510 gate landed — and exposed a residual self-referential false positive

This is the finding of the run, and it's a good one: **AgentFluent's own error-handling vocabulary trips its own error detector when agents read AgentFluent's source.**

[#510/#555](https://github.com/frederick-douglas-pearce/agentfluent/issues/510) shipped the actionability gate: `_build_parameter_retry_signal` now returns `None` unless `run_calls[0].is_error` is true (`trace_signals.py:271`), and the docstring correctly claims this "keeps that message truthful" by keying the message on the first attempt. **The intended fix works** — the v0.9 keyword-regex-on-successful-result class (paging/query-refinement described as "failed with") is gated out.

**But the gate depends on `is_error`, and `is_error` is *synthesized* from result text by `detect_is_error_from_text` when the parser has no explicit flag** (`signals.py:49`). That synthesis matches `ERROR_REGEX` against the leading 200 chars — and for a **successful `Read`/`Grep`, the leading 200 chars are the top of the file**. On this corpus, agents spent the release cycle reading AgentFluent's *own diagnostics source*, whose files are literally named and documented for error handling.

Breakdown of the 25 fires:

| Category | Count | What it is |
|---|---|---|
| **Genuine tool error** | **15** | `<tool_use_error>InputValidationError` (offset-as-array, missing required param), `EISDIR`, `File does not exist`, `File content exceeds maximum allowed tokens` — real first-attempt failures, correctly fired |
| **Self-referential content FP** | **10** | Successful `Read`/`Grep` of source whose *head* contains error vocabulary |

Every one of the 10 false positives is a successful read of AgentFluent's own code or docs:

> - architect → `Grep` `parser.py:33:from agentfluent.diagnostics.signals import detect_is_error_from_text` — the identifier **contains** "error"
> - Explore → `Read` `class ParameterRetryRule: """PARAMETER_RETRY -> recommend input_examples...`
> - candidate-verifier → `Read` `"""Behavior signal extraction... Detects error patterns in output..."`
> - architect → `Read` `def compute_error_rate(inv: AgentInvocation) -> float:`
> - architect → `Read` release-loop skill (`name: release-loop / description: Run one routed iteration of the supervised dev loop...`)
> - pm → `Read` `run_diagnostics` pipeline docstring (×2)

The rendered message quotes the successfully-read source as `First attempt failed with: '1 --- 2 name: release-loop 3 description: ...'` — the exact "quotes success as failure" symptom #510 set out to kill, surviving through a *different* path (`detect_is_error_from_text` synthesis rather than the old keyword-regex fallback). **40% of PARAMETER_RETRY fires on this corpus (10/25) are this self-referential FP.** Layer on the actionability caveat carried from v0.9 — genuine fires are 15/15 on built-in `Read`/`Grep`, whose definitions the user can't add `input_examples` to — and the signal's *practically-actionable* yield on this corpus is effectively zero.

**This is the same root cause #281 already identified** for `iter_error_matches` ("98% of full-text matches were mid-text code identifiers like `tool_error_sequence`, `is_error?`") — but the 200-char leading-window defense that fixed the *counting* path doesn't help the *synthesis* path, because here the error keyword sits at the **very top** of the read (a grep hit line, a class name, a module docstring), not buried deep where the window bound excludes it.

**The tight fix:** on file-reading tools (`Read`/`Grep`/`Glob`), require the result to *start with* a structured error signature (`<tool_use_error>`, `EISDIR`/`ENOENT`/`EACCES`, `File does not exist`, `File content ... exceeds maximum`) rather than merely *contain* error vocabulary anywhere in the leading window. All 15 genuine fires begin with such a signature; all 10 FPs have the keyword mid-line. An anchored check separates them cleanly and is unit-testable from the fixtures already captured. **This should be the top v0.11 candidate.**

## First fires this cycle

### `permission_failure` — pm denied `WebFetch` (first fire since it shipped)

**1 fire, `critical`, `target: tools`:** *"Subagent 'pm' was denied access to tool 'WebFetch' ('blocked')."* The `PERMISSION_FAILURE` signal has existed since #231/#239 (v0.5) but stayed silent across three dogfoods — this is its **first real-data fire**. It's a **true positive with a genuine config question:** the pm agent definition lists `WebFetch` among its tools, yet a settings/permission layer blocked the call at runtime. Either the allow-list should grant `WebFetch` to pm (if research fetches are intended) or pm's prompt should stop reaching for it. Cheap to resolve, and a clean demonstration that the tool-access-audit surface fires correctly when the config and the observed behavior disagree. Worth a quick look at which layer produced the `'blocked'` — agent def vs `settings.local.json`.

### Hook-coverage recommendations (#424/#425/#426) — first dogfood with `target: hooks`

**2 recommendations, both `warning`, both `target: hooks`:** pm and architect each drew *"Add a PostToolUse hook that logs or gates on `duration_ms` to surface slow tool calls,"* driven by `duration_outlier` (pm: 65.8s/tool_use, 5.6×IQR above Q3; architect: up to 3.5×IQR). This is the new `hook_inspector` surface working end-to-end on real data for the first time — `DurationOutlierRule` now has a hook-coverage branch and emits `target: hooks` when a slow agent lacks `duration_ms` instrumentation. The recommendation is sound and actionable (both are custom agents whose configs the user owns). Recommendation-target distribution this run: `prompt: 24, tools: 10, subagent: 4, hooks: 2, description: 1` — the hook target is live and firing conservatively, exactly as intended.

### Delegation suggestions — surface populated for the first time, but low-cohesion

The corpus finally crossed the clustering threshold: **9 delegation suggestions** emitted (v0.9 skipped this surface entirely). The problem is they're **mostly noise on this corpus.** Cohesion scores run 0.29–0.73 (mostly ~0.4), and the auto-generated names/descriptions are vague TF-IDF buckets: `glossary-docs` ("glossary, docs, run"), `py-existing` ("py, existing, reuse"), `efficiency-hot` ("efficiency, hot, path"), `comments-quality`. These cluster the *ad-hoc review/verify subagents* Fred spins up per-PR (`AC-verify #504`, `Reuse review of #372`, `Angle A: line-by-line scan`) — genuinely recurring *shapes* of work, but the clustering keys on surface tokens and produces buckets too generic to paste in as an agent. Two calibration notes: (a) the suggested `model` is pinned to `claude-opus-4-7` (one release stale — current default is `4-8`); (b) low-cohesion clusters (< ~0.4) probably shouldn't surface as *named agent drafts* at all — they read as "you delegate a lot of review work" rather than "define *this* agent." Candidate for a cohesion floor before rendering a `yaml_draft`.

## Model turns — general-purpose is *more* serialized, not less

`avg_tool_calls_per_turn` remains the efficiency ratio that matters (high = batches calls within a turn; ~1.0 = serializes one call per round-trip):

| Agent | Inv | Tool uses | Turns | **Tool calls/turn** | Tokens/turn | $/turn |
|---|---|---|---|---|---|---|
| claude-code-guide (builtin) | 8 | 97 | 45 | **2.16** | 6,041 | $0.0067 |
| pm (custom) | 41 | 981 | 536 | **1.83** | 6,826 | $0.0078 |
| architect (custom) | 90 | 1,451 | 875 | **1.66** | 5,674 | $0.0053 |
| anthropic-research (custom) | 4 | 36 | 22 | 1.64 | 7,714 | $0.0090 |
| candidate-verifier (custom) | 1 | 48 | 33 | 1.45 | 2,341 | $0.0027 |
| Explore (builtin) | 30 | 521 | 402 | 1.30 | 3,922 | $0.0031 |
| candidate-promoter (custom) | 3 | 24 | 21 | 1.14 | 5,314 | $0.0062 |
| **general-purpose (builtin)** | **189** | **1,722** | **1,930** | **0.89** | 3,675 | $0.0031 |

**The dominant agent got *more* serial, not less.** `general-purpose` — 189 invocations (up from 150), the single biggest token consumer at 7.1M — dropped from **0.96 → 0.89 tool calls per turn.** It's doing *less* than one tool call per model turn on average: a Read, a turn, another Read, another turn. The custom agents (architect 1.66, pm 1.83) still batch roughly 2× tighter. Three cuts running, the model-turn metric independently fingers the same structural hotspot: the built-in `general-purpose` catch-all is the serialization sink, and the lever is unchanged — wrap narrow tasks in custom subagents. Its prompt isn't user-editable, so the only fix is displacement, which the delegation-suggestions surface (above) is trying — however clumsily — to automate.

## Deferred v0.9 candidates — status on the v0.10 window

**#512 shipped; #513/#514 are open on v0.11.0.** All three re-measure against a corpus that *straddles* their fix dates, so none is a clean post-fix read.

| Candidate | v0.9 | **v0.10** | Verdict |
|---|---|---|---|
| **#512** pm `get_issue` retries (apply #479 prompt fix to pm) | 7 | **8** | **Shipped** — pm.md was tightened (`mcp__github__get_issue` 404-handling, closed COMPLETED 2026-06-30). But pm.md is a *user-global* agent (no `src/` artifact, maintainer-tooling only), and the fix landed 3 days before this cut — most of the 70-session corpus (and most of pm's 41 invocations) predate it. The 8 retries are a **pre-fix reading**, not evidence the fix failed. Same corpus-straddle caveat as #479/#513; re-measure at v0.11 on a fully-post-fix window. Do not re-file. |
| **#513** re-measure #479 (architect `Read`/`get_issue`) | 29 / 3 | **32 / 4** | ⬆️ flat-to-up, but **not measurable:** architect's `Read` retries are contaminated by the *same self-referential pattern* as PARAMETER_RETRY — `retry_loop` counts consecutive same-tool `Read`s, many of which are legitimate paging through AgentFluent's own multi-file diffs, not error recovery. #479's effect stays un-readable until the `retry_loop` denominator gets the same paging-vs-error disambiguation the PARAMETER_RETRY fix implies (see v0.11 candidate 3). Defer judgment again. |
| **#514** README documentation-thrash | #1 (64 edits) | **#2 (66 edits)** | README held ~flat but is no longer the top `file_rework` target — it's displaced by `.claude/loop/v0.10.0/queue.md` (79) and `prd-loop-engineering.md` (65), the loop-engineering pilot artifacts churned all cycle. README thrash is real but *stable*, not accelerating; the structural fix (link release prose out to CHANGELOG) is still the right call but low-urgency. Already filed + milestoned. |

## Calibration signals holding steady

- **`ERROR_PATTERN`: 1 fire** — the same lone true positive as the last two cuts: `anthropic-research` output contains "not found" (self-bootstrap registration string). #333's per-invocation trace gating holds precision at the floor across three cycles. No drift.
- **`reviewer_caught`: 69** (was 62), all on architect — the v0.6 quality signal firing as designed, growing with PR volume. Healthy.
- **`feat_fix_proximity`: 31** — identical to v0.9. The #402 2-file threshold continues to suppress trivial coincidences. No re-calibration needed.
- **`user_correction`: 6** — real mid-thread steering (architect-review requests, the "does api calls include synthetic messages" question that motivated #511, a `/simplify` framing correction). Working.
- **`TOOL_ORCHESTRATION_CHAIN`: 4 INFO**, all carrying the low-confidence caveat — the expected metadata-only proxy on token-heavy agents, no genuine orchestration on this corpus. Unchanged from v0.9; still tracked as #499.
- **`unused_agent`: 1** — `marketer` (user-scope, cross-project, 0 invocations here), the same lone not-actionable-here fire.

## Critical-severity recommendations — same shape, still built-in-bound

Five `critical` aggregated recommendations:

| Agent | Signal | Count | Actionable? |
|---|---|---|---|
| general-purpose | tool_error_sequence | 16 | Built-in — wrap-in-subagent only |
| architect | tool_error_sequence | 4 | Custom — but see #513 paging caveat |
| candidate-verifier | tool_error_sequence | 2 | Custom — 1 invocation, low volume |
| candidate-promoter | tool_error_sequence | 2 | Skill-converted residual |
| **pm** | **permission_failure** | 1 | **Custom — new, genuinely actionable (WebFetch allow-list)** |

Four of five are the familiar `tool_error_sequence` shape on the heavy agents; the fifth — pm's `permission_failure` — is the one fresh, cleanly-actionable critical this cycle.

## Offload candidates — negative-savings, fourth cycle running

10 offload candidates, **all ≤ $0 savings** (largest: `bash-agent` −$354, `bash-main` −$180, `release-bash` −$142, `loop-task` −$134; smallest `prices-genai` −$12). **Fourth consecutive dogfood with the same verdict:** naive offloading loses to parent-thread cache efficiency (97.0%) on this workload. The alternative-model targets updated to `claude-opus-4-8 → claude-sonnet-4-6` (concrete target model present, validating #170). The default behavior correctly hides these; "no actionable offloads" remains the right answer, not a missing-data symptom. This is a load-tested invariant of the workload now, not a finding.

## v0.11 candidates, ranked by dogfood evidence

Listed in priority order. Two are genuinely-new engine issues filed against **v0.11.0**; the rest reconcile to existing backlog items.

1. **PARAMETER_RETRY: anchor `is_error` synthesis to a leading error signature on file-reading tools (HIGH — [#580](https://github.com/frederick-douglas-pearce/agentfluent/issues/580)).** 40% of fires (10/25) are self-referential FPs — successful `Read`/`Grep` of error-handling source whose head contains error vocabulary. Require `Read`/`Grep`/`Glob` results to *start with* a structured error signature (`<tool_use_error>`, `EISDIR`/`ENOENT`/`EACCES`, `File does not exist`, `File content ... exceeds maximum`) before synthesizing `is_error`. All 15 genuine fires pass; all 10 FPs fail. Unit-testable from captured fixtures. Same root cause as #281; the 200-char window doesn't cover head-of-file keywords.
2. **`retry_loop` paging-vs-error disambiguation (MED — [#581](https://github.com/frederick-douglas-pearce/agentfluent/issues/581), unblocks #513).** `retry_loop` counts consecutive same-tool `Read`s, conflating legitimate multi-file paging with error recovery — which is *why* #513 (#479 re-measurement) can't get a clean read. Apply the same first-attempt-error gate PARAMETER_RETRY uses. This is the shared root cause behind candidate 1 and the un-readable architect/pm retry numbers.
3. **Delegation-suggestions cohesion floor + model-pin refresh (MED — [#183](https://github.com/frederick-douglas-pearce/agentfluent/issues/183) comment).** The surface populated for the first time but renders low-cohesion (~0.3) TF-IDF buckets as named agent drafts. Add a cohesion floor (~0.4) below which a cluster surfaces as an *observation* rather than a paste-ready `yaml_draft`, and refresh the stale `claude-opus-4-7` model pin. #183 ("delegation drafts: skill-aware provenance + actionability note") already owns this surface — logged there as a dogfood evidence comment rather than a competing issue.

**Reconciled to existing issues (not re-filed):**
- **#512** pm `get_issue` — closed COMPLETED; shipped as a user-global pm.md edit, corpus predates it. Re-measure at v0.11, no new work.
- **#513** #479 re-measurement — open on v0.11.0; blocked on candidate 2. Commented with the v0.10 numbers + the disambiguation dependency.
- **#514** README thrash — open on v0.11.0; commented with the v0.10 data point (stable ~65, dropped to #2 behind loop-engineering artifacts).

**Not filed (inline observation only):**
- `TOOL_ORCHESTRATION_CHAIN` 4 INFO — expected corpus artifact, tracked as #499.
- Offload negative-savings — correct, fourth confirmation, not a bug.
- pm `WebFetch` `permission_failure` — a config decision for Fred (grant vs remove), not an engine bug; resolve directly.

## What v0.10 proved

The release shipped clean: 70 sessions parsed with `tier3_degraded: false`, `warnings: []`, no exceptions; model turns populate at every level and `total_model_turns == api_call_count` exactly (9,415) with `<synthetic>` (40) correctly excluded and now documented (#511). The two new surfaces both fired correctly on first real-data contact — `hook_inspector` drew sound `target: hooks` duration recommendations for pm and architect (#424/#425/#426), and `permission_failure` caught a real config gap (pm→WebFetch) on its first-ever fire. #534's pricing correction means absolute cost isn't comparable to prior cuts, but cost-per-session held at $26 through the fix — the honest number kept trending down.

The one genuinely actionable engine finding is the **PARAMETER_RETRY self-referential false positive**: #510's `is_error` gate closed the class it targeted, but 40% of fires on this corpus are AgentFluent's own error-handling vocabulary tripping `detect_is_error_from_text` when agents successfully read its source. It's a precise, testable, one-rule fix — and a fitting dogfood result: the tool that diagnoses agents got caught mis-diagnosing the agents that *build the tool*. That's exactly the eating-its-own-tail signal the dogfood ritual exists to surface, and it should land before the v0.11 cut.

The next dogfood will be at v0.11.0. Candidates 1 (the PARAMETER_RETRY anchor) and 2 (the shared `retry_loop` disambiguation, which also unblocks #513) should land before then so they show up in the v0.11 changelog rather than as backfilled notes.
