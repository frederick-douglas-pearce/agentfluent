# Contributing to AgentFluent

Thanks for your interest in contributing! AgentFluent is a local-first CLI tool for analyzing AI agent session data — token usage, tool patterns, behavior diagnostics, and agent-configuration quality scoring. See the [README](README.md) for the project overview.

## How AgentFluent relates to CodeFluent

AgentFluent is a standalone sibling project to [CodeFluent](https://github.com/frederick-douglas-pearce/codefluent). They share data sources (Claude Code JSONL sessions) and some infrastructure, but analyze fundamentally different things:

- **CodeFluent** measures *human AI fluency* — how well a developer collaborates with Claude Code in interactive sessions.
- **AgentFluent** diagnoses *agent quality* — why an agent misbehaves and what concrete changes to its configuration fix it.

See [`CLAUDE.md`](CLAUDE.md) for the full architectural context, including the novel components (behavior signal extraction, correlation engine, recommendation templates) and what was ported from CodeFluent.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** for package and environment management
- **[`gh` CLI](https://cli.github.com/)** — authenticated (`gh auth login`) for PR workflows
- **Git**

## Dev setup

```bash
git clone https://github.com/frederick-douglas-pearce/agentfluent.git
cd agentfluent
uv sync                  # create venv, install dev + runtime deps
uv run agentfluent --help
```

The CLI entry point is `agentfluent` (via `src/agentfluent/cli/main.py`). Commands:

- `agentfluent list` — discover projects and sessions in `~/.claude/projects/`
- `agentfluent analyze --project <slug>` — token/cost/tool/agent analytics + diagnostics
- `agentfluent config-check` — score agent definitions in `~/.claude/agents/` and `.claude/agents/`

## Project layout

```
src/agentfluent/
├── cli/              # Typer app, commands, formatters, exit codes
├── core/             # JSONL parser, session models, project discovery
├── agents/           # agent invocation extraction + models
├── analytics/        # token/cost, tool pattern, per-agent metrics, pricing
├── config/           # agent definition scanner + scoring rubric
└── diagnostics/      # behavior signals, correlation, recommendations
tests/
├── unit/             # fast tests with JSONL fixtures
└── integration/      # tests against real ~/.claude/projects/ data, skipped in CI
docs/                 # research + market analysis
.claude/specs/        # PRDs, decisions, backlog
```

See [`CLAUDE.md`](CLAUDE.md) for detailed architecture notes.

## Picking work to contribute

AgentFluent is a maintainer-led project on a defined roadmap. To keep contributor effort on a clear path to merge:

1. **Comment on the issue** you want to work on, or open a new issue describing the change first.
2. **Wait for a maintainer to assign the issue to you** before writing code. Assignment confirms the work is unclaimed, scoped correctly, and aligned with the current milestone.
3. **Then open a PR** referencing the issue (`Closes #N`).

Issues already assigned, or actively in flight on a feature branch, are not available — please pick another. Issues labeled [`good first issue`](https://github.com/frederick-douglas-pearce/agentfluent/issues?q=is%3Aopen+is%3Aissue+label%3A%22good+first+issue%22) or [`help wanted`](https://github.com/frederick-douglas-pearce/agentfluent/issues?q=is%3Aopen+is%3Aissue+label%3A%22help+wanted%22) are good entry points.

**Unassigned PRs may be closed without review.** This isn't a judgement on the work — it's the only sustainable way to avoid duplicate effort when multiple contributors target the same issue at once. If your PR is closed for this reason, you're welcome to claim a different issue and try again.

## Development workflow

### Running tests

```bash
uv run pytest -m "not integration"          # unit tests (runs in <1s, run before every commit)
uv run pytest                               # includes integration tests (needs ~/.claude/projects/ data)
uv run pytest --cov=agentfluent             # with coverage
```

### Linting + type checking

```bash
uv run ruff check src/ tests/               # lint
uv run ruff check --fix src/ tests/         # auto-fix
uv run mypy src/agentfluent/                # strict type check
```

All three must pass before merge. CI runs them on every PR (`.github/workflows/ci.yml`).

### Branch + commit conventions

- Branch from `main` using `feature/<issue-number>-short-description` or `fix/<issue-number>-...`
- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`. These drive automated version bumps via release-please.
- Breaking changes: add `!` (`feat!: remove legacy API`) or include `BREAKING CHANGE:` in the body.
- Keep PRs focused — one issue per PR whenever possible.

### Before opening a PR

Run the checklist in [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md). Update `CLAUDE.md` if your change touches architecture or conventions.

### Feature specification

For non-trivial features, use the PM subagent workflow described in `CLAUDE.md`:
- PM agent drafts spec in `.claude/specs/` and opens GitHub issues
- Architect agent reviews the spec/design *before* implementation (posts findings on the issue)
- Implementation addresses any blocking architect concerns

## Releases

Releases are automated via [release-please](https://github.com/googleapis/release-please). Every push to `main` updates a rolling "release PR" with the next version and changelog. Merging that PR tags the release, publishes to PyPI (via [Trusted Publisher](https://docs.pypi.org/trusted-publishers/)), and attaches built sdist + wheel to the GitHub release.

You don't need to edit `CHANGELOG.md` or version numbers manually — release-please derives them from Conventional Commit messages.

## Security

Report security issues via GitHub's private vulnerability reporting (see `SECURITY.md`). Do not open public issues for security concerns.

## Questions?

Open a [GitHub Discussion](https://github.com/frederick-douglas-pearce/agentfluent/discussions) or file an issue.
