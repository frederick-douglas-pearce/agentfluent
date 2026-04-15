# AgentFluent MVP Backlog

Full epic and story breakdown for the MVP. Each section below is a GitHub issue body ready for creation via `gh issue create`.

**Label conventions:**
- Epic labels: `epic:scaffolding`, `epic:parser`, `epic:agent-extraction`, `epic:analytics`, `epic:config-assessment`, `epic:diagnostics`, `epic:cli-output`
- Type labels: `enhancement`, `documentation`, `testing`, `infrastructure`
- Priority labels: `priority:high`, `priority:medium`, `priority:low`

**Create labels first:**
```bash
gh label create "epic:scaffolding" --color "0E8A16" --description "E1: Project scaffolding and infrastructure"
gh label create "epic:parser" --color "1D76DB" --description "E2: JSONL parser and session discovery"
gh label create "epic:agent-extraction" --color "5319E7" --description "E3: Agent invocation extraction"
gh label create "epic:analytics" --color "D93F0B" --description "E4: Execution analytics"
gh label create "epic:config-assessment" --color "FBCA04" --description "E5: Agent configuration assessment"
gh label create "epic:diagnostics" --color "B60205" --description "E6: Diagnostics preview"
gh label create "epic:cli-output" --color "006B75" --description "E7: CLI output and formatting"
gh label create "enhancement" --color "A2EEEF" --description "New feature or request"
gh label create "documentation" --color "0075CA" --description "Documentation improvements"
gh label create "testing" --color "D4C5F9" --description "Test coverage and infrastructure"
gh label create "infrastructure" --color "E4E669" --description "CI/CD, tooling, project setup"
gh label create "priority:high" --color "B60205" --description "Must have for MVP"
gh label create "priority:medium" --color "FBCA04" --description "Should have for MVP"
gh label create "priority:low" --color "0E8A16" --description "Nice to have"
```

---

## E1: Project Scaffolding

**Issue title:** Epic: Project scaffolding and infrastructure
**Labels:** `epic:scaffolding`, `infrastructure`

**Body:**

### Summary

Set up the Python project structure, dependency management, testing infrastructure, and CI/CD pipeline. This is the foundation everything else builds on.

### Success Criteria

- [ ] Python package with `pyproject.toml` managed by uv
- [ ] CLI entry point (`agentfluent`) is installable and responds to `--help`
- [ ] pytest runs with coverage reporting
- [ ] Linting (ruff) and type checking configured
- [ ] GitHub Actions CI runs tests on every PR
- [ ] CLAUDE.md updated to reflect Python-only stack and uv tooling

### Stories

- [ ] #N -- Initialize Python package with uv and pyproject.toml
- [ ] #N -- Create CLI skeleton with Typer
- [ ] #N -- Set up pytest infrastructure with fixtures directory
- [ ] #N -- Configure GitHub Actions CI pipeline
- [ ] #N -- Update CLAUDE.md for Python-only MVP

---

### E1-S1: Initialize Python package with uv and pyproject.toml

**Issue title:** Initialize Python package with uv and pyproject.toml
**Labels:** `epic:scaffolding`, `infrastructure`, `priority:high`

**Body:**

### Summary

Initialize the AgentFluent Python project using `uv` with a proper package structure.

### Acceptance Criteria

- Given a fresh clone, when `uv sync` is run, then all dependencies are installed and the virtual environment is created
- The `pyproject.toml` defines:
  - Package name: `agentfluent`
  - Python version: >=3.11
  - CLI entry point: `agentfluent = "agentfluent.cli.main:app"`
  - Dev dependencies: pytest, pytest-cov, ruff, mypy (or pyright)
  - Runtime dependencies: typer, rich, pydantic
- Package structure matches the layout in the PRD (Section 4)
- `__init__.py` files exist in all package directories
- `.python-version` file is present
- `uv.lock` is committed

### Implementation Notes

- Use `uv init` as the starting point
- See PRD Section 4 for the full package tree
- Developer chooses between mypy and pyright for type checking
- All `__init__.py` files can be empty initially

### Dependencies

None -- this is the first story.

---

### E1-S2: Create CLI skeleton with Typer

**Issue title:** Create CLI skeleton with Typer
**Labels:** `epic:scaffolding`, `enhancement`, `priority:high`

**Body:**

### Summary

Create the CLI entry point with Typer, registering stub commands that will be implemented in later epics.

### Acceptance Criteria

- Given the package is installed, when `agentfluent --help` is run, then help text is displayed listing all commands
- Given `agentfluent list --help`, then help text for the list command is shown
- Given `agentfluent analyze --help`, then help text for the analyze command is shown
- Given `agentfluent config-check --help`, then help text for the config-check command is shown
- Stub commands print "Not yet implemented" and exit with code 0
- Global options `--format` (json/table) and `--verbose`/`--quiet` flags are defined
- Version flag (`--version`) prints the package version

### Implementation Notes

- `cli/main.py` creates the Typer app and registers command groups
- `cli/commands/` contains one module per command (analyze.py, list.py, config_check.py)
- Stubs should accept the flags they'll eventually need (e.g., `--project`, `--session`) even though they don't use them yet
- See PRD Section 5.7 for output mode specs

### Dependencies

- E1-S1 (package structure must exist)

---

### E1-S3: Set up pytest infrastructure with fixtures directory

**Issue title:** Set up pytest infrastructure with fixtures directory
**Labels:** `epic:scaffolding`, `testing`, `priority:high`

**Body:**

### Summary

Configure pytest with coverage reporting and create the test directory structure with initial fixture files.

### Acceptance Criteria

- Given the project is set up, when `uv run pytest` is run, then pytest discovers and runs tests
- Given `uv run pytest --cov=agentfluent`, then coverage report is generated
- Test directory structure exists: `tests/unit/`, `tests/integration/`, `tests/fixtures/`
- `tests/conftest.py` exists with shared fixtures (e.g., paths to fixture files)
- At least one placeholder test exists and passes
- `pyproject.toml` contains pytest configuration (testpaths, coverage settings)
- Fixture directory contains at least one anonymized JSONL snippet (can be minimal -- a single valid session message)

### Implementation Notes

- Integration tests should be marked with `@pytest.mark.integration` so they can be run separately
- Fixture JSONL files should be anonymized versions of real session data, covering the key message types documented in CLAUDE.md
- See PRD Section 7 for the full test strategy

### Dependencies

- E1-S1 (package structure must exist)

---

### E1-S4: Configure GitHub Actions CI pipeline

