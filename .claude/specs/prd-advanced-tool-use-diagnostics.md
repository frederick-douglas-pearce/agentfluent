# PRD: Advanced Tool Use Diagnostics

**Status:** Draft
**Date:** 2026-05-18
**Author:** PM Agent
**Decision log:** See `decisions.md` D035 for the LLM-call-augmentation tracking discipline.
**Target release:** v0.9 (feature-specific PRD; the v0.9 release PRD will reference this doc)

---

## 1. Motivation

Anthropic's engineering blog post ["Advanced Tool Use"](https://www.anthropic.com/engineering/advanced-tool-use) (published 2026-05) describes three platform capabilities that directly improve agent tool-call accuracy and token efficiency:

| Capability | What it does | Quantitative anchor |
|---|---|---|
| **Tool Search Tool** | Dynamic tool discovery via `defer_loading: true` -- only loads relevant tool schemas per turn | Opus 4.5: 79.5% -> 88.1% accuracy; 85% context-window reduction (122k -> 191k tokens preserved) |
| **Programmatic Tool Calling** | Tools invoked from within a `code_execution` sandbox; intermediate results stay in the sandbox, not the context window | 37% token reduction (43,588 -> 27,297) on complex research tasks |
| **Tool Use Examples** | `input_examples` array on tool definitions; demonstrates parameter conventions that JSON schemas cannot express | 72% -> 90% accuracy on complex parameter handling |

All three capabilities are **observable from existing JSONL session data.** AgentFluent can detect the behavioral symptoms these features address (oversized tool inventories, wasteful orchestration chains, parameter-retry patterns) and recommend the specific platform feature that fixes each one. This is directly in AgentFluent's core value prop: observe behavior, recommend config changes.

### Why this is v0.9 scope, not v0.8

v0.8 ("Sharpen the signal") prioritizes fixing misleading signals over broadening detection (see `prd-v0.8.md` Section 1). v0.9 swings the pendulum back toward breadth. This feature is the marquee item for that swing because:

1. **Public attention creates a timing window.** The article is generating attention among Agent SDK developers -- AgentFluent's primary audience. Shipping diagnostics that reference and quantify these capabilities while the article is fresh maximizes adoption.
2. **The data is already available.** All three signals can be extracted from existing JSONL session data + `toolUseResult` metadata. No new data sources required (contrast with Tier 3 GitHub enrichment, which needed `gh` CLI integration).
3. **The recommendations are concrete and paste-ready.** Unlike some diagnostics that say "consider improving X," these point to specific platform features with documented configuration syntax. The Tool Use Examples signal can even extract the successful parameter shape from the JSONL and present it as a paste-ready `input_examples` entry.

### Relationship to parked epic #371

Epic #371 (Tool-inventory diagnostics for large-surface agents) was filed 2026-05-14 and PARKED with the explicit relevance trigger: "Anthropic ships `defer_loading` in the Agent SDK (not just Claude Code)." The Advanced Tool Use article confirms `defer_loading` is now available as a platform-level API feature (`tools[].defer_loading: true` in `messages.create`), not just Claude Code's internal implementation. **The relevance trigger has fired.**

#371's child story #372 (`TOOL_INVENTORY_OVERSIZED`) is the Tool Search diagnostic signal. This PRD incorporates #372 and adds two new signals for the other two capabilities. See Section 4 for the #371 graduation recommendation.

---

## 2. Goals

1. **Ship `TOOL_ORCHESTRATION_CHAIN` diagnostic signal** detecting multi-step tool-call chains with large intermediate payloads, recommending Programmatic Tool Calling (`allowed_callers: ["code_execution_20250825"]`)
2. **Ship `PARAMETER_RETRY` diagnostic signal** detecting parameter-retry patterns on tool calls, recommending Tool Use Examples (`input_examples` on tool definitions), with paste-ready example extraction from successful calls
3. **Graduate #371 from parked status** -- update relevance trigger citations, promote #372 to v0.9 scope alongside the two new signals
4. **Establish LLM-call-augmentation tracking discipline** (D035) as a project convention, with the `TOOL_ORCHESTRATION_CHAIN` signal as candidate #1

## 3. Non-Goals

- LLM-powered analysis (stays rule-based for v0.9; see Section 9 for augmentation candidates)
- Auto-applying recommended fixes (D002 non-goal preserved; paste-ready examples are informational, not auto-applied)
- Implementing Tool Search, Programmatic Tool Calling, or Tool Use Examples in AgentFluent itself (AgentFluent diagnoses agents, it does not run them)
- Modifying v0.8 scope or issues
- Deep subagent trace parsing for orchestration chain detection (use parent-session `toolUseResult` metadata; trace-level enhancement is a follow-up)
- Webapp dashboard
- Agent SDK source-code parsing for tool definition extraction (deferred per D004)

