# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AgentFluent is a local-first agent analytics tool. Its primary target is developers building agents with the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) (Python and TypeScript), with secondary support for Claude Code subagents. It analyzes session data (JSONL files from `~/.claude/projects/`) to evaluate agent **quality** -- not just execution monitoring. The core differentiator is behavior-to-improvement diagnostics: analyzing observed agent behavior and distilling concrete, actionable improvements -- whether that means updating a stored prompt, adding or configuring an MCP server, writing a new rule, command, skill, or subagent, adjusting tool access, or changing model selection.

Tagline: "The tools that exist tell you what your agent did. This tool tells you what to change."

## How AgentFluent Differs from CodeFluent

AgentFluent is a standalone sibling project to [CodeFluent](https://github.com/frederick-douglas-pearce/codefluent). They share data sources (Claude Code JSONL sessions) and some infrastructure, but analyze fundamentally different things:

- **CodeFluent** measures **human AI fluency** -- how well a developer collaborates with Claude Code in interactive sessions. It scores human prompts against 11 research-backed fluency behaviors (from Anthropic's AI Fluency Index) and coaches developers to improve their prompting patterns. Config maturity scoring helps users set up their `.claude/` project to support better human-AI collaboration.
- **AgentFluent** diagnoses **agent quality** -- why an agent misbehaves and what concrete changes to its configuration fix it. It correlates observed behavior (retries, errors, tool patterns) back to specific gaps in the agent's prompt, tool setup, rules, or other configuration surfaces.

The analysis is fundamentally different because agents and interactive sessions have different structures:

- **Prompts are static artifacts, not ephemeral.** Agent prompts live in code (`ClaudeAgentOptions`, `AgentDefinition`) or `.claude/agents/*.md` files -- they're version-controlled and run identically every time. A flaw compounds at scale instead of being corrected mid-conversation.
- **Descriptions are trigger logic.** An agent's `description` field controls when it gets delegated to -- a poor description means the agent never fires or fires for wrong tasks. No equivalent exists in interactive use.
- **No conversational feedback loop.** A human course-corrects mid-session. An agent runs its prompt blind -- retries, errors, and wrong tool choices repeat systematically.
- **The config surface is the entire agent.** In CodeFluent, config supports the human. In AgentFluent, the config *is* the agent -- prompt, allowed_tools, hooks, MCP servers, subagent definitions, model, skills, commands -- there's nothing else to tune.

CodeFluent asks: "How fluent is the developer?" AgentFluent asks: "How do we make this agent better?"

## Project Status

MVP in progress. Package scaffolding and CLI skeleton are complete. See `.claude/specs/prd-mvp.md` for the full MVP spec and `.claude/specs/backlog-mvp.md` for the implementation backlog (issues #1-#42). The research document at `docs/AGENT_ANALYTICS_RESEARCH.md` contains the market analysis, competitive landscape, and technical feasibility study.

## Architecture Context

### Code Reuse from CodeFluent

CodeFluent's Python webapp (FastAPI backend) contains working implementations that can be ported/adapted:

- **JSONL parser** (`parser.py`) -- same session format
- **Token analytics** (`analytics.py`) -- same metrics
- **Config scanner** (`config_scanner.py`) -- needs agent-specific categories
- **Pricing lookup** (`pricing.py`) -- identical
- **Cache infrastructure** -- same pattern
- **Conversation assembly** -- same gap-based splitting

### Novel Components (to build)

- Agent behavior metrics (task completion, tool errors, retry patterns, stuck detection)
- Behavior-to-improvement correlation engine -- maps observed agent issues to specific config changes
- Agent-specific scoring rubric and prompt templates
- Prompt version tracking and regression analysis
- Recommendation engine spanning the full agent config surface (prompts, tools, MCP servers, rules, commands, skills, subagents, model selection, hooks)

### Data Sources

Agent SDK and Claude Code subagent sessions are stored as JSONL in `~/.claude/projects/`. Key fields:
- `type: "user"` -- programmatic prompt (system prompt + user message)
- `type: "assistant"` -- model responses with `tool_use` blocks
- `type: "tool_result"` -- tool execution results, including metadata block with `total_tokens`, `tool_uses`, `duration_ms`, `agentId`

### Four Core Features

1. **Agent Execution Analytics** -- token usage, cost, tool call patterns, error rates (reuse from CodeFluent)
2. **Agent Behavior Diagnostics** -- score agent configuration against best practices, correlate behavior to prompt and config issues, generate specific improvement recommendations (novel)
3. **Regression Detection** -- compare agent behavior across prompt/config versions (novel)
4. **Agent Configuration Assessment** -- tool access audit, model selection, hook coverage, MCP server review (reuse/adapt from CodeFluent)

## Delivery Strategy

- **CLI tool as the primary interface** -- `agentfluent analyze`, `agentfluent diff`, JSON output for programmatic consumption. Fits Agent SDK developers' workflow (terminal, CI/CD pipelines) and enables integration into PR checks when agent configs change.
- **Webapp dashboard for visualization** -- charts, trends, side-by-side comparisons. The CLI is the analytical core; the webapp is a view into it.
- **VS Code extension is a future consideration** -- not the initial focus. CodeFluent already serves the VS Code interactive user. AgentFluent lives where agent developers work: terminal and CI pipelines.

## Product Development Workflow

This project uses a PM subagent (`~/.claude/agents/pm.md`) for feature specification and backlog management. The PM agent reads project context, creates GitHub issues (epics and stories), and writes longer-form specs to `.claude/specs/`. It has no access to Bash, Edit, or code -- only Read, Write (scoped to `.claude/specs/` via hook), and GitHub MCP tools (issues + labels only).

### When to invoke the PM agent

Delegate to the pm subagent when the human's request involves:
- A new feature or capability that needs scoping before implementation
- A pain point or problem statement that needs translation into stories
- Scope or priority questions ("should we do A or B first?")
- Ambiguous requirements where assumptions would be required to proceed

Do NOT invoke the PM agent for:
- Bug fixes with clear reproduction steps
- Refactoring with no behavior change
- Purely technical decisions (dependency updates, tooling, CI)
- Requests that reference an existing GitHub issue with clear acceptance criteria

### Spec and issue conventions

- **PRDs:** `.claude/specs/prd-<feature-slug>.md`
- **Decision log:** `.claude/specs/decisions.md` (append-only)
- **Epics:** GitHub issues with `epic:` label prefix
- **Stories:** GitHub issues tagged with parent epic label

### When to invoke the Architect agent

Delegate to the architect subagent (`~/.claude/agents/architect.md`) for design review **before implementation begins**. The architect agent is read-only -- it reviews plans, not code. It posts its findings as comments on the relevant GitHub issue so they persist across sessions.

Invoke the architect agent when:
- You're about to start a non-trivial feature (new module, schema change, new analytics pipeline)
- The implementation plan touches cross-module interfaces or shared data models (e.g., `SessionMessage`, `AgentInvocation`)
- The feature involves a new diagnostics rule, correlation engine logic, or recommendation template
- The roadmap shows upcoming features that could be affected by design decisions

Do NOT invoke the architect agent for:
- Simple bug fixes with clear solutions
- Documentation-only changes
- Dependency updates or CI changes
- Post-implementation code review (use `/simplify` or `/review` instead)

### Working from specs

When implementing from a PM-produced spec or issue:
- Reference the story's acceptance criteria as your definition of done
- Check for architect review comments on the issue -- address any blocking concerns before implementing
- Do not exceed the scope defined in the spec
- If the spec is technically infeasible or incomplete, STOP and report
  back to the human before proceeding -- do not silently adapt

## Code Style & Conventions

- Keep files small: if a file exceeds 300 lines, consider splitting
- Use descriptive variable names over comments
- Error handling: wrap external calls (API, file I/O) in try/except with user-friendly messages
- Type annotations on all public functions (mypy strict mode is enabled)
- Use Pydantic v2 models for data structures that cross module boundaries
- Linting: `uv run ruff check src/` -- auto-fixable with `--fix`
- Type checking: `uv run mypy src/agentfluent/`

## Branching & PR Workflow

- **`main`** -- Always releasable. All changes require a PR with passing CI before merge.
- **Feature branches** -- `feature/<issue-number>-short-description` (e.g., `feature/12-session-parser`)
- **Bug fix branches** -- `fix/<issue-number>-short-description` (e.g., `fix/15-token-count-overflow`)
- **Commit to feature/fix branches freely** -- push often, squash or merge to main via PR.

### Commit Messages (Conventional Commits)

This project uses [Conventional Commits](https://www.conventionalcommits.org/).

**Required prefixes:**
- `feat:` -- new feature (triggers minor version bump)
- `fix:` -- bug fix (triggers patch version bump)
- `docs:` -- documentation only
- `test:` -- adding or updating tests
- `chore:` -- maintenance, dependencies, CI
- `refactor:` -- code change that neither fixes a bug nor adds a feature

**Breaking changes:** Add `!` after the type (e.g., `feat!: remove legacy API`) or include `BREAKING CHANGE:` in the commit body. Triggers a major version bump.

## Production Standards

- **All new features must have tests.** No merging without test coverage for the change.
- **No regressions:** All tests must pass before any commit to main.
- **Security:** Sanitize user-controlled strings before rendering. Never interpolate untrusted input into shell commands. Redact API keys in error messages.
- **Update CLAUDE.md** when a story changes conventions, architecture, or package structure.

### Testing Conventions

- **Framework:** pytest via `uv run pytest`
- **Coverage:** `uv run pytest --cov=agentfluent`
- **Unit tests:** `tests/unit/` -- use anonymized JSONL fixtures in `tests/fixtures/`
- **Integration tests:** `tests/integration/` -- marked with `@pytest.mark.integration`, run against real `~/.claude/projects/` data, skipped in CI
- **CI runs:** `pytest -m "not integration"` (unit tests only)

## JSONL Data Format

Claude Code and Agent SDK sessions are stored at `~/.claude/projects/` as JSONL files. AgentFluent's analysis targets differ from CodeFluent's -- we care about agent behavior signals, not human fluency signals.

### Directory structure
```
~/.claude/projects/
├── -home-user-project-name/
│   ├── session-uuid-1.jsonl              # main session
│   ├── session-uuid-1/
│   │   └── subagents/
│   │       ├── agent-<agentId-1>.jsonl   # full subagent trace
│   │       └── agent-<agentId-2>.jsonl
│   ├── session-uuid-2.jsonl
│   └── ...
└── -home-user-other-project/
    └── ...
```

**Subagent JSONL files** contain complete internal traces (tool_use/tool_result pairs, per-step token usage, `is_error` flags, reasoning steps). All messages have `isSidechain: true`. The `agentId` links subagent files to the parent session's `tool_result` metadata. MVP enumerates these files; deep parsing is deferred to v1.1 (see D008).

### Message types AgentFluent extracts

**`type: "assistant"` -- Model responses (token usage + tool calls)**
```json
{
  "type": "assistant",
  "message": {
    "model": "claude-opus-4-6",
    "role": "assistant",
    "content": [
      {"type": "text", "text": "..."},
      {"type": "tool_use", "name": "Agent", "input": {
        "subagent_type": "pm",
        "description": "...",
        "prompt": "..."
      }}
    ],
    "usage": {
      "input_tokens": 3,
      "output_tokens": 2,
      "cache_creation_input_tokens": 14450,
      "cache_read_input_tokens": 19155
    }
  },
  "timestamp": "2026-02-27T01:10:24.420Z"
}
```

**`type: "tool_result"` -- Tool execution results (agent metadata here)**
```json
{
  "type": "tool_result",
  "content": "agent's final summary text",
  "metadata": {
    "total_tokens": 31621,
    "tool_uses": 14,
    "duration_ms": 122963,
    "agentId": "uuid"
  }
}
```

**`type: "user"` -- Prompts**
```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": "plain string OR array of content blocks"
  },
  "timestamp": "2026-02-27T01:10:20.969Z"
}
```
**NOTE:** `message.content` can be either a plain string or an array of blocks (`[{"type": "text", "text": "..."}]`). The parser MUST handle both.

### Key signals for agent analysis

- **Agent tool_use blocks** -- `name: "Agent"` with `subagent_type`, `description`, `prompt` in input. Identifies which agent was invoked and the delegation prompt.
- **tool_result metadata** -- `total_tokens`, `tool_uses`, `duration_ms`, `agentId`. Enables cost-per-invocation, efficiency metrics, and continuity tracking.
- **Error patterns in tool_result content** -- self-reported failures ("blocked", "unable to", "don't have access") extractable via pattern matching.
- **Retry sequences** -- consecutive tool_use/tool_result pairs for the same tool indicate retry behavior.
- **Tool diversity** -- count of unique tool names in assistant content blocks.

### Types to skip

- `file-history-snapshot` -- metadata
- `progress`, `hook_progress`, `bash_progress` -- streaming events
- `system` -- system messages
- `create` -- file creation events

## Tech Stack

**Python-only for MVP** (see decision D001 in `.claude/specs/decisions.md`).

- **Language:** Python >=3.12
- **Package/dependency management:** [uv](https://docs.astral.sh/uv/)
- **CLI framework:** Typer (built on Click) + Rich for terminal formatting
- **Data models:** Pydantic v2
- **Testing:** pytest + pytest-cov
- **Linting:** ruff
- **Type checking:** mypy (strict mode)
- **Data format:** JSONL (Claude Code session files)

### Package Layout

Uses `src/` layout (`src/agentfluent/`). See `.claude/specs/prd-mvp.md` Section 4 for the full package tree. Key subpackages:

- `cli/` -- Typer app, commands, formatters
- `core/` -- JSONL parser, session models, project discovery
- `agents/` -- agent invocation extraction and models
- `analytics/` -- token/cost metrics, tool patterns, pricing
- `config/` -- agent definition scanner and scoring
- `diagnostics/` -- behavior signals, correlation, recommendations