**Issue title:** Configure GitHub Actions CI pipeline
**Labels:** `epic:scaffolding`, `infrastructure`, `priority:high`

**Body:**

### Summary

Set up GitHub Actions to run tests, linting, and type checking on every PR to main.

### Acceptance Criteria

- Given a PR is opened against main, then the CI workflow runs automatically
- CI runs: `uv run ruff check`, `uv run mypy agentfluent` (or pyright), `uv run pytest --cov=agentfluent`
- CI uses uv for dependency installation (not pip)
- Integration tests are excluded from CI (they require real session data on `~/.claude/projects/`)
- CI fails if: any test fails, ruff reports errors, type checker reports errors
- Workflow file: `.github/workflows/ci.yml`

### Implementation Notes

- Use `astral-sh/setup-uv` GitHub Action for uv installation
- Python version should match `.python-version`
- Consider caching uv's package cache for faster runs
- Integration tests excluded via: `pytest -m "not integration"`

### Dependencies

- E1-S1 (package structure), E1-S3 (pytest infrastructure)

---

### E1-S5: Update CLAUDE.md for Python-only MVP

**Issue title:** Update CLAUDE.md for Python-only MVP
**Labels:** `epic:scaffolding`, `documentation`, `priority:medium`

**Body:**

### Summary

Update CLAUDE.md to reflect the decisions made during MVP planning: Python-only stack, uv for dependency management, updated code reuse references.

### Acceptance Criteria

- "Architecture Context > Code Reuse from CodeFluent" section references Python module names (not .ts files)
- "Tech Stack" section states Python-only for MVP, uv for dependency management
- Package structure is documented or referenced (link to PRD)
- Testing conventions are documented (pytest, unit vs integration marks)
- Branching workflow section is verified accurate

### Implementation Notes

- See decisions D001 and D007 in `.claude/specs/decisions.md`
- Keep CLAUDE.md as the authoritative developer reference -- don't duplicate PRD content, link to it

### Dependencies

- E1-S1 (to confirm final package structure)

---

## E2: JSONL Parser + Session Discovery

**Issue title:** Epic: JSONL parser and session discovery
**Labels:** `epic:parser`, `enhancement`

**Body:**

### Summary

Build the core data layer: discover projects and sessions from `~/.claude/projects/`, parse JSONL session files into typed data models. Everything downstream depends on this.

### Success Criteria

- [ ] Project discovery scans `~/.claude/projects/` and lists projects with metadata
- [ ] Session discovery lists sessions within a project with timestamps and sizes
- [ ] JSONL parser handles all message types documented in CLAUDE.md
- [ ] Parser handles `message.content` as both string and array-of-blocks
- [ ] Malformed lines are skipped gracefully
- [ ] `agentfluent list` command is functional
- [ ] Unit tests cover all message types and edge cases
- [ ] Integration tests validate against real session data

### Stories

- [ ] #N -- Define core data models for parsed sessions
- [ ] #N -- Implement project and session discovery
- [ ] #N -- Implement JSONL session parser
- [ ] #N -- Wire up `agentfluent list` command
- [ ] #N -- Add parser unit tests with JSONL fixtures
- [ ] #N -- Add discovery and parser integration tests

---

### E2-S1: Define core data models for parsed sessions

**Issue title:** Define core data models for parsed sessions
**Labels:** `epic:parser`, `enhancement`, `priority:high`

**Body:**

### Summary

Define Pydantic (or dataclass) models for parsed JSONL data. These models are the contract between the parser and all downstream consumers (analytics, extraction, diagnostics).

### Acceptance Criteria

- Models defined for: `SessionMessage`, `ToolUseBlock`, `ToolResult`, `Usage`, `MessageContent`
- `SessionMessage` captures: type, timestamp, content (normalized to text), tool_use blocks, usage stats, metadata
- `ToolUseBlock` captures: id, name, input (dict)
- `ToolResult` captures: tool_use_id, content, is_error, metadata (optional dict with total_tokens, tool_uses, duration_ms, agentId)
- `Usage` captures: input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens
- Models handle optional fields gracefully (metadata may be absent)
- Models are importable from `agentfluent.core.session`

### Implementation Notes

- See PRD Section 6 for model guidance
- See CLAUDE.md "JSONL Data Format" for exact field names and types
- `message.content` can be string or list -- the model should normalize to a consistent representation
- Developer decides Pydantic v2 vs dataclasses

### Dependencies

- E1-S1 (package structure)

---

### E2-S2: Implement project and session discovery

**Issue title:** Implement project and session discovery
**Labels:** `epic:parser`, `enhancement`, `priority:high`

**Body:**

### Summary

Scan `~/.claude/projects/` to discover available projects and their sessions. Provide metadata (session count, file sizes, date ranges) without parsing full file contents.

### Acceptance Criteria

- Given `~/.claude/projects/` contains project directories, when `discover_projects()` is called, then all projects are returned with: slug, path, session count, total size bytes, earliest/latest session timestamps
- Given a project path, when `discover_sessions(project_path)` is called, then all `.jsonl` files are returned with: filename, file size, modified timestamp
- Project slugs are derived from directory names (e.g., `-home-fdpearce-project` -> display-friendly name)
- Empty project directories are included with session_count=0
- Non-existent `~/.claude/projects/` path produces a clear error message
- Functions are in `agentfluent.core.discovery`

### Implementation Notes

- Session timestamps can be derived from file modification time for the discovery listing (full parsing happens later)
- The slug-to-friendly-name conversion should handle the dash-encoded path format (e.g., `-home-fdpearce-Documents-Projects-git-codefluent` -> `codefluent` or the full path)
- Consider making the base path configurable (env var or CLI flag) for testing

### Dependencies

- E1-S1 (package structure)

---

### E2-S3: Implement JSONL session parser

**Issue title:** Implement JSONL session parser
**Labels:** `epic:parser`, `enhancement`, `priority:high`

**Body:**

### Summary

Parse JSONL session files into typed data models. This is the core data ingestion component.

### Acceptance Criteria

- Given a JSONL file path, when `parse_session(path)` is called, then a list of `SessionMessage` objects is returned
- Given `message.content` as a plain string, then text is correctly extracted
- Given `message.content` as an array of content blocks, then text is correctly extracted from each block
- Given message types to skip (`file-history-snapshot`, `progress`, `hook_progress`, `bash_progress`, `system`, `create`), then they are excluded from results
- Given a line with invalid JSON, then parsing continues with remaining lines (warning logged)
- Given a line with valid JSON but missing expected fields, then it is handled gracefully
- Given an empty file, then an empty list is returned
- `tool_use` blocks within assistant messages are extracted into `ToolUseBlock` models
- `tool_result` messages preserve metadata dict when present
- `usage` data on assistant messages is captured
- Parser is in `agentfluent.core.parser`

