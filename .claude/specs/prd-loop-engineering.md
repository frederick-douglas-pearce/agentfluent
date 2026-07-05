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
- **Cap iterations/budget; detect "stuck" (repeat action after same error) → escalate.** (The
  per-iteration budget record + cap fields are defined in §6.1/§6.2.)

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
Dependency-ordered. One row per issue. **`Route` and `Status` are separate columns** (a row
can be Route `research`, Status `blocked`). **Pipeline statuses** — advanced by the
orchestrator as the issue moves through §7.1, so an interrupted run leaves a non-terminal
status resume keys on (§7.6): `queued → routed → planning → plan-approved → implementing →
in-pr → in-review`. **Terminal statuses** — the run converges when every row is terminal:
`done`; `deferred` (Route `stub-defer`); `blocked` (an unmet in-run dependency, or
`blocked: too-large` awaiting a split). **Two non-terminal, resting statuses** sit outside both
the pipeline and the terminal set: **`hold`** — a durable, human-set merge-hold that survives
`/clear`; and **`parked`** — a row whose work is gated on an **external event** (a release cut, a
dogfood window — *not* an in-run dependency), with the awaited condition in Notes as
`awaiting: <condition>`. Both retain their Route and are released only by the human (a `hold` by
clearing the hold at the merge gate; a `parked` row by explicit un-park, §7.1 step 0.1 — which
flips it back to `routed`). While any `hold` **or `parked`** row remains the run is NOT complete
(§9 distinguishes the resting `RUN PARKED` state from terminal `RUN COMPLETE`), but neither blocks
selecting other queued work (§7.1 steps 0–1).

