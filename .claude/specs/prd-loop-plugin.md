# PRD — Extract release-loop into a reusable Claude Code plugin

**Status:** Draft (2026-07-10) · **Owner:** Fred + Claude Code (PM) · **Epic:** `epic:loop-plugin`
**Distribution mechanism:** Claude Code plugin (decided — see Decisions below)

> Package the *generic* supervised dev-loop engine now living in AgentFluent
> (`.claude/skills/release-loop/` + `.claude/specs/prd-loop-engineering.md` +
> `.claude/hooks/guard_append_only.py`) as an installable **Claude Code plugin**, so other
> projects can consume the battle-tested engine and customize a small per-project config —
> instead of copy-pasting the whole harness (which drifts).

---

## 1. Problem & motivation

The release-loop harness is battle-tested in AgentFluent (multiple release runs across
v0.10.x/v0.11.0; see the loop-engineering spec §11 calibration learnings and the #500/#517/#559
trail). It is *already ~90% generic*: `SKILL.md` references every project-specific value **by
parameter name** (`LEDGER_ROOT`, `BACKLOG_SOURCE`, `PRIORITY_LABELS`, `SCOPE_AGENT`,
`DESIGN_AGENT`, `CODE_REVIEW`, `LINT_CMD`/`TYPE_CMD`/`TEST_CMD`, `BRANCH_FMT`, `COMMIT_CONV`,
`MERGE_METHOD`, …), and `prd-loop-engineering.md` §4.0 already isolates those values in one
Project Parameters table with a §4.4 porting checklist.

The blocker to reuse is **distribution**: the only way to run the loop in another repo today is
to copy the skill + the 869-line spec + the hook and hand-edit them. That copy immediately
drifts from the AgentFluent original, and there is no shared upgrade path. The generic engine
and the project bindings are also **interleaved** inside a single spec document, so "the engine"
is not yet a movable artifact.

**Decision already made by Fred:** distribute as a **Claude Code plugin**, not a seed-repo or a
copier/cookiecutter template. A plugin gives a single installable engine + a documented
per-project config seam, with a real upgrade path (`/plugin` reinstall) rather than a fork.

## 2. Goals & non-goals

**Goals**
- A generic, installable `release-loop` plugin: bundles the orchestrator skill, the generic
  engine doc, and the append-only guard hook.
- A crisp **engine ↔ config seam**: generic control-flow/semantics in the plugin; a small
  (~40-line) per-project `loop.config.md` in the target repo.
- Self-bootstrapping onboarding (`/init-loop`) so a new project can adopt the loop without
  hand-copying anything.
- Prove parity by **dogfooding**: AgentFluent itself switches from its in-repo skill to
  consuming the installed plugin, and runs a real iteration with no behavior regression.

**Non-goals (explicitly out of scope)**
- The AgentFluent-specific **research pipeline** (`anthropic-research`, `candidate-verifier`,
  `promote-candidates`) — must be EXCLUDED from the plugin (project-specific, not part of the
  generic engine).
- The Agent SDK / headless `claude -p` graduation (loop-engineering spec §13) — the plugin is
  the *Claude-Code-native* packaging step that must come first; the SDK port is downstream.
- Changing loop *semantics* (gates, convergence, resume, park/hold, budget caps). This is a
  packaging + refactor effort, not a behavior change. Any semantic change is a separate issue.
- The pm + architect subagents: already user-global and shared across projects — they are
  referenced by the config (`SCOPE_AGENT`/`DESIGN_AGENT`), not bundled into the plugin.

## 3. Confirmed Claude Code plugin packaging facts (bindings for acceptance criteria)

- **Manifest:** `.claude-plugin/plugin.json`. That directory holds ONLY the manifest;
  `skills/`, `hooks/`, `commands/` live at the **plugin root**, not under `.claude-plugin/`.
- **Skills:** `skills/<name>/SKILL.md` inside the plugin (same shape as a project skill).
- **Hooks** ship in the plugin and are referenced via `${CLAUDE_PLUGIN_ROOT}/hooks/…`.
- A plugin skill reads its own **bundled** sibling files via `${CLAUDE_PLUGIN_ROOT}/…`, and
  reads the **per-project** config in the target repo via
  `${CLAUDE_PROJECT_DIR}/.claude/loop.config.md`.
- **Distribution:** a `.claude-plugin/marketplace.json` in a marketplace repo; users run
  `/plugin marketplace add <owner/repo>` then `/plugin install <plugin>@<marketplace>`. Can be
  installed user-global or auto-loaded per-project via `.claude/settings.json` `enabledPlugins`.

## 4. The engine ↔ config seam (the load-bearing refactor)

`prd-loop-engineering.md` currently interleaves generic engine logic with project-specific
content. The split:

**Generic → `loop-engine.md` (ships in the plugin):** control flow (select → triage → plan →
architect → human gate → implement → AC-verify → commit/PR → review → security → merge →
journal); gate/convergence/resume semantics; `RUN COMPLETE`/`RUN PARKED`/`RUN RESUMED`
sentinels; park/hold state machine; budget-cap machinery; the ledger format (`queue.md`,
`progress.md`, `issue-<N>.plan.md`); the router *procedure shape* (§7.3).

**Per-project → `loop.config.md` (lives in the target repo, ~40 lines):**
- The **§4.0 Project Parameters table** (the binding seam) — all named parameters.
- **§7.2 architect triggers** (`ARCHITECT_TRIGGERS`) — this project's "needs design review"
  conditions.