### Implementation Notes

- Read line by line (not load entire file) -- session files can be large
- Use Python's `json` module for parsing
- Log malformed lines with line number for debugging
- See CLAUDE.md "JSONL Data Format" for exact schemas and "Types to skip" for exclusion list

### Dependencies

- E2-S1 (data models must be defined)

---

### E2-S4: Wire up agentfluent list command

**Issue title:** Wire up agentfluent list command
**Labels:** `epic:parser`, `enhancement`, `priority:high`

**Body:**

### Summary

Connect the discovery module to the CLI `list` command, replacing the stub from E1.

### Acceptance Criteria

- Given `agentfluent list`, then all projects are displayed in a Rich table with: name, session count, size, date range
- Given `agentfluent list --project SLUG`, then sessions in that project are listed with: filename, size, modified date, message count (requires parsing)
- Given `agentfluent list --project INVALID`, then an error message is shown and exit code is 1
- Given `agentfluent list --format json`, then output is valid JSON with the same data
- Given no projects exist, then a helpful message is shown

### Implementation Notes

- The `--project` flag triggers session-level listing which requires parsing each file (at least partially for message count)
- Consider a "quick mode" that shows file metadata only vs "full mode" that parses for message counts
- See PRD Section 5.1 for full spec

### Dependencies

- E1-S2 (CLI skeleton), E2-S2 (discovery), E2-S3 (parser for message counts)

---

### E2-S5: Add parser unit tests with JSONL fixtures

**Issue title:** Add parser unit tests with JSONL fixtures
**Labels:** `epic:parser`, `testing`, `priority:high`

**Body:**

### Summary

Create comprehensive unit tests for the JSONL parser using anonymized fixture files.

### Acceptance Criteria

- Fixture files in `tests/fixtures/` cover:
  - Session with plain string content
  - Session with array-of-blocks content
  - Session with assistant message containing tool_use blocks
  - Session with tool_result containing metadata block
  - Session with types that should be skipped (progress, system, etc.)
  - File with malformed JSON line(s)
  - Empty file
  - Session with Agent tool_use block (name="Agent" with subagent fields)
- Tests validate:
  - Correct number of messages returned after filtering
  - Content text correctly extracted for both string and array formats
  - tool_use blocks correctly parsed
  - Metadata correctly captured from tool_result
  - Usage data correctly captured from assistant messages
  - Malformed lines don't crash parser
  - Skip types are excluded
- All tests pass and provide >90% coverage of parser module

### Implementation Notes

- Anonymize real session data to create fixtures -- change file paths, user names, project names
- Keep fixture files small (5-20 lines each) for readability
- One fixture file per scenario where possible

### Dependencies

- E1-S3 (test infrastructure), E2-S1 (models), E2-S3 (parser)

---

### E2-S6: Add discovery and parser integration tests

**Issue title:** Add discovery and parser integration tests
**Labels:** `epic:parser`, `testing`, `priority:medium`

**Body:**

### Summary

Integration tests that run against real session data from `~/.claude/projects/`.

### Acceptance Criteria

- Tests marked with `@pytest.mark.integration`
- Tests validate:
  - `discover_projects()` returns at least one project when real data exists
  - `discover_sessions()` returns sessions for a known project
  - `parse_session()` successfully parses a real session file without errors
  - Parsed messages have valid timestamps, non-empty content where expected
- Tests skip gracefully if `~/.claude/projects/` doesn't exist (CI environment)
- Tests do not depend on specific project names or session contents (data may change)

### Implementation Notes

- Use `pytest.mark.skipif` for environment detection
- These tests validate that the parser handles real-world JSONL variations, not just the fixtures

### Dependencies

- E2-S2 (discovery), E2-S3 (parser), E1-S3 (test infrastructure)

---

## E3: Agent Invocation Extraction

**Issue title:** Epic: Agent invocation extraction
**Labels:** `epic:agent-extraction`, `enhancement`

**Body:**

### Summary

Extract agent invocation data from parsed sessions -- identify which agents were called, their metadata, and per-invocation metrics. This layer sits between the raw parser and the analytics/diagnostics consumers.

### Success Criteria

- [ ] Agent tool_use blocks (name="Agent") are identified in parsed sessions
- [ ] Subagent metadata extracted: type, description, prompt, tokens, tool_uses, duration, agentId
- [ ] tool_use blocks matched to their corresponding tool_result
- [ ] Built-in vs custom agents distinguished
- [ ] Per-invocation efficiency metrics computed
- [ ] Unit and integration tests cover extraction logic

### Stories

- [ ] #N -- Define agent invocation data models
- [ ] #N -- Implement agent invocation extractor
- [ ] #N -- Add agent extraction tests

---

### E3-S1: Define agent invocation data models

**Issue title:** Define agent invocation data models
**Labels:** `epic:agent-extraction`, `enhancement`, `priority:high`

**Body:**

### Summary

Define data models for agent invocations extracted from session data.

### Acceptance Criteria

- `AgentInvocation` model captures: agent_type, is_builtin, description, prompt, total_tokens, tool_uses, duration_ms, agent_id, output_text, tokens_per_tool_use, duration_per_tool_use
- `is_builtin` correctly classifies known built-in agents: "explore", "plan", "general-purpose" (and any variations)
- `tokens_per_tool_use` and `duration_per_tool_use` are computed properties
- Model handles missing metadata gracefully (tool_result may lack metadata block)
- Models are in `agentfluent.agents.models`

### Implementation Notes

- See CLAUDE.md "Key signals for agent analysis" for field sources
- See research doc "Custom Agent Data: PM Agent Analysis" for real-world examples of metadata values
- Built-in agent classification may need updating as Anthropic adds new built-in agents -- make the list configurable or at least easy to update

### Dependencies

- E2-S1 (base session models)

---

### E3-S2: Implement agent invocation extractor

**Issue title:** Implement agent invocation extractor
**Labels:** `epic:agent-extraction`, `enhancement`, `priority:high`

**Body:**

### Summary

Extract agent invocations from a list of parsed session messages by matching Agent tool_use blocks with their corresponding tool_result blocks.

### Acceptance Criteria

