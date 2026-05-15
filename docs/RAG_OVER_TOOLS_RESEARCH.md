# RAG over Tool Descriptions: Research and Implications for AgentFluent

Research note on the technique of retrieving tool descriptions per turn instead of stuffing all tool schemas into the context window. Conducted May 2026 to inform AgentFluent's eval framework roadmap. The work itself is parked — current AgentFluent target agents do not have tool inventories large enough to make retrieval pay off — but the eval primitives generalize, and Anthropic's own first-party "Tool Search" feature means the technique will appear in real-world agent configurations even if AgentFluent users don't roll their own.

## Table of Contents

1. [Background and Motivation](#background-and-motivation)
2. [How the Technique Works](#how-the-technique-works)
3. [The Inflection Point](#the-inflection-point)
4. [Feasibility with Claude Code Subagents and Agent SDK](#feasibility-with-claude-code-subagents-and-agent-sdk)
5. [Anthropic's First-Party Solution: Tool Search](#anthropics-first-party-solution-tool-search)
6. [Failure Modes and Caveats](#failure-modes-and-caveats)
7. [Implications for AgentFluent's Eval Framework](#implications-for-agentfluents-eval-framework)
8. [Sources](#sources)

---

## Background and Motivation

Agents with large tool inventories pay a double cost: every tool's JSON Schema and description sits in the system prompt on every turn, inflating token usage and giving the model more distractors to choose from when selecting the right tool. Recent papers demonstrate that **retrieving only the relevant subset of tool descriptions per turn** improves tool-call accuracy and reduces token usage substantially.

The headline numbers from the three most relevant papers:

| Paper | Year | Tool-call accuracy | Token reduction | Tool inventory tested |
|---|---|---|---|---|
| **RAG-MCP** (arXiv 2505.03275) | 2025 | 43.13% vs 13.62% baseline | ~49% (1,084 vs 2,134 tokens) | Up to 1,100 MCP servers |
| **Toolshed** (arXiv 2410.14594) | 2024 | +46% / +56% / +47% Recall@5 on ToolE / Seal-Tools | not reported | ToolE, Seal-Tools benchmarks |
| **MCP-Zero** (arXiv 2506.01056) | 2025 | preserved while reducing context | 98% | 308 servers / 2,797 tools |

The upstream motivation paper — Hsieh et al. 2023, "Tool Documentation Enables Zero-Shot Tool-Usage with LLMs" (arXiv 2308.00675) — established that tool *documentation alone* (no examples or demos) is enough for zero-shot tool use. That's what makes retrieval workable: a good description carries enough signal that the model can use a tool sight-unseen.

## How the Technique Works

The shape is consistent across implementations:

1. **Index.** Embed each tool's name + description (and sometimes synthetic usage examples) into a vector store. Off-the-shelf embeddings work; no fine-tuning required.
2. **Retrieve per turn.** At each model turn, embed the user query (or the current trajectory) and retrieve the top-k tools.
3. **Splice.** Send only the retrieved tools' schemas in the `tools` array on that turn's API call. Everything else stays out of context.

Implementation details that vary:

- **Encoder**: RAG-MCP uses Qwen-max as an LLM-based encoder; Toolshed uses off-the-shelf embeddings enhanced with a "pre-retrieval" stage that augments tool docs with synthesized questions and key-info enrichments before indexing.
- **Top-k commitment**: RAG-MCP retrieves top-k then commits to the single best tool. Toolshed evaluates at Recall@5.
- **Mid-trajectory rediscovery**: This is the open problem. Toolshed adds a post-retrieval self-reflection step. MCP-Zero takes a more aggressive approach — the agent itself emits explicit "I need a tool that does X" queries when the right tool isn't in its current view. Dynamic ReAct (arXiv 2509.20386) addresses it via iterative retrieval.

## The Inflection Point

The most actionable finding for AgentFluent: **retrieval has a clear threshold below which it doesn't help and may hurt.**

From the RAG-MCP heatmap and Anthropic's published data:

| Tool inventory size | Behavior |
|---|---|
| **<~20 tools** | Dump-everything baseline wins; retrieval adds latency and a new failure mode for negligible gain. |
| **~30–70 tools** | Baseline starts degrading; retrieval starts winning. RAG-MCP shows the baseline >90% successful on tool positions 1–30, degrading 31–70. |
| **>~100 tools** | Baseline collapses. RAG-MCP shows accuracy falling apart beyond ~100; Anthropic's Tool Search takes Opus 4.5 from 79.5% → 88.1% on their large-library eval. |

For reference points on real-world tool surfaces: a single Jira MCP server is ~17K tokens of schemas. A five-MCP bundle (Slack + Jira + GitHub + filesystem + custom) easily exceeds 55K tokens. Anthropic has logged internal setups where tool definitions alone consumed 134K tokens before the agent did any work.

A typical Claude Code subagent with 5–10 declared tools is firmly in the "baseline wins" regime. The technique becomes interesting once you're stacking multiple MCP servers — a configuration AgentFluent does not yet have first-class support for measuring.

## Feasibility with Claude Code Subagents and Agent SDK

**You cannot DIY RAG-over-tools for a Claude Code subagent today.**

- The `tools:` frontmatter field on a subagent is static. Every invocation receives the full declared inventory.
- The parent agent cannot filter or trim a subagent's tool list at delegation time.
- `PreToolUse` hooks can modify tool *inputs*, not the *schema list*.
- `SessionStart` hook output goes into context but doesn't gate which tool definitions are visible.
- MCP's `list_changed` notification is server-level, not task-level — useful for tools coming online/offline, not for per-turn routing.
- There is no documented "tool router" MCP server pattern, where one server would surface a routing tool that internally retrieves and proxies to specialized subtools.

There's an open feature request in the Claude Code repo ([anthropics/claude-code#41068](https://github.com/anthropics/claude-code/issues/41068), "Skill-Driven On-Demand MCP & Tool Loading") asking for exactly this, but it has not shipped.

**The Claude Agent SDK is slightly more flexible but ends in the same place.** You can build `AgentDefinition` objects at session start via factory functions, which lets different sessions get different tool inventories. But once a session starts, the agent's tools are fixed — there's no API to swap them mid-session.

So the practical answer is: dynamic tool loading for agents in the Anthropic ecosystem today happens via Anthropic's built-in Tool Search, not user-implemented retrieval.

## Anthropic's First-Party Solution: Tool Search

Claude Code 2.1+ ships **Tool Search**, which defers full tool schema loading until the model actually needs a tool:

- At session start, only tool *names* (and brief summaries) are loaded.
- When the model decides it needs a specific tool, the full schema is fetched on demand.
- Reported ~85% reduction in tool-schema context overhead.

**Setup is trivial:**

- On individual tool definitions: `defer_loading: true`.
- Environment-wide: `ENABLE_TOOL_SEARCH=auto` enables it automatically when tool schemas would exceed 10% of the context window.

Mechanistically Tool Search uses BM25/regex search over tool names rather than embedding-based retrieval. It's not quite the same thing as the academic RAG-over-tools papers — there's no vector store, no semantic retrieval — but it achieves the same goal (only relevant schemas in context per turn) and ships in production.

This is the version of the technique AgentFluent users will actually encounter in the wild.

## Failure Modes and Caveats

Worth surfacing because these inform what AgentFluent's eval framework should measure:

- **Semantic imprecision.** Dynamic ReAct notes the correct tool often isn't rank-1, forcing larger k that re-bloats context.
- **Cross-domain queries.** Retrieval pulls from one domain, missing multi-app plans that span Slack + Jira + GitHub etc.
- **Dependency on doc quality.** Poorly documented tools become invisible to the retriever — they exist but won't be retrieved. **This is a directly measurable AgentFluent signal.**
- **Chicken-and-egg.** Agent doesn't know what to search for if it doesn't know the tool exists. Mitigated by active discovery (MCP-Zero) or iterative refinement, but not solved.
- **Small inventories regress.** Implicit in every paper; explicit in Anthropic's framing that Tool Search targets "large tool libraries." Turning Tool Search on for a 5-tool agent is pure overhead.

## Implications for AgentFluent's Eval Framework

AgentFluent's current target agents (5–10 tools) are not candidates for the technique. But the eval primitives generalize, and bigger surfaces are coming — once AgentFluent supports Agent SDK developers running heavier MCP bundles, the framework should be ready to measure whether their agents *should* be on Tool Search.

Four diagnostic lines fall out naturally from the existing AgentFluent architecture and are captured as GitHub stories under epic [#371](https://github.com/frederick-douglas-pearce/agentfluent/issues/371) — **Tool-inventory diagnostics for large-surface agents**:

1. **[#372 — `tool_inventory_oversized` signal](https://github.com/frederick-douglas-pearce/agentfluent/issues/372).** Compares declared tool count against unique tools observed per session across the analysis window. Structurally identical to the `unused_agent` rule landed in #346 — configured-vs-observed comparison, INFO severity, recommendation targets the agent's `tools:` frontmatter (or `ClaudeAgentOptions.allowed_tools`) suggesting Tool Search adoption.

2. **[#373 — `tool_description_quality` signal](https://github.com/frederick-douglas-pearce/agentfluent/issues/373).** Scoring rubric for tool descriptions in the agent's tool inventory. Bad descriptions break Tool Search even when the tool itself is fine. Needs a research/spike phase to define the rubric — the Toolshed paper's pre-retrieval stage (synthesized questions, key-info enrichment) is one starting point.

3. **[#374 — Tool-schema token cost attribution](https://github.com/frederick-douglas-pearce/agentfluent/issues/374).** Extends the analytics module to attribute a portion of `cache_creation_input_tokens` to the tool schema overhead, surfacing it as a separate line item in token metrics reports. Lets users see exactly how many tokens they're paying for tool definitions they may never invoke.

4. **[#375 — Tool Search adoption regression detection](https://github.com/frederick-douglas-pearce/agentfluent/issues/375).** Extends the `agentfluent diff` command to flag the expected sharp token-cost drop when an agent flips `defer_loading: true`. Same shape as comparing prompt versions, with a specific annotation when schemas-attributed token delta is large and negative.

The epic is **parked, not scheduled** — the work becomes relevant when a real AgentFluent user runs the tool against an agent with a heavier MCP surface, or when the broader ecosystem makes Tool Search common enough that diagnostics around it become table-stakes. Priority assessment lives as a comment on the epic.

## Sources

- RAG-MCP: [arXiv 2505.03275](https://arxiv.org/abs/2505.03275) — "Mitigating Prompt Bloat in LLM Tool Selection via Retrieval-Augmented Generation"
- Toolshed: [arXiv 2410.14594](https://arxiv.org/abs/2410.14594) — "Scale Tool-Equipped Agents with Advanced RAG-Tool Fusion"
- MCP-Zero: [arXiv 2506.01056](https://arxiv.org/abs/2506.01056) — needle-in-haystack tool retrieval
- Dynamic ReAct: [arXiv 2509.20386](https://arxiv.org/html/2509.20386v1) — iterative tool retrieval for ReAct agents
- Hsieh et al.: [arXiv 2308.00675](https://arxiv.org/abs/2308.00675) — "Tool Documentation Enables Zero-Shot Tool-Usage with LLMs"
- Anthropic: [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) — Tool Search benchmark data and `defer_loading` setup
- Anthropic Claude Code feature request: [#41068](https://github.com/anthropics/claude-code/issues/41068) — Skill-Driven On-Demand MCP & Tool Loading (open)
- LangChain: [Custom Agent with Tool Retrieval](https://langchain-fanyi.readthedocs.io/en/latest/modules/agents/agents/custom_agent_with_tool_retrieval.html) — DIY pattern in third-party frameworks
