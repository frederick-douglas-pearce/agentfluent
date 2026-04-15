# PRD: AgentFluent MVP

**Status:** Draft
**Date:** 2026-04-14
**Author:** PM Agent
**Decision log:** See `decisions.md` for key decisions made during planning.
**Backlog:** See `backlog-mvp.md` for the full epic/story breakdown with issue bodies.

---

## 1. Problem Statement

Developers building AI agents with Claude Code subagents and the Claude Agent SDK have no local-first tool that evaluates agent **quality**. Existing tools monitor execution (traces, latency, cost) but none diagnose **why** an agent misbehaves or **what to change** in its configuration.

The gap is well-documented in the research (see `docs/AGENT_ANALYTICS_RESEARCH.md`):
- 57% of organizations have agents in production
- Quality is the top deployment barrier (32% of respondents)
- Agent success drops from 60% to 25% across multiple runs
- Every community tool converges on dashboards; none provide quality analysis or prompt diagnostics

## 2. MVP Scope

**Option B (Stretch MVP):** Execution analytics + configuration assessment + diagnostics preview that demonstrates behavior-to-config correlation using limited signals from subagent metadata.

### In Scope

1. **Project/session discovery** -- scan `~/.claude/projects/`, let user select a project, list sessions
2. **JSONL parsing** -- parse session files, extract messages, handle all content formats
3. **Agent invocation extraction** -- identify Agent tool_use blocks, extract subagent metadata (type, description, prompt, tokens, tool_uses, duration, agentId)
4. **Execution analytics** -- token usage, cost, cache efficiency, tool call patterns, per-agent cost attribution, efficiency metrics (tokens/tool_use, duration/tool_use)
5. **Agent configuration assessment** -- scan agent definition files at `~/.claude/agents/` and `.claude/agents/`, score against best practices (description quality, tool restrictions, model selection)
6. **Diagnostics preview** -- correlate observable behavior signals (error patterns in output text, high retry counts, efficiency outliers) to specific config improvement recommendations
7. **CLI interface** -- `agentfluent analyze`, `agentfluent list`, `agentfluent config-check`, JSON output mode for programmatic consumption
8. **Test infrastructure** -- unit tests with fixtures, integration tests against real session data, CI/CD pipeline

### Out of Scope (Deferred)

- **Agent SDK source parsing** -- scanning Python/TypeScript source for `AgentDefinition` objects. Deferred until Agent SDK test data exists.
- **Prompt regression detection** (`agentfluent diff`) -- comparing behavior across prompt versions. Requires multiple data points over time.
- **LLM-powered analysis** -- using Claude API for deeper prompt scoring or recommendation generation. MVP uses rule-based heuristics only.
- **Webapp dashboard** -- visualization layer. CLI is the analytical core for MVP.
- **VS Code extension** -- different audience; CodeFluent serves VS Code users.
- **"Apply fix" automation** -- auto-modifying agent configs based on recommendations. High risk, requires trust.
- **Managed Agents support** -- different data format (server-side events), different system entirely.
- **Cross-project aggregation** -- analyzing patterns across multiple projects. Single-project focus for MVP.

## 3. Target User

Python developers building Claude Code subagents and/or Agent SDK agents. They work in the terminal, use `~/.claude/agents/` and `.claude/agents/` for agent definitions, and want to understand whether their agents are performing well without setting up cloud infrastructure.

## 4. Architecture

### Language & Runtime

**Python** (sole language for MVP). Fred is a Python developer; CodeFluent's Python webapp (FastAPI backend) contains working implementations of the JSONL parser, token analytics, config scanner, and pricing lookup that can be ported/adapted.

### Package Structure

