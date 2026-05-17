# Agent Portfolio Analysis — v0.7 dogfood run

**Date:** 2026-05-17
**Author:** Claude (Opus 4.7) with Fred Pearce
**Source:** `uv run agentfluent analyze --project agentfluent --diagnostics --git --json`
**Sessions analyzed:** 25 (up from 11 in the 2026-04-29 baseline)
**agentfluent version:** v0.7.0 (installed) — published earlier today
**Baseline:** [`2026-04-29-agent-portfolio-analysis.md`](2026-04-29-agent-portfolio-analysis.md)

## Purpose

Second dogfood run. Three goals:

1. Answer specific questions on the **tester agent** decision (still unused — keep or remove?).
2. Use the new v0.7 signals (`unused_agent`, `feat_fix_proximity`, `--git`) to surface things the v0.4-era run couldn't.
3. Sanity-check the agentfluent recommendations against the lived experience of the last ~3 weeks of dev work — verify the tool's output matches reality before relying on it for v0.8 scoping.

## Key numbers — delta from baseline

| Metric | 2026-04-29 (v0.4, 11 sessions) | 2026-05-17 (v0.7, 25 sessions) | Δ |
|---|---|---|---|
| Total cost | $861 | $1,729 | +$868 (+101%) |
| Total tokens | 1.33B | 2.52B | +89% |
| API calls | 4,095 | 8,116 | +98% |
| Cache efficiency | 98.4% | 97.8% | −0.6pp |
| Total agent invocations | 209 | 377 | +80% |
| **Agent token %** | **~0.5%** | **0.6%** | unchanged |
| Sessions added | — | +14 sessions in 18 days | ~5 sessions/week |

**The parent-thread thesis still holds** — 99.4% of tokens are parent-thread. The product story hasn't shifted: the dollars are spent in the parent, not in subagents.

**Costs scaled roughly linearly with sessions** ($69/session avg in v0.4 era → $69/session avg in v0.7 era). The v0.6 quality-axis work + v0.7 release work didn't change the per-session cost profile materially.

## Agent invocations — full breakdown

| Agent | Calls | Tokens | Avg dur/call | Tool uses |
|---|---|---|---|---|
| general-purpose (builtin) | 178 | 6.6M | 104s | 1,575 |
| Explore (builtin) | 92 | 2.7M | **210s** | 1,050 |
| architect (custom, opus) | 60 | 3.6M | 141s | 1,064 |
| pm (custom, opus) | 38 | 2.3M | **1,998s** (~33min) | 602 |
| Plan (builtin) | 6 | 258k | 290s | 71 |
| claude-code-guide (builtin) | 3 | 67k | 47s | 21 |
| **tester (custom, sonnet)** | **0** | **0** | — | — |

