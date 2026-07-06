# PRD: AgentFluent v0.11 -- Recognize the Primary Audience

**Status:** Ratified 2026-07-06 (issues filed; see epic + stories below)
**Date:** 2026-07-06
**Author:** PM Agent
**Governing decision:** [D013](decisions.md) (main-session model-routing scope -- AgentFluent owns SDK main sessions, not Claude Code interactive main sessions). [D001](decisions.md) (Python-only MVP -- bounds the TS-SDK question). [D049](decisions.md) (this release's scoping/milestone call).
**Upstream discovery:** `.claude/specs/agent-sdk-session-format-findings.md` (epic #517, closed in v0.10). This PRD operationalizes that doc's [§8 "Downstream follow-ups"](agent-sdk-session-format-findings.md).
**Primary downstream consumer (now pulled IN):** [#112](https://github.com/frederick-douglas-pearce/agentfluent/issues/112) (model-routing diagnostics for the main Agent SDK session).

---

## 1. Theme

**"Recognize the primary audience."**

v0.10 was themed *"Meet the Primary Audience"* -- it made first empirical contact with Agent SDK session data and ended, deliberately, at **"the corpus exists and the format is documented."** The durable deliverable was a findings doc, not a shipped capability: AgentFluent could not yet *tell* an SDK-hosted agent session apart from a Claude Code interactive session, even though the two are co-located in the same `~/.claude/projects/` directories and the tool's stated PRIMARY audience is the SDK developer.

v0.11 closes that gap **comprehensively** (full scope -- Fred chose depth over a limited cut; "no rush" to prod). It turns the discovery into first-class ingestion, a visible indicator, the multi-level trace linker, the first SDK-specific diagnostic (#112), and -- the headline enabler -- a **repo-tracked Agent SDK dogfood-runner** that accrues real SDK session history *during* the dev cycle so the post-release dogfood has meaningful data instead of a cold start.

One-line pitch: **"AgentFluent now knows an SDK-hosted agent when it sees one, says so, routes its models, and dogfoods itself with one."**

### Why this theme, why now

The findings doc (§2) established that a reliable **intrinsic discriminator already exists** -- `entrypoint == "sdk-py"` on 119/119 corpus lines vs `entrypoint == "cli"` for Claude Code interactive. No heuristic, no `--scope` flag, no user input required. Surfacing it is the load-bearing primitive that gates D013 correctness: main-session diagnostics must apply to SDK mains but NOT to Claude Code interactive mains. Full scope this release means we not only surface it and label it, but build the first consumer (#112) and the trace linker (S5) on top, and stand up the dogfood-runner (S0) that exercises exactly those surfaces.

## 2. Goals

1. **Stand up a repo-tracked Agent SDK dogfood-runner** (S0) that runs AgentFluent's own dogfood analysis by fanning out subagents over a **bounded rolling window** of the corpus, each driving the real `agentfluent analyze` CLI, and scheduled on a cadence from day one so it accrues real SDK session history during the v0.11 cycle.
2. **Surface the SDK-vs-Claude-Code discriminator** (S1): read `entrypoint`, derive session classification `sdk`/`cli`/`unknown`.
3. **Show the indicator in `analyze`** (S2): CLI badge + JSON emitting **both** `session_kind` and raw `entrypoint`.
4. **Surface `toolUseResult.resolvedModel`** (S3) on `ToolResultMetadata`.
5. **Make SDK line-type skipping intentional + fix the doc nit** (S4).
6. **Build the multi-level trace-to-invocation linker** (S5), settling the `totalTokens` inclusivity/double-counting question explicitly.
7. **Build #112** -- model-routing diagnostics for the SDK main session, the first consumer of S1/S3.
8. **Keep the pricing foundation + retry calibration moving; ship catch-up docs.**

## 3. Non-Goals

- **Verifying the TypeScript SDK `sdk-ts` value.** Inferred but unverified (findings §2); D001 scopes the MVP to Python. S1's classifier is forward-compatible (any `entrypoint` beginning `sdk` classifies as SDK) so no TS probe is needed to avoid mislabeling a future TS session -- but confirming the `sdk-ts` string is out of scope.
- **Non-model SDK options metadata** (`permissionMode`, `allowed_tools`, `mcp_servers`, loaded skills/plugins). Findings §3 established these are **runtime-only** (delivered via the `SystemMessage(init)` stream event, never written to JSONL). Unrecoverable from session files -- no story possible.
- **Porting the research-scout** into the dogfood-runner. The scout is throwaway here: that work is migrating to the `claude-code-sessions` repo, so porting it would be superseded. S0 is a purpose-built dogfood-runner, not a scout port.
- **Completing cost-lever coverage** -- discretionary levers (fast-mode #536, server-tool surcharges #539) move to v0.12's cost-lever epic (§7).
- LLM-powered analysis (D035). Auto-applying fixes (D002). Webapp dashboard, cross-project aggregation.

## 4. In Scope

### Stream A: Agent SDK Ingestion + Dogfood-Runner (epic `epic:agent-sdk-ingestion`, v0.11)

Full scope. Fixtures already committed (`tests/fixtures/sdk_session/`, `tests/fixtures/nested_session/`) back S1-S3/S5 -- no corpus generation for those; S0 generates *live* sessions.

| Story | Title | Type | Priority | Deps |
|-------|-------|------|----------|------|
| S0 | Repo-tracked Agent SDK dogfood-runner + cadence schedule | chore | high | None -- **BUILD FIRST** |
| S1 | Surface `entrypoint`; derive session classification sdk/cli/unknown | feat | high | None |
| S2 | SDK-vs-Claude-Code indicator in `analyze` (CLI badge + JSON `session_kind` + raw `entrypoint`) | feat | high | S1 |
| S3 | Surface `toolUseResult.resolvedModel` on `ToolResultMetadata` | feat | high | None |
| S4 | Add SDK line types to `SKIP_TYPES`; fix `status` doc-example nit | chore | medium | None |
| S5 | Multi-level trace-to-invocation linker (cross-file join + `parent_invocation_id`; settle `totalTokens` inclusivity) | feat | high | S3 (uses surfaced result metadata) |
| #112 | Model-routing diagnostics for the SDK main session (first consumer of S1/S3) | enhancement | high | S1, S3 |

**S0 -- Dogfood-runner SDK agent + cadence (BUILD FIRST, the headline enabler).**
A **repo-tracked** Agent SDK agent (uses `query()`, lives in the repo as code -- NOT a user-global Claude Code subagent) that runs AgentFluent's own dogfood analysis. It **fans out subagents** over the corpus (e.g. one per project-slug or per signal-category), each **driving the real `agentfluent analyze` CLI** (thin orchestration -- it does NOT reimplement analysis), and the parent synthesizes a report. It doubles as the canonical **example SDK agent** for docs/tests.
- **Distinct from #522.** #522 was a *synthetic matrix generator* for discovery (produced sessions as the deliberate product). S0 does *real work* (dogfood analysis) where session data is a *byproduct*.
- **Why this over a research-scout port:** (a) it is the platonic AgentFluent user -- an SDK agent doing agent-quality analysis; (b) durable -- dogfood is a permanent need, so it can't be migrated away (the scout will be, to `claude-code-sessions`); (c) it automates the manual post-release dogfood ritual Fred runs every release; (d) self-reinforcing -- each run's sessions feed the next cycle's corpus.
- **Bootstrap:** starts by analyzing the existing Claude Code corpus (which AgentFluent parses today) and graduates to SDK-session analysis as S1-S5 land -- so it produces real SDK sessions from day one without waiting on parser work.
- **Bounded rolling analysis window (NOT the full corpus each run).** Each run analyzes a bounded, configurable rolling window rather than the entire historical corpus: (a) lighter data, (b) more relevant diffs, (c) surfaces *sudden deltas* -- a spike against a recent baseline is an early warning that "we just introduced a problem," which whole-corpus re-analysis would drown out. This is AgentFluent's own regression-detection value proposition pointed inward. Window length keys off the fixed loop interval (daily loop -> last few days, enough overlap to catch deltas cleanly); exact length is a **build-time tuning decision, not a filing decision** -- just require a bounded window is *configurable and applied*. Reuse the existing date-range / `--since` surface (D024/D025; `prd-date-range-filtering.md`) on each `analyze` call -- not net-new analysis. Comparing consecutive windows is exactly what `agentfluent diff` does, so the runner naturally dogfoods the regression surface too.
- **Because it spawns subagents and can route parent-Opus/subagent-Haiku, it exercises S5's trace linker and #112's model-routing signal** -- it dogfoods the exact v0.11 surfaces.
- **DoD MUST include BOTH** (a) building the agent AND (b) scheduling it on a cadence (repo cron/scheduled job, same scheduling surface as the #451 research-scout cron; start on a simple daily-ish interval the moment it lands). The scheduling is NOT a follow-up -- the entire value is accruing real SDK session history *during* the v0.11 cycle. An event-based trigger (on merge to main) is a noted future enhancement, not MVP.
- **Commit scope:** maintainer/dogfood infra + example, not pip-installed CLI behavior -> `chore:` per the CLAUDE.md scope convention.

**S1 -- Surface `entrypoint` + session classification.**
- *Given* an SDK session (all lines `entrypoint == "sdk-py"`), *when* parsed, *then* messages expose `entrypoint` and the session classifies `sdk`. *Given* `entrypoint == "cli"`, classifies `cli`. *Given* a line missing `entrypoint`, classifies `unknown`, no exception.
- Classifier keys on `entrypoint.startswith("sdk")` so a future `sdk-ts` classifies as SDK without a probe. Version-pin caveat comment (SDK >= 0.2.106 / CLI 2.1.185). Fixture-tested across all three states.

**S2 -- Indicator in `analyze` (headline user-facing delta).**
- *Given* an SDK session, `analyze` shows a visible SDK badge/label distinct from Claude Code; JSON emits **both** `session_kind: "sdk"|"cli"|"unknown"` **and** the raw `entrypoint`. *Given* `unknown`, renders a neutral/unlabeled state (no crash, no false "CC" claim). Snapshot test covers all three states.

**S3 -- Surface `resolvedModel`.**
- Add `resolved_model: str | None` (alias `resolvedModel`) to `ToolResultMetadata`. *Given* the model-divergence fixture (sonnet parent -> haiku child), `metadata.resolved_model` == child model; absent field -> `None`, no crash.

**S4 -- `SKIP_TYPES` hygiene + doc nit.**
- Add `queue-operation, attachment, last-prompt, ai-title` to `SKIP_TYPES` in `core/session.py`. Parser skips them intentionally; no change to extracted data. Fix `CLAUDE.md` `status: "success"` example to `"completed"`.

**S5 -- Multi-level trace-to-invocation linker.**
- Cross-file `toolUseId` join linking a subagent trace file to its invoking `tool_use`; derive `parent_invocation_id` at every depth (flat-at-all-depths layout, findings §4).
- **Named sub-task in the ACs: settle the `totalTokens` inclusivity/double-counting question.** Determine whether a parent invocation's `totalTokens` already includes its children's tokens before any aggregation sums them, and encode the decision so no consumer double-counts. This is the real analytical risk -- "no rush" means resolve it, not defer it.
- Fixture: `tests/fixtures/nested_session/` (multi-level) + `sdk_session/` (level-1 rollup).

**#112 -- Model-routing diagnostics for the SDK main session (first consumer).**
- Pulled into v0.11. Its pre-discovery ACs are revisited per findings §7: "Detect Agent SDK sessions" is satisfied by S1's classifier; complexity classification applies to the main session via per-assistant `message.model`; subagent-vs-main distinction uses `isSidechain` + S3's `resolved_model`. Emits model-routing recommendations for `ClaudeAgentOptions.model`; reuses the #95 pricing infra for cost-savings; output distinguishes SDK-main from subagent suggestions.

### Stream B: Pricing Foundation (kept in v0.11)

| # | Title | Disposition |
|---|-------|-------------|
| #545 | genai-prices dependency + adapter, replace hand-maintained `_PRICING` base rates | keep -- foundational |
| #546 | native date-aware lookup via genai-prices constraints + session timestamp | keep |
| #547 | formalize base + overlay merge seam, document in `COST_MODEL.md` | keep |

### Stream C: Retry-loop calibration (kept)

| # | Title | Disposition |
|---|-------|-------------|
| #580 | PARAMETER_RETRY: anchor is_error synthesis to a leading error signature | keep |
| #581 | retry_loop: disambiguate legitimate paging from error recovery (unblocks #513) | keep |

### Stream D: Dogfood & Docs (kept / carried)

| # | Title | Disposition |
|---|-------|-------------|
| #513 | Re-measure architect prompt tightening (#479) at dogfood | keep (needs #581) |
| #514 | Investigate README documentation-thrash | carried |
| #469 | analytics: per-turn diagnostic ratios (stub) | carried tracking stub |
| #549 | docs: catch up README + GLOSSARY + CHANGELOG for v0.11.0 | required, auto-created |

### Bumped to v0.12 (partial pricing bump, §7)

| # | Title | Rationale |
|---|-------|-----------|
| #536 | feat(pricing): price fast mode (usage.speed) premium rates | discretionary cost-lever -> v0.12 cost-lever epic |
| #539 | feat(pricing): account for server-side tool surcharges | discretionary cost-lever -> v0.12 cost-lever epic |

## 5. Sizing Sanity Check

| Release | Issues |
|---------|--------|
| v0.8 | 13 |
| v0.9 | 18 |
| v0.10 | ~15 |
| **v0.11 (full scope)** | **~20** (7 SDK stream incl. #112 + epic, 3 pricing, 2 retry, 4 dogfood/docs) |

Top of the band -- appropriate for a deliberately comprehensive release with "no rush" to prod. The SDK stream carries the weight (S0 dogfood-runner, S5 linker, and #112 are the meaty items); the parser-surfacing stories (S1-S4) are small and fixture-backed.

## 6. Dependencies

```
STREAM A (SDK)
[S0 dogfood-runner + cadence]  -- BUILD FIRST; bootstraps on CC corpus, graduates to SDK
[S1 entrypoint + classify]     -- load-bearing primitive
[S2 indicator]                 -- depends S1
[S3 resolvedModel]             -- independent
[S4 SKIP_TYPES + doc nit]      -- independent, trivial
[S5 trace linker]              -- depends S3; settle totalTokens inclusivity (named sub-task)
[#112 model routing]           -- depends S1 + S3

STREAM B (pricing foundation): [#545] -> [#546], [#547]
STREAM C (retry): [#580]; [#581] -> unblocks [#513]
STREAM D (docs): [#513] needs #581; [#514],[#469] carried; [#549] last
```

Sequence: **S0 first** (accrue history early) -> S1 -> S2; S3/S4 parallel; then #112 + S5 (heavier consumer/linker). Streams A/B/C parallelizable.

## 7. Milestone recommendation (ratified: PARTIAL BUMP)

**SDK is the v0.11 headline; pricing partially bumped.**
- **Keep in v0.11:** pricing foundation #545/#546/#547 (load-bearing for v0.12's cost-lever epic; independent of SDK; small).
- **Bump to v0.12:** discretionary cost-levers #536 (fast-mode) + #539 (server-tool surcharges) -- same category as v0.12's existing data-residency/batch levers; consolidate under v0.12's "complete Claude cost-lever coverage" epic (#535). Both already carry `epic:cost-coverage`, so no epic is split.
- Rejected: full coexist (two competing marquees, no headline) and full pricing bump (stalls the #545 foundation, overloads v0.12).

## 8. Separate backlog item (NOT in this epic, NOT v0.11)

**Competitive-landscape research agent** (#596) -- low priority (Fred likes it; backlog, no milestone). A periodic SDK research agent (parallel subagents, one per tool) watching **competing/overlapping tools** in AgentFluent's space (agent-trace evaluation, improvement-recommendation products), feeding `docs/AGENT_ANALYTICS_RESEARCH.md`. **Scoped tightly to competing tools** so it does NOT overlap/drift into the existing anthropic-feature-watch pipeline (Anthropic first-party + adjacent ecosystem chatter). Candidate to become a durable dogfood source later, but not now.

## 9. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| S5 `totalTokens` double-counting | Corrupted cost/efficiency aggregates | Named AC sub-task resolves inclusivity before any consumer sums; fixture-locked |
| S0 accrues little history before release | Cold-start dogfood anyway | Cadence scheduled the moment S0 lands (DoD requirement), bootstrapped on CC corpus from day one |
| `entrypoint` value drifts on SDK/CLI upgrade | Indicator mislabels | Version-pin caveat; `unknown` degrades gracefully; classifier keys on `sdk` prefix |
| Future TS `sdk-ts` never verified | TS sessions mislabeled | Prefix-match classifies any `sdk*` as SDK -- forward-compat without a probe |
| S0 CLI-driving subagents mask real analysis errors | Dogfood reports false-green | S0 is thin orchestration over the real CLI; it surfaces CLI exit/errors rather than reimplementing analysis |

## 10. Success Criteria

1. S0 dogfood-runner built AND scheduled on a cadence; runs a bounded rolling window; produces a synthesized report; accrues SDK sessions during the cycle.
2. Parser surfaces `entrypoint` + classifies sdk/cli/unknown (S1), three-state fixture test.
3. `analyze` shows the indicator; JSON emits both `session_kind` + `entrypoint` (S2).
4. `resolved_model` surfaced on `ToolResultMetadata` (S3), divergence-fixture test.
5. SKIP_TYPES intentional for the four SDK types; `status` doc-example fixed (S4).
6. Trace linker joins cross-file invocations + derives `parent_invocation_id`; `totalTokens` inclusivity resolved and encoded (S5).
7. #112 emits SDK main-session model-routing recs, distinguishing main from subagent.
8. Pricing foundation (#545/#546/#547) + retry (#580/#581) land; #513 measured at dogfood.
9. All new production code >80% coverage; no regressions.
10. Docs reflect what shipped (#549).

## 11. Release Checklist

- [ ] S0 merged: dogfood-runner built + bounded rolling window + cadence scheduled + example-agent role documented
- [ ] S1 merged: `entrypoint` + classification, three-state fixture test
- [ ] S2 merged: indicator in CLI + JSON (both fields); unknown renders neutrally
- [ ] S3 merged: `resolved_model` on `ToolResultMetadata`
- [ ] S4 merged: SKIP_TYPES + `CLAUDE.md` status example
- [ ] S5 merged: trace linker + `totalTokens` inclusivity resolved (named sub-task)
- [ ] #112 merged: SDK main-session model routing
- [ ] #545/#546/#547 merged (pricing foundation); #580/#581 merged (retry)
- [ ] #536/#539 moved to v0.12
- [ ] #513 measured; #514 investigated; #469 assessed
- [ ] #549 merged: docs catch-up
- [ ] Competitive-landscape research agent (#596) filed (backlog, low priority)
- [ ] D049 appended to `decisions.md`
- [ ] `pytest --cov` >80%; `ruff` clean; `mypy` clean; version bump to 0.11.0