```
agentfluent/
  __init__.py
  cli/                  # CLI entry points (Click/Typer)
    __init__.py
    main.py             # CLI app, command registration
    commands/
      analyze.py        # agentfluent analyze
      list.py           # agentfluent list
      config_check.py   # agentfluent config-check
    formatters/
      table.py          # Rich table output
      json_output.py    # JSON output mode
  core/
    __init__.py
    parser.py           # JSONL session parser
    session.py          # Session/conversation models
    discovery.py        # Project/session discovery
  agents/
    __init__.py
    extractor.py        # Agent invocation extraction from JSONL
    models.py           # Agent invocation data models
  analytics/
    __init__.py
    tokens.py           # Token usage, cost, cache metrics
    pricing.py          # Model pricing lookup
    tools.py            # Tool call pattern analysis
    efficiency.py       # Per-agent efficiency metrics
  config/
    __init__.py
    scanner.py          # Agent definition file scanner
    scoring.py          # Config quality scoring rubric
    models.py           # Config assessment models
  diagnostics/
    __init__.py
    signals.py          # Behavior signal extraction (errors, retries)
    correlator.py       # Signal-to-recommendation mapping
    recommendations.py  # Recommendation models and templates
tests/
  __init__.py
  fixtures/             # Anonymized JSONL test data
  conftest.py
  unit/
    test_parser.py
    test_extractor.py
    test_analytics.py
    test_config_scanner.py
    test_diagnostics.py
  integration/
    test_real_sessions.py
    test_cli.py
```

### CLI Framework

Typer (built on Click, provides type hints, auto-generates help). Alternative: plain Click if Typer adds unwanted complexity. Developer's call.

### Key Dependencies (suggestions, developer decides)

- **Typer** or **Click** -- CLI framework
- **Rich** -- terminal formatting (tables, colors, panels)
- **Pydantic** -- data models and validation
- **pytest** -- testing
- **pytest-cov** -- coverage reporting

### Output Modes

All commands support `--format json` for programmatic consumption and `--format table` (default) for human-readable output.

## 5. Feature Specifications

### 5.1 Project & Session Discovery

**Command:** `agentfluent list [--project PROJECT_SLUG]`

**Behavior:**
- Scan `~/.claude/projects/` for project directories
- Without `--project`: list all projects with session counts, total size, date range
- With `--project`: list all sessions in that project with timestamps, message counts, agent invocation counts

**Acceptance Criteria:**
- Given `~/.claude/projects/` contains project directories, when `agentfluent list` runs, then all projects are listed with session count and date range
- Given a valid project slug, when `agentfluent list --project SLUG` runs, then all sessions are listed with metadata
- Given an invalid project slug, then a clear error message is shown
- Both `--format json` and `--format table` produce correct output

### 5.2 JSONL Parser

**Behavior:**
- Read `.jsonl` files line by line, parse JSON
- Handle `message.content` as both string and array-of-blocks
- Skip non-analytical message types: `file-history-snapshot`, `progress`, `hook_progress`, `bash_progress`, `system`, `create`
- Extract: user messages, assistant messages (with tool_use blocks and usage), tool_results (with metadata)
- Graceful handling of malformed lines (log warning, skip)

**Acceptance Criteria:**
- Given a JSONL file with mixed message types, when parsed, then only analytical types are returned
- Given `message.content` as a plain string, when parsed, then text is correctly extracted
- Given `message.content` as an array of blocks, when parsed, then text is correctly extracted from each block
- Given a malformed JSON line, when parsed, then parsing continues with remaining lines
- Parser returns typed data models (Pydantic or dataclass)

### 5.3 Agent Invocation Extraction

**Behavior:**
- Identify assistant messages containing `tool_use` blocks where `name == "Agent"`
- Extract from tool_use input: `subagent_type`, `description`, `prompt`
- Match tool_use to subsequent `tool_result` and extract metadata: `total_tokens`, `tool_uses`, `duration_ms`, `agentId`
- Distinguish built-in agents (Explore, Plan, general-purpose) from custom agents
- Compute per-invocation metrics: tokens/tool_use, duration/tool_use

**Acceptance Criteria:**
- Given a session with Agent tool_use blocks, when extracted, then all agent invocations are identified with correct metadata
- Given a tool_result with metadata block, when extracted, then total_tokens, tool_uses, duration_ms, and agentId are captured
- Built-in agents are correctly categorized separately from custom agents
- Per-invocation efficiency metrics are computed correctly

### 5.4 Execution Analytics

**Command:** `agentfluent analyze [--project PROJECT] [--session SESSION] [--agent AGENT_TYPE]`

