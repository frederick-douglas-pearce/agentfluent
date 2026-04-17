## Summary
<!-- 1-3 bullet points describing what this PR does -->

## Test plan
- [ ] Unit tests pass: `uv run pytest -m "not integration"`
- [ ] Lint clean: `uv run ruff check src/ tests/`
- [ ] Type check clean: `uv run mypy src/agentfluent/`
- [ ] New/changed behavior has test coverage
- [ ] Manual smoke test via `uv run agentfluent ...` — required for CLI output changes

## Breaking changes
<!--
Does this change a public contract? If yes, describe the before/after and the migration path. Examples:
- JSON envelope `version` field bumped (e.g., 1 → 2)
- CLI flag renamed, removed, or changed default value
- Exit code semantics changed for an error path
- Pydantic model field removed or renamed
-->