- Given parsed session messages, when `extract_agent_invocations(messages)` is called, then all Agent invocations are returned as `AgentInvocation` objects
- Agent tool_use blocks are identified by `name == "Agent"` in assistant message content
- Each tool_use is matched to its subsequent tool_result by `tool_use_id`
- When tool_result has metadata, then total_tokens, tool_uses, duration_ms, agentId are captured
- When tool_result lacks metadata, then those fields are None
- Output text is extracted from tool_result content
- Efficiency metrics (tokens_per_tool_use, duration_per_tool_use) are computed when data is available
- Sessions with no agent invocations return an empty list
- Extractor is in `agentfluent.agents.extractor`

### Implementation Notes

- The tool_use `input` dict contains: `subagent_type`, `description`, `prompt` (and possibly other fields)
- tool_result matching: the tool_result following an Agent tool_use has a `tool_use_id` field that matches the tool_use `id`
- Handle edge case: tool_use without a matching tool_result (agent was interrupted)
- See CLAUDE.md examples for exact JSON structure

### Dependencies

- E2-S1 (session models), E2-S3 (parser), E3-S1 (agent models)

---

### E3-S3: Add agent extraction tests

**Issue title:** Add agent extraction tests
**Labels:** `epic:agent-extraction`, `testing`, `priority:high`

**Body:**

### Summary

Unit and integration tests for the agent invocation extractor.

### Acceptance Criteria

- Unit tests with fixtures covering:
  - Session with a single built-in agent invocation (Explore)
  - Session with a custom agent invocation (e.g., PM agent) including full metadata
  - Session with multiple agent invocations of different types
  - Session with agent tool_use but missing tool_result (interrupted)
  - Session with tool_result lacking metadata block
  - Session with no agent invocations
- Integration test: extract agent invocations from a real session known to contain them
- Tests validate correct field values, correct built-in classification, correct efficiency metric computation

### Implementation Notes

- Fixture files can extend those from E2-S5 or create new ones specific to agent extraction
- The CodeFluent project sessions contain real PM agent invocations for integration testing

### Dependencies

- E1-S3 (test infrastructure), E3-S1 (models), E3-S2 (extractor)

---

## E4: Execution Analytics

**Issue title:** Epic: Execution analytics
**Labels:** `epic:analytics`, `enhancement`

**Body:**

### Summary

Compute execution metrics from parsed sessions: token usage, cost, cache efficiency, tool patterns, and per-agent attribution. Powers the `agentfluent analyze` command.

### Success Criteria

- [ ] Token usage totals computed (input, output, cache_creation, cache_read)
- [ ] Cost computed using model pricing lookup
- [ ] Cache efficiency ratio calculated
- [ ] Tool call frequency and diversity metrics
- [ ] Per-agent type cost attribution and efficiency metrics
- [ ] `agentfluent analyze` command functional
- [ ] Tests cover all metric computations

### Stories

- [ ] #N -- Implement model pricing lookup
- [ ] #N -- Implement token and cost analytics
- [ ] #N -- Implement tool pattern analytics
- [ ] #N -- Implement per-agent execution metrics
- [ ] #N -- Wire up agentfluent analyze command
- [ ] #N -- Add analytics unit and integration tests

---

### E4-S1: Implement model pricing lookup

**Issue title:** Implement model pricing lookup
**Labels:** `epic:analytics`, `enhancement`, `priority:high`

**Body:**

### Summary

Pricing table mapping model names to per-token costs for input, output, cache_creation, and cache_read.

### Acceptance Criteria

- Given a model name (e.g., "claude-opus-4-6", "claude-sonnet-4-20250514", "claude-haiku-3-5-20241022"), when pricing is looked up, then correct per-token rates are returned
- Pricing handles: input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens
- Unknown model names return a sensible default or None with a warning
- Pricing data is in a single, easily-updatable location (dict, JSON file, or similar)
- Module is at `agentfluent.analytics.pricing`

### Implementation Notes

- Port from CodeFluent's Python pricing module
- Current Anthropic pricing as of April 2026 should be the starting point
- Consider a configuration file for pricing data so users can update without code changes

### Dependencies

- E1-S1 (package structure)

---

### E4-S2: Implement token and cost analytics

**Issue title:** Implement token and cost analytics
**Labels:** `epic:analytics`, `enhancement`, `priority:high`

**Body:**

### Summary

Compute token usage totals and dollar costs from parsed session messages.

### Acceptance Criteria

- Given parsed session messages with usage data, when `compute_token_metrics(messages)` is called, then totals for input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens are correct
- Given token totals and model name, when cost is computed, then dollar amount uses correct pricing
- Cache efficiency ratio = cache_read / (cache_read + input_tokens), returned as a percentage
- Handles sessions with mixed models (sum costs across models)
- Handles messages missing usage data gracefully
- Module is at `agentfluent.analytics.tokens`

### Implementation Notes

- Port and adapt from CodeFluent's Python analytics module
- Return a structured result object (not just a dict) -- see PRD Section 6 `ExecutionMetrics`

### Dependencies

- E2-S1 (session models), E2-S3 (parser), E4-S1 (pricing)

---

### E4-S3: Implement tool pattern analytics

**Issue title:** Implement tool pattern analytics
**Labels:** `epic:analytics`, `enhancement`, `priority:medium`

**Body:**

### Summary

Analyze tool call patterns across a session: frequency by tool name, unique tool count, tool diversity metrics.

### Acceptance Criteria

- Given parsed session messages, when `compute_tool_metrics(messages)` is called, then tool call frequency by name is returned (sorted by frequency)
- Unique tool count is computed
- Top N tools account for what percentage of total calls (concentration metric)
- Handles sessions with no tool calls
- Module is at `agentfluent.analytics.tools`

### Implementation Notes

- Tool calls are in assistant message content blocks where `type == "tool_use"`
- This counts ALL tool calls in the session, not just Agent calls
- The concentration metric ("4 of 12 tools account for 95% of calls") is useful for config assessment recommendations later

### Dependencies

- E2-S1 (session models), E2-S3 (parser)

---

### E4-S4: Implement per-agent execution metrics

**Issue title:** Implement per-agent execution metrics
**Labels:** `epic:analytics`, `enhancement`, `priority:high`

**Body:**

### Summary

Compute execution metrics grouped by agent type: cost attribution, invocation count, efficiency metrics.

### Acceptance Criteria

- Given a list of `AgentInvocation` objects, when `compute_agent_metrics(invocations)` is called, then per-agent-type metrics are returned:
  - Invocation count
  - Total tokens and cost per agent type
  - Average tokens_per_tool_use per agent type
  - Average duration_per_tool_use per agent type
  - Total duration per agent type
- Metrics are computed separately for built-in vs custom agents
- Summary includes: total agent cost as percentage of session cost
- Handles invocations with missing metadata (excluded from averages, counted in invocation count)
- Module is at `agentfluent.analytics.efficiency`