**Curated-subset invariant.** The queue built at init (§7.5) is the authoritative work set;
milestone membership may drift afterward, and that drift is **surfaced to the human once, never
auto-applied** — neither auto-added on join nor auto-ejected on leave (§7.1 step 1 milestone-roster
reconciliation). A corollary is a Notes discipline: **write a `parked`/`blocked` row's Notes as the
durable curation DECISION** (`awaiting: v0.11.0 cut`, `deliberately out of <run> at init
(curation)`, `kept: out-of-<run> roster (curation)`), **never the mutable live evidence** ("not in
milestone", "no PR yet") — the latter is contradicted by a later live re-check and destabilizes
resume (the v0.10.0 row-12 failure).

The header carries a `mode:` field that gates **the merge gate only** — it does
**not** change the plan gate, which is conditional in *every* mode (§7.1 step 5 / skill §5: the
plan gate stops only on ambiguous ACs, risk/irreversibility, agent disagreement, or genuine
uncertainty — never merely because of `mode:`). The two modes:
- **`calibration`** (default) — the human approves **every** merge; the loop never auto-merges
  (§7.1 step 11). Plan gate conditional.
- **`escalation-only`** — the human loosens the **merge gate per route**: a route the human has
  *graduated* auto-merges when CI + AC-verifier + review are green and the version bump is
  ≤ patch (a `docs`/`chore` change produces no bump, which qualifies). *Which* routes are
  currently graduated is a mutable human decision, recorded per-run in the `graduated-routes:`
  header and in the decision log (see **D047** for the first graduation — `docs`+`research`) —
  never frozen into this mechanism definition (the #584 lesson: graduation *state* is evidence,
  not a rule). The human merge gate is
  **retained** for every non-graduated route and, regardless of route, for any of: a `feat:`/
  breaking change, a risky/irreversible change, a touched security surface, a contested review
  finding, or a `hold` row — **and, by default-deny, whenever route graduation or any
  always-escalate condition is uncertain, fall back to the human merge gate.** Plan gate
  conditional (unchanged). Loosening to `escalation-only` presupposes the calibration
  prerequisites are met (these pinned mode semantics, #563, plus per-iteration budget
  journaling — the `- Budget:` record and `iteration-cap:`/`subagent-cap:` fields below,
  #565); it cannot run headless (§13/§14).

The set of graduated routes is recorded in a `graduated-routes:` header field beside `mode:`
(default `none`; e.g. `graduated-routes: docs, research`, per D047). Under `mode: calibration` it is inert. *Which*
routes graduate and the criteria for promoting one (#562) are out of scope here; this field only
gives the merge gate (§7.1 step 11) a place to read the human's decision from.

The header also carries two **budget caps** (both default `none` = uncapped), #565:
`iteration-cap:` (max **issues per run** — in this spec one "iteration" = one issue) and
`subagent-cap:` (max **subagent runs per iteration**). The orchestrator checks them at iteration
start (§7.1 step 1) as a **retrospective circuit-breaker** against the ledger — it does not watch
its own spend mid-turn (that is why token/cost is deferred, §6.2). Cumulative iterations are
counted as the **distinct issues at a terminal status** (`done`/`deferred`/`blocked`) in
`queue.md` — the authoritative status file — never by counting `progress.md` blocks, since a
`/clear`-resume re-enters an iteration and would double-count. `subagent-cap` is enforced by
reading the **prior** iteration's journaled `- Budget:` line (§6.2): if it breached, halt before
starting the next. On breach the behavior is **advisory in manual re-invoke** (journal + surface
it and proceed — the human who invoked is the budget authority) and **halting under the driver**
(§9). The caps bound `escalation-only`'s runaway-consumption risk; bad-merge risk is already
covered by §6.1's default-deny/always-escalate machinery.

```markdown
# Loop run: v0.10.0
_mode: calibration_
_graduated-routes: none_
_iteration-cap: none_       # max issues per run; none = uncapped (#565)
_subagent-cap: none_        # max subagent runs per iteration; none = uncapped (#565)
_Last updated: <ISO8601 by orchestrator>_

| # | Issue | Route | Status | Depends on | PR | Notes |
|---|-------|-------|--------|-----------|----|----|
| 1 | #500 pm clobbers decisions.md | code | done | — | #550 | precondition |
| 2 | #518 SDK hello-world probe | research | queued | — | — | first in epic #517 |
| 3 | #522 representative SDK agent | research | blocked | #518 | — | needs S1a findings |
| 4 | #510 PARAMETER_RETRY fixes | code | queued | — | — | high |
| 5 | #469 per-turn ratios | stub-defer | deferred | dogfood | — | D041 — do not implement |
| 6 | #513 post-release re-measure | code | parked | — | — | awaiting: v0.10.0 dogfood window |
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
- Budget: subagent-runs=3 · gate-rounds=architect=0,code-review=1,ac-verify=1 · wall-clock=18m · tokens=deferred
- Merged: squash #NNN. Issue #518 closed.
- Next: #522 now unblocked.
```

The `- Budget:` line is the per-iteration cost record (#565). Fields:
- **`subagent-runs`** — the proxy cost signal the orchestrator can count for free (the v0.10.0
  run's only quantitative signal was "~6 subagent runs/issue", §11.3). Its blind spot:
  parent-thread token burn (a long implement step spawns no subagent yet can be the largest
  consumer) is invisible to run-count — which is precisely what the deferred `tokens` field
  eventually fixes.
- **`gate-rounds`** — architect / code-review / ac-verify round counts (feeds §12 review-thrash
  detection).
- **`wall-clock`** — elapsed time including human gate-wait; recorded for the §12 corpus, **not a
  cap input** (an iteration that waited overnight for approval is not "expensive").
- **`tokens=deferred`** — a reserved, named slot. Per-iteration token/cost is computed **post-hoc
  by AgentFluent over the loop's own JSONL** (the loop JSONL is a first-class corpus, §12), not
  inside the skill (the orchestrator can't cleanly slice its live session mid-turn). The future
  SDK driver (§13) backfills it via usage callbacks — keeping the slot named now makes that a
  backfill, not a format change.

### 6.3 `issue-<N>.plan.md` — per-issue plan (architect-reviewed, human-approved)
```markdown
# Plan: #<N> — <title>
**Route:** <code|research|docs>  **Branch:** <BRANCH_FMT>

## Value framing (route-scaled — see §3)
<feat: backbone activity + 1–3 `as a … I want … so that …`, each with who-benefits +
prevalence + a falsifier ("what observation would show this is misdirected?"); discharge cheap
falsifiers here (run the grep/corpus pass) rather than only stating them.
fix: who hits it / how often / what breaks. docs: who reads it / what it unblocks.
research: question + downstream decision + what a null result means.
chore:/refactor: what internal tooling/quality it serves + why now.
Add a source-fidelity note if the rationale leans on any externally-cited source.>

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

The live skill lives at `.claude/skills/release-loop/SKILL.md`; the block below is a
**byte-identical mirror** of it (the body IS the orchestrator's operating procedure), kept
here so the spec stays self-contained and ready-to-drop-in. The two copies are CI-guarded
to stay byte-for-byte identical (`tests/unit/test_loop_skill_drift.py`) — **edit both
together**; on a conflict `SKILL.md` is the operative copy and this block mirrors it.

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
1. Identify the active run (most recent `LEDGER_ROOT/<run>/`). If none exists, ask the user which
   milestone/label to run, then INITIALIZE per §7.5 of the spec. Otherwise scan the FULL
   `progress.md` for the **most recent** run-state sentinel — the last of `{RUN COMPLETE, RUN
   PARKED, RUN RESUMED}` by append order (the log is append-only, so a superseded sentinel still
   sits above; last one wins) — and act only on it:
   - `RUN COMPLETE` (§9) → report done and STOP; do not re-scan (the run is terminal).
   - `RUN PARKED — awaiting <condition>` (§9) — the run finished all *workable* rows and rests on
     an external event:
     - **If this invocation explicitly releases the park** (the human names a met condition — "the
       cut is out, resume"): perform the concrete un-park mutation, **scoped to the released
       condition** — flip back to `routed` (retain Route, clear the `awaiting:` marker) ONLY the
       `parked` rows whose `awaiting:` condition the human named; leave rows still gated on *other*
       conditions `parked` with their markers intact (if which rows a release covers is ambiguous,
       ask — do NOT flip all, that would prematurely release a still-unmet gate). Append a `RUN
       RESUMED` sentinel (now last-wins) and continue to step 2; any rows left parked simply
       re-append `RUN PARKED` at the next §1 pass (last-wins over `RUN RESUMED`), which the existing
       machinery handles. A bare re-fire (e.g. the `/loop` driver) does NOT release the park.
     - **Otherwise take the cheap parked path (no full re-scan):** read `queue.md` + the FULL
       `progress.md`, run the §1 milestone-roster reconciliation (the one scan a parked run still
       owes — this is how milestone drift is still caught), then **re-derive selectability from
       `queue.md` alone** (no git/PR reconcile). If that produced selectable work (a joiner the
       human pulled in, or an in-run dep that has since cleared) fall through to §1 **at selection**
       (the reconciliation just ran — do not repeat it); otherwise STOP and report "parked —
       awaiting <condition>" **without** running §0 step 3 resume or any per-row live reconcile —
       skipping resume is provably safe here (a valid PARKED state has every non-`parked` row
       terminal, so no interrupted pipeline row can coexist).
   - `RUN RESUMED` or no sentinel → continue to step 2 (a released or never-parked run runs
     normally).
2. Read `queue.md` (note its `mode:` / `graduated-routes:` header and any `hold`/`parked` rows) and the tail of `progress.md`.
3. **Resume before selecting (spec §7.6).** If any row sits in an *interrupted* status —
   non-terminal and NOT `queued`/`routed`/`hold`/`parked` (i.e. `planning`/`plan-approved`/
   `implementing`/`in-pr`/`in-review`) — a prior iteration was cut off. Reconcile it against
   LIVE git/PR state as the source of truth — branch exists? PR open? already merged? CI
   status? — plus the working tree (status is only a coarse anchor; git wins on conflict),
   then re-enter the pipeline at the matching stage and FINISH that issue BEFORE selecting a
   new one. This is what makes "one PR at a time" hold across `/clear`/compaction. A `hold` or
   `parked` row is NOT an interruption: skip it here — a `hold` stays held until the human
   releases the merge, a `parked` row stays gated until its external condition is released (step
   1); neither blocks working other issues.

## 1. Select
**Budget cap (iteration start, retrospective).** Read `iteration-cap:` / `subagent-cap:` from the
`queue.md` header (both default `none` = uncapped). Cumulative iterations = the count of **distinct
issues at a terminal status** (`done`/`deferred`/`blocked`) in `queue.md` — never count
`progress.md` blocks (a `/clear`-resume re-enters an iteration and double-counts). Breach = that
count ≥ `iteration-cap`, OR the **prior** iteration's journaled `- Budget:` line (§12) shows
`subagent-runs` ≥ `subagent-cap`. On breach: **manual re-invoke is advisory** — journal + surface
it and proceed (the human who invoked is the budget authority); **the driver halts.** Inert while
both caps are `none`.

**Milestone-roster reconciliation (iteration start).** The queue built at init (§7.5) is the
authoritative work set — the *curated subset*; milestone membership may drift afterward, and drift
is **surfaced to the human once, never auto-applied** — neither auto-added on join nor auto-ejected
on leave. Compute the delta between the live `BACKLOG_SOURCE` roster (one `gh issue list
--milestone <run> --state open`) and `queue.md`, deduping against prior curation records via a
FULL-file scan of `progress.md` (not the tail) for exact `- surfaced-join:` / `- surfaced-leave:`
lines:
- **Joined** (in the milestone, no `queue.md` row, not already surfaced) → surface once: "#N joined
  <run> after init — pull in, or leave out? (never auto-added)." Record `- surfaced-join: #N` in a
  `## <ISO8601> — curation` block. Only on the human's "pull in" add a `queued` row; a bare surface
  never adds one.
- **Left** (a non-terminal `queue.md` row whose issue is no longer in the milestone, not already
  recorded) → surface once: "#N left <run> — eject, or keep? (never auto-ejected)." Record
  `- surfaced-leave: #N`. On "keep", write the decision to the row's Notes (`kept: out-of-<run>
  roster (curation)`) so it self-dedups; on "eject", an in-flight leaver (`planning`..`in-review`,
  open PR) is **finish-then-reconsider**, not a bare eject (only a pre-pipeline row ejects cleanly —
  close/clean its PR+branch first), then set the row `deferred` with a curation Notes reason.
This paragraph is the sub-unit the §0 step 1 parked path invokes standalone.

A row is **selectable** if its status is `queued`/`routed`, OR it is `blocked` on an unmet
dependency that has SINCE cleared (all its `Depends on` issues are now `done` — re-route it via
§2; this does NOT apply to a `blocked: too-large` park, which waits on a split). A `parked` row is
never selectable here — it is released only by explicit human un-park (§0 step 1). Among selectable
rows pick by `PRIORITY_LABELS` order, tiebreak issue-number ascending. If none are selectable,
determine the resting state from the remaining non-terminal rows (test in this order):
- Any `hold` row present → report "<n> held — awaiting human merge-release" and STOP **without** a
  sentinel (a held row needs the human now; the run is neither complete nor cleanly parked).
- Else if ≥1 `parked` row is present AND every non-`parked` row is `done`/`deferred` → append the §9
  `RUN PARKED — awaiting <condition(s)>` sentinel to `progress.md` (name the awaited condition(s) +
  the parked rows) and STOP. This is a **resting, non-terminal** state: the next invocation
  short-circuits on it (§0 step 1) instead of re-reconciling. (Tested BEFORE COMPLETE so a
  release-gated row is not swallowed as terminal; it requires truly-terminal peers — a plain
  in-run-`blocked` row present routes to pending below, not to a false park.)
- Else if EVERY row is terminal (`done`/`deferred`/`blocked`) → append the §9 `RUN COMPLETE —
  <run-slug>` sentinel to `progress.md` (counts + any blocked/deferred items) and STOP
  (convergence).
- Else (rows still `blocked` on an open in-run dependency, or `blocked: too-large` awaiting a split)
  → report what's pending and STOP without a sentinel.
**Size guard:** before entering the pipeline, estimate scope from the issue body — if it
plausibly touches many files or spans multiple unrelated acceptance-criteria clusters (won't
fit one context window), mark it `blocked: too-large`, escalate to SCOPE_AGENT to split, and
go back to select. Aggressively offload reading/analysis to subagents (architect, AC-verifier)
within an iteration to conserve the parent's context.

## 2. Triage / route (if not already routed)
Run §7.3 to set the row's **Route** (`code`/`research`/`docs`/`stub-defer`) and its **initial
Status** (Route and Status are distinct — §6.1): `stub-defer` → Status `deferred` (terminal); an
unmet in-run dependency → Status `blocked` (record the dep, or `too-large`, in Notes — the Route is
retained so the row resumes as that route when the dependency clears, §1); a row whose work is
gated on an **external event** (a release cut, a dogfood window — not an in-run issue) → Status
`parked` with Notes `awaiting: <condition>` (non-terminal, resting; released only by explicit human
un-park, §0 step 1); otherwise → Status `routed`. If the Status is `deferred`/`blocked`/`parked`,
journal why and go back to §1 — do not implement.

**Write parked/blocked Notes as the curation DECISION, never the mutable evidence.** The durable
*why* (`awaiting: v0.11.0 cut`, `deliberately out of <run> at init (curation)`, `kept: out-of-<run>
roster (curation)`) survives a later live re-check; the mutable live evidence ("not in milestone",
"no PR yet") is contradicted by the next re-check and destabilizes resume (the v0.10.0 row-12
failure).

## 3. Plan
Set the row status to `planning`. Fetch the issue (`gh issue view <N>`). Write
`issue-<N>.plan.md` (template in spec §6.3), copying acceptance criteria verbatim. Lighter for
research/docs.

**Value framing (opens the plan, route-scaled).** State *why this should exist* in
user terms — the question the architect/AC-verifier/code-review gates never ask (they check we
build the thing right, not that it's the right thing). You write it inline; it is not extra
ceremony. Scale it to the route:
- **`feat:`** — a compact user-story map: one backbone activity + 1–3 `as a <user>, I want
  <capability>, so that <outcome>` stories. Each carries **who benefits**, its **prevalence**
  (how often real configs/corpora actually hit it), and a **falsifier** — *what single
  observation would show this feature is misdirected?* (e.g. "~0 matching instances in any real
  corpus"). A story with no credible user, or no checkable falsifier, is a red flag.
- **`fix:`** — one line: who hits the bug, how often, what breaks without the fix.
- **`docs:`** — who reads it and what it unblocks.
- **`research:`** — the question, the downstream decision it informs, and what a **null result**
  would mean (a null that changes nothing is a sign the question isn't worth asking).
- **`chore:` / `refactor:`** — one line: what internal tooling or quality this serves and why
  now; no user-facing story required (state "internal tooling, no PyPI-visible change" if so).

**Discharge cheap falsifiers at plan time — don't just state them.** If a story's falsifier is
checkable *before* code (a grep / corpus / prevalence pass), RUN it now, or escalate; a
stated-but-unrun falsifier is not sufficient. This is the load-bearing step: the cheap corpus
pass is exactly what caught #437 — but only post-hoc. Defer discharge only when the check
genuinely requires the built feature.

**Source-fidelity check (any externally-cited justification).** If the issue's rationale leans on
an external source — a research-scout (`anthropic-feature-watch`) candidate, a linked article, a
postmortem — confirm the source actually supports the generalization the issue makes: **locus**
(does the incident occur on the surface this feature inspects?), **evidence base** (n, scope,
whether the source itself generalizes), and **current relevance** (already fixed upstream?
version-specific?). An issue that extrapolates past what its source establishes is misdirected
regardless of implementation quality. (The #437 lesson — decision D046.)

**When you can't articulate it, escalate — don't build.** If you cannot state a credible user
*and* a checkable falsifier, route the issue to SCOPE_AGENT (pm) BEFORE implementing; do not
proceed on a plan whose value story doesn't hold.

## 4. Architect gate (conditional)
If any §7.2 trigger fires OR you are unsure about the design, invoke the DESIGN_AGENT with
the plan; address `blocking`/`important` concerns before coding. Skip for docs and trivial
research.

## 5. Human gate (conditional — every mode)
The plan gate is **conditional in every mode** — `mode:` gates the merge gate only (§11), never
this one. It is **value-first**: present the §3 value framing (user-story map / value statement)
alongside the approach, and treat a **non-credible value story — no plausible user, or no
checkable falsifier — as itself a reason to STOP**, not just ambiguous ACs. Present the plan and
STOP for approval when: the value story doesn't hold; acceptance criteria are ambiguous; the
change is risky/irreversible; SCOPE/DESIGN agents disagree or punt; or you are otherwise unsure.
Otherwise proceed (note "auto-approved" + why in the journal). Route scope/value questions to
SCOPE_AGENT and design questions to DESIGN_AGENT BEFORE escalating to the human. On approval
(human or auto), advance the row to `plan-approved`.

## 6. Implement (you, the parent thread)
Advance the row to `implementing`. Create the branch (`BRANCH_FMT`). Implement code + tests +
docs per the plan. TDD where it fits (write failing tests, commit, do not modify tests later).
Run `LINT_CMD`, `TYPE_CMD`, `TEST_CMD` until green. Do NOT stage unrelated pre-existing
working-tree changes.

## 7. Verify done (independent, fresh context)
Run the AC-verifier (spec §7.4): a fresh check that the diff satisfies EVERY acceptance
criterion — verify state, not your claim. If gaps, fix and re-verify (max 2 rounds, else
escalate).

## 8. Commit + PR
Commit with correct `COMMIT_CONV` scope. Open the PR; **replicate `PR_TEMPLATE` fully** in
the body; make the Security-review choice up front. Advance the row to `in-pr` and record the
PR number. Wait for CI; fix until green.

## 9. Code review
Advance the row to `in-review`. Run CODE_REVIEW on the diff. Implement viable findings;
decline others with a one-line rationale; **verify recs were applied**. Bounded to 2 rounds —
contested findings escalate to the human, do not loop. Commit fixes.

## 10. Security review (by route)
- `.claude/`-only change → run local `/security-review` (the labeled workflow excludes
  `.claude/`; the local skill needs `git remote set-head origin -a` if it errors on
  `origin/HEAD...`).
- Otherwise, if a sensitive surface is touched → apply `needs-security-review` ONLY now
  (dev-complete). Skip for docs/no-surface changes.
Address findings ≥ the project's confidence bar.

## 11. Merge
Read the run `mode` and `graduated-routes` from the `queue.md` header. The merge gate is the
**only** gate `mode` changes (§5 is conditional in every mode). A row is **auto-merge-eligible**
only when ALL of these hold:
- `mode: escalation-only`, AND
- the row's Route is listed in the header's `graduated-routes` field, AND
- the version bump is ≤ patch — a `docs`/`chore` change produces no bump, which qualifies, AND
- the row is **not** `hold`, AND
- none of the always-escalate conditions apply: a `feat:`/breaking change, a risky/irreversible
  change, a touched security surface, or a contested review finding.

**Default-deny:** if route graduation or any always-escalate condition is uncertain, the row is
**not** auto-merge-eligible — fall back to the human merge gate.

If the row is **not** auto-merge-eligible — which includes *every* row under `mode: calibration`
(the default) and any `hold` row — STOP and ask the human before merging; never auto-merge.
**If the human holds the merge (now or in any later invocation),
WRITE the hold to the row before stopping** — set Status `hold` (record the reason in Notes) so
it persists across `/clear`; resume (step 0.3), §1, and this gate all key on Status `hold` and
honor it until the human clears it (restoring the row's prior status). When the row **is**
auto-merge-eligible (or the human has approved), and CI + security are green AND the row is not
`hold`:
squash-merge with an explicit `--subject` carrying the correct `COMMIT_CONV` scope,
`--delete-branch`. Confirm the issue closed.

## 12. Journal + stop
Append the iteration block to `progress.md`, including a `- Budget:` line (spec §6.2):
`subagent-runs=<n>` · `gate-rounds=architect=<a>,code-review=<c>,ac-verify=<v>` ·
`wall-clock=<elapsed, includes gate-wait — not a cap input>` · `tokens=deferred` (computed
post-hoc by AgentFluent over the loop JSONL; the named slot keeps the line forward-stable).
Set the `queue.md` row to `done` (or `blocked`/`deferred` with reason); note newly-unblocked
issues. The ledger is gitignored — do NOT commit it (spec §6.4). STOP. (Driver re-invokes with
fresh context for the next issue.)

## Escalation rubric (when unsure)
Scope/priority/requirements — including any plan whose value story lacks a credible user or a
checkable falsifier (§3) — → SCOPE_AGENT (pm), before implementing. Design/implementation →
DESIGN_AGENT. Escalate to the HUMAN only when those disagree/punt, ACs are unresolvable, an
action is destructive/irreversible, a review finding is contested, or the same step failed twice.

## Guardrails
One PR at a time (no stacked PRs). **Stuck = the same error SIGNATURE recurs** — grep the FULL
`progress.md` (not just the tail) for the signature: an identical CI failure, or the same
tool+args failing again — NOT merely re-entering a status (a legitimate `/clear`-resume
re-enters `implementing` and must not be flagged). On a genuine repeat: stop, escalate, mark
`blocked`, move on. Respect any iteration/budget cap (`iteration-cap:`/`subagent-cap:` in the
`queue.md` header): checked at iteration start (§1) against the ledger — **advisory in manual
re-invoke (journaled + surfaced, not gating), halted by the driver**.

## Tool surface — and what you must NOT do
This skill intentionally runs with the full session toolset (no `allowed-tools` restriction):
an orchestrator needs Write/Edit, Bash(git+gh+tests), Agent (pm/architect/AC-verifier), and
the built-in review skills. With that power come hard limits — never force-push; never bypass
failing CI (no `gh pr merge --admin`, never merge red); only `--delete-branch` the PR's own
branch; never `git add` unrelated pre-existing working-tree changes; never edit the
user-global SCOPE_AGENT/DESIGN_AGENT definitions. The C1 append-only guard and the
human/merge gates are the enforced backstops; the rest of this list is your contract.
````

### 7.2 Architect-gate triggers (from CLAUDE.md)
Fire the DESIGN_AGENT when the plan: touches shared models (`SessionMessage`,
`AgentInvocation`); changes a cross-module interface; adds a new diagnostics rule,
correlation logic, or analytics pipeline; **or the orchestrator is unsure.** Bias toward
calling it (read-only, cheap vs. a bad implementation). Skip for docs and trivial research.

### 7.3 Router — classification procedure
Set the row's **Route** (the semantic kind) and its **initial Status** *separately* — they are
distinct columns (§6.1), so a dependency-blocked research issue is Route `research` / Status
`blocked`, not Route `blocked`.

**Route**, from labels + body signals, in order:
1. Issue body says explicitly "NOT implementation-ready" / is a stub (e.g. #469, D041) →
   `stub-defer`.
2. Label `documentation` and change is confined to `docs/`/markdown → `docs`.
3. Label `research` or epic `*-discovery`, throwaway scaffolding, "artifact under study is
   data" → `research` (no test-coverage gate; placement outside `src/`; no runtime-dep
   leakage into the package).
4. Otherwise (`bug`/`enhancement` touching `src/`) → `code` (full pipeline).

**Initial Status:** Route `stub-defer` → `deferred` (terminal). Else if the row's work is gated on
an **external event** (a release cut, a dogfood window — not an in-run issue) → `parked` with Notes
`awaiting: <condition>` (non-terminal, resting; released only by explicit human un-park, §7.1 step
0/1). Else if any `Depends on` issue is not `done` → `blocked` (record the dep in Notes; the
semantic Route is retained so the row resumes as that route once the dependency clears, §1). Else →
`routed`.

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
   Write `queue.md` (§6.1) with header `mode: calibration` **unless the human has already
   graduated routes for this project** — check the decision log (e.g. **D047**: `docs`+`research`
   graduated) and, if so, init `mode: escalation-only` + the graduated `graduated-routes:`
   instead, so a prior graduation persists across runs rather than silently resetting to
   calibration. Step 11 reads `mode`/`graduated-routes` to gate **the merge gate**, per route; it
   never affects the plan gate. Also set `iteration-cap: none` and `subagent-cap: none` (the
   budget caps, #565; the human sets them when loosening).
5. Append an "init" block to `progress.md`. (Ledger is gitignored — not committed.)

### 7.6 Resume after `/clear` or compaction
The next `/release-loop` invocation's §0 resume step (step 3) reads `queue.md` + tail of
`progress.md` and, finding any *interrupted* row (non-terminal and NOT
`queued`/`routed`/`hold`/`parked`), finishes it before selecting new work. A `hold` **or `parked`**
row is **excluded** — a `hold` is a deliberate, durable human merge-hold and a `parked` row is
gated on an external event (§6.1), neither an interruption; leave them (a `hold` until the human
clears it at §7.1 step 11; a `parked` row until explicit un-park at §7.1 step 0.1) and neither
blocks other work. A run resting under a `RUN PARKED` sentinel is likewise **not** an interrupted
row — §7.1 step 0 short-circuits it on the cheap parked path and never enters this resume scan
(safe: a valid PARKED state has no non-terminal pipeline row). The on-disk
ledger row status is only a **coarse anchor** (which stage); the **live git/PR state is the
source of truth** for the details (the ledger is uncommitted, §6.4): for an in-flight row,
check whether its branch exists, whether a PR is open (or already merged), and the PR's CI
status, and resume at the matching pipeline stage — git wins on any conflict with a stale
status. Stages 4/7/10 need no distinct status because the surrounding statuses bracket them:
a `plan-approved` row re-enters at implement (§6), so the architect/human gates are NOT re-run.
The one external-side-effect stage is the architect (§4) — it posts a comment to the issue —
so on the rare resume of a `planning` row, check for an existing architect comment and skip
re-invoking if present (do not double-post). AC-verify (§7) is side-effect-free; security (§10)
re-labeling is a GitHub no-op. **Working-tree reconciliation:** if a crashed prior attempt left uncommitted changes, inspect them before
proceeding — keep and continue if they match the plan, or `git restore`/stash if they're
partial/unrelated. A resumed `implementing` row is NOT "stuck" (stuck keys on a repeated error
signature, not status re-entry — see Guardrails).

---

## 8. Routing rules & mechanical rules

| Route | Pipeline differences |
|-------|----------------------|
| `code` | full pipeline, all gates |
| `research` | lighter plan; **no test-coverage gate**; architect optional; security only if deps added; place outside `src/` |
| `docs` | skip architect + security; light review; `docs:` scope |
| `stub-defer` | do NOT implement; journal why; leave in backlog (Status `deferred`) |

`blocked` and `parked` are **Status overlays, not Routes**: a row keeps its semantic Route (`code`/
`research`/`docs`) while resting on an unmet in-run dependency (`blocked`) or an external event
(`parked`). Skip it; a `blocked` row returns to selection when its dependency closes (§1, §7.3), a
`parked` row when the human un-parks it (§7.1 step 0.1).

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
| Merge | user (calibration / non-graduated route) → orchestrator (auto: graduated routes, D047) | CI+security green | squash |

**Convergence & the resting states.** When nothing is selectable, §7.1 step 1 classifies the run
into one of four outcomes (tested in order: hold → parked → complete → pending) and appends a
`progress.md` **run-state sentinel**; §7.1 step 0 reads the **most recent** sentinel by append
order (last-wins, since the log is append-only) and acts only on it:
- **`RUN COMPLETE — <run-slug>`** (terminal) — every row is terminal (`done`/`deferred`/`blocked`).
  Summarizes counts + any blocked/deferred items; the orchestrator stops and reports, and a later
  re-invocation short-circuits without re-scanning.
- **`RUN PARKED — awaiting <condition>`** (resting, **non-terminal**) — all *workable* rows are
  terminal but ≥1 `parked` row awaits an external event (a release cut, a dogfood window). A
  re-invocation short-circuits (§7.1 step 0): it runs only the cheap milestone-roster reconciliation
  + a `queue.md` selectability re-derivation, then re-reports parked **without** the expensive
  per-row live reconcile / resume — until the human explicitly releases a **named** condition
  (which flips only the `parked` rows awaiting *that* condition back to `routed`, appends a
  superseding `RUN RESUMED` sentinel, and leaves rows gated on other conditions parked) or a
  pulled-in joiner / cleared dep makes work selectable again. This is what stops a release-gated run from
  re-reaching a *new* conclusion on every re-fire (the #584 "converged-pending-release"
  instability). Distinct from an *interrupted* row needing resume (§7.6): a valid PARKED state has
  no non-terminal pipeline row, so resume is safely skipped.
- **held / pending** (no sentinel) — a `hold` row needs the human now, or a row is still `blocked`
  on an open in-run dependency; the orchestrator reports and stops without a sentinel (the run is
  not complete and not cleanly parked).

**Guardrails:**
iteration/budget caps live in the `queue.md` header (`iteration-cap:`/`subagent-cap:`, §6.1) and
are checked at iteration start against the ledger — **advisory in manual re-invoke (journaled +
surfaced, not gating — the human who invoked is the budget authority), halted by the driver**;
one PR at a time; stuck-detection (repeated error signature) → escalate.
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
7. **Calibrate** — run 2–3 issues with the human present at the **merge** gate (and at the plan
   gate whenever it fires on uncertainty — the plan gate is conditional in every mode, §6.1);
   tune router + escalation thresholds; then loosen to escalation-only.
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
  (sandbox, budget cap, expanded allow-list) each step needs. (The supervised → escalation-only
  *definitions* are now pinned in §6.1 / §11, #563; the per-iteration budget *record + cap* are
  pinned in §6.1 / §6.2, #565 — what remains open there is **enforcement in headless mode** (the
  driver halts; a `claude -p` loop has no live turn to surface an advisory into). What remains open
  on graduation is the per-route *criteria* — e.g. how many zero-veto merges before `code`
  graduates — tracked in #562.)
- **Async-ask UX in headless mode (§13).** `claude -p` is non-interactive by definition —
  there is no live turn to block into — so the supervised loop's *blocking* gate (stop
  mid-turn, wait for a human click) cannot exist headless. §2's principle ("HITL as an async
  *ask*, not a blocking babysit") becomes mandatory there. Mechanism is partly in place: the
  `hold` / `blocked` ledger statuses (§6.1) already turn a human gate into a durable, parked
  row that survives `/clear`; the human answers out of band (release the hold, comment on the
  issue, edit the ledger) and the next launch resumes via §0/§7.6. **Open:** on an
  uncertainty, does the run *halt-and-exit* (leaving one parked ask) or *park-and-continue*
  (record the ask, move to the next selectable issue, exit when only parked/held rows remain)?
  How is the ask surfaced beyond the ledger — issue comment, push/email ping, both? And what
  is the human's review surface for a run they did *not* watch live (streamed
  `--output-format stream-json` vs. reading `progress.md` + the PR/issue trail after the
  fact)? Note review/verify re-plumbing (§13): `/code-review`/`/security-review`/`/verify`
  don't carry into `claude -p`, so the gate machinery itself changes, not just the human's
  presence.
- **Hybrid mode selection.** Headless graduation is not all-or-nothing (§13: graduate *part*
  of the loop). The likely end-state keeps **both** an interactive `/release-loop` (for
  calibration, risky/irreversible issues, anything worth being live for — blocking gates work)
  **and** a headless `claude -p` loop (for the escalation-only steady state, where low-risk
  routes flow through untouched and only genuine asks surface as parked rows). **Open:** what
  is the selection boundary — per `mode:` (already gates auto-merge, §7.1 step 11), per route
  (e.g. `docs`/`research` headless-eligible, `code` stays interactive until proven), per
  issue-level flag, or a confidence threshold? Headless presupposes the gates are already
  loosened (it cannot run under `mode: calibration`, which stops at every merge gate), so the
  boundary is downstream of the graduation ladder above, not independent of it.

## 15. References
- Research synthesis (this session): Huntley `ghuntley.com/ralph`; Anthropic posts above;
  12-factor-agents; Willison `designing-agentic-loops`.
- CLAUDE.md (workflow, conventions, constraints); `decisions.md` D001/D041.
- Memory: `project_loop_engineering_harness`, `feedback_background_agents_no_git`,
  `project_security_review_label_timing`, `project_architect_pm_agents_global`.
