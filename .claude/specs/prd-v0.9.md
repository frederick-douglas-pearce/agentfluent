# PRD: AgentFluent v0.9 -- Count Every Turn

**Status:** Draft
**Date:** 2026-05-30
**Author:** PM Agent
**Decision log:** See `decisions.md` D040-D042 for scoping decisions.
**Backlog:** See `backlog-v0.9.md` for the full sequenced backlog.

---

## 1. Theme

**"Count every turn."**

v0.8 sharpened existing signals: duration metrics stopped lying, retry noise was suppressed, reviewer effectiveness reads correctly, and Tier 3 GitHub enrichment extended the data surface. The tool now detects, explains, and shares findings across three diagnostics axes using three data sources (JSONL, local git, GitHub API). The v0.8 dogfood confirmed the calibration work landed: ERROR_PATTERN precision went from 0% to 100%, FEAT_FIX_PROXIMITY is firing on plausible commit pairs, and Tier 3 infrastructure shipped clean.

But the tool still lacks a fundamental metric: **how many model turns did an agent take?**

A model turn is one merged assistant message -- one API round-trip. It is distinct from tool calls (actions within a turn) and tokens (the cost of each turn). An agent that calls 10 tools in 3 turns is fundamentally different from one that calls 10 tools in 10 turns: the first batches tool calls within turns (efficient); the second serializes them (wasteful). The difference is invisible in today's output.

The v0.8 dogfood analysis (`.claude/specs/analysis/2026-05-30-v08-dogfood/analysis.md`) exposed this gap directly: the parent-session `assistant_message_count` field is already computed at `analytics/pipeline.py:81` but is not named as "model turns," not shown in the CLI output, and not available in the JSON schema as a distinct metric. Subagent traces contain the raw data but don't expose turn counts. The `agentfluent diff` command has no turn-level comparisons.

Fred's insight from the v0.8 dogfood: **"If you reduce turns for a given task while keeping quality high, cost and velocity drop more than any other fix."** Turns are the headline metric for agent efficiency. v0.9 makes them visible at every level of the analytics stack.

Alongside the model-turn infrastructure, v0.9 ships two complementary streams:

- **Dogfood-surfaced fixes** -- the v0.8 dogfood (#477-#481) exposed concrete reporting gaps: `active_duration` not shown alongside wall-clock in the summary table (#480), the `cleanupPeriodDays` silent truncation trap (#481), and documentation gaps for Tier 3 healthy silence (#478). These are cheap wins that compound user trust.

- **Advanced Tool Use diagnostics** (Epic #403) -- three new diagnostic signals (`PARAMETER_RETRY`, `TOOL_ORCHESTRATION_CHAIN`, `TOOL_INVENTORY_OVERSIZED`) detecting patterns that Anthropic's Advanced Tool Use engineering blog post documented with quantified impact. This is the natural complement to model-turn metrics: turns tell you *how many round-trips* an agent makes; the Advanced Tool Use signals tell you *why those round-trips are wasteful* and point to platform features that fix each pattern.

One-line pitch: **"Count turns. Cut waste. Ship the signals that explain why."**

### Why this theme

The alternative was to focus on broadening detection further (webapp dashboard, cross-project aggregation, config file layer) or to focus exclusively on Advanced Tool Use diagnostics. Both paths were rejected because:

1. **Model-turn metrics are the missing foundation.** Existing metrics (tokens, tool calls, duration, cost) all benefit from turn-level normalization. "20 tool errors" is ambiguous; "0.8 tool errors per turn across 25 turns" is diagnostic. The turn count is a natural denominator for every per-invocation metric, and the model-turn stories (#465-#470) were designed as a batch for this reason.

2. **The model-turn + Advanced Tool Use pairing is synergistic.** `TOOL_ORCHESTRATION_CHAIN` (#406) detects agents with high tool-call counts per turn -- a ratio that only makes sense once model turns exist as a first-class metric. `avg_tool_calls_per_turn` (computed in #467) is the exact denominator the orchestration-chain signal needs. Shipping them together means the analytics and diagnostics tell a coherent story.

3. **The dogfood fixes are cheap insurance.** Three small issues (#477, #478, #480, #481) from the v0.8 analysis fix reporting gaps that misled the tool's own author. Landing them before the next dogfood prevents the same confusion from recurring.

## 2. Goals

1. **Surface model-turn counts at every analytics level** -- parent session (#465), subagent invocation (#466), per-agent-type rollup with efficiency ratios (#467), and diff integration (#470)
2. **Investigate trace-missing invocations** -- determine why ~20% of subagent invocations lack trace files (#468) and whether the current `None` treatment is correct long-term
3. **Ship three Advanced Tool Use diagnostic signals** -- `PARAMETER_RETRY` (#405), `TOOL_ORCHESTRATION_CHAIN` (#406 + #407 calibration), and `TOOL_INVENTORY_OVERSIZED` (#404 + #372)
4. **Fix dogfood-surfaced reporting gaps** -- `active_duration` in summary table (#480), `cleanupPeriodDays` warning (#481), Tier 3 healthy-silence docs (#478)
5. **Clean up agent configuration** based on dogfood evidence -- remove dead tester agent (#477), tighten architect prompt (#479)
6. **Ship docs that reflect what shipped** -- catch-up issue (new)

## 3. Non-Goals

- LLM-powered analysis (stays rule-based; D035 tracks candidates)
- Auto-applying recommended fixes (D002)
- Webapp dashboard
- Cross-project aggregation
- Config file layer (`~/.config/agentfluent/config.yaml`)
- Per-turn diagnostic ratios (#469) -- stub only, requires dogfood validation after #465-#467 ship
- Tier 3 multi-author corpus validation (#482) -- blocked on external traffic, tracked separately
- `agentfluent report` for `diff` output -- deferred from v0.7 and v0.8, still deferred
- Tool-description quality rubric (#373) -- research component, not v0.9
- Tool-schema token attribution (#374) -- analytics enhancement, blocked on #373
- Tool Search regression in diff (#375) -- blocked on #374
- Conventional Commits scope enforcement automation (#447) -- tracked, not urgently needed

## 4. In Scope

### Stream A: Model-Turn Integration (Epic #403-adjacent, under `epic:analytics`)

Six stories surfacing model-turn counts across the analytics stack. Dependency-ordered. All six are already filed as GitHub issues.

| # | Title | Effort | Priority | Deps |
|---|-------|--------|----------|------|
| #465 | Surface parent-session model-turn count in CLI and JSON | XS (~0.5 day) | medium | None |
| #466 | Add model-turn count to SubagentTrace and AgentInvocation | S (~1 day) | medium | None |
| #467 | Per-agent-type model-turn rollup and efficiency ratios | S (~1 day) | medium | #466 |
| #468 | Research: investigate trace-missing subagent invocations (~20%) | XS (~0.5 day) | medium | None |
| #470 | Integrate model-turn metrics into `agentfluent diff` | S-M (~1-1.5 days) | medium | #465, #467 |
| #469 | Per-turn diagnostic ratios (stub -- requires dogfood) | TBD | low | #467 |

**Note:** #469 is a scope placeholder, not implementation-ready. It requires dogfood validation after #465-#467 ship to determine which per-turn ratios are diagnostically useful. Its acceptance criteria explicitly state "NOT a commitment to implement." It is included in v0.9 scope as a tracking item only -- it ships as a stub or defers to v0.10 depending on dogfood results.

### Stream B: Advanced Tool Use Diagnostics (Epic #403)

Four stories for three new diagnostic signals. Already filed as GitHub issues with full specs. Detailed spec at `.claude/specs/prd-advanced-tool-use-diagnostics.md`.

| # | Title | Effort | Priority | Deps |
|---|-------|--------|----------|------|
| #405 | `PARAMETER_RETRY` signal with paste-ready `input_examples` extraction | M (2-3 days) | high | None |
| #404 | Graduate #372: refresh citations, upgrade priority | XS (<0.5 day) | high | None |
| #406 | `TOOL_ORCHESTRATION_CHAIN` signal (Tier A metadata-only) | M (2-3 days) | high | None |
| #407 | `TOOL_ORCHESTRATION_CHAIN` calibration check | S (1-2 days) | medium | #406 |

**Note:** #372 (`TOOL_INVENTORY_OVERSIZED`) is the implementation story that #404 refreshes. #404 is an update task; #372 is the build. Together they count as one signal delivery. #372 is already filed.

### Stream C: Dogfood Fixes (v0.8 analysis follow-ups)

Five stories addressing concrete issues surfaced in the v0.8 dogfood analysis. All already filed.

| # | Title | Effort | Priority | Deps |
|---|-------|--------|----------|------|
| #481 | Detect/warn `cleanupPeriodDays` at default (30) | S-M (1-2 days) | high | None |
| #480 | Surface `active_duration` alongside wall-clock in agent summary | S (1-2 days) | medium | None |
| #478 | Document "Tier 3 can be healthily silent" | XS (<0.5 day) | medium | None |
| #477 | Remove tester subagent | XS (<0.5 day) | medium | None |
| #479 | Architect prompt tightening (Read + get_issue retries) | XS (<0.5 day) | medium | None |

### Stream D: Docs

| # | Title | Effort | Priority | Deps |
|---|-------|--------|----------|------|
| NEW | docs: catch up README + GLOSSARY + CHANGELOG for v0.9.0 | M (2-3 days) | required | All features |

**Total in-scope: ~17 issues (16 must-include + 1 stub tracking), ~17-26 dev days**

## 5. Research Grounding

### Model-turn research sources

The model-turn metric concept and its integration plan were developed from the following sources:

1. **v0.8 dogfood analysis** (`.claude/specs/analysis/2026-05-30-v08-dogfood/analysis.md`) -- exposed the gap: `assistant_message_count` exists in the pipeline but is invisible in output. The analysis author was working with turn-adjacent data (tool calls, duration, cost) without the unifying denominator.

2. **Issue bodies #465-#470** -- contain the detailed design rationale. Key insights documented in-issue:
   - **#465:** `assistant_message_count` is already computed at `pipeline.py:81` via `_count_type(messages, "assistant")`. The work is surfacing + naming, not computing.
   - **#466:** `model_turns` and `tool_calls` are independently meaningful -- neither bounds the other (assistant messages can have zero tool calls; a single turn can have many). The issue explicitly warns against documenting `len(tool_calls) >= model_turns` as an invariant.
   - **#467:** Fred's quote: "If you reduce turns for a given task while keeping quality high, cost and velocity drop more than any other fix." The ratios section documents why each ratio matters.
   - **#468:** ~20% trace-missing rate (6 of 31 pm invocations, all from session `08b03d83-...`). Five hypotheses documented; investigation is research-only, no code change.

3. **v0.8 dogfood JSON** (`.claude/specs/analysis/2026-05-30-v08-dogfood/analyze.json`) -- contains `assistant_message_count` per session, confirming the backing data exists. Example values: 20, 16, 85, 71 turns across analyzed sessions.

4. **Existing codebase** -- `assistant_message_count` computed at `analytics/pipeline.py:163` via `_count_type(messages, "assistant")`. `SubagentTrace` in `traces/models.py` stores parsed trace messages but does not count assistant messages.

**Research gap:** No standalone model-turn research document exists. The research is distributed across the issue bodies and dogfood analysis. This is adequate for the scope -- the metric is well-defined (one merged assistant message = one turn), the backing data exists, and the design decisions are documented in-issue. A consolidated research document is unnecessary given the XS-S sizing of the individual stories.

### Advanced Tool Use research

Documented at `.claude/specs/prd-advanced-tool-use-diagnostics.md` (full PRD) and in the Anthropic engineering blog post cited in Epic #403. Quantitative anchors: 79.5% -> 88.1% accuracy (Tool Search), 37% token reduction (Programmatic Tool Calling), 72% -> 90% accuracy (Tool Use Examples). LLM-call augmentation tracking discipline established in D035.

## 6. Dependencies

```
STREAM A (Model-Turn Integration) -- partially sequential
[#465 parent-session turns]     -- independent
[#466 subagent-invocation turns] -- independent
[#467 per-agent-type rollup]    -- depends on #466
[#468 trace-missing research]   -- independent
[#470 diff integration]         -- depends on #465, #467
[#469 per-turn diagnostic stub] -- depends on #467 (stub only; full impl deferred)

STREAM B (Advanced Tool Use Diagnostics) -- mostly independent
[#404 graduate #372]            -- independent
[#405 PARAMETER_RETRY]          -- independent
[#406 TOOL_ORCHESTRATION_CHAIN] -- independent
[#407 calibration check]        -- depends on #406

STREAM C (Dogfood Fixes) -- all independent
[#481 cleanupPeriodDays warning] -- independent
[#480 active_duration in table]  -- independent
[#478 Tier 3 silence docs]      -- independent
[#477 remove tester]             -- independent
[#479 architect prompt]          -- independent

STREAM D (Docs) -- last
[NEW docs catch-up]              -- after all features
```

### Cross-stream independence

Streams A, B, and C have zero cross-dependencies and can be implemented in any interleaving. Within Stream A, the dependency chain is `#466 -> #467 -> #470` and `#465 -> #470` (both feed into diff integration). Within Stream B, `#406 -> #407` is the only chain. Stream D depends on all features being final.

### Synergy between streams (but not dependency)

`#467` (avg_tool_calls_per_turn) produces the exact ratio that makes `#406` (TOOL_ORCHESTRATION_CHAIN) more interpretable. They don't depend on each other mechanically, but shipping them together means the analytics and diagnostics tell a coherent story.

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `TOOL_ORCHESTRATION_CHAIN` precision < 70% on dogfood data | Signal ships with known FP rate, undermining diagnostics trust | #407 calibration check gates release. If precision < 70%, tune thresholds before shipping. Signal is INFO severity (not WARNING) to reflect precision uncertainty. |
| #469 (per-turn diagnostic ratios) scope creep | Stub becomes implementation commitment, adds 3-5 days | Explicit acceptance criteria in #469 say "NOT implementation-ready." Defers to v0.10 if dogfood data is insufficient. |
| `cleanupPeriodDays` detection (#481) reading wrong config path | Warning fires incorrectly or misses the setting | AC requires testing all three states (missing, default, configured). Both global and project-level settings.json checked. |
| Fragment-merging affects turn counts | `model_turns` != raw JSONL line count, confusing users | #465 AC explicitly documents "one merged assistant message" definition. GLOSSARY entry clarifies. |
| Trace-missing research (#468) reveals a CC bug, not a version boundary | Complicates the `None` treatment in #466/#467 | #468 deliverable is a written finding + recommendation. If a code fix is warranted, it becomes a separate v0.9.1 story. |

## 8. Success Criteria

v0.9 is successful when:

1. **Model turns are visible at every level.** `agentfluent analyze` output shows model turns in Token Usage (parent session), Agent Invocations (per-type avg turns), and Per-Invocation Detail (per-invocation turns). JSON output includes `model_turns` / `total_model_turns` at both session and aggregate levels.
2. **Turn-based efficiency ratios are computed.** Per-agent-type `avg_turns_per_invocation`, `avg_tool_calls_per_turn`, `avg_tokens_per_turn`, and `estimated_avg_cost_per_turn_usd` are in the JSON envelope and visible in CLI where appropriate.
3. **Diff shows turn deltas.** `agentfluent diff` shows model-turn changes per agent type and at the parent-session level. Pre-turn-era envelopes degrade gracefully (0 fallback, no crash).
4. **Trace-missing investigation is documented.** #468 comment posted with findings, hypothesis assessment, and code-change recommendation (if any).
5. **`PARAMETER_RETRY` fires on dogfood data.** At least one TP detection on the agentfluent or CodeFluent corpus with a paste-ready `input_examples` extraction.
6. **`TOOL_ORCHESTRATION_CHAIN` calibration passes.** >= 15 detections sampled, precision >= 70%. If not, one round of threshold tuning completes before release.
7. **`cleanupPeriodDays` warning fires.** On a fresh install with default settings, `agentfluent analyze` warns about the 30-day cleanup window.
8. **`active_duration` shown alongside wall-clock.** Agent summary table distinguishes wall-clock from active duration. pm's misleading 49-min average is no longer the only number a reader sees.
9. **All new code has >80% test coverage.** No regressions.
10. **Docs reflect what shipped.** README, GLOSSARY, CHANGELOG all updated.

## 9. Release Checklist

- [ ] #465 merged: parent-session model turns in CLI + JSON
- [ ] #466 merged: model turns on SubagentTrace + AgentInvocation
- [ ] #467 merged: per-agent-type turn rollup + efficiency ratios
- [ ] #468 documented: trace-missing investigation findings posted
- [ ] #470 merged: model-turn metrics in diff
- [ ] #469 assessed: dogfood validation determines if per-turn ratios are implementable in v0.9 or deferred
- [ ] #405 merged: PARAMETER_RETRY signal
- [ ] #404 + #372 merged: TOOL_INVENTORY_OVERSIZED graduated + implemented
- [ ] #406 merged: TOOL_ORCHESTRATION_CHAIN signal
- [ ] #407 completed: calibration check (gates release -- threshold tune if needed)
- [ ] #481 merged: cleanupPeriodDays detection + warning
- [ ] #480 merged: active_duration in agent summary table
- [ ] #478 merged: Tier 3 healthy-silence documentation
- [ ] #477 merged: tester agent removed
- [ ] #479 merged: architect prompt tightened
- [ ] NEW merged: docs catch-up for v0.9.0
- [ ] Dogfood run: model turns visible at all levels, Advanced Tool Use signals fire on real data
- [ ] `uv run pytest --cov=agentfluent` passes with >80% coverage
- [ ] `uv run ruff check src/` clean
- [ ] `uv run mypy src/agentfluent/` clean
- [ ] CHANGELOG updated via release-please
- [ ] Version bump to 0.9.0