### Implementation Notes

- This consumes output from E3 (agent extraction) combined with E4-S1 (pricing)
- The "agent cost as percentage of session cost" requires both session-level and agent-level cost data

### Dependencies

- E3-S2 (agent extractor), E4-S1 (pricing), E4-S2 (token analytics)

---

### E4-S5: Wire up agentfluent analyze command

**Issue title:** Wire up agentfluent analyze command
**Labels:** `epic:analytics`, `enhancement`, `priority:high`

**Body:**

### Summary

Connect analytics modules to the CLI `analyze` command, replacing the stub from E1.

### Acceptance Criteria

- Given `agentfluent analyze --project SLUG`, then analytics are computed across all sessions in the project and displayed
- Given `agentfluent analyze --project SLUG --session FILE`, then analytics for a single session are displayed
- Given `agentfluent analyze --project SLUG --agent pm`, then only PM agent metrics are shown
- Output includes: token totals, cost, cache efficiency, tool patterns, agent-specific metrics
- `--format json` produces valid JSON with all metrics
- `--format table` produces Rich-formatted tables
- `--quiet` produces a one-line summary (total cost, total tokens, agent invocation count)
- Given no sessions match, then a helpful message is shown with exit code 2

### Implementation Notes

- This is the main analytical command -- it pulls together parser, extractor, and all analytics modules
- See PRD Section 5.4 for full spec
- Consider a `--latest N` flag to analyze only the N most recent sessions (useful for large projects)

### Dependencies

- E1-S2 (CLI skeleton), E2-S2 (discovery), E2-S3 (parser), E3-S2 (extractor), E4-S2 (token analytics), E4-S3 (tool analytics), E4-S4 (agent metrics)

---

### E4-S6: Add analytics unit and integration tests

**Issue title:** Add analytics unit and integration tests
**Labels:** `epic:analytics`, `testing`, `priority:high`

**Body:**

### Summary

Tests for all analytics computations: pricing, tokens, cost, tools, and per-agent metrics.

### Acceptance Criteria

- Unit tests with known input values verify:
  - Pricing lookup returns correct rates for known models
  - Token totals are summed correctly
  - Cost computation uses correct pricing per model
  - Cache efficiency ratio is computed correctly
  - Tool frequency counts are correct
  - Per-agent metrics (averages, totals) are correct
  - Edge cases: missing usage data, unknown models, empty sessions, invocations without metadata
- Integration test: run full analytics pipeline on a real session and validate output structure (not exact values, since data changes)
- >90% coverage on analytics modules

### Dependencies

- E1-S3 (test infrastructure), E4-S1 through E4-S4

---

## E5: Agent Configuration Assessment

**Issue title:** Epic: Agent configuration assessment
**Labels:** `epic:config-assessment`, `enhancement`

**Body:**

### Summary

Scan agent definition files (`.claude/agents/*.md` at both user and project level), parse their YAML frontmatter and prompt body, and score them against a best-practices rubric. This epic is independent of session data and can be developed in parallel with E3/E4.

### Success Criteria

- [ ] Scanner discovers agent definition files at both `~/.claude/agents/` and `.claude/agents/`
- [ ] YAML frontmatter correctly parsed for all config fields
- [ ] Prompt body (markdown below frontmatter) extracted
- [ ] Scoring rubric evaluates: description quality, tool restrictions, model selection, prompt body quality
- [ ] Per-agent scores and specific recommendations generated
- [ ] `agentfluent config-check` command functional
- [ ] Tests cover scanning, parsing, and scoring

### Stories

- [ ] #N -- Define config assessment data models
- [ ] #N -- Implement agent definition scanner and parser
- [ ] #N -- Implement config scoring rubric
- [ ] #N -- Wire up agentfluent config-check command
- [ ] #N -- Add config assessment tests

---

### E5-S1: Define config assessment data models

**Issue title:** Define config assessment data models
**Labels:** `epic:config-assessment`, `enhancement`, `priority:high`

**Body:**

### Summary

Data models for agent configuration data and scoring results.

### Acceptance Criteria

- `AgentConfig` model captures: name, file_path, scope (user/project), model, tools, disallowed_tools, description, prompt_body, raw_frontmatter (dict for unknown fields)
- `ConfigScore` model captures: agent_name, overall_score (0-100), dimension_scores (dict of dimension -> score), recommendations (list)
- `ConfigRecommendation` model captures: dimension, severity (info/warning/critical), message, current_value (what was found), suggested_action
- Models are in `agentfluent.config.models`

### Implementation Notes

- `raw_frontmatter` preserves any fields not explicitly modeled, for forward compatibility
- Severity levels: info (suggestion), warning (should fix), critical (likely causing issues)

### Dependencies

- E1-S1 (package structure)

---

### E5-S2: Implement agent definition scanner and parser

**Issue title:** Implement agent definition scanner and parser
**Labels:** `epic:config-assessment`, `enhancement`, `priority:high`

**Body:**

### Summary

Discover and parse agent definition `.md` files from user and project scopes.

### Acceptance Criteria

- Given `~/.claude/agents/` contains `.md` files, when `scan_agents(scope="user")` is called, then all agent definitions are returned as `AgentConfig` objects
- Given `.claude/agents/` (cwd) contains `.md` files, when `scan_agents(scope="project")` is called, then all are returned
- Given `scope="all"`, then both locations are scanned
- YAML frontmatter is parsed for: model, tools, disallowedTools, description, memory, isolation, color, and any other fields
- Prompt body (everything after the YAML frontmatter closing `---`) is captured as a string
- Files without valid YAML frontmatter are reported with a warning but don't crash the scanner
- Module is at `agentfluent.config.scanner`

### Implementation Notes

- Use PyYAML or ruamel.yaml for YAML parsing
- Frontmatter format: `---\n...yaml...\n---\nmarkdown body`
- The `tools` field can be a list of strings; `disallowedTools` similarly
- Consider making the project path configurable for testing (not hardcoded to cwd)

### Dependencies

- E5-S1 (config models)

---

### E5-S3: Implement config scoring rubric

**Issue title:** Implement config scoring rubric
**Labels:** `epic:config-assessment`, `enhancement`, `priority:high`

**Body:**

### Summary

Score agent configurations against a best-practices rubric with specific, actionable recommendations.

### Acceptance Criteria