---

## 4. #371 Graduation Recommendation

**Recommendation: Graduate #371 from PARKED to active. Promote #372 into v0.9 alongside the two new signals. Keep #373, #374, #375 parked.**

Rationale:

- **#372 (`TOOL_INVENTORY_OVERSIZED`)** is directly complementary to the two new signals. Together, the three signals cover all three Advanced Tool Use capabilities in a coherent diagnostics story. #372 is also the lowest-risk of the four #371 children (S sizing, existing structural analog in `unused_agent`).
- **#373 (tool description quality rubric)** requires a scoring rubric and potentially NLP analysis of tool descriptions. This is a larger effort with a research component (the rubric spike). Defer to v0.9.1 or v0.10.
- **#374 (tool-schema token attribution)** requires changes to token-metrics reporting. Useful but not diagnostic -- it is an analytics enhancement, not a behavior-to-config signal. Defer.
- **#375 (Tool Search adoption regression in diff)** is blocked on #374. Defer.

The epic's status changes from PARKED to active. Its child stories are triaged: #372 in v0.9, #373-#375 remain deferred.

---

## 5. Signal Specifications

### 5.1. `TOOL_ORCHESTRATION_CHAIN` -- Programmatic Tool Calling diagnostic

**What it detects:** Agent invocations where the agent makes N+ sequential tool calls with large intermediate `tool_result` payloads that are consumed only to produce the next tool call's input -- a pattern where the intermediate data passes through the context window unnecessarily.

**Observable proxies (rule-based):**

| Proxy | Source | How to detect |
|---|---|---|
| High sequential tool-call count | `toolUseResult.totalToolUseCount` on `SessionMessage.metadata` | Count > threshold (default: 10) per invocation |
| High token-to-tool ratio | `toolUseResult.totalTokens / totalToolUseCount` | Ratio exceeds threshold (suggests large intermediate payloads) |
| Same-tool reuse with derived params | Subagent trace `tool_use` blocks (when trace available) | 3+ calls to the same tool with varying `input` where later calls' inputs reference data from earlier results |
| Large total tokens relative to final output | `toolUseResult.totalTokens` vs. final `tool_result` content length | High ratio = most tokens were intermediate processing, not final answer |

**Detection tiers:**

- **Tier A (metadata-only, ships in v0.9):** Fires on invocations where `totalToolUseCount >= 10` AND `totalTokens / totalToolUseCount > 2000` (i.e., average 2k+ tokens per tool call). This is a coarse proxy: high tool-call count with high per-call token overhead indicates orchestration with large intermediates.
- **Tier B (trace-enhanced, follow-up):** When subagent trace is available, inspect the actual `tool_use`/`tool_result` sequence for same-tool chains with derived parameters. Higher precision but requires trace parsing.

**Known precision risk:** The metadata-only proxy cannot distinguish between:
- (a) Orchestration chains where intermediate results are consumed and discarded (true positive -- Programmatic Tool Calling would help)
- (b) Agents that genuinely need each intermediate result in the context window for reasoning (false positive)

