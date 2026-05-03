## Summary
<!-- 1-3 bullet points describing what this PR does -->

## Test plan
- [ ] Unit tests pass: `uv run pytest -m "not integration"`
- [ ] Lint clean: `uv run ruff check src/ tests/`
- [ ] Type check clean: `uv run mypy src/agentfluent/`
- [ ] New/changed behavior has test coverage
- [ ] Manual smoke test via `uv run agentfluent ...` — required for CLI output changes

## Security review
Pick one. (If "Needs review", apply the `needs-security-review` label only when the PR is dev-complete and ready to merge — the workflow runs once against the SHA at label-add time and is not re-fired by later pushes, so labeling early produces a stale review against pre-merge code.)
- [ ] **Skip review** — no security-sensitive surface (refactor, test-only, internal logic, docs, model additions consumed only by trusted internal code).
- [ ] **Needs review** — touches any of: `.claude/hooks/`, secret handling, `pyproject.toml`, `.github/workflows/`, CLI argument parsing, path resolution, JSONL parsing, network calls, subprocess invocation, or rendering of user-controlled strings.

## Breaking changes
<!--
Does this change a public contract? If yes, describe the before/after and the migration path. Examples:
- JSON envelope `version` field bumped (e.g., 1 → 2)
- CLI flag renamed, removed, or changed default value
- Exit code semantics changed for an error path
- Pydantic model field removed or renamed
-->