- Scoring dimensions and criteria:
  - **Description quality (0-25):** present (5), length >= 20 chars (5), contains action verbs (5), specific to task (5), distinguishes from other agents (5)
  - **Tool restrictions (0-25):** has `tools` OR `disallowedTools` (15), list is minimal/appropriate (10)
  - **Model selection (0-25):** model is specified (10), model matches task complexity (15) -- e.g., Haiku for read-only tasks, Sonnet/Opus for complex tasks
  - **Prompt body quality (0-25):** present and non-empty (5), length >= 100 chars (5), has structured sections (5), mentions error handling (5), defines success criteria (5)
- Overall score is the sum of dimension scores (0-100)
- Each dimension that scores below threshold generates a specific recommendation
- Recommendations include: what was found, why it matters, what to change
- Module is at `agentfluent.config.scoring`

### Implementation Notes

- Scoring is rule-based (no LLM) for MVP
- "Contains action verbs" could be a simple keyword check (e.g., "analyze", "create", "review", "check")
- "Model matches task complexity" is heuristic -- if tools are read-only (Read, Glob, Grep) and model is Opus, suggest Haiku/Sonnet
- The rubric weights and thresholds should be in a configuration dict, not hardcoded throughout the code, so they're easy to tune

### Dependencies

- E5-S1 (config models), E5-S2 (scanner)

---

### E5-S4: Wire up agentfluent config-check command

**Issue title:** Wire up agentfluent config-check command
**Labels:** `epic:config-assessment`, `enhancement`, `priority:high`

**Body:**

### Summary

Connect config scanner and scoring to the CLI `config-check` command.

### Acceptance Criteria

- Given `agentfluent config-check`, then all agents (user + project scope) are scanned, scored, and displayed
- Given `--scope user`, then only `~/.claude/agents/` is scanned
- Given `--scope project`, then only `.claude/agents/` is scanned
- Given `--agent pm`, then only the PM agent is scored
- Output shows per-agent: name, scope, overall score, dimension scores, recommendations
- Recommendations are color-coded by severity (Rich formatting)
- `--format json` produces valid JSON with scores and recommendations
- Given no agent definition files found, then a helpful message is shown

### Implementation Notes

- See PRD Section 5.5 for full spec
- Consider showing a summary line: "3 agents scanned, average score 62/100, 7 recommendations"

### Dependencies

- E1-S2 (CLI skeleton), E5-S2 (scanner), E5-S3 (scoring)

---

### E5-S5: Add config assessment tests

**Issue title:** Add config assessment tests
**Labels:** `epic:config-assessment`, `testing`, `priority:high`

**Body:**

### Summary

Tests for agent definition scanning, parsing, and scoring.

### Acceptance Criteria

- Fixture files in `tests/fixtures/agents/` with sample agent .md files:
  - Well-configured agent (high score expected)
  - Agent with no tools restriction
  - Agent with vague 1-word description
  - Agent with empty prompt body
  - Agent with no frontmatter
  - Agent with all fields populated
- Unit tests validate:
  - Scanner discovers files in both user and project paths (use temp directories)
  - Parser correctly extracts all frontmatter fields
  - Parser correctly extracts prompt body
  - Each scoring dimension produces expected scores for fixture agents
  - Recommendations are generated for low-scoring dimensions
  - Overall score computation is correct
- Integration test: scan real agent definitions from `~/.claude/agents/` (if they exist)

### Dependencies

- E1-S3 (test infrastructure), E5-S1 through E5-S3

---

## E6: Diagnostics Preview

**Issue title:** Epic: Diagnostics preview
**Labels:** `epic:diagnostics`, `enhancement`

**Body:**

### Summary

The stretch MVP feature: correlate observable behavior signals (from session analytics) with agent configuration data (from config assessment) to generate specific improvement recommendations. This is the differentiator -- "tells you what to change."

Limited to three signal types for MVP: error patterns in output text, token consumption outliers, and duration outliers.

### Success Criteria

- [ ] Error patterns detected in agent output text via keyword/regex matching
- [ ] Token consumption outliers identified (per-agent-type comparison)
- [ ] Duration outliers identified
- [ ] Signals correlated to specific config surfaces (prompt, tools, model)
- [ ] Actionable recommendations generated with evidence
- [ ] Diagnostics output integrated into `agentfluent analyze --diagnostics`
- [ ] Tests cover signal detection and recommendation generation

### Stories

- [ ] #N -- Implement behavior signal extraction
- [ ] #N -- Implement signal-to-config correlation engine
- [ ] #N -- Implement recommendation templates
- [ ] #N -- Integrate diagnostics into analyze command
- [ ] #N -- Add diagnostics tests

---

### E6-S1: Implement behavior signal extraction

**Issue title:** Implement behavior signal extraction
**Labels:** `epic:diagnostics`, `enhancement`, `priority:high`

**Body:**

### Summary

Extract behavior signals from agent invocation data: error patterns in output text, token consumption outliers, duration outliers.

### Acceptance Criteria

- Given agent invocations, when `extract_signals(invocations)` is called, then detected signals are returned as `DiagnosticSignal` objects
- **Error pattern detection:** regex/keyword matching in agent output_text for: "blocked", "unable to", "don't have access", "failed", "error", "retry", "permission denied", "not found", "timed out"
  - Each match produces a signal with: signal_type="error_pattern", severity, matched keyword, text snippet (context around match)
- **Token outlier detection:** invocations where tokens_per_tool_use is > 2x the mean for that agent type
  - Signal with: signal_type="token_outlier", severity="warning", actual value, mean value
- **Duration outlier detection:** invocations where duration_per_tool_use is > 2x the mean for that agent type
  - Signal with: signal_type="duration_outlier", severity="warning", actual value, mean value
- Handles agents with only one invocation (no outlier detection possible -- skip)
- Module is at `agentfluent.diagnostics.signals`

### Implementation Notes

- Error keywords should be configurable (list, not hardcoded inline)
- Outlier threshold (2x mean) should be configurable
- For MVP, "mean" is computed across all invocations of the same agent_type in the analyzed data
- Severity: error patterns with "blocked"/"permission denied" are "critical"; others are "warning"

### Dependencies

- E3-S1 (agent models with output_text and efficiency metrics)

---

### E6-S2: Implement signal-to-config correlation engine

**Issue title:** Implement signal-to-config correlation engine
**Labels:** `epic:diagnostics`, `enhancement`, `priority:high`

**Body:**

### Summary

Map detected behavior signals to specific agent configuration surfaces and generate actionable recommendations.

### Acceptance Criteria

