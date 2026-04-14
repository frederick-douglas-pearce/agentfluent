# AgentFluent

Local-first agent analytics with prompt diagnostics. The tools that exist tell you what your agent did. This tool tells you what to change.

## What Is This?

AgentFluent analyzes AI agent session data (Claude Code subagents + Agent SDK) to provide:

- **Agent execution analytics** -- token usage, cost tracking, tool call patterns, error rates
- **Agent prompt diagnostics** -- score system prompts against best practices, correlate agent behavior to prompt quality issues, generate specific improvement recommendations
- **Prompt regression detection** -- compare agent behavior across prompt versions
- **Agent configuration assessment** -- tool access audit, model selection analysis, hook coverage

## Background

Born from [CodeFluent](https://github.com/frederick-douglas-pearce/codefluent) research identifying a gap in the agent observability market: existing tools monitor agent execution (traces, latency, cost) but none evaluate agent *quality* or diagnose *prompt-level issues* from local session data.

See `docs/AGENT_ANALYTICS_RESEARCH.md` for the full market analysis and technical feasibility study.

## Status

Early stage -- defining scope, architecture, and initial backlog.
