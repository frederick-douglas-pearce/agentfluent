# Loop Engineering Harness — Implementation Spec

**Status:** Draft (2026-06-29) · **Owner:** Fred + Claude Code · **Pilot:** v0.10.0

> A generalized, semi-autonomous "loop engineering" harness that runs a project's dev
> workflow (plan → architect → implement → review → merge) as a loop over a backlog, with
> human gates only where the orchestrator is genuinely unsure.
>
> **This document is self-contained and build-ready.** A fresh agent with no prior
> conversation context can build every component from §5–§9 alone. AgentFluent-specific
> values are isolated in the **Project Parameters** table (§4.0); porting to another project
> means editing §4.0 **plus** the project-specific content flagged in the §4.4 Porting
> checklist. Read §4.0 and §4.4 first.
>
> **Runtime dependency:** the `/release-loop` skill reads this spec live (it points the
> orchestrator at §4.0), so the spec must ship alongside the skill when porting, with paths
> updated. **Built-in tooling:** `/code-review`, `/security-review`, `/verify`, `/simplify`,
> `/review` are **Claude Code built-in skills** (not files to build); `/code-review` and
> `/security-review` were verified working in the 2026-06-29 session.

---

## 1. Goals & non-goals

**Goals**
- Reusable harness (point it at any milestone/label/backlog), not a one-off script.
- Preserve the workflow that already works; add only the missing scaffolding.
- One issue per loop iteration, fresh context per iteration, durable state on disk.
- Human gate for any decision the orchestrator is unsure about; route scope→pm,
  design→architect before escalating to the human.
- Survive `/clear` and context compaction (all state externalized to files).
- Double as an AgentFluent dogfood corpus + product wedge (§12).

**Non-goals (deferred)**
- Fully unattended/headless `claude -p` loops (§13).
- Parallel multi-issue implementation via worktrees (blocked by the validate-only
  constraint, §4.2).
- Changing the repo's permission/sandbox posture.

---

## 2. Research-grounded principles (the "why" behind the gates)