- Given a list of `DiagnosticSignal` objects and optionally the agent's `AgentConfig`, when `correlate(signals, config)` is called, then `Recommendation` objects are returned
- Correlation rules (MVP set):
  - Error pattern "blocked"/"permission denied"/"don't have access" -> check `tools`/`disallowedTools` in config -> recommend reviewing tool access
  - Error pattern "failed"/"error"/"retry" -> check prompt body for error handling instructions -> recommend adding error handling guidance
  - Token outlier -> check prompt body length and specificity -> recommend more focused instructions
  - Token outlier + large tools list -> recommend restricting tool list
  - Duration outlier -> check model selection -> recommend a faster model if task is simple
  - Duration outlier + high tool_uses -> check prompt for clear task boundaries -> recommend more specific task scoping
- When `AgentConfig` is None (agent def not found), recommendations are generic ("check your agent's tool configuration")
- When `AgentConfig` is available, recommendations reference specific config fields ("your agent 'pm' has no `tools` restriction in `~/.claude/agents/pm.md`")
- Module is at `agentfluent.diagnostics.correlator`

### Implementation Notes

- Correlation rules should be structured data (list of rule objects), not a chain of if/else
- This makes rules easy to add, test individually, and eventually configure
- When config is available, recommendations become much more specific -- this is where E5 and E6 connect

### Dependencies

- E5-S1 (config models, for optional config input), E6-S1 (signal extraction)

---

### E6-S3: Implement recommendation templates

**Issue title:** Implement recommendation templates
**Labels:** `epic:diagnostics`, `enhancement`, `priority:medium`

**Body:**

### Summary

Structured recommendation templates that produce human-readable, actionable output.

### Acceptance Criteria

- Each recommendation includes: target_config_surface (prompt/tools/model/hooks), severity, human-readable message, evidence references (signal type + data)
- Messages follow the pattern: "[What was observed] + [Why it matters] + [What to change]"
- Examples:
  - "Agent 'pm' output contains 'blocked' (2 occurrences). This indicates tool access issues. Check that `tools` in `~/.claude/agents/pm.md` includes the required tools, and verify hook permissions."
  - "Agent 'pm' averages 2,259 tokens per tool call, 2.3x above the mean for custom agents. Consider adding more specific instructions to the prompt body to reduce exploration."
  - "Agent 'pm' invocations average 13.4s per tool call, significantly above the 8.8s mean. If tasks are routine, consider switching from claude-opus-4-6 to claude-sonnet-4-20250514."
- Templates are in `agentfluent.diagnostics.recommendations`
- Templates support both human-readable (Rich) and structured (JSON) output

### Implementation Notes

- Template strings with format placeholders -- not hardcoded prose
- JSON output should include the structured data (not just the rendered string)

### Dependencies

- E6-S2 (correlator produces the data that templates render)

---

### E6-S4: Integrate diagnostics into analyze command

**Issue title:** Integrate diagnostics into analyze command
**Labels:** `epic:diagnostics`, `enhancement`, `priority:high`

**Body:**

### Summary

Add diagnostics output to the `agentfluent analyze` command.

### Acceptance Criteria

- Given `agentfluent analyze --project SLUG --diagnostics`, then diagnostics section is appended to the analytics output
- Diagnostics section shows: detected signals grouped by agent, recommendations sorted by severity
- Without `--diagnostics` flag, diagnostics are still shown if signals are detected, but in a summary form ("3 diagnostic signals detected. Run with --diagnostics for details.")
- `--format json` includes a `diagnostics` key with signals and recommendations
- When no agent invocations exist in the analyzed sessions, diagnostics section states "No agent invocations found -- diagnostics require agent activity"
- When agent config files are found, diagnostics cross-reference them for more specific recommendations

### Implementation Notes

- The analyze command now orchestrates: parse -> extract agents -> compute analytics -> extract signals -> correlate with config -> format output
- Config scanning (from E5) is optional for diagnostics -- if agent def files aren't found, generic recommendations are still generated

### Dependencies

- E4-S5 (analyze command), E6-S1 (signals), E6-S2 (correlator), E6-S3 (templates)

---

### E6-S5: Add diagnostics tests

**Issue title:** Add diagnostics tests
**Labels:** `epic:diagnostics`, `testing`, `priority:high`

**Body:**

### Summary

Tests for behavior signal extraction, correlation engine, and recommendation generation.

### Acceptance Criteria

- Unit tests for signal extraction:
  - Agent output containing error keywords -> signals detected with correct type and severity
  - Agent output with no error keywords -> no error signals
  - Agent invocations with token outlier -> token_outlier signal generated
  - Agent invocations with duration outlier -> duration_outlier signal generated
  - Single invocation of agent type -> no outlier detection (insufficient data)
- Unit tests for correlator:
  - Error pattern signal + config with no tools restriction -> tool access recommendation
  - Error pattern signal + config with tools specified -> error handling prompt recommendation
  - Token outlier signal + config with long tools list -> tool restriction recommendation
  - Duration outlier signal + config with overqualified model -> model downgrade recommendation
  - Signals without config -> generic recommendations
- Unit tests for recommendation templates:
  - Rendered messages contain the expected pattern (observation + reason + action)
  - JSON output includes all structured fields
- Integration test: run full diagnostics pipeline on a real session with agent invocations

### Dependencies

- E1-S3 (test infrastructure), E6-S1 through E6-S3

---

## E7: CLI Output + Formatting

**Issue title:** Epic: CLI output and formatting
**Labels:** `epic:cli-output`, `enhancement`

**Body:**

### Summary

Polish the CLI output across all commands: consistent Rich formatting, proper JSON output, verbose/quiet modes, help text, exit codes.

### Success Criteria

- [ ] All commands produce consistent Rich table output by default
- [ ] All commands produce valid, structured JSON with `--format json`
- [ ] `--verbose` adds per-invocation detail to all commands
- [ ] `--quiet` produces summary-only output for all commands
- [ ] Exit codes are consistent: 0 success, 1 error, 2 no data
- [ ] Help text includes usage examples
- [ ] JSON output is parseable by `jq` and has a stable schema

### Stories

- [ ] #N -- Implement Rich table formatters for all commands
- [ ] #N -- Implement JSON output schema and formatters
- [ ] #N -- Implement verbose and quiet output modes
- [ ] #N -- Add CLI output tests
- [ ] #N -- Add help text with usage examples

---

### E7-S1: Implement Rich table formatters for all commands

**Issue title:** Implement Rich table formatters for all commands
**Labels:** `epic:cli-output`, `enhancement`, `priority:high`

**Body:**

### Summary

Create Rich-formatted table output for all three commands (list, analyze, config-check).

### Acceptance Criteria

