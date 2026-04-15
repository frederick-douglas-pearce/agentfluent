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
