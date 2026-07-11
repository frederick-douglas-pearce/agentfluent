# Loop config — AgentFluent

Per-project bindings for the supervised dev loop. `loop-engine.md` (the generic engine)
references every value below **by parameter name**; this file is the only surface a porting
project edits. A non-Python / non-`src/` / non-GitHub project revises **all four sections
below** (parameters, architect triggers, source layout, security routing) — never the engine.

See `.claude/skills/release-loop/loop-engine.md` for the operating procedure and semantics.

---

## 1. Project parameters

The binding table. The engine names each parameter in `CAPS`; the values here are AgentFluent's.

| Parameter | AgentFluent value | Notes |
|-----------|-------------------|-------|
| `BACKLOG_SOURCE` | GitHub milestone (e.g. `v0.11.0`) via `gh` | could be a label (an epic is the label case — `gh issue list --label epic:<name>`), or a local `TODO.md` |
| `SCOPE_AGENT` | `pm` (user-global subagent) | answers scope/priority/requirements questions; remove if project has none |
| `DESIGN_AGENT` | `architect` (user-global subagent) | reviews plans pre-implementation; remove if none |
| `CODE_REVIEW` | `/code-review` (Claude Code **built-in skill**) | independent post-impl review; verified 2026-06-29. The repo's `/review`/`/simplify` (CLAUDE.md) are alternatives, not this. |
| `SECURITY_REVIEW` | local `/security-review` (built-in skill) for `.claude/`-only; else `needs-security-review` label → `security-review.yml` | see §4 below |
| `VERIFY` | `/verify` (built-in skill) | runtime behavior check when an AC needs proof-by-running |
| `PRIORITY_LABELS` | `priority:high > priority:medium > priority:low`; tiebreak: issue number ascending | drives selection (engine → Select / Initialization) |
| `ARCHITECT_TRIGGERS` | see §2 below | **project-specific — edit when porting** |
| `SOURCE_LAYOUT` | see §3 below | router uses this; **edit when porting** |
| `TEST_CMD` | `uv run pytest -m "not integration"` | |
| `LINT_CMD` | `uv run ruff check src/ tests/` | |
| `TYPE_CMD` | `uv run mypy src/agentfluent/` | |
| `CI_STATUS_CMD` | `gh pr checks <PR>` | |
| `BRANCH_FMT` | `feature/<n>-slug` / `fix/<n>-slug` | from CLAUDE.md |
| `COMMIT_CONV` | Conventional Commits; `.claude/**`→`chore:`/`docs:` | see commit-scope rule below |
| `PR_TEMPLATE` | `.github/PULL_REQUEST_TEMPLATE.md` (must replicate) | |
| `MERGE_METHOD` | squash, `--delete-branch`, explicit `--subject` scope | |
| `APPEND_ONLY_FILES` | `.claude/specs/decisions.md` | guarded by the C1 hook |
| `PERMISSION_POSTURE` | background agents validate-only → parent implements | see hard constraints below |
| `LEDGER_ROOT` | `.claude/loop/` | **gitignored** — local working state, never committed |
| `RELEASE_SCHEME` | SemVer via release-please; PyPI artifact | the engine's merge gate reads "≤ patch bump or no bump"; a project with **no** release cycle (e.g. an epic-sourced run) treats every change as "no release-artifact bump" |

**Workflow conventions (from CLAUDE.md).** Branch from `main`; PR with passing CI before
merge (naming `BRANCH_FMT`). PR body **must replicate** `PR_TEMPLATE` (CI's `PR Template Check`
rejects otherwise). Tests required for code changes; no regressions; mypy strict on `src/`.

**Commit-scope rule.** `.claude/**` changes are maintainer-only tooling → `chore:`/`docs:`,
never `feat:`/`fix:` (avoids release-please mis-bumps). The orchestrator sets the squash
**`--subject` scope explicitly**, not inheriting the PR title.