- **§7.3 router signals** (`SOURCE_LAYOUT`) — src layout, dep-leakage rule, stub/defer markers.
- **§7.1 step-10 security routing** — the `.claude/`-only-vs-label + `git remote set-head`
  GitHub-ism (GitHub/host-repo specific).

`SKILL.md` is updated to read **config for bindings** and **engine for logic**. Per the
loop-engineering §4.4 porting checklist, a non-Python / non-`src/` / non-GitHub project revises
all four config sections — never the engine.

**Sequencing constraint:** the split is done **in-place in AgentFluent first** to prove the seam
before anything is ported into a plugin. **A live v0.11.0 loop run is in progress — the refactor
must not break the running skill mid-run** (SKILL.md, the engine doc it reads at runtime, and the
CI drift-guard `tests/unit/test_loop_skill_drift.py` must stay coherent through the change).

## 5. Work breakdown (stories)

| # | Story | Repo | Depends on |
|---|-------|------|-----------|
| S1 | Split the spec into generic engine + per-project config (in-place in AgentFluent) — **critical path** | agentfluent | — |
| S2 | Create the plugin + marketplace repo scaffold (`plugin.json` + `marketplace.json` + dir layout) | new plugin repo | — (soft: S1 seam) |
| S3 | Port the generic artifacts into the plugin (SKILL.md, `loop-engine.md`, `guard_append_only.py`); rewrite paths to `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PROJECT_DIR}` | new plugin repo | S1, S2 |
| S4 | `/init-loop` scaffolder command (reads target CLAUDE.md/pyproject, generates `loop.config.md`, adds ledger `.gitignore` + settings snippet, creates ledger dir) | new plugin repo | S3 |
| S5 | Dogfood: convert AgentFluent to consume the installed plugin + local `loop.config.md`; run a real iteration for parity | agentfluent | S3, S4 |
| S6 | Docs / porting guide (plugin README: install, init, config reference, what to exclude, §4.4 porting notes) | new plugin repo | S3 (finalize after S5) |

**Priority order:** S1 (critical path, do first) → S2 → S3 → S4 → S5 → S6. S2 can start in
parallel with S1, but S3 needs both.

## 6. Risks & constraints

- **Critical path is S1.** The engine/config seam is a shared-interface decision — it warrants
  an **architect gate at implementation time** (loop-engineering spec §7.2: cross-module
  interface / shared data model). Get the seam right before porting; a wrong cut multiplies
  across every downstream story.
- **Do not break the live v0.11.0 run** (S1 constraint above).
- **Cross-repo tracking.** S2/S3/S4/S6 land in a *new* plugin/marketplace repo that does not yet
  exist; these tracking issues live in the agentfluent backlog. Link the plugin-repo PRs back.
- **CI drift-guard.** AgentFluent CI keeps SKILL.md byte-identical to the spec mirror
  (`test_loop_skill_drift.py`). S1 changes both; S5 (removing the in-repo skill) must update or
  retire that guard so CI stays green.
- **No release milestone.** This is maintainer tooling that ships **no PyPI artifact**. Per the
  #559 precedent (loop-harness tooling ejected from release milestones), none of these issues get
  a version milestone. In-AgentFluent commits (S1, S5) land as `chore:`/`docs:` scope.

## 7. Acceptance criteria (epic-level)

- [ ] `prd-loop-engineering.md` is split into a generic `loop-engine.md` + a per-project
      `loop.config.md`; SKILL.md reads config for bindings and engine for logic; the live
      v0.11.0 run is not broken; the CI drift-guard stays coherent. (S1)
- [ ] A plugin repo exists with a valid `.claude-plugin/plugin.json`, a
      `.claude-plugin/marketplace.json`, and the root `skills/`, `hooks/` layout. (S2)
- [ ] The generic SKILL.md, `loop-engine.md`, and `guard_append_only.py` live in the plugin with
      all paths rewritten to `${CLAUDE_PLUGIN_ROOT}` (bundled reads) and `${CLAUDE_PROJECT_DIR}`
      (per-project config + ledger); the research pipeline is NOT included. (S3)
- [ ] `/init-loop` scaffolds a target repo: generates `loop.config.md`, adds the ledger
      `.gitignore` entry + `enabledPlugins` settings snippet, and creates the ledger dir. (S4)
- [ ] AgentFluent consumes the installed plugin (in-repo skill removed / replaced by a local
      `loop.config.md`) and completes one real iteration with no behavior regression. (S5)
- [ ] A plugin README documents install, `/init-loop`, the config reference, what to exclude
      (research pipeline), and the §4.4 porting notes for non-Python/non-GitHub projects. (S6)

## 8. References

- Source artifacts: `.claude/skills/release-loop/SKILL.md`,
  `.claude/specs/prd-loop-engineering.md` (§4.0 parameters, §4.4 porting, §13 SDK roadmap),
  `.claude/hooks/guard_append_only.py`.
- Loop trail: #500 (C1 append-only guard, done), #517 (Agent SDK discovery epic, done — the SDK
  graduation the plugin precedes), #559 (loop tooling ejected from release milestones —
  no-milestone precedent).
- Decision: D051 (plugin distribution mechanism) in `.claude/specs/decisions.md`.