**Three observations standing out:**
- **`tester` still at zero invocations** across 25 sessions — addressed below.
- **`general-purpose` doubled invocations** (62→178) and is now by far the most-used agent. That's where ~half of all subagent activity routes.
- **`pm` avg duration 1,998s (33 min)** — double the 999s seen in the 4/29 baseline. This is the [#230](https://github.com/frederick-douglas-pearce/agentfluent/issues/230) wait-time-vs-active-duration measurement issue *not yet shipped* (still all `duration_ms`, no `active_duration_ms` on invocations). Calling it out below as a still-pending data-quality fix.

## Tester verdict — recommend keeping (with new scope), not removing

**Data**: 0 invocations across 25 sessions over 18 days. The `unused_agent` signal fires cleanly:

> Agent 'tester' is defined in /home/fdpearce/Documents/Projects/git/agentfluent/.claude/agents/tester.md but has 0 invocations across 25 analyzed sessions.

But the picture is more interesting than "delete it."

**Why it never fired**: re-reading the tester definition + the original justification, tester's trigger is very specific — "one or more pytest tests are failing." That's a narrow surface. Most agentfluent test work in the last 3 weeks was:
- *Writing new* tests for v0.7 features (which tester's spec explicitly excludes)
- Fixing tests that broke due to *intended behavior changes* (also excluded)
- The standard `uv run pytest -m "not integration"` green path that doesn't need an agent at all

So tester didn't fire because **the situations it's scoped for didn't arise often enough to clear the trigger threshold**, not because the agent itself is broken.

**What does the data say about the problem we built tester to solve?** The 4/29 run pointed to `general-purpose`'s 22 tool_error_sequences + 29 retry_loops on test-loop workflows. In the v0.7 data, `general-purpose` retry_loops jumped to **62** and tool_error_sequences to **18**. So:
- The underlying signal that motivated tester (retry-prone test loops in general-purpose) is **still there and worse** in absolute terms.
- But the work of writing-new-tests is what's driving that retry volume now (per qualitative observation), not pytest failure diagnosis.

**Recommendation**: don't remove. Instead, **broaden the scope** to include test *authoring* and *refactoring*, not just failure diagnosis. The spec was deliberately conservative to leave the Haiku downgrade open, but 18 days of zero invocations is evidence the conservative scope is too narrow. Specific changes to consider:
- Remove the "Do NOT invoke for: writing new tests, designing test strategy, refactoring tests" exclusions from the description.
- Keep the model at sonnet for now (designing new tests is more complex than fixing existing ones; Haiku downgrade can come later via `model_mismatch: overspec` signal once we have observation data).
- Add tools: `Write` (currently has `Read, Edit, Bash, Grep` only). Authoring tests needs file creation.

**Re-evaluate in the next dogfood run.** If broadened-scope tester still has zero invocations after 2-3 weeks, *then* delete.

**Marketer agent is the other unused_agent signal.** It lives at `~/.claude/agents/marketer.md` (user-scope, not project), so it's relevant across all my projects. Different context — not yet a "remove" call.

## Model downgrade opportunities — none yet

**`model_mismatch` signals: 0.** No agent has enough invocations on a too-powerful model to trigger the overspec gate yet. So this run gives no automated downgrade signal.

Manual read of the by-model breakdown:
- claude-opus-4-7 parent: $1,472 (85% of total)
- claude-opus-4-6 + 4-7 subagent: $175 (10%)
- claude-haiku-4-5 subagent: $7.70 (0.4%) — likely the Explore agent

The savings opportunity isn't a model downgrade on existing subagents — it's the parent-thread share that's the lever. Which leads to the offload section.

## Offload candidates — all negative-savings, as expected

The v0.7 negative-savings filter (#344) correctly suppresses 2 candidates with negative estimated savings:
- `existing-code` (39 invocations, opus→sonnet, -$284) — high-confidence cluster but the alternative model's cost-per-token would exceed parent-thread cache savings.
- `bash-pr` (454 invocations, opus→sonnet, -$1,617) — massive cluster but same economics.

**This is the prior dogfood's "naive offloading loses to cache efficiency" finding made quantitative.** The filter is doing its job; the recommendation is *not* to offload these to subagents. They need a different strategy if at all (e.g., delegating to Haiku rather than Sonnet, or accepting that high-cache-efficiency parent-thread work is genuinely the right place for them).

## Tool error issues — retry hot spots

| Tool | Retry count | Top retry-er |
|---|---|---|
| **Read** | **104** | general-purpose (most), architect |
| Bash | 17 | mixed |
| Edit | 9 | mixed |
| `mcp__github__get_issue` | 8 | architect (most) |
| Grep | 6 | mixed |
| `mcp__github__search_code` | 3 | architect |

**`Read` retries are the biggest single source of waste — 104 of 152 total retry_loops (68%).** Two patterns likely explaining most of these:

1. **Speculative file reads after a Grep miss** — agent searches for a symbol, doesn't find it, tries Read with slightly-different paths.
2. **Re-reading the same file across sequential analysis steps** when the agent doesn't remember it already read it.

Neither has an obvious config-level fix (these are built-in tool behaviors). One pattern worth investigating:

- **The `architect` agent's `mcp__github__get_issue` retries** (8 of 8). architect frequently fetches issues; retries suggest either rate-limiting, transient failures, or the agent retrying after a 404 on a guessed issue number. **Worth adding to architect's prompt**: "When `mcp__github__get_issue` returns an error, do not retry without first confirming the issue number is correct — check the user's message or the URL bar of any link they provided."

## New v0.7 signals firing

### `unused_agent` (2 signals)
- `tester` (addressed above)
- `marketer` (user-scope; cross-project context required)

### `feat_fix_proximity` (33 signals — the new `--git` data)

The new Tier 2 signal fires substantially on this repo. Of 33 feat→fix pairs:
- 15 had a reviewer in the loop (INFO severity)
- 14 had NO reviewer (WARNING)
- 4 had no matching session (unknown reviewer state)

**Interpretation**: of 18 "warning" severity feat→fix pairs, ~78% are real "shipped without review, fixed shortly after" patterns. The architect agent runs frequently (60 invocations, 54 reviewer_caught signals, 31% parent_acted rate) — but it's not gating *every* feature commit. That's an actionable insight:

- **Consider invoking architect more reliably during feature work**, especially anything touching diagnostics rules, CLI command surfaces, or JSON envelope schema. Those are the change classes most prone to the v0.7 feat-then-fix pattern (e.g., the `--session` scope rework needed three follow-on PRs).

### `reviewer_caught` (54 signals — 31% parent_acted rate)

architect's findings have a 31% follow-through rate — meaning ~1 in 3 architect recommendations result in parent thread edits to the mentioned files. That's a real-but-improvable number. The recommendation engine flags this as "audit architect's prompt to see why follow-through is patchy."

**Hypothesis worth testing**: architect output is often long and prose-heavy. The parent thread may miss specific actionable items in the wall of text. Consider tightening architect's output convention to lead with a numbered action list before the analysis prose.

## Delegation cluster suggestions — top recurring parent-thread work

The clustering pass surfaces 9 patterns. Top candidates worth considering as new subagents:

| Cluster | Size | Tools | Confidence | New agent worth building? |
|---|---|---|---|---|
| `py-existing` | 39 | Bash, Read | medium | **Maybe** — existing-Python investigation pattern. Probably what `general-purpose` is doing today; specializing would help. |
| `efficiency-hot` | 34 | Bash, Read | medium | Read-only performance investigation. Could route to Haiku for cost savings. |
| `comments-py` | 49 | Bash, Read | low | Confidence too low to act on; investigate if it persists. |
| `cluster-members` | 10 | Bash, Read | medium | Likely internal to agentfluent's own clustering work — meta. |
| `pricing-sonnet` | 6 | Bash, Read | medium | Small cluster; probably one-off pricing-update work. |
| `calibration-notebook` | 9 | Bash, Read | medium | Calibration sweep workflow; recurring. |
| `release-yml` | 5 | Bash, Glob, Read | medium | release-please tinkering; probably not subagent-worthy. |
| `text--detect-is-error` | 6 | Bash, Read | medium | The same suggestion that surfaced in the 4/29 run. Still hasn't become an agent. |
| `py-json` | 20 | Bash, Grep, Read | low | JSON-envelope work; mostly already done in v0.7. |

**Honest read**: none of these are slam-dunk new-agent candidates. `py-existing` (39 invocations) is the most promising but conceptually overlaps with what `general-purpose` does — building a `py-explore` agent would just be re-skinning Explore with a Python-narrowed prompt. The win would be in retry reduction (specialized agent = better tool routing), not cost.

**A more interesting alternative**: instead of building a new agent, **tighten the description on existing agents** to capture more of these clusters. The architect, pm, and Explore descriptions could be updated to more aggressively claim recurring patterns that today route to `general-purpose` by default.

## Decisions / next actions

| Action | Owner | When |
|---|---|---|
| **Broaden `tester` scope** to include test authoring + refactoring; add `Write` tool | shipped this PR | done |
| **Update architect prompt** with `mcp__github__get_issue` retry guidance | shipped this PR (`~/.claude/agents/architect.md`, user-scope) | done |
| **#394** — extend `active_duration_ms` to non-trace agents (pm wait-time problem) | v0.8 | filed |
| **#395** — down-weight `retry_loop` on built-in tools (Read noise) | v0.8 | filed |
| **#396** — `reviewer_caught` parent_acted interpretation (account for legitimate rejection) | v0.8 | filed |
| Defer building `py-existing` agent — re-evaluate next dogfood run after tester scope change observed | — | next dogfood |
| Defer marketer-agent decision — separate cross-project context | — | — |
| Keep `gh-watcher` deferred — still hasn't surfaced as a recurring need in v0.7 dogfood | — | — |

## pm agent efficiency — beyond the measurement fix

The wait-time measurement fix (#394) makes pm's *reported* duration honest, but doesn't make pm itself faster. Separately worth considering for the next round of pm-spec tightening:

- **Batch question-asking**. pm currently fires `AskUserQuestion` mid-stream when ambiguity arises, which incurs a context-switch cost on the user *and* an idle-wait cost on pm. Tightening pm's prompt to make explicit assumptions and ask once at well-defined checkpoints (rather than every step) would reduce both. Pattern: "I'll assume X, Y, Z and proceed. If you want a different call on any of these, stop me before [milestone]."
- **Restrict the wandering surface**. pm currently has broad tool access (Read + Write to `.claude/specs/`, GitHub MCP). Most pm sessions stay in a narrow lane (read PRD context → propose stories → write specs). The retry_loop count (18 for pm) is non-trivial; tightening the description to say "if your task requires reading project source code in depth, return to the parent for that work" might steer pm out of the rabbit holes that drive retries.
- **Acknowledge the human-in-the-loop reality in the spec**. pm is the most user-coupled agent in the portfolio by design. The spec should explicitly call this out so dogfood readers (and future-me) don't try to optimize wait time away — some wait is intentional. The #394 fix and any pm-tightening should both treat "active duration" as the metric, not wall-clock.

## v0.8 input from this run

Three findings that should inform v0.8 scoping:

1. **`feat_fix_proximity` is producing useful signal at v0.7's default thresholds.** 33 detections, ~half with no-reviewer evidence, on a single repo over 90 days. Worth a v0.8 calibration check to confirm precision (are the 14 no-reviewer pairs *really* quality misses, or are some explained by intentional iteration?) — see how reviewer_caught's calibration was handled in #274.

2. **The retry_loop signal on `Read` (104 detections) is dominant noise.** Most are not actionable at the agent-config layer because Read is a built-in tool. Worth considering whether retry_loop should down-weight built-in-tool retries or split the signal into "retry on user-editable tool" vs "retry on built-in tool" — only the former is actionable.

3. **Negative-savings offload-candidate filtering (#344) is the right call.** The two real candidates here had massive cluster sizes (39, 454) but truly negative savings. Without the filter, the CLI table would have led with two "don't do this" recommendations. The fix is working as intended.

## Marketing notes (out of scope, for the eventual blog post)

The 4/29 dogfood doc's "save real money" / "wall-time-and-reliability" framing still holds. Two specific updates from this run:

- **Concrete dollar number for the parent-thread thesis**: $1,472 of $1,729 (85%) is opus-on-the-parent-thread on this project, on this month. That's a real, citable figure.
- **The `tester` story is honest evidence the tool tells the truth about your own decisions.** "I built an agent based on agentfluent's recommendation; agentfluent then told me I never used it. Here's what I learned about agent-spec scope from that." That's a more interesting Show-HN angle than "look at this useful tool" — it's a tool that argues back.