The semantic question "did this intermediate result affect the final output in a way that requires it to be in the context window?" is the **first concrete LLM-call augmentation candidate** (see Section 9). The rule-based version approximates this with the token-to-tool ratio proxy. A calibration step (analogous to #402's `feat_fix_proximity` calibration) is included as a child story.

**Recommendation text:**
- Observation: "Agent '{name}' made {N} tool calls consuming {tokens} tokens across {sessions} sessions. Average token cost per tool call: {ratio} tokens."
- Reason: "Sequential tool-call chains with large intermediate payloads inflate context window usage. Programmatic Tool Calling lets the agent orchestrate tool calls in a sandboxed code-execution environment where intermediate results don't enter the context window. Anthropic reports a 37% token reduction on complex research tasks."
- Action: "Consider migrating tools called by this agent to `allowed_callers: [\"code_execution_20250825\"]` so intermediate results are processed in the code-execution sandbox. See https://www.anthropic.com/engineering/advanced-tool-use for configuration details."
- Config file: point at the agent's definition file or tool definition

**Axis classification:** `cost` (primary benefit is token reduction). `SIGNAL_AXIS_MAP: SignalType.TOOL_ORCHESTRATION_CHAIN: Axis.COST`

**Severity:** `INFO` (Tier A metadata-only has known precision limitations; promote to `WARNING` in Tier B when trace evidence confirms the chain)

### 5.2. `PARAMETER_RETRY` -- Tool Use Examples diagnostic

**What it detects:** Same tool called 2+ times consecutively by the same agent with different `input` shapes, especially when the first call returned `is_error: true` or a validation error in `tool_result.content`. Indicates the agent is guessing at parameter formats that `input_examples` would disambiguate.

**Observable proxies:**

| Proxy | Source | How to detect |
|---|---|---|
| Consecutive same-tool calls with different input | Subagent trace `tool_use` blocks | Tool name matches, `input` dicts differ, no intervening different-tool calls |
| First call failed | Trace `tool_result` with `is_error: true` or error text patterns ("invalid", "validation", "missing required", "type error") | Pattern match on `tool_result.content` |
| Input shape evolution | Comparing `input` dict keys across consecutive calls | Keys added, removed, or value types changed between attempts |

**Detection tiers:**

- **Tier A (trace-required, ships in v0.9):** Fires when a subagent trace shows 2+ consecutive calls to the same tool where (a) the first call's `tool_result` has `is_error: true` or matches error patterns, AND (b) the `input` dict keys or value structure changed between calls. This is high-precision: an error followed by an input-shape change is strong evidence of parameter confusion.
- **Tier B (metadata-fallback):** For invocations without traces, use `toolUseResult.toolStats` to detect tools called 3+ times in a single invocation as a coarse proxy. Lower precision but provides coverage for non-trace agents.

**Paste-ready example extraction (bonus capability):**

When the signal fires AND a subsequent call to the same tool succeeded, extract the successful call's `input` dict and present it as a paste-ready `input_examples` entry in the recommendation. This leverages the fact that the JSONL already contains the working parameter shape.

Format in recommendation output:
```
Suggested input_examples entry for tool '{tool_name}':
  {
    "key1": "value1",
    "key2": "value2"
  }
```

This is a step toward "apply fix" automation without crossing the auto-apply non-goal (see D002). The user still copies and pastes manually. The value is that AgentFluent extracts the correct shape from observed behavior rather than requiring the user to construct it from documentation.

**Recommendation text:**
- Observation: "Agent '{name}' retried tool '{tool}' {N} times with different parameter shapes before succeeding. First attempt failed with: '{error_summary}'."
- Reason: "Parameter-retry patterns indicate the agent is guessing at input formats. Adding concrete examples to the tool definition (`input_examples` array) improves accuracy from 72% to 90% on complex parameter handling (Anthropic benchmark)."
- Action: "Add an `input_examples` array to the '{tool}' tool definition showing the expected parameter shape. Suggested example based on the successful call: [extracted example]"
- Config file: point at the tool definition source

**Axis classification:** `speed` (parameter retries waste time and tokens; the primary user-felt pain is execution delay). `SIGNAL_AXIS_MAP: SignalType.PARAMETER_RETRY: Axis.SPEED`

**Severity:** `WARNING` (parameter retries with errors are a clear behavioral defect with a concrete fix)

### 5.3. `TOOL_INVENTORY_OVERSIZED` -- Tool Search diagnostic (existing #372)

Unchanged from #372's specification. Key updates from the article:

- **Quantitative anchor update:** Add the article's Opus 4.5 benchmark (79.5% -> 88.1%) alongside the RAG-MCP research (43% vs 14%). The article provides a stronger authority source for the recommendation.
- **Configuration syntax update:** The recommendation text should reference the platform-level `defer_loading: true` syntax from the article, not just Claude Code's internal implementation. Specifically:
  - API/SDK agents: add a `tool_search_tool_regex_20251119` tool to the `tools[]` array and set `defer_loading: true` on the individual tools that should load lazily.
  - MCP servers: use an `mcp_toolset` block with `default_config: {"defer_loading": true}`.
  - Claude Code agents: `defer_loading: true` in agent frontmatter (the original Claude Code-only path).
- **Priority upgrade:** From `priority:medium` to `priority:high` given the article's confirmation that the feature is production-ready on the platform.

The verbatim Observation/Reason/Action recommendation text (refreshed for the article) lives on #372's implementation checklist; the developer should copy it from there.

No structural changes to #372's acceptance criteria, thresholds (30 tools, 0.5 utilization ratio), or the `SIGNAL_AXIS_MAP` (cost) entry.

---

## 6. Dependency Graph

```
TOOL_ORCHESTRATION_CHAIN (metadata-only Tier A)     -- independent
PARAMETER_RETRY (trace-required Tier A)             -- independent
TOOL_INVENTORY_OVERSIZED (#372, existing)            -- independent

TOOL_ORCHESTRATION_CHAIN calibration                 -- depends on TOOL_ORCHESTRATION_CHAIN
```

All three signals are independent of each other and can be implemented in any order. The calibration story depends on its parent signal being implemented first.

Cross-epic independence: this entire epic has zero dependencies on v0.8 work. It uses the existing diagnostics pipeline infrastructure (signal types, correlator rules, aggregation, axis classification) without modification.

---

## 7. Sizing and Sequencing

| Story | Effort | Priority | Deps |
|---|---|---|---|
| #372 update (TOOL_INVENTORY_OVERSIZED priority + citation refresh) | XS (<0.5 day) | high | None |
| PARAMETER_RETRY signal + paste-ready extraction | M (2-3 days) | high | None |
| TOOL_ORCHESTRATION_CHAIN signal (Tier A metadata) | M (2-3 days) | high | None |
| TOOL_ORCHESTRATION_CHAIN calibration | S (1-2 days) | medium | TOOL_ORCHESTRATION_CHAIN |

**Total: ~6-9 dev days (4 stories)**

### Recommended implementation order

1. **PARAMETER_RETRY** -- highest signal/effort ratio (trace data already available, detection is precise, paste-ready extraction is a differentiated capability)
2. **TOOL_INVENTORY_OVERSIZED** -- lowest effort (existing spec, existing structural analog)
3. **TOOL_ORCHESTRATION_CHAIN** -- highest precision risk (metadata-only Tier A is approximate)
4. **TOOL_ORCHESTRATION_CHAIN calibration** -- runs against real data after signal implementation

---

## 8. Open Questions

### OQ1: Should TOOL_ORCHESTRATION_CHAIN Tier A require a minimum session count?

The metadata-only proxy (high totalToolUseCount + high token-per-call ratio) could fire on a single unusual invocation. Should we require the pattern to appear across N+ invocations (like `RETRY_LOOP` uses a per-agent aggregation)?

**Recommendation:** Yes, require 3+ invocations showing the pattern. Reduces false positives from one-off complex tasks. Consistent with existing signal aggregation patterns.

### OQ2: Should paste-ready examples be extracted for all successful calls, or only for calls that followed a failed attempt?

Extracting from all successful calls would provide examples even for tools that work correctly (a documentation aid). Extracting only from post-failure successes is more targeted.

**Recommendation:** Post-failure only for v0.9. The signal is about parameter-retry patterns; the example extraction is evidence for that specific signal, not a general documentation feature. Broadening to all calls is a natural v0.10 extension ("tool documentation generator").

### OQ3: Does #372 get a separate milestone assignment or does it stay unmilestoned until v0.9 milestone is created?

**Recommendation:** Keep unmilestoned until v0.9 milestone exists. Update the issue body and priority label now. Milestone assignment happens when the v0.9 release PRD establishes the milestone.

---

## 9. LLM-Call Augmentation Candidates

This section establishes the tracking discipline proposed in D035. AgentFluent is rule-based (D002), but some detections would benefit materially from an optional LLM call. This list tracks those candidates without committing to implementation.

### Candidate #1: TOOL_ORCHESTRATION_CHAIN -- intermediate-result relevance classification

| Field | Value |
|---|---|
| **Signal** | `TOOL_ORCHESTRATION_CHAIN` |
| **Sub-detection** | Distinguishing true orchestration chains (intermediate results consumed and discarded) from legitimate multi-step reasoning (intermediate results needed in context for final synthesis) |
| **What the rule-based version does** | Uses token-to-tool ratio as a proxy: high ratio = likely orchestration. Cannot distinguish cases where high intermediate tokens are legitimately needed for reasoning. |
| **What an LLM call would do** | Given the final output and the intermediate tool results, classify whether each intermediate result materially affected the final output. If not, the intermediate could have been processed in a code-execution sandbox. |
| **Approximate cost/call** | ~2k input tokens (final output + summarized intermediates) + ~200 output tokens = ~$0.02-0.05 per invocation at Haiku pricing |
| **Expected precision delta** | Rule-based: estimated 60-70% precision. LLM-augmented: estimated 85-90% precision. **Measured (#407, 2026-06-02): ~0% precision / ~100% rule-based FP rate** on the agentfluent+codefluent dogfood corpus -- the estimated 60-70% never materialized because the proxy measures whole-invocation token burn, not intermediate-result size, so it fires only on token-heavy reasoning agents (a corpus with no genuine orchestration chains). |
| **Expected recall delta** | Minimal. Both approaches detect the same candidates; the LLM only reclassifies FPs. |
| **When to implement** | The >30% FP-rate trigger is now **met** (#407: ~100%). The deterministic first move is **Tier B** trace-level detection (#499), which measures intermediate-result size directly (summed tool-result tokens vs. final-output size); the LLM call here is the *complement* for residual semantic cases, gated on a general-purpose "optional LLM call" infrastructure (env var for API key, cost tracking, opt-in flag). Per the standing stance (D035 / #402), evaluate a classical-ML path before an LLM judge. |

### Future candidates (placeholder entries -- detail TBD)

- **ERROR_PATTERN semantic classification:** Distinguishing true error reports from benign error-like text in tool output (relates to #333). An LLM could classify "error" mentions as actionable vs. incidental.
- **REVIEWER_CAUGHT finding quality scoring:** Rating whether a reviewer subagent's finding is substantive vs. stylistic. Currently uses keyword heuristics ("blocker", "must", "concern").
- **Tool description quality scoring (#373):** Rating tool descriptions for retrieval-friendliness. Keyword density and length heuristics are weak proxies for whether a description helps a retriever find the right tool.

---

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| TOOL_ORCHESTRATION_CHAIN Tier A precision is low (<60%) | Users dismiss the signal; trust damage | Severity is INFO, not WARNING. Calibration (#407) measured ~0% precision on the current corpus (a corpus artifact -- no orchestration agents). Mitigated by an explicit low-confidence caveat on every emission (D043); Tier B (#499) is the precision fix; LLM-augmentation (D035) the complement. |
| PARAMETER_RETRY requires subagent traces (Tier A) | Signal only fires for agents with traces; non-trace agents get no coverage | Tier B metadata fallback (3+ calls to same tool in `toolStats`) provides coarse coverage. Tier A is the high-confidence path; Tier B is additive. |
| Paste-ready example extraction produces misleading examples | User copies an example that works in one context but not another | Examples are presented as "suggested based on observed successful call" with a caveat. The user is responsible for reviewing. Not auto-applied (D002). |
| #372 has been parked for a reason -- insufficient data to calibrate | Signal fires on everyone with >30 tools regardless of actual degradation | The utilization ratio gate (uses <50% of declared tools) is the second filter. Combined threshold (30 tools AND low utilization) is conservative. Calibration against real data is a follow-up. |

---

## 11. Success Criteria

v0.9 advanced tool use diagnostics are successful when:

1. **PARAMETER_RETRY fires on genuine parameter-retry patterns.** At least one instance detected in the agentfluent or CodeFluent dogfood corpus where the agent retried a tool with a different input shape after an error.
2. **Paste-ready example extraction produces a valid `input_examples` entry.** The extracted JSON matches the tool's `input_schema` and is copy-pasteable.
3. **TOOL_ORCHESTRATION_CHAIN fires on high-tool-count invocations.** The signal triggers on invocations with 10+ tool calls and >2k tokens/call. The >=70% precision target is an **accepted known limitation for v0.9**: the #407 calibration measured ~0% precision on the current dogfood corpus, but this is a corpus artifact (it contains no agents running genuine orchestration chains, so there are no true positives to find) -- not a broken signal. The signal ships live at INFO with an explicit low-confidence caveat (D043); trace-level Tier B detection (#499) is the tracked path to meeting the precision bar. See `.claude/specs/analysis/407-calibration/`.
4. **TOOL_INVENTORY_OVERSIZED fires on agents with >30 declared tools.** Updated recommendation references the article's benchmarks.
5. **All three signals contribute to the correct axis.** TOOL_ORCHESTRATION_CHAIN -> cost, PARAMETER_RETRY -> speed, TOOL_INVENTORY_OVERSIZED -> cost.
6. **LLM-augmentation candidates list is established.** D035 appended to `decisions.md`. Candidate #1 documented with the fields specified above.
7. **All new code has >80% test coverage.** No regressions.

---

## 12. References

- [Anthropic: Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use) -- source article
- #371 -- parked epic: Tool-inventory diagnostics for large-surface agents
- #372 -- `TOOL_INVENTORY_OVERSIZED` signal (child of #371)
- #373-#375 -- remaining #371 children (deferred)
- D002 -- stretch MVP scope: rule-based heuristics, not LLM-powered
- D022 -- single-axis signal classification
- D035 -- LLM-call augmentation tracking discipline (established by this PRD)
- `docs/RAG_OVER_TOOLS_RESEARCH.md` -- tool-inventory research synthesis
- `prd-v0.8.md` -- v0.8 PRD (this feature is explicitly NOT v0.8 scope)