**Metrics produced:**
- **Token usage:** total input/output/cache_creation/cache_read tokens per session
- **Cost:** dollar cost using model pricing lookup (per session, per agent type)
- **Cache efficiency:** cache_read / (cache_read + input) ratio
- **Tool patterns:** tool call frequency by name, unique tool count
- **Agent-specific:** cost per agent type, invocations per agent type, efficiency metrics (tokens/tool_use, duration/tool_use)
- **Session summary:** total messages, duration, agent invocations count

**Acceptance Criteria:**
- Given a session with assistant messages containing usage data, when analyzed, then token totals are correct
- Given model names in assistant messages, when analyzed, then costs are computed using correct pricing
- Given agent invocations with metadata, when analyzed, then per-agent cost attribution is correct
- Given `--agent pm`, when analyzed, then only PM agent invocations are included
- `--format json` output contains all metrics in a structured schema

### 5.5 Agent Configuration Assessment

**Command:** `agentfluent config-check [--scope user|project|all] [--agent AGENT_NAME]`

**Behavior:**
- Scan `~/.claude/agents/*.md` (user scope) and `.claude/agents/*.md` (project scope)
- Parse YAML frontmatter for: model, tools, disallowedTools, description, other config fields
- Score each agent definition against a rubric:
  - **Description quality:** Is it present? Is it specific enough to trigger correct delegation? (Check length, presence of action verbs, specificity)
  - **Tool restrictions:** Are `tools` or `disallowedTools` specified? (Principle of least privilege)
  - **Model selection:** Is a model specified? Is it appropriate for the task? (Cost vs capability)
  - **Prompt body quality:** Length, presence of structured sections, error handling instructions, success criteria
- Produce a per-agent score and specific improvement recommendations

**Acceptance Criteria:**
- Given agent .md files with YAML frontmatter, when scanned, then all config fields are correctly parsed
- Given an agent with no `tools` restriction, then a recommendation to restrict tools is generated
- Given an agent with a vague 1-word description, then a recommendation to improve description is generated
- Given `--scope user`, then only `~/.claude/agents/` is scanned
- Given `--scope project`, then only `.claude/agents/` is scanned
- Default (`--scope all`) scans both locations
- `--format json` output includes scores and recommendations per agent

### 5.6 Diagnostics Preview

**Command:** Output included in `agentfluent analyze` when agent invocations are present. Also available via `agentfluent analyze --diagnostics`.

**Behavior:**
- Extract behavior signals from observable data:
  - **Error patterns in output text:** regex/keyword matching for "blocked", "unable to", "don't have access", "failed", "error", "retry"
  - **High token consumption:** invocations with tokens/tool_use significantly above agent-type average
  - **Duration outliers:** invocations taking significantly longer than average for agent type
  - **Low tool efficiency:** high tool_uses with low output quality signals
- Correlate signals to config surfaces:
  - Error pattern in output -> check if agent definition restricts relevant tools, check if prompt addresses error handling
  - High token consumption -> check if prompt is focused, check if tools list is minimal
  - Duration outlier -> check model selection (overqualified model for simple task)
- Generate specific, actionable recommendations:
  - "Agent 'pm' reported 'blocked' in output. Check that allowed_tools includes Write access and that hook permissions are configured."
  - "Agent 'pm' uses 2,259 tokens per tool call (above average). Consider adding more specific instructions to reduce exploration."