From 2026 loop-engineering practice (Huntley's ralph; Anthropic's
`effective-harnesses-for-long-running-agents`, `effective-context-engineering-for-ai-agents`,
`multi-agent-research-system`, `demystifying-evals-for-ai-agents`; HumanLayer's
12-factor-agents; Willison's `designing-agentic-loops`). **Established** practices adopted:

- **One issue per iteration**, sized to one context window.
- **Externalize state to files; fresh context per iteration** (Anthropic's
  `claude-progress.txt` pattern).
- **A machine-checkable verification signal every iteration** — verify *environment state*,
  not the agent's *claim* of done.
- **Independent fresh-context review beats self-critique** (validated in §11).
- **HITL as an async "ask"** raised on uncertainty, not a blocking babysit.
- **Cap iterations/budget; detect "stuck" (repeat action after same error) → escalate.**

**Contested, NOT adopted yet:** pure bash-loop autonomy, unattended runs, async fan-out.
Both camps agree the deciding factor is *verification gates + explicit state* — the spine of
this design.

---

## 3. Architecture at a glance

```
driver (/loop or manual re-invoke, fresh context each time)
   └─ /release-loop skill  ── reads ──>  .claude/loop/<run>/ (ledger: queue.md, progress.md, issue-<N>.plan.md)
        └─ ONE routed iteration per invocation:
             select unblocked issue → triage/route → plan → [architect] → [human gate]
               → implement → AC-verify → commit+PR → /code-review → [security] → merge → journal → stop
        └─ subagents (advisory, parent-driven): pm (scope), architect (design)
        └─ skills: /code-review, /security-review, /verify
        └─ guard: .claude/hooks/guard_append_only.py (protects append-only logs)
```

One iteration = one issue. The driver re-invokes with fresh context; the skill rebuilds
state from the ledger each time. This is the ralph "fresh context + filesystem-as-memory"
pattern adapted to an issue-driven, gated workflow.

---

## 4. Constraints & project parameters

### 4.0 Project Parameters (change ONLY this table to port to another project)

| Parameter | AgentFluent value | Notes |
|-----------|-------------------|-------|
| `BACKLOG_SOURCE` | GitHub milestone (e.g. `v0.10.0`) via `gh` | could be a label, or a local `TODO.md` |
| `SCOPE_AGENT` | `pm` (user-global subagent) | answers scope/priority/requirements questions; remove if project has none |
| `DESIGN_AGENT` | `architect` (user-global subagent) | reviews plans pre-implementation; remove if none |
| `CODE_REVIEW` | `/code-review` (Claude Code **built-in skill**) | independent post-impl review; verified 2026-06-29. The repo's `/review`/`/simplify` (CLAUDE.md) are alternatives, not this. |
| `SECURITY_REVIEW` | local `/security-review` (built-in skill) for `.claude/`-only; else `needs-security-review` label → `security-review.yml` | see §8 |
| `VERIFY` | `/verify` (built-in skill) | runtime behavior check when an AC needs proof-by-running |
| `PRIORITY_LABELS` | `priority:high > priority:medium > priority:low`; tiebreak: issue number ascending | drives selection (§7.1 step 1, §7.5 step 4) |
| `ARCHITECT_TRIGGERS` | see §7.2 (shared models, cross-module interfaces, new diagnostics rule/pipeline) | **project-specific — edit when porting** |
| `SOURCE_LAYOUT` | package `src/agentfluent/`; tests `tests/`; research outside `src/` | router uses this (§7.3); **edit when porting** |
| `TEST_CMD` | `uv run pytest -m "not integration"` | |
| `LINT_CMD` | `uv run ruff check src/ tests/` | |
| `TYPE_CMD` | `uv run mypy src/agentfluent/` | |
| `CI_STATUS_CMD` | `gh pr checks <PR>` | |
| `BRANCH_FMT` | `feature/<n>-slug` / `fix/<n>-slug` | from CLAUDE.md |
| `COMMIT_CONV` | Conventional Commits; `.claude/**`→`chore:`/`docs:` | §4.3 |
| `PR_TEMPLATE` | `.github/PULL_REQUEST_TEMPLATE.md` (must replicate) | |
| `MERGE_METHOD` | squash, `--delete-branch`, explicit `--subject` scope | |
| `APPEND_ONLY_FILES` | `.claude/specs/decisions.md` | guarded by the hook |
| `PERMISSION_POSTURE` | background agents validate-only → parent implements | §4.2 |
| `LEDGER_ROOT` | `.claude/loop/` | **gitignored** — local working state, never committed (§6.4) |

### 4.1 Workflow conventions (AgentFluent — from CLAUDE.md)
- Branch from `main`; PR with passing CI before merge. Branch naming `BRANCH_FMT`.
- PR body **must replicate** `PR_TEMPLATE` (CI's `PR Template Check` rejects otherwise).
- Tests required for code changes; no regressions; mypy strict on `src/`.

### 4.2 Hard constraints that shape the design (mark which are general vs. project)
1. **(Project) Background/non-interactive agents are validate-only here** — `settings.local.json`
   withholds Edit/Write/git/gh from agents that can't prompt. **The parent (interactive)
   thread does all implementation + git/gh.** No fan-out of implementation. *(In a project
   without this restriction, iterations could parallelize via worktrees — see §13.)*
2. **(General, Claude Code) Subagents can't invoke subagents** — the orchestrator drives
   `SCOPE_AGENT`/`DESIGN_AGENT` directly.
3. **(Project) CI gated on `branches:[main]`; stacked PRs break it** → one PR at a time,
   each branched from `main`.
4. **(Project) `SCOPE_AGENT`/`DESIGN_AGENT` are user-global**, not repo-tracked; editing them
   yields no PR and needs a session restart.

### 4.3 Commit scope rule (AgentFluent)
`.claude/**` changes are maintainer-only tooling → `chore:`/`docs:`, never `feat:`/`fix:`
(avoids release-please mis-bumps). The orchestrator sets the **squash subject scope
explicitly**, not inheriting the PR title.

### 4.4 Porting checklist (what to edit beyond §4.0)
Editing §4.0 is necessary but not sufficient — these sections carry project-specific content
copied into the operating procedure:
1. **§4.0 table** — all parameters (agents, commands, conventions, layout, priority labels).
2. **§7.2 architect triggers** (`ARCHITECT_TRIGGERS`) — your project's "needs design review"
   conditions; the AgentFluent list names `SessionMessage`/`AgentInvocation`/diagnostics.
3. **§7.3 router signals** (`SOURCE_LAYOUT`) — `src/` layout, package-dep-leakage rule, and
   any project-specific stub/defer markers (AgentFluent cites `#469`/`D041` as examples).
4. **§7.1 skill step 10** — the `.claude/`-only-vs-label security routing and the
   `git remote set-head` GitHub-ism are GitHub/this-repo specific.
5. **Ship this spec with the skill** — the skill reads it at runtime; update the path.
A non-Python / non-`src/` / non-GitHub project must revise all five, not just §4.0.

---

## 5. Components to build

| # | Component | Path | Status |
|---|-----------|------|--------|
| C1 | Append-only guard hook | `.claude/hooks/guard_append_only.py` | **Done** (#500/PR #550) |
| C2 | State ledger convention | `.claude/loop/<run>/` | Build (§6) |
| C3 | `/release-loop` orchestrator skill | `.claude/skills/release-loop/SKILL.md` | Build (§7) |
| C4 | Router (issue→route) | folded into C3 | Build (§7.3) |
| C5 | AC-verifier | composed `/code-review`+`/verify`+checklist prompt | Build (§7.4) |

**Deliberately not building:** a stuck-detection hook (ledger + auto-mode backstop suffice),
a separate triage agent (rules suffice), a shared hook lib (standalone stdlib scripts are
intentional).

### 5.1 C1 — Append-only guard hook (done; how to extend)
Shipped in PR #550. A `PreToolUse` hook denying a `Write` to a registered append-only file
when it would drop any existing entry-ID. To protect another file: add `{path-suffix:
compiled-id-regex}` to `APPEND_ONLY_FILES` in the hook, with an **anchored multiline** ID
pattern (`^##\s+(...)`), and add a drift-guard test asserting the pattern matches the real
file's headings exactly once each. Scope is `Write`-only (documented residual: `Edit`,
`Bash` redirection). Fail-closed on unparseable event / non-ENOENT read error; allow on
ENOENT (new file). Wired in `.claude/settings.json` under `PreToolUse` with a `Write` matcher.

---

## 6. C2 — State ledger

Create `LEDGER_ROOT/<run-slug>/` (e.g. `.claude/loop/v0.10.0/`) containing three artifacts.
The orchestrator is the only writer except where noted.

### 6.1 `queue.md` — work list (authoritative status)
Dependency-ordered. One row per issue. Statuses: `queued → routed → planning →
plan-approved → implementing → in-pr → in-review → done | blocked | deferred`.

```markdown
# Loop run: v0.10.0
_Last updated: <ISO8601 by orchestrator>_

| # | Issue | Route | Status | Depends on | PR | Notes |
|---|-------|-------|--------|-----------|----|----|
| 1 | #500 pm clobbers decisions.md | code | done | — | #550 | precondition |
| 2 | #518 SDK hello-world probe | research | queued | — | — | first in epic #517 |
| 3 | #522 representative SDK agent | research | blocked | #518 | — | needs S1a findings |
| 4 | #510 PARAMETER_RETRY fixes | code | queued | — | — | high |
| 5 | #469 per-turn ratios | stub-defer | deferred | dogfood | — | D041 — do not implement |
```

### 6.2 `progress.md` — append-only journal (survives /clear + compaction)
The orchestrator APPENDS one block per iteration (and per gate decision). Never rewritten.
This is the audit trail and the resume anchor.

```markdown
## <ISO8601> — #518 (research) — iteration start
- Selected: #518 (highest-priority unblocked).
- Route: research (probe; no test-coverage gate).
- Plan: issue-518.plan.md written.
- Architect: skipped (research scaffolding, no shared-interface impact).
- Human gate: plan auto-approved (route=research, low ambiguity).
- Implemented: research/agent-sdk-probe/probe.py; recorded findings in <path>.
- AC-verify: 3/3 acceptance criteria met (location, discriminator, options metadata).
- PR: #NNN (chore scope). CI: green.
- Code-review: 0 findings. Security: n/a (no deps added).
- Merged: squash #NNN. Issue #518 closed.
- Next: #522 now unblocked.
```

### 6.3 `issue-<N>.plan.md` — per-issue plan (architect-reviewed, human-approved)
```markdown
# Plan: #<N> — <title>
**Route:** <code|research|docs>  **Branch:** <BRANCH_FMT>

## Acceptance criteria (verbatim from issue)
- [ ] ...

## Approach
<steps, files to touch, tests to add>

## Architect triggers hit
<which §7.2 triggers fired, or "none">

## Risks / open questions for human
<empty if none>
```

### 6.4 Lifecycle & commit policy
- **Init:** orchestrator creates the dir + `queue.md` from `BACKLOG_SOURCE` (§7.5).
- **Per iteration:** update one `queue.md` row through its statuses; append `progress.md`;
  write/update `issue-<N>.plan.md`.
- **Commit policy — gitignore the ledger** (`LEDGER_ROOT/` is added to `.gitignore`). It is
  local working state: it survives `/clear`/compaction on disk, but is **never committed**.
  This is deliberate — committing it has no legal landing spot under this repo's rules
  (`main` is PR-only, no stacked PRs, and folding ledger commits into an issue's squash-merged
  PR would pollute that PR's scope). Resolves the branch-protection collision the design
  review flagged.
- **Dogfood corpus (§12) harvest:** read the JSONL + `progress.md` from the working tree
  directly; if a versioned audit trail is later wanted, snapshot the ledger into a dedicated
  `docs(loop):` PR on demand — do not stream per-iteration ledger commits.
- **No git/ledger divergence:** because the ledger is uncommitted, resume (§7.6) reconciles
  the on-disk ledger against *live* git/PR state (branch exists? PR open? CI status?), which
  is the source of truth — not a possibly-stale commit.

---

## 7. C3 — `/release-loop` orchestrator skill

Create `.claude/skills/release-loop/SKILL.md`. Below is the **complete, ready-to-drop-in
content** (the body IS the orchestrator's operating procedure). A builder may copy it
verbatim, adjusting only `Project Parameters` references.

````markdown
---
name: release-loop
description: Run one routed iteration of the supervised dev loop over a backlog (milestone/label). Selects the next unblocked issue, routes it, drives plan→architect→implement→review→merge with human gates on uncertainty, and journals to the ledger. Invoke once per issue; re-invoke (or drive via /loop) for the next. Use when the user wants to work a backlog as a loop, "run the release loop", or "do the next issue".
---

# Release Loop — orchestrator (ONE issue per invocation)

You are the orchestrator of a supervised dev loop. Each invocation handles exactly ONE
issue end-to-end, journals, and stops. State lives in the ledger, not your context — so a
fresh invocation resumes correctly. Read the project parameters in
`.claude/specs/prd-loop-engineering.md` §4.0.

## 0. Load or initialize state
1. Identify the active run (most recent `LEDGER_ROOT/<run>/`), or ask the user which
   milestone/label to run if none exists, then INITIALIZE per §7.5 of the spec.
2. Read `queue.md` and the tail of `progress.md`.

## 1. Select
Among issues whose status is `queued`/`routed` and whose `Depends on` issues are all `done`,
pick by `PRIORITY_LABELS` order, tiebreak issue-number ascending. If none are unblocked:
report status and STOP (convergence, or all remaining blocked/deferred — say which).
**Size guard:** before entering the pipeline, estimate scope from the issue body — if it
plausibly touches many files or spans multiple unrelated acceptance-criteria clusters (won't
fit one context window), mark it `blocked: too-large`, escalate to SCOPE_AGENT to split, and
go back to select. Aggressively offload reading/analysis to subagents (architect, AC-verifier)
within an iteration to conserve the parent's context.

## 2. Triage / route (if not already routed)
Classify into `code | research | docs | stub-defer | blocked` using §7.3. Update the row.
If `stub-defer` or `blocked`, journal why and go back to §1 (do not implement).

## 3. Plan
Fetch the issue (`gh issue view <N>`). Write `issue-<N>.plan.md` (template in spec §6.3),
copying acceptance criteria verbatim. Lighter for research/docs.

## 4. Architect gate (conditional)
If any §7.2 trigger fires OR you are unsure about the design, invoke the DESIGN_AGENT with
the plan; address `blocking`/`important` concerns before coding. Skip for docs and trivial
research.

## 5. Human gate (conditional — supervised mode)
Present the plan and STOP for approval when: acceptance criteria are ambiguous; the change is
risky/irreversible; SCOPE/ DESIGN agents disagree or punt; or you are otherwise unsure.
Otherwise proceed (note "auto-approved" + why in the journal). Route scope questions to
SCOPE_AGENT and design questions to DESIGN_AGENT BEFORE escalating to the human.

## 6. Implement (you, the parent thread)
Create the branch (`BRANCH_FMT`). Implement code + tests + docs per the plan. TDD where it
fits (write failing tests, commit, do not modify tests later). Run `LINT_CMD`, `TYPE_CMD`,
`TEST_CMD` until green. Do NOT stage unrelated pre-existing working-tree changes.

## 7. Verify done (independent, fresh context)
Run the AC-verifier (spec §7.4): a fresh check that the diff satisfies EVERY acceptance
criterion — verify state, not your claim. If gaps, fix and re-verify (max 2 rounds, else
escalate).

## 8. Commit + PR
Commit with correct `COMMIT_CONV` scope. Open the PR; **replicate `PR_TEMPLATE` fully** in
the body; make the Security-review choice up front. Wait for CI; fix until green.

## 9. Code review
Run CODE_REVIEW on the diff. Implement viable findings; decline others with a one-line
rationale; **verify recs were applied**. Bounded to 2 rounds — contested findings escalate to
the human, do not loop. Commit fixes.

## 10. Security review (by route)
- `.claude/`-only change → run local `/security-review` (the labeled workflow excludes
  `.claude/`; the local skill needs `git remote set-head origin -a` if it errors on
  `origin/HEAD...`).
- Otherwise, if a sensitive surface is touched → apply `needs-security-review` ONLY now
  (dev-complete). Skip for docs/no-surface changes.
Address findings ≥ the project's confidence bar.

## 11. Merge
When CI + security are green AND the human has not held the merge: squash-merge with an
explicit `--subject` carrying the correct `COMMIT_CONV` scope, `--delete-branch`. Confirm the
issue closed. In early calibration, STOP and ask the human before merging.

## 12. Journal + stop
Append the iteration block to `progress.md`; set the `queue.md` row to `done` (or
`blocked`/`deferred` with reason); note newly-unblocked issues. The ledger is gitignored —
do NOT commit it (spec §6.4). STOP. (Driver re-invokes with fresh context for the next issue.)

## Escalation rubric (when unsure)
Scope/priority/requirements → SCOPE_AGENT. Design/implementation → DESIGN_AGENT. Escalate to
the HUMAN only when those disagree/punt, ACs are unresolvable, an action is
destructive/irreversible, a review finding is contested, or the same step failed twice.

## Guardrails
One PR at a time (no stacked PRs). **Stuck = the same error SIGNATURE recurs** (identical
CI failure, or the same tool+args failing again) — NOT merely re-entering a status (a
legitimate `/clear`-resume re-enters `implementing` and must not be flagged). On a genuine
repeat: stop, escalate, mark `blocked`, move on. Respect any iteration/budget cap (stored in
the ledger; enforced by the driver — inert in manual re-invoke mode).
````

### 7.2 Architect-gate triggers (from CLAUDE.md)
Fire the DESIGN_AGENT when the plan: touches shared models (`SessionMessage`,
`AgentInvocation`); changes a cross-module interface; adds a new diagnostics rule,
correlation logic, or analytics pipeline; **or the orchestrator is unsure.** Bias toward
calling it (read-only, cheap vs. a bad implementation). Skip for docs and trivial research.

### 7.3 Router — classification procedure
Decide route from labels + body signals, in order:
1. Issue body says explicitly "NOT implementation-ready" / is a stub (e.g. #469, D041) →
   `stub-defer`.
2. Any `Depends on` issue not `done` → `blocked`.
3. Label `documentation` and change is confined to `docs/`/markdown → `docs`.
4. Label `research` or epic `*-discovery`, throwaway scaffolding, "artifact under study is
   data" → `research` (no test-coverage gate; placement outside `src/`; no runtime-dep
   leakage into the package).
5. Otherwise (`bug`/`enhancement` touching `src/`) → `code` (full pipeline).

### 7.4 C5 — AC-verifier
Default: **compose existing tools**, don't mint an agent.
1. After implementation, spawn a fresh subagent with ONLY: the issue's acceptance criteria
   (verbatim) + `git diff main...HEAD`. Prompt: *"For each acceptance criterion, state
   met/not-met with the file:line or test that satisfies it. Verify the diff actually does
   this; do not assume. Return a checklist + overall done/not-done."*
2. For behavior that needs runtime proof, also run `/verify` (runs the app).
3. `/code-review` (§9) provides the adversarial bug pass.
Promote to a dedicated `ac-verifier` agent only if the composed approach proves too loose.

### 7.5 Initialization procedure (new run)
1. Derive `<run-slug>` from `BACKLOG_SOURCE`: milestone → the milestone name (`v0.10.0`);
   label → the label (slugified); `TODO.md` → its basename. `mkdir -p LEDGER_ROOT/<run-slug>`.
2. Enumerate `BACKLOG_SOURCE` (e.g. `gh issue list --milestone <run> --state open --json
   number,title,labels`).
3. For each issue: determine route (§7.3) and dependencies (parse "Depends on"/"blocked by"
   refs in the body; respect epic ordering notes).
4. Topologically order by dependency, then by `PRIORITY_LABELS` (tiebreak issue-number asc).
   Write `queue.md` (§6.1).
5. Append an "init" block to `progress.md`. (Ledger is gitignored — not committed.)

### 7.6 Resume after `/clear` or compaction
No special action: the next `/release-loop` invocation reads `queue.md` + tail of
`progress.md` and continues. The on-disk ledger is reconciled against **live git/PR state**
as the source of truth (the ledger is uncommitted, §6.4): for an in-flight row, check whether
its branch exists, whether a PR is open, and the PR's CI status, and resume at the matching
pipeline stage. **Working-tree reconciliation:** if a crashed prior attempt left uncommitted
changes, inspect them before proceeding — keep and continue if they match the plan, or
`git restore`/stash if they're partial/unrelated. A resumed `implementing` row is NOT "stuck"
(stuck keys on a repeated error signature, not status re-entry — see Guardrails).

---

## 8. Routing rules & mechanical rules

| Route | Pipeline differences |
|-------|----------------------|
| `code` | full pipeline, all gates |
| `research` | lighter plan; **no test-coverage gate**; architect optional; security only if deps added; place outside `src/` |
| `docs` | skip architect + security; light review; `docs:` scope |
| `stub-defer` | do NOT implement; journal why; leave in backlog |
| `blocked` | skip; revisit when dependency closes |

**Mechanical rules (both learned in the #500 run — §11):**
- `.claude/`-only change → local `/security-review`, not the label (workflow excludes
  `.claude/`). Local skill needs `origin/HEAD` set.
- Squash subject scope set explicitly (`chore:` for `.claude/**`), not inherited from PR title.

---

## 9. Gates, escalation, convergence, guardrails

(See §7.1 skill body for the operative procedure.) Gate table:

| Gate | Who | When | Output |
|------|-----|------|--------|
| Plan | orchestrator | every issue | `issue-<N>.plan.md` |
| Architect | DESIGN_AGENT | §7.2 triggers or unsure | issue comment |
| Human (plan) | user | only if uncertain/irreversible | approve/redirect |
| AC-verify | fresh subagent (+`/verify`) | every code/research issue | done/not-done + gaps |
| Code review | CODE_REVIEW | every code issue | findings → fixes |
| Security | local `/security-review` or label | by route | clean/findings |
| Merge | user (calibration) → orchestrator (later) | CI+security green | squash |

**Convergence:** the run is complete when every `queue.md` row is terminal (`done` |
`deferred` | `blocked`). The **completion sentinel** is a final `progress.md` block titled
`RUN COMPLETE — <run-slug>` summarizing counts (done/deferred/blocked) and listing any
blocked items + reasons; on reaching it, the orchestrator stops and reports. **Guardrails:**
iteration/budget caps live in the ledger and are enforced by the driver (inert in manual
re-invoke mode); one PR at a time; stuck-detection (repeated error signature) → escalate.
The #500 guard hook protects append-only logs once the loop commits its own work.

---

## 10. Build checklist (ordered — a fresh agent follows this)

1. **C1 guard hook** — done (#500). Verify present + wired; extend `APPEND_ONLY_FILES` if the
   project needs more protected logs.
2. **C2 ledger** — add `LEDGER_ROOT/` (`.claude/loop/`) to `.gitignore`; create the dir and
   write the three templates (§6) as the run's seed (uncommitted, §6.4).
3. **C3 skill** — create `.claude/skills/release-loop/SKILL.md` from §7.1 verbatim; adjust
   `Project Parameters` references for the host project.
4. **C4 router** — embodied in the skill (§7.3); no separate file.
5. **C5 AC-verifier** — embodied in the skill (§7.4); compose existing tools.
6. **Smoke test** — run `/release-loop` against a single easy issue in supervised mode; walk
   every gate; confirm the ledger updates and the PR ships.
7. **Calibrate** — run 2–3 issues with the human at every plan gate; tune router + escalation
   thresholds; then loosen to escalation-only.
8. **Commit** the harness — skill + the `.gitignore` entry (NOT the ledger, which is
   gitignored) — under `chore(loop):` via the normal PR flow.

Each component's acceptance: C1 wired + drift-guard test green; C2 three files exist and
parse; C3 skill is discoverable (`/release-loop`) and runs one full iteration end-to-end on a
real issue; C5 produces a per-criterion checklist.

---

## 11. Calibration learnings (#500 pilot, PR #550 — merged)

1. **Independent post-implementation review is load-bearing.** Fresh-context `/code-review`
   caught a real false-allow (the `D\d+` ID regex collapsed the live `## D038-A:` entry onto
   `## D038:`, silently allowing a clobber — *the exact data loss #500 guards*). **Both the
   architect and the parent missed it.** Keep the gate; it paid off on a 165-line hook.
2. **Both review stages add non-overlapping value** — architect caught design issues pre-code
   (fail-direction split, anchored regex); fresh finders caught the implementation bug.
3. **Triage matters for cost** — one issue spent ~6 subagent runs. Non-code issues must skip
   gates they don't need.
4. The two §8 mechanical rules both bit us and are now encoded.

---

## 12. Dogfooding & product wedge

The loop's JSONL is a first-class AgentFluent corpus. Roadmap candidates (file separately,
not in scope here): per-iteration cost/convergence trend; stall/spin detection;
"claimed-done-but-AC-unmet" (hallucinated completion); context-rot indicators; review-thrash
detection. Tagline fit: *"…tells you why your loop stalled and what to change."*

---

## 13. Roadmap: Agent SDK graduation

After the v0.10 proof-of-concept, graduate part of the loop to the **Claude Agent SDK**
(Python): exercises the SDK (a standing need) and generates SDK-shaped data, dovetailing with
the **#517 agent-sdk-discovery epic**. The deterministic control flow (queue, routing, gates)
maps onto an SDK `query()` loop with `AgentDefinition` subagents and `PreToolUse`/`Stop`
hooks; this also unlocks parallel iterations (worktrees) once not bound by the validate-only
constraint. Defer until the supervised parent-loop is proven. **Note for the SDK port:** the
interactive built-in skills (`/code-review`, `/security-review`, `/verify`) and PR-label
workflows don't carry into a headless `claude -p` loop — the SDK version must invoke
equivalent review/verify steps as in-process subagent calls or programmatic GHA triggers.

---

## 14. Open questions
- AC-verifier: composed vs. dedicated `ac-verifier` agent? Start composed; promote if loose.
- Per-issue plan: ledger only, or also posted to the issue (like architect reviews) for
  persistence/visibility?
- Graduation criteria from supervised → escalation-only → headless, and the guardrails
  (sandbox, budget cap, expanded allow-list) each step needs.

## 15. References
- Research synthesis (this session): Huntley `ghuntley.com/ralph`; Anthropic posts above;
  12-factor-agents; Willison `designing-agentic-loops`.
- CLAUDE.md (workflow, conventions, constraints); `decisions.md` D001/D041.
- Memory: `project_loop_engineering_harness`, `feedback_background_agents_no_git`,
  `project_security_review_label_timing`, `project_architect_pm_agents_global`.
