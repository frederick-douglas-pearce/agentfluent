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

## Secrets handling

AgentFluent reads local Claude Code session data from `~/.claude/projects/`. Those JSONL files can contain any file contents Claude (or its subagents) has ever read during a session -- including `.env` files, shell rc files, and other credential sources. `.gitignore` does not protect against this persistence.

This repo ships Claude Code hooks (`.claude/settings.json` + `.claude/hooks/`) that block reads of common credential files and detect known API key patterns in tool output. See [`docs/SECURITY.md`](docs/SECURITY.md) for the leak vector, the layered defense, how to audit your existing session store for historical leaks, and the discipline rules that pair with the hooks.

AgentFluent itself emits only aggregate metrics today, but the rule is forward-looking: any future feature that surfaces raw session content must re-apply secret-pattern redaction at the display layer.