**Hard constraints that shape the loop here (project-specific unless noted):**
1. Background/non-interactive agents are **validate-only** — `settings.local.json` withholds
   Edit/Write/git/gh from agents that can't prompt. The parent (interactive) thread does all
   implementation + git/gh; no fan-out of implementation. *(A project without this restriction
   could parallelize iterations via worktrees — see engine roadmap.)*
2. *(General, Claude Code)* Subagents can't invoke subagents — the orchestrator drives
   `SCOPE_AGENT`/`DESIGN_AGENT` directly.
3. CI is gated on `branches:[main]`; stacked PRs break it → one PR at a time, each from `main`.
4. `SCOPE_AGENT`/`DESIGN_AGENT` are user-global, not repo-tracked; editing them yields no PR
   and needs a session restart.

---

## 2. `ARCHITECT_TRIGGERS`

Fire `DESIGN_AGENT` when the plan: touches shared models (`SessionMessage`, `AgentInvocation`);
changes a cross-module interface; adds a new diagnostics rule, correlation logic, or analytics
pipeline; **or the orchestrator is unsure.** Bias toward calling it (read-only, cheap vs. a bad
implementation). Skip for docs and trivial research.

---

## 3. `SOURCE_LAYOUT` — router signals

The project-specific inputs to the engine's Route classification (engine → Router):

- **Package layout:** code in `src/agentfluent/`; tests in `tests/`; research/throwaway
  scaffolding lives **outside** `src/` with **no runtime-dep leakage** into the package.
- **`docs` label / path:** GitHub label `documentation`, change confined to `docs/`/markdown.
- **`research` label / path:** GitHub label `research` or an epic named `*-discovery`; "artifact
  under study is data"; no test-coverage gate.
- **`stub-defer` marker:** issue body says "NOT implementation-ready" / is a tracking stub
  (e.g. #469, D041 — carried, not implemented).
- **`code` (default):** `bug`/`enhancement` touching `src/` → full pipeline.

---

## 4. Security routing (host-repo / GitHub specifics)

The engine's step-10 security gate reads its *specifics* from here (engine keeps the gate's
position + confidence-bar discipline):

- **`.claude/`-only change** → run local `SECURITY_REVIEW` (`/security-review`). The labeled
  workflow (`security-review.yml`) **excludes** `.claude/`, so the label does nothing here; the
  local skill needs `git remote set-head origin -a` if it errors on `origin/HEAD...`.
- **Otherwise, sensitive surface touched** → apply the `needs-security-review` label **only when
  dev-complete** (the workflow triggers on `[labeled]`, not on push — labeling early leaves later
  commits unreviewed). Skip for docs/no-surface changes.

**Mechanical rules (both learned in the #500 pilot):** `.claude/`-only → local review, not the
label (workflow excludes `.claude/`; local skill needs `origin/HEAD` set); squash `--subject`
scope set explicitly (`chore:` for `.claude/**`), not inherited from the PR title.

---

## 5. Project examples referenced by the engine's guidance

The engine states its principles generically; these are the AgentFluent instances it draws on,
kept here so the engine stays project-agnostic:

- **Post-hoc token/cost analyzer** (engine journal step, `tokens=deferred`): AgentFluent itself,
  run over the loop's own JSONL corpus.
- **Externally-cited-source fidelity check** (engine Plan step): a research-scout candidate feed
  (`anthropic-feature-watch`) is one such source. The #437 / decision **D046** episode is the
  worked example — a feature extrapolated past what its source established.
- **Cheap-falsifier-caught-late** (engine Plan step): the #437 corpus pass caught a misdirected
  feature, but only post-hoc.
- **Notes-as-decision, not-evidence** (engine Triage step + ledger discipline): the v0.10.0
  row-12 resume-instability failure is the worked example of mutable live evidence in Notes
  destabilizing resume.
- **First route graduation** (engine merge gate, `graduated-routes`): decision **D047** graduated
  `docs`+`research`.