- `list` command: project table (name, sessions, size, dates), session table (file, size, date, messages)
- `analyze` command: summary panel, token/cost table, tool frequency table, agent metrics table, diagnostics panel (if applicable)
- `config-check` command: per-agent score card with dimension scores, recommendations list with severity color-coding
- Color coding: green (good/high score), yellow (warning/medium), red (critical/low score)
- Tables render correctly in 80-column terminal
- Formatters are in `agentfluent.cli.formatters.table`

### Implementation Notes

- Use Rich Tables, Panels, and Console for formatting
- Keep formatters separate from command logic -- commands produce data objects, formatters render them
- Consider a `Formatter` protocol/base class for consistency

### Dependencies

- All commands functional (E2-S4, E4-S5, E5-S4, E6-S4)

---

### E7-S2: Implement JSON output schema and formatters

**Issue title:** Implement JSON output schema and formatters
**Labels:** `epic:cli-output`, `enhancement`, `priority:high`

**Body:**

### Summary

Structured JSON output mode for all commands, suitable for piping to `jq` or programmatic consumption.

### Acceptance Criteria

- All commands with `--format json` output valid JSON to stdout
- JSON schema includes a top-level `version` field for future compatibility
- No Rich formatting, color codes, or escape sequences in JSON output
- Pydantic models serialize cleanly to JSON (datetime as ISO strings, enums as strings)
- JSON includes all data shown in table format plus additional detail (no information loss)
- Formatters are in `agentfluent.cli.formatters.json_output`

### Implementation Notes

- Use Pydantic's `.model_dump(mode="json")` for serialization
- Print to stdout; all non-data output (warnings, progress) goes to stderr
- Test with `| jq .` to validate

### Dependencies

- All commands functional, data models finalized

---

### E7-S3: Implement verbose and quiet output modes

**Issue title:** Implement verbose and quiet output modes
**Labels:** `epic:cli-output`, `enhancement`, `priority:medium`

**Body:**

### Summary

`--verbose` and `--quiet` flags across all commands.

### Acceptance Criteria

- `--verbose`: adds per-invocation breakdown (each agent invocation with metrics), per-session breakdown (when analyzing a project), raw signal data in diagnostics
- `--quiet`: summary only -- single line or minimal output per command
  - `list`: "N projects, M total sessions"
  - `analyze`: "Project X: $Y.YY cost, N tokens, M agent invocations, K diagnostic signals"
  - `config-check`: "N agents scanned, average score MM/100, K recommendations"
- `--quiet` output fits in 5 lines or fewer
- `--verbose` and `--quiet` are mutually exclusive
- Both work with `--format json` (JSON structure adjusts: minimal for quiet, expanded for verbose)

### Dependencies

- E7-S1 (table formatters), E7-S2 (JSON formatters)

---

### E7-S4: Add CLI output tests

**Issue title:** Add CLI output tests
**Labels:** `epic:cli-output`, `testing`, `priority:medium`

**Body:**

### Summary

Tests for CLI output formatting, exit codes, and output mode switching.

### Acceptance Criteria

- Tests invoke CLI commands via subprocess (or Typer's test client)
- Validate:
  - Exit code 0 on success
  - Exit code 1 on error (invalid project, missing flags)
  - Exit code 2 on no data
  - `--format json` output is valid JSON
  - `--quiet` output is <= 5 lines
  - `--version` prints version string
  - `--help` produces help text
- Test both table and JSON output for at least one command

### Dependencies

- E7-S1 through E7-S3

---

### E7-S5: Add help text with usage examples

**Issue title:** Add help text with usage examples
**Labels:** `epic:cli-output`, `documentation`, `priority:low`

**Body:**

### Summary

Enhance `--help` output with usage examples for each command.

### Acceptance Criteria

- Each command's help text includes at least 2 usage examples
- Examples:
  - `agentfluent list` -- list all projects
  - `agentfluent list --project codefluent` -- list sessions in codefluent project
  - `agentfluent analyze --project codefluent --agent pm` -- analyze PM agent in codefluent
  - `agentfluent analyze --project codefluent --format json | jq '.cost'` -- programmatic usage
  - `agentfluent config-check --scope user` -- check user-level agents only
- Top-level help includes a brief description of AgentFluent's purpose

### Implementation Notes

- Typer supports `help` parameter on commands and `epilog` for examples
- Consider a `rich_help_panel` for grouped options

### Dependencies

- All commands functional

---

## Implementation Priority Order

The recommended implementation sequence, accounting for dependencies:

### Phase 1: Foundation (E1)
1. E1-S1 -- Initialize Python package with uv
2. E1-S2 -- Create CLI skeleton with Typer
3. E1-S3 -- Set up pytest infrastructure
4. E1-S4 -- Configure GitHub Actions CI
5. E1-S5 -- Update CLAUDE.md

### Phase 2: Core Data Layer (E2)
6. E2-S1 -- Define core data models
7. E2-S2 -- Implement project/session discovery
8. E2-S3 -- Implement JSONL parser
9. E2-S4 -- Wire up list command
10. E2-S5 -- Parser unit tests
11. E2-S6 -- Discovery/parser integration tests

### Phase 3: Agent Layer (E3) + Analytics (E4) -- can partially overlap with E5
12. E3-S1 -- Agent invocation data models
13. E3-S2 -- Agent invocation extractor
14. E3-S3 -- Agent extraction tests
15. E4-S1 -- Model pricing lookup
16. E4-S2 -- Token and cost analytics
17. E4-S3 -- Tool pattern analytics
18. E4-S4 -- Per-agent execution metrics
19. E4-S5 -- Wire up analyze command
20. E4-S6 -- Analytics tests

### Phase 4: Config Assessment (E5) -- can develop in parallel with Phase 3
21. E5-S1 -- Config assessment data models
22. E5-S2 -- Agent definition scanner/parser
23. E5-S3 -- Config scoring rubric
24. E5-S4 -- Wire up config-check command
25. E5-S5 -- Config assessment tests

### Phase 5: Diagnostics (E6)
26. E6-S1 -- Behavior signal extraction
27. E6-S2 -- Signal-to-config correlation engine
28. E6-S3 -- Recommendation templates
29. E6-S4 -- Integrate diagnostics into analyze command
30. E6-S5 -- Diagnostics tests

### Phase 6: Polish (E7)
31. E7-S1 -- Rich table formatters
32. E7-S2 -- JSON output formatters
33. E7-S3 -- Verbose/quiet modes
34. E7-S4 -- CLI output tests
35. E7-S5 -- Help text with examples

**Total: 7 epics, 35 stories**