**Acceptance Criteria:**
- Given agent output containing error keywords, when analyzed, then error signals are detected and reported
- Given an agent invocation with tokens/tool_use 2x above type average, then an efficiency warning is generated
- Each recommendation references a specific config surface (prompt, tools, model, hooks)
- Recommendations are actionable (say what to change, not just what's wrong)
- `--format json` output includes signals and recommendations in structured format

### 5.7 CLI Output & Formatting

**Behavior:**
- Default: Rich-formatted tables with color coding for scores/thresholds
- `--format json`: structured JSON to stdout (pipe-friendly, no color codes)
- `--format table`: explicit table format (same as default)
- `--verbose`: include additional detail (per-invocation breakdowns, raw signal data)
- `--quiet`: summary only (counts, totals, overall score)
- Global `--help` and per-command help with examples

**Acceptance Criteria:**
- JSON output is valid JSON parseable by `jq`
- Table output renders correctly in standard 80-column terminal
- `--quiet` output fits in 5 lines or fewer per command
- Exit codes: 0 for success, 1 for error, 2 for "no data found"

## 6. Data Models (Key Types)

These are guidance for the developer -- exact implementation is their decision.

```
SessionMessage: type, timestamp, content (text), tool_use blocks, usage, metadata
AgentInvocation: agent_type, description, prompt, total_tokens, tool_uses, duration_ms, agent_id, output_text, is_builtin
ExecutionMetrics: total_tokens (by type), cost, cache_efficiency, tool_counts, duration, agent_invocations
AgentConfig: name, file_path, scope (user/project), model, tools, disallowed_tools, description, prompt_body
ConfigScore: agent_name, overall_score, dimension_scores, recommendations
DiagnosticSignal: signal_type, severity, agent_invocation_ref, evidence (text snippet or metric value)
Recommendation: target_config_surface, severity, message, evidence_refs
```

## 7. Test Strategy

### Unit Tests (fixtures)
- Anonymized JSONL snippets covering: plain text content, array content, agent tool_use, tool_result with metadata, malformed lines, all skip types
- Parser correctness for each message type
- Agent extraction with and without metadata
- Analytics computation with known expected values
- Config scoring with known rubric outcomes
- Signal detection with known patterns

### Integration Tests (real data)
- Run against actual sessions from `~/.claude/projects/` (CodeFluent and AgentFluent project sessions)
- Validate end-to-end: discovery -> parsing -> extraction -> analytics -> output
- CLI invocation tests (subprocess, check exit codes and output structure)

### CI/CD
- pytest with coverage reporting
- Pre-commit hooks or CI checks for linting (ruff) and type checking (mypy or pyright)
- GitHub Actions workflow

## 8. Success Criteria

The MVP is successful when:
1. `agentfluent list` discovers and lists projects/sessions from `~/.claude/projects/`
2. `agentfluent analyze` produces correct token, cost, and tool metrics for sessions containing agent invocations
3. `agentfluent config-check` scans agent definition files and produces scored assessments with actionable recommendations
4. Diagnostics preview detects at least 3 signal types (error patterns, token outliers, duration outliers) and maps them to config surface recommendations
5. All output is available in both human-readable (table) and machine-readable (JSON) formats
6. Test suite covers core parsing, extraction, analytics, and config scoring with >80% coverage on those modules
7. CI pipeline runs tests on every PR

## 9. Sequencing

Implementation order (each epic builds on the previous):

1. **E1: Project Scaffolding** -- Python package, CLI skeleton, test infrastructure, CI
2. **E2: JSONL Parser + Session Discovery** -- core data layer everything else depends on
3. **E3: Agent Invocation Extraction** -- identifies agent data within parsed sessions
4. **E4: Execution Analytics** -- token/cost/tool metrics, depends on E2+E3
5. **E5: Agent Configuration Assessment** -- independent of session data, can parallel with E4
6. **E6: Diagnostics Preview** -- correlates E4 analytics with E5 config data
7. **E7: CLI Output + Formatting** -- polishes output across all commands

Note: E5 (config assessment) could be developed in parallel with E3+E4 since it reads agent definition files independently of session data. The developer should use judgment on parallelization.

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| JSONL format changes in future Claude Code versions | Parser breaks | Defensive parsing with graceful degradation; version detection if format includes version field |
| Limited test data for custom agents | Can't validate diagnostics | Use CodeFluent project sessions (PM agent invocations exist); create test fixtures from real data |
| Agent definition format changes | Config scanner breaks | Parse defensively; log warnings for unrecognized fields |
| Diagnostics recommendations feel generic | Low perceived value | Start with high-confidence signals (error keywords) and expand; label recommendations with confidence level |
| Scope creep into LLM-powered features | Delays MVP | Hard boundary: MVP is rule-based only. LLM features are a separate epic post-MVP. |
