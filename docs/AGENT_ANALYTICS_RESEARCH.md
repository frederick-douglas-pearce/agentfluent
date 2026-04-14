# Agent Analytics Research

Research into the opportunity for analyzing AI agent sessions — both Claude Code subagents and Agent SDK-based programmatic agents. Conducted April 2026 to inform CodeFluent's agent features roadmap and evaluate whether a separate agent analytics product is warranted.

## Table of Contents

1. [Anthropic's Agent Ecosystem](#anthropics-agent-ecosystem)
2. [Agent Observability Market Landscape](#agent-observability-market-landscape)
3. [The Gap: Agent Quality Analysis](#the-gap-agent-quality-analysis)
4. [CodeFluent Agent Features (v1.3 Roadmap)](#codefluent-agent-features-v13-roadmap)
5. [Separate Product Opportunity: Agent Prompt Diagnostics](#separate-product-opportunity-agent-prompt-diagnostics)
6. [Technical Feasibility](#technical-feasibility)
7. [Sources](#sources)

---

## Anthropic's Agent Ecosystem

As of April 2026, Anthropic offers three distinct agent systems:

### Claude Code Subagents (Interactive)

Custom subagents defined as Markdown files with YAML frontmatter. Run locally within Claude Code CLI sessions.

- **Scope:** `.claude/agents/` (project), `~/.claude/agents/` (user), managed settings (org), CLI flags (session)
- **Built-in agents:** Explore (Haiku, read-only), Plan (read-only), general-purpose (all tools)
- **Config:** model, tools, disallowedTools, memory, isolation (worktree), color
- **Key constraint:** One level of delegation only — subagents cannot spawn sub-subagents
- **Data:** Sessions written to `~/.claude/projects/` as JSONL (same format as main sessions)
- **Relevance to CodeFluent:** Direct — these are interactive sessions with human fluency signals

### Claude Agent SDK (Programmatic)

Claude Code as a library for Python and TypeScript. Same tools, agent loop, and context management as the CLI, but programmable for production apps and CI/CD.

- **Same JSONL format:** SDK agents write to `~/.claude/projects/` just like CLI sessions
- **Same config system:** Supports `.claude/` settings, skills, commands, CLAUDE.md when `settingSources: ['project']`
- **Same subagent system:** `agents` parameter with `AgentDefinition` shape
- **SDK-specific features:** Programmatic hooks (callbacks, not shell commands), sessions (resume/fork), MCP integration
- **Key difference:** Prompts are hardcoded in application code, not typed by a human
- **Relevance to CodeFluent:** Indirect — same data format but different evaluation criteria needed

### Claude Managed Agents (Cloud API)

Pre-built agent harness running in Anthropic's managed infrastructure. Completely separate from Claude Code.

- **API-based:** `/v1/agents`, `/v1/sessions`, `/v1/environments`
- **Cloud containers:** Pre-installed packages, network access, persistent filesystems
- **Multi-agent:** Coordinator pattern with threads (research preview)
- **Beta:** `managed-agents-2026-04-01` header required
- **Data:** Server-side event streams, not local JSONL
- **Relevance to CodeFluent:** None — different system, different data format, different users

### Key Distinction

| Feature | Claude Code Subagents | Agent SDK | Managed Agents |
|---|---|---|---|
| Where it runs | Local CLI | Local (your app) | Anthropic cloud |
| Session data | Local JSONL | Local JSONL | Server-side events |
| Prompts | Human-typed | Hardcoded in code | API-defined |
| Multi-agent | 1 level deep | 1 level deep | Coordinator threads |
| Config | `.claude/agents/*.md` | `AgentDefinition` objects | API JSON payloads |

---

## Agent Observability Market Landscape

### Tier 1: Enterprise Agent Observability Platforms

Major platforms focused on production agent monitoring via tracing and instrumentation:

| Platform | Focus | Key Strength | Limitation |
|---|---|---|---|
| **LangSmith** | LangChain ecosystem tracing | Deep framework integration, low overhead | Tightly coupled to LangChain/LangGraph |
| **Langfuse** | Open-source LLM observability | Self-hostable, Claude Agent SDK integration via OpenTelemetry | Requires instrumentation code |
| **Arize / Phoenix** | Enterprise monitoring + open-source | Drift detection, embedding analysis, compliance certs | Complex setup for small teams |
| **Braintrust** | Quality management + evaluation | Trace-to-test pipeline, CI/CD native | Opinionated workflow |
| **Datadog LLM** | Infrastructure-level monitoring | Token usage, latency, error rates, prompt injection detection | Heavyweight, expensive |

**What they do well:** Trace execution, measure latency, track cost, detect errors.
**What they don't do:** Evaluate agent *quality* or *effectiveness*. They answer "is it running?" not "is it running well?"

Langfuse has a specific Claude Agent SDK integration that instruments every tool call and model completion via OpenTelemetry spans.

### Tier 2: Agent Evaluation Frameworks

Platforms that score agent outputs against rubrics:

| Platform | Approach | Best For |
|---|---|---|
| **DeepEval** | LLM-as-judge, trajectory metrics | Automated eval pipelines |
| **Galileo** | Rubric-based scoring, guardrails | Enterprise quality gates |
| **Braintrust Evals** | Trace-to-test, automated optimization | CI/CD quality regression |
| **Maxim** | Multi-dimension scoring | Broad evaluation coverage |

**What they do well:** Score task completion, measure accuracy, detect regressions.
**What they don't do:** Analyze local session data, diagnose prompt-level issues, work without cloud infrastructure.

### Tier 3: Claude Code Local Analytics

Growing ecosystem of local-first tools parsing Claude Code JSONL files:

| Tool | Focus | Key Feature |
|---|---|---|
| **claude-view** | Real-time dashboard | Rust SIMD-accelerated parsing, zero telemetry |
| **claude-code-analytics** | Session archiving + analysis | Hook-based capture, 300+ model support |
| **agents-observe** | Multi-agent observability | Live subagent execution trees, parent-child tracking |
| **claude-code-otel** | OpenTelemetry bridge | Exports Claude Code sessions to OTel-compatible platforms |
| **clauditor** | Session state management | Handoff notes, structured state injection |
| **Observagent** | Hook-based observability | Zero-config setup, subagent lifecycle events |

**What they do well:** Monitor usage, track cost, visualize sessions — all locally.
**What they don't do:** Evaluate quality, score effectiveness, provide actionable recommendations for improvement.

### Market Data

- 57% of organizations have agents in production (LangChain 2026 State of AI Agents)
- Quality cited as top barrier to deployment by 32% of respondents
- Enterprise agents achieve ~60% success on single runs, dropping to ~25% across eight runs
- Agent SDK launched April 2026 — ecosystem still forming

---

## The Gap: Agent Quality Analysis

**Nobody is evaluating agent quality from local session data.**

- Tier 1 traces execution but doesn't score effectiveness
- Tier 2 evaluates outputs but requires instrumentation and cloud infrastructure
- Tier 3 monitors usage and cost but doesn't assess how well the agent performed

The question "my Agent SDK agent ran 500 sessions last week — were they any good?" has no answer today. Specifically:

1. **No prompt-to-behavior diagnostics.** When an agent misbehaves, developers iterate on prompts blind — they see the agent retrying or erroring but can't diagnose whether the system prompt is the root cause.
2. **No local-first quality scoring.** All quality evaluation requires cloud infrastructure or framework instrumentation.
3. **No agent-specific recommendations.** Existing tools report what happened; none suggest what to change.

---

## CodeFluent Agent Features (v1.3 Roadmap)

These features serve CodeFluent's existing audience (Claude Code interactive users) and are tracked in the GitHub backlog:

| Issue | Feature | Milestone |
|---|---|---|
| #238 | Scan `.claude/agents/` for subagent definitions in config maturity | v1.3 |
| #239 | Track custom subagent invocations in conversation metrics | v1.3 |
| #240 | Agent-aware recommendations for adoption and optimization | v1.3 |
| #241 | Agent advisor — suggest agents from conversation patterns (LLM-powered) | v2.0 |

These focus on **interactive subagent usage** — measuring whether Claude Code users effectively leverage custom agents for task decomposition, and coaching them to improve.

---

## Separate Product Opportunity: Agent Prompt Diagnostics

### The Insight

Assessing prompt quality matters *more* for hardcoded agent prompts than for interactive human prompts, because:

1. **No real-time feedback loop.** A human typing prompts gets immediate feedback and can course-correct. A hardcoded prompt runs the same flawed way every time — at scale.
2. **The "guessing game" problem.** Developers iterate on agent prompts blind. They see the agent misbehaving (retries, wrong tool choices, hallucinated outputs) but can't diagnose whether the prompt phrasing is the root cause or whether the issue is environmental.
3. **Prompt quality drives agent quality.** The 60% → 25% success rate drop across runs often traces back to prompt brittleness — missing edge case handling, ambiguous instructions, or insufficient constraint specification.

### Product Concept

A local-first agent analytics tool that reads Agent SDK session JSONL files and provides:

#### Agent Execution Analytics (reuse from CodeFluent)
- Token usage, cost tracking, cache efficiency (reuse `analytics.ts`)
- Tool call patterns, error rates, retry frequency
- Session duration, completion rates
- Cost-per-task and cost trend analysis

#### Agent Prompt Diagnostics (novel)
- **Prompt scoring against agent best practices** — Does the system prompt specify clear success criteria? Does it handle error cases? Does it constrain tool usage appropriately? Are there missing guardrails?
- **Behavior-to-prompt correlation** — "Your agent retries Bash commands 40% of the time. The system prompt doesn't specify error handling behavior — adding 'If a command fails, read the error output before retrying' could reduce retries."
- **Prompt regression detection** — Compare agent behavior across prompt versions. Did the prompt change improve or degrade task completion rate?
- **Prompt optimization** — Generate improved prompt variants with specific behavioral fixes, similar to CodeFluent's Prompt Optimizer but targeting agent system prompts.

#### Agent Configuration Assessment (reuse from CodeFluent)
- Tool access audit — principle of least privilege violations
- Model selection analysis — cost vs capability tradeoffs
- Hook coverage assessment
- MCP server configuration review

#### Agent-Specific Recommendations
- "Your agent uses 12 tools but only 4 account for 95% of invocations — restrict the tool list to reduce confusion and cost"
- "Session traces show the agent reads files before every edit, but the system prompt doesn't mention this pattern — codify it to make it reliable"
- "Error recovery takes an average of 3.2 turns — add explicit error handling instructions to the system prompt"

### What Transfers from CodeFluent

| Component | Reusability | Notes |
|---|---|---|
| JSONL parser (`parser.ts`) | High | Same session format |
| Token analytics (`analytics.ts`) | High | Same metrics apply |
| Config scanner (`configScanner.ts`) | Medium | Agent-specific categories needed |
| Prompt scoring framework | Medium | Different scoring rubric needed for agent prompts |
| Prompt optimizer | Medium | Same template approach, different optimization targets |
| Conversation assembly | High | Same gap-based splitting |
| Pricing lookup (`pricing.ts`) | High | Identical |
| Cache infrastructure | High | Same pattern |

### What's New (Requires Building)

- Agent behavior metrics: task completion rate, tool error rate, retry patterns, stuck detection
- Prompt-to-behavior correlation engine
- Agent-specific scoring rubric and prompt template
- Prompt version tracking and regression analysis
- Agent-optimized recommendations engine

### Competitive Positioning

| Tool | Monitors | Evaluates Quality | Local-First | Prompt Diagnostics |
|---|---|---|---|---|
| LangSmith | Yes | Partial (evals) | No | No |
| Langfuse | Yes | Partial (scores) | Self-host | No |
| agents-observe | Yes | No | Yes | No |
| claude-view | Yes | No | Yes | No |
| **This product** | Yes | Yes | Yes | **Yes** |

The unique angle is **prompt diagnostics** — connecting observed agent behavior to specific prompt improvements. No existing tool does this.

---

## Technical Feasibility

### Data Availability

Agent SDK sessions use the same JSONL format as Claude Code CLI sessions. The key fields are:
- `type: "user"` — the programmatic prompt (system prompt + user message)
- `type: "assistant"` — model responses with tool_use blocks
- `type: "tool_result"` — tool execution results (including errors)
- Token usage on assistant messages
- Timestamps for all events

Error patterns, retry sequences, and tool call failures are all visible in the JSONL data.

### Architecture Options

1. **Standalone tool** — New project repo, shares code with CodeFluent via extracted packages or copy
2. **Monorepo sibling** — Lives alongside CodeFluent in the same repo, shares parser/analytics modules
3. **Plugin architecture** — CodeFluent core with swappable "lenses" (interactive fluency vs agent diagnostics)

Option 1 is cleanest for different audiences. Option 3 is most ambitious but risks scope creep.

### Estimated Effort

- **MVP (monitoring + basic scoring):** 2-3 weeks leveraging CodeFluent's parser/analytics
- **Prompt diagnostics (novel feature):** 3-4 weeks for scoring rubric, prompt templates, behavior correlation
- **Full product:** 6-8 weeks total

---

## Community Validation: The Observability Gap Is Real

### The Production Pain Point

Reza Rezvani (CTO, Berlin-based), who runs Claude Code agents in production, identifies the gap directly in his [10-step framework article](https://alirezarezvani.medium.com/how-to-build-claude-code-agents-from-scratch-the-10-step-framework-i-actually-use-in-production-6f6a358f4f8c):

> "No built-in observability. There is no dashboard, no trace viewer, no audit log. Hooks give you event-level control, but aggregating that into a monitoring system is your problem. For teams running agents at scale, this is a genuine gap."

His broader experience (documented across multiple articles) highlights additional friction:
- **Agent proliferation chaos** — losing track of which agent did what, agents stepping on each other's work, spending more time managing agents than they save
- **Hook configuration brittleness** — case-sensitive matchers (`"bash"` doesn't match `"Bash"`), no warnings when nothing matches, 50% activation rate by default (improved to 84% with careful tuning)
- **Framework fatigue** — moved away from LangChain because modifying agent behavior required "editing Python classes, redeploying containers, and debugging serialization errors"

### The DIY Observability Problem

A separate practitioner ([documented on Substack](https://doneyli.substack.com/p/i-built-my-own-observability-for)) identified seven specific failures when trying to build their own Claude Code observability:

1. **Unbounded growth** — log files became thousands of lines without rotation
2. **Truncated data** — prompts capped at 500 characters, losing crucial context
3. **One-way capture** — only recorded inputs, not Claude's responses or tool usage
4. **Non-queryable** — flat text files prevented analytical queries
5. **Lost context** — multi-turn debugging sessions appeared as disconnected entries
6. **Manual annotation** — outcome tracking abandoned after two attempts
7. **Siloed data** — per-project logs with no cross-project pattern recognition

Their solution was self-hosted Langfuse (6-service Docker stack) — effective but heavyweight infrastructure that most solo developers and small teams won't set up.

### The Ecosystem Response

The gap has spawned a growing ecosystem of community tools, all hook-based:

| Tool | Approach | Limitation |
|---|---|---|
| [agents-observe](https://github.com/simple10/agents-observe) | Real-time hook streaming dashboard | Monitoring only, no quality analysis |
| [Claude Code Agent Monitor](https://github.com/hoangsonww/Claude-Code-Agent-Monitor) | React + SQLite dashboard via hooks | Session tracking, no recommendations |
| [claude-code-otel](https://github.com/ColeMurray/claude-code-otel) | OpenTelemetry bridge for Claude Code | Exports to OTel platforms, setup complexity |
| [Claude HUD](https://aitoolly.com/ai-news/article/2026-03-20-claude-hud-a-new-plugin-for-real-time-monitoring-of-claude-code-context-and-agent-activity) | Plugin for context/agent monitoring | Real-time only, no historical analysis |
| Self-hosted Langfuse | Full LLM observability platform | 6-service Docker stack, heavyweight |
| [Datadog AI Agents Console](https://www.datadoghq.com/blog/claude-code-monitoring/) | Enterprise Claude Code monitoring | Enterprise pricing, org-level focus |
| [Dynatrace integration](https://www.dynatrace.com/hub/detail/claude-code-agent-monitoring/) | Enterprise agent monitoring | Enterprise pricing, infrastructure-heavy |

**Common pattern:** Every solution focuses on **monitoring** (what happened?) but none address **quality analysis** (was it good?) or **prompt diagnostics** (why did it fail and how do I fix the prompt?).

### Where AgentFluent Fits

The observability gap is well-established and widely felt. But the community response has converged on real-time dashboards and trace viewers — the equivalent of `tail -f` for agents. Nobody is building the analytical layer that answers:

1. **"Is my agent getting better or worse over time?"** — trend analysis across prompt versions
2. **"Why does my agent retry so much?"** — prompt-to-behavior correlation
3. **"What should I change in my system prompt?"** — actionable recommendations, not just traces
4. **"How does my agent compare to best practices?"** — scoring against agent design rubrics

This is the layer between raw monitoring (agents-observe, Claude HUD) and heavyweight platforms (Langfuse, Datadog). A local-first tool that reads the same JSONL data everyone already has, requires no Docker stack or cloud infrastructure, and provides quality scoring + prompt diagnostics rather than just dashboards.

The positioning would be: **"The tools that exist tell you what your agent did. This tool tells you what to change."**

---

## Subagent Session Data Analysis (April 2026)

### How Subagent Data Appears in JSONL

Subagent activity is **inlined into the parent session file** — no separate JSONL files are created. When Claude spawns a subagent, the sequence in the parent session is:

1. Assistant message with `tool_use` block where `name: "Agent"` and `input` contains `subagent_type`, `description`, and `prompt`
2. The subagent runs in its own isolated context (not visible in the parent JSONL)
3. A `tool_result` block returns only the subagent's final summary to the parent session

The `isSidechain` flag exists in the schema but is not set to `true` for any observed session files. All 14 JSONL files in the codefluent project are primary sessions.

### Observable vs Hidden Data

**Updated 2026-04-14** based on analysis of actual custom PM agent invocations. The metadata block on `tool_result` provides significantly more data than initially expected.

| Data Point | Visible in Parent JSONL | Hidden (Subagent Internal) |
|---|---|---|
| Which agent was invoked | Yes (`subagent_type` field) | — |
| Delegation prompt | Yes (`prompt` in Agent tool input) | — |
| Agent description | Yes (`description` field) | — |
| Final result/summary | Yes (`tool_result` content) | — |
| Total tokens per invocation | **Yes** (metadata `total_tokens`) | Per-tool-call breakdown |
| Tool use count per invocation | **Yes** (metadata `tool_uses`) | Which specific tools were called |
| Duration per invocation | **Yes** (metadata `duration_ms`) | Per-step timing |
| Agent session ID | **Yes** (metadata `agentId`) | — |
| Agent self-reported issues | **Yes** (extractable from output text) | Internal error details |
| Internal tool calls | No | Yes (Read, Grep, Bash, etc.) |
| Internal reasoning steps | No | Yes |
| Retries and errors (internal) | No | Yes |

### Observed Subagent Usage Patterns (CodeFluent Project)

Analysis of recent sessions shows Claude actively delegates to built-in subagents:

| Session Date | Agent Calls | Dominant Pattern |
|---|---|---|
| Apr 14 (PM agent) | 2 custom (pm) + 4 built-in | First custom agent usage — PM agent for v1.2 planning |
| Apr 12 | 4 | Explore × 3, Plan × 1 — research + planning |
| Apr 9 | 11 | Explore-heavy with Plan — investigation session |
| Apr 7 | 21 | Plan + general-purpose — heavy implementation |
| Apr 3 | 13 | Explore + Plan — research/planning session |

### Custom Agent Data: PM Agent Analysis (April 14, 2026)

First invocation of a custom subagent (PM agent) provided concrete data on what's extractable:

**Invocation 1 — v1.2 prioritization review:**
- `total_tokens`: 31,621
- `tool_uses`: 14 (Read, Glob, Grep + GitHub MCP tools)
- `duration_ms`: 122,963 (~2 min)
- Output: 7,005 chars — structured analysis with milestone assessment, gap identification, priority recommendations

**Invocation 2 — artifact creation (issues, PRDs, decision log):**
- `total_tokens`: 44,414
- `tool_uses`: 22
- `duration_ms`: 294,552 (~5 min)
- Output: 32,355 chars — full PRD, decision log, 7 issue bodies, epic updates
- **Hook failure detected:** Agent's output text begins with "The hook is consistently blocking writes..." — self-reported tool access issue is visible in the return data

**Key observations:**
1. **Task profiles are distinguishable.** Research (14 tools, 2 min) vs execution (22 tools, 5 min) — different task types produce measurably different resource profiles.
2. **Efficiency metrics are computable.** Tokens per tool use: 2,259 (research) vs 2,019 (execution). Duration per tool use: 8.8s vs 13.4s. These could indicate agent prompt quality — a well-prompted agent should use fewer tokens per tool call.
3. **Self-reported failures are extractable.** The agent's inability to write spec files (hook misconfiguration) was documented in its own output text. Pattern matching on output could detect "blocked", "unable to", "don't have access" signals without needing internal traces.
4. **The `agentId` enables continuity tracking.** Different IDs for each invocation — could track whether agents are continued (SendMessage) vs fresh-spawned, indicating delegation strategy patterns.

### Implications for CodeFluent Agent Tracking (#239)

The metadata block discovery significantly expands what's achievable in CodeFluent's agent invocation tracking. Issue #239 should capture:
- `total_tokens` — enables cost-per-agent-invocation metrics
- `tool_uses` — complexity signal, efficiency denominator
- `duration_ms` — performance tracking
- `agentId` — continuity tracking (fresh vs continued agents)

These fields enable per-agent analytics cards without any Agent SDK migration:
- **Cost per agent type** — "Your PM agent costs $0.12 per invocation on average"
- **Efficiency comparison** — "Explore agents use 60% fewer tokens than general-purpose for similar tasks"
- **Duration trends** — "PM agent invocations are getting faster as your backlog stabilizes"

### Implications for AgentFluent Prototype

**What's testable with subagent data (more than initially expected):**
- Invocation frequency and patterns — which agents are used, how often, for what tasks
- Delegation effectiveness — is the agent description triggering appropriate delegation?
- Output quality — does the returned summary lead to course-corrections by the user?
- Configuration quality — tool restrictions, model selection, description clarity
- **Cost attribution per agent invocation** — `total_tokens` from metadata
- **Efficiency metrics** — tokens per tool use, duration per tool use
- **Failure detection** — self-reported issues extractable from output text
- **Agent continuity patterns** — fresh spawn vs SendMessage continuation

**What still requires Agent SDK data (AgentFluent-specific features):**
- Prompt-to-behavior correlation — connecting system prompt phrasing to *specific* internal tool errors, retries, and stuck patterns (need per-tool-call traces)
- Detailed error analysis — which tool failed, what the error was, how many retries
- Prompt regression detection — comparing internal behavior across prompt versions
- Internal reasoning analysis — did the agent follow its instructions or drift?

### Bootstrap Strategy

**Updated 2026-04-14:** The metadata block discovery makes subagent data more useful than initially expected. Custom Claude Code subagents provide enough data for meaningful agent analytics — not just configuration quality, but cost tracking, efficiency metrics, failure detection, and continuity analysis.

1. **Done:** Deploy custom PM agent for real work in Claude Code. First invocations produced actionable data (April 14).
2. **CodeFluent v1.2:** Capture metadata block in agent invocation tracking (#239) — `total_tokens`, `tool_uses`, `duration_ms`, `agentId`
3. **CodeFluent v1.3:** Agent config scanning (#238), agent-aware recommendations (#240), agent advisor (#241)
4. **When the gap hurts:** If lack of internal visibility (per-tool-call traces) becomes a blocker for optimizing subagent prompts, rebuild key agents using the Agent SDK to get full traces — that's the trigger for an AgentFluent prototype
5. **AgentFluent signal:** The trigger is when you find yourself asking "why did my agent make 22 tool calls?" and the metadata alone can't answer it. That's when per-tool-call traces from the Agent SDK become essential.

---

## Sources

### Anthropic Documentation
- [Claude Code Subagents](https://code.claude.com/docs/en/sub-agents)
- [Agent SDK Overview](https://code.claude.com/docs/en/agent-sdk/overview)
- [Managed Agents Overview](https://platform.claude.com/docs/en/managed-agents/overview)
- [Managed Agents Multi-Agent](https://platform.claude.com/docs/en/managed-agents/multi-agent)

### Agent Observability Platforms
- [LangSmith Observability](https://www.langchain.com/langsmith/observability)
- [Langfuse Claude Agent SDK Integration](https://langfuse.com/integrations/frameworks/claude-agent-sdk)
- [Braintrust AI Observability Buyer's Guide 2026](https://www.braintrust.dev/articles/best-ai-observability-tools-2026)
- [15 AI Agent Observability Tools in 2026](https://aimultiple.com/agentic-monitoring)

### Agent Evaluation
- [Galileo Agent Evaluation Framework](https://galileo.ai/blog/agent-evaluation-framework-metrics-rubrics-benchmarks)
- [DeepEval AI Agent Evaluation](https://deepeval.com/guides/guides-ai-agent-evaluation)
- [Microsoft AI Agent Performance Measurement](https://www.microsoft.com/en-us/dynamics-365/blog/it-professional/2026/02/04/ai-agent-performance-measurement/)

### Claude Code Local Analytics & Observability
- [claude-view: Mission Control Dashboard](https://recca0120.github.io/en/2026/04/07/claude-view-mission-control/)
- [agents-observe: Real-time Claude Code Observability](https://github.com/simple10/agents-observe)
- [Claude Code Agent Monitor](https://github.com/hoangsonww/Claude-Code-Agent-Monitor)
- [claude-code-analytics](https://github.com/sujankapadia/claude-code-analytics)
- [claude-code-otel: OpenTelemetry for Claude Code](https://github.com/ColeMurray/claude-code-otel)
- [Claude HUD Plugin](https://aitoolly.com/ai-news/article/2026-03-20-claude-hud-a-new-plugin-for-real-time-monitoring-of-claude-code-context-and-agent-activity)
- [Datadog AI Agents Console for Claude Code](https://www.datadoghq.com/blog/claude-code-monitoring/)
- [Dynatrace Claude Code Agent Monitoring](https://www.dynatrace.com/hub/detail/claude-code-agent-monitoring/)
- [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)

### Practitioner Experiences
- [Reza Rezvani: 10-Step Production Agent Framework](https://alirezarezvani.medium.com/how-to-build-claude-code-agents-from-scratch-the-10-step-framework-i-actually-use-in-production-6f6a358f4f8c)
- [Reza Rezvani: 6-Month Production Hooks Report](https://alirezarezvani.medium.com/the-claude-code-hooks-nobody-talks-about-my-6-month-production-report-30eb8b4d9b30)
- [Reza Rezvani: 4 Subagent Mistakes That Kill Your Workflow](https://dev.to/alireza_rezvani/4-claude-code-subagent-mistakes-that-kill-your-workflow-and-the-fixes-3n72)
- [Building Custom Observability for Claude Code (Substack)](https://doneyli.substack.com/p/i-built-my-own-observability-for)
