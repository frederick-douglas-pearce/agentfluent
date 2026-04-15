# Decision Log

Append-only log of significant trade-off decisions made during AgentFluent development.

---

## D001: Python as sole MVP language

**Date:** 2026-04-14
**Context:** CLAUDE.md lists "TypeScript and Python" as the tech stack. The research doc references TypeScript file names (parser.ts, analytics.ts) for code reuse from CodeFluent.
**Decision:** MVP will be Python-only. No TypeScript.
**Rationale:**
- Fred is a Python developer and will be the primary contributor
- CodeFluent's Python webapp (FastAPI backend) already has working Python implementations of the JSONL parser, token analytics, config scanner, and pricing lookup
- CLI tool maps naturally to Python ecosystem (Typer/Click, Rich)
- Agent SDK has a Python SDK, aligning with the primary target audience
- TypeScript reuse references in CLAUDE.md were from the VS Code extension side; the Python webapp code is equally reusable

**Action required:** Update CLAUDE.md "Architecture Context > Code Reuse from CodeFluent" section to reference Python module names instead of .ts files, and update "Tech Stack" section to reflect Python-only for MVP.

---

## D002: Stretch MVP scope (Option B) with diagnostics preview

**Date:** 2026-04-14
**Context:** Two MVP options were presented: (A) execution analytics + config assessment only, (B) add diagnostics preview demonstrating behavior-to-config correlation.
**Decision:** Option B -- include diagnostics preview.
**Rationale:**
- The tagline ("tells you what to change") requires at least a preview of behavior-to-config correlation to be credible
- Subagent metadata (total_tokens, tool_uses, duration_ms) plus output text pattern matching provides enough signal for meaningful recommendations without needing internal traces
- Rule-based heuristics (not LLM-powered) keeps complexity bounded
- Preview scope: error pattern detection, efficiency outliers, duration outliers -- three signal types is achievable

**Trade-off:** Adds approximately 1 week to MVP timeline. Mitigated by keeping diagnostics rule-based and limiting to 3 signal types.

---

## D003: Project-agnostic from day one

**Date:** 2026-04-14
**Context:** Could have hardcoded the tool to analyze a single known project for faster MVP.
**Decision:** Project discovery and selection is part of MVP scope.
**Rationale:**
- `~/.claude/projects/` contains multiple project directories; users need to choose
- CodeFluent and AgentFluent's own project sessions serve as test data, requiring multi-project support
- Discovery is low-complexity (directory listing) with high usability value
- Avoids hardcoded paths that would need refactoring later

---

## D004: Both user-level and project-level agent definition scanning

**Date:** 2026-04-14
**Context:** Agent definitions live in two locations: `~/.claude/agents/` (user-level, shared across projects) and `.claude/agents/` (project-level, specific to a repo).
**Decision:** Scan both locations in MVP. Agent SDK source parsing deferred.
**Rationale:**
- Both locations are documented in Anthropic's agent system and are actively used
- The `--scope` flag (user/project/all) gives users control
- Agent SDK `AgentDefinition` objects in source code require AST parsing -- significantly more complex, deferred until Agent SDK test data exists

---

## D005: Comprehensive test strategy (unit fixtures + integration against real data)

**Date:** 2026-04-14
**Context:** Options ranged from minimal unit tests to full integration testing.
**Decision:** Both unit tests with anonymized fixtures AND integration tests against real session data. CI/CD pipeline from the start.
**Rationale:**
- Fred considers extensive testing essential for this project
- Real session data from CodeFluent and AgentFluent projects is available for integration testing
- Fixtures ensure reproducibility; real data ensures correctness against actual formats
- CI/CD infrastructure prevents regressions as the codebase grows

---

## D006: CLI framework recommendation (Typer)

**Date:** 2026-04-14
**Context:** Fred had no strong preference on CLI framework, only that it be Python.
**Decision:** Recommend Typer (built on Click) as default, with Click as fallback.
**Rationale:**
- Typer provides type-hint-based CLI definition, reducing boilerplate
- Built on Click, so all Click features are available if needed
- Auto-generates `--help` from type annotations and docstrings
- Rich integration for formatted output
- Developer makes final call on framework choice

---

## D007: uv for dependency management

**Date:** 2026-04-14
**Context:** Fred specified uv as the dependency management tool. Alternatives considered: pip, poetry, pdm.
**Decision:** Use uv for all dependency management, virtual environment creation, and script running.
**Rationale:**
- Fred's explicit preference
- uv is fast, supports pyproject.toml natively, handles lockfiles, and replaces pip/pip-tools/virtualenv
- Aligns with modern Python tooling trends
- Project scaffolding should use `uv init` and `pyproject.toml`, not `setup.py` or `requirements.txt`

---

## D008: Subagent trace discovery does NOT expand MVP scope

**Date:** 2026-04-15
**Context:** Full subagent session JSONL traces discovered at `~/.claude/projects/<project>/<session-uuid>/subagents/agent-<agentId>.jsonl` (350 files across projects). Contains complete tool_use/tool_result sequences with `is_error` flags, per-step token usage, internal reasoning, all with `isSidechain: true`. Features previously classified as "requires Agent SDK data" (prompt-to-behavior correlation, detailed error analysis, internal reasoning analysis) are now feasible with existing Claude Code subagent data. See CodeFluent decisions D6-D8 for the discovery details.

**Options considered:**
- A) Expand MVP to parse subagent traces -- adds E8 (subagent trace parser) and E9 (deep diagnostics) to MVP scope
- B) Keep MVP scope fixed; subagent trace parsing becomes v1.1, with minor MVP adjustments to acknowledge the data exists

**Decision:** Option B -- MVP scope stays fixed. Subagent trace parsing is v1.1.

**Rationale:**
- The MVP is already a stretch scope (D002) with 7 epics and 35 stories
- Subagent trace parsing has real complexity: discovering `<session-uuid>/subagents/` directories, linking subagent files to parent session invocations via `agentId`, parsing a second JSONL format with `isSidechain: true`, handling parent-child relationships
- The MVP's value proposition ("tells you what to change") works with parent-session metadata. Full traces improve recommendation *quality* but don't change whether the concept works
- More data does not mean more MVP scope -- it means a better v1.1 with genuine per-tool-call evidence behind every recommendation
- The discovery is better served as the headline feature of v1.1 ("deep diagnostics") than as MVP scope creep

**MVP changes (minimal):**
- #14 (session discovery): add subagent file counting (enumerate, don't parse)
- #36 (diagnostics integration): add forward-looking note when subagent traces are detected

**Post-MVP additions (v1.1 roadmap):**
- E8: Subagent Trace Parser (discover, model, parse, link subagent JSONL files)
- E9: Deep Diagnostics (retry sequences, error recovery patterns, prompt-to-behavior correlation with per-tool-call evidence)

**Impact on positioning:** CodeFluent Decision D8 correctly identifies that AgentFluent's trigger is now "audience divergence, not data availability." For the MVP, this means AgentFluent demonstrates its value with existing metadata-level analysis. In v1.1, it leapfrogs to full-trace analysis that no other local-first tool offers. The phased approach turns the discovery into two product moments instead of one.
