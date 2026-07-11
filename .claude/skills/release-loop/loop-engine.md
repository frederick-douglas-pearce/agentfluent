# Loop engine — generic operating procedure & semantics

This is the **project-agnostic engine** for the supervised dev loop: control flow, gate /
convergence / resume semantics, the ledger format, the router procedure shape, and the budget
machinery. It contains **no project-specific values**.

Every name in `CAPS` (`BACKLOG_SOURCE`, `LEDGER_ROOT`, `SCOPE_AGENT`, `DESIGN_AGENT`,
`CODE_REVIEW`, `PRIORITY_LABELS`, `ARCHITECT_TRIGGERS`, `SOURCE_LAYOUT`, `LINT_CMD`/`TYPE_CMD`/
`TEST_CMD`, `BRANCH_FMT`, `COMMIT_CONV`, `MERGE_METHOD`, `RELEASE_SCHEME`, …) is bound in the
per-project **`loop.config.md`**. **Read that config first** — this engine depends on the
config's parameter *vocabulary*, never its layout.

Cross-references within this doc are by **named section** (e.g. "the Resume procedure below"),
and pipeline steps are numbered 0–12. The live skill (`SKILL.md`) is the thin entry point that
loads this engine plus the config; the full procedure lives here, once.

---

## The pipeline (ONE issue per invocation)

You are the orchestrator of a supervised dev loop. Each invocation handles exactly ONE issue
end-to-end, journals, and stops. State lives in the ledger, not your context — so a fresh
invocation resumes correctly.

### 0. Load or initialize state
1. Identify the active run (most recent `LEDGER_ROOT/<run>/`). If none exists, ask the user which
   `BACKLOG_SOURCE` (milestone/label/`TODO.md`) to run, then INITIALIZE per the Initialization
   procedure below. Otherwise scan the FULL `progress.md` for the **most recent** run-state
   sentinel — the last of `{RUN COMPLETE, RUN PARKED, RUN RESUMED}` by append order (the log is
   append-only, so a superseded sentinel still sits above; last one wins) — and act only on it:
   - `RUN COMPLETE` (see Convergence) → report done and STOP; do not re-scan (the run is terminal).
   - `RUN PARKED — awaiting <condition>` (see Convergence) — the run finished all *workable* rows
     and rests on an external event:
     - **If this invocation explicitly releases the park** (the human names a met condition — "the
       cut is out, resume"): perform the concrete un-park mutation, **scoped to the released
       condition** — flip back to `routed` (retain Route, clear the `awaiting:` marker) ONLY the
       `parked` rows whose `awaiting:` condition the human named; leave rows still gated on *other*
       conditions `parked` with their markers intact (if which rows a release covers is ambiguous,
       ask — do NOT flip all, that would prematurely release a still-unmet gate). Append a `RUN
       RESUMED` sentinel (now last-wins) and continue to step 2; any rows left parked simply
       re-append `RUN PARKED` at the next step-1 pass (last-wins over `RUN RESUMED`), which the
       existing machinery handles. A bare re-fire (e.g. the `/loop` driver) does NOT release the
       park.
     - **Otherwise take the cheap parked path (no full re-scan):** read `queue.md` + the FULL
       `progress.md`, run the step-1 roster reconciliation (the one scan a parked run still owes —
       this is how `BACKLOG_SOURCE` drift is still caught), then **re-derive selectability from
       `queue.md` alone** (no git/PR reconcile). If that produced selectable work (a joiner the
       human pulled in, or an in-run dep that has since cleared) fall through to step 1 **at
       selection** (the reconciliation just ran — do not repeat it); otherwise STOP and report
       "parked — awaiting <condition>" **without** running the step-3 resume or any per-row live
       reconcile — skipping resume is provably safe here (a valid PARKED state has every non-`parked`
       row terminal, so no interrupted pipeline row can coexist).
   - `RUN RESUMED` or no sentinel → continue to step 2 (a released or never-parked run runs
     normally).
2. Read `queue.md` (note its `mode:` / `graduated-routes:` header and any `hold`/`parked` rows) and the tail of `progress.md`.
3. **Resume before selecting (see the Resume procedure below).** If any row sits in an *interrupted*
   status — non-terminal and NOT `queued`/`routed`/`hold`/`parked` (i.e. `planning`/`plan-approved`/
   `implementing`/`in-pr`/`in-review`) — a prior iteration was cut off. Reconcile it against
   LIVE git/PR state as the source of truth — branch exists? PR open? already merged? CI
   status? — plus the working tree (status is only a coarse anchor; git wins on conflict),
   then re-enter the pipeline at the matching stage and FINISH that issue BEFORE selecting a
   new one. This is what makes "one PR at a time" hold across `/clear`/compaction. A `hold` or
   `parked` row is NOT an interruption: skip it here — a `hold` stays held until the human
   releases the merge, a `parked` row stays gated until its external condition is released (step
   1); neither blocks working other issues.

### 1. Select
**Budget cap (iteration start, retrospective).** Read `iteration-cap:` / `subagent-cap:` from the
`queue.md` header (both default `none` = uncapped). Cumulative iterations = the count of **distinct
issues at a terminal status** (`done`/`deferred`/`blocked`) in `queue.md` — never count
`progress.md` blocks (a `/clear`-resume re-enters an iteration and double-counts). Breach = that
count ≥ `iteration-cap`, OR the **prior** iteration's journaled `- Budget:` line (step 12) shows
`subagent-runs` ≥ `subagent-cap`. On breach: **manual re-invoke is advisory** — journal + surface
it and proceed (the human who invoked is the budget authority); **the driver halts.** Inert while
both caps are `none`.

**Roster reconciliation (iteration start).** The queue built at init (see Initialization) is the
authoritative work set — the *curated subset*; `BACKLOG_SOURCE` membership may drift afterward, and
drift is **surfaced to the human once, never auto-applied** — neither auto-added on join nor
auto-ejected on leave. Compute the delta between the live `BACKLOG_SOURCE` roster (one enumeration,
e.g. `gh issue list --milestone <run> --state open` for a milestone source) and `queue.md`,
deduping against prior curation records via a FULL-file scan of `progress.md` (not the tail) for
exact `- surfaced-join:` / `- surfaced-leave:` lines:
- **Joined** (in the `BACKLOG_SOURCE` roster, no `queue.md` row, not already surfaced) → surface
  once: "#N joined <run> after init — pull in, or leave out? (never auto-added)." Record `-
  surfaced-join: #N` in a `## <ISO8601> — curation` block. Only on the human's "pull in" add a
  `queued` row; a bare surface never adds one.
- **Left** (a non-terminal `queue.md` row whose issue is no longer in the roster, not already
  recorded) → surface once: "#N left <run> — eject, or keep? (never auto-ejected)." Record
  `- surfaced-leave: #N`. On "keep", write the decision to the row's Notes (`kept: out-of-<run>
  roster (curation)`) so it self-dedups; on "eject", an in-flight leaver (`planning`..`in-review`,
  open PR) is **finish-then-reconsider**, not a bare eject (only a pre-pipeline row ejects cleanly —
  close/clean its PR+branch first), then set the row `deferred` with a curation Notes reason.
This paragraph is the sub-unit the step-0.1 parked path invokes standalone.

A row is **selectable** if its status is `queued`/`routed`, OR it is `blocked` on an unmet
dependency that has SINCE cleared (all its `Depends on` issues are now `done` — re-route it via
step 2; this does NOT apply to a `blocked: too-large` park, which waits on a split). A `parked` row
is never selectable here — it is released only by explicit human un-park (step 0.1). Among
selectable rows pick by `PRIORITY_LABELS` order, tiebreak issue-number ascending. If none are
selectable, determine the resting state from the remaining non-terminal rows (test in this order):
- Any `hold` row present → report "<n> held — awaiting human merge-release" and STOP **without** a
  sentinel (a held row needs the human now; the run is neither complete nor cleanly parked).
- Else if ≥1 `parked` row is present AND every non-`parked` row is `done`/`deferred` → append the
  `RUN PARKED — awaiting <condition(s)>` sentinel (see Convergence) to `progress.md` (name the
  awaited condition(s) + the parked rows) and STOP. This is a **resting, non-terminal** state: the
  next invocation short-circuits on it (step 0.1) instead of re-reconciling. (Tested BEFORE COMPLETE
  so a release-gated row is not swallowed as terminal; it requires truly-terminal peers — a plain
  in-run-`blocked` row present routes to pending below, not to a false park.)
- Else if EVERY row is terminal (`done`/`deferred`/`blocked`) → append the `RUN COMPLETE —
  <run-slug>` sentinel (see Convergence) to `progress.md` (counts + any blocked/deferred items) and
  STOP (convergence).
- Else (rows still `blocked` on an open in-run dependency, or `blocked: too-large` awaiting a split)
  → report what's pending and STOP without a sentinel.
**Size guard:** before entering the pipeline, estimate scope from the issue body — if it
plausibly touches many files or spans multiple unrelated acceptance-criteria clusters (won't
fit one context window), mark it `blocked: too-large`, escalate to `SCOPE_AGENT` to split, and
go back to select. Aggressively offload reading/analysis to subagents (`DESIGN_AGENT`,
AC-verifier) within an iteration to conserve the parent's context.

### 2. Triage / route (if not already routed)
Run the Router (below) to set the row's **Route** (`code`/`research`/`docs`/`stub-defer`) and its
**initial Status** (Route and Status are distinct — see Ledger format → queue.md): `stub-defer` →
Status `deferred` (terminal); an unmet in-run dependency → Status `blocked` (record the dep, or
`too-large`, in Notes — the Route is retained so the row resumes as that route when the dependency
clears, step 1); a row whose work is gated on an **external event** (a release cut, a dogfood
window — not an in-run issue) → Status `parked` with Notes `awaiting: <condition>` (non-terminal,
resting; released only by explicit human un-park, step 0.1); otherwise → Status `routed`. If the
Status is `deferred`/`blocked`/`parked`, journal why and go back to step 1 — do not implement.

**Write parked/blocked Notes as the curation DECISION, never the mutable evidence.** The durable
*why* (`awaiting: <external condition>`, `deliberately out of <run> at init (curation)`, `kept:
out-of-<run> roster (curation)`) survives a later live re-check; the mutable live evidence ("not in
roster", "no PR yet") is contradicted by the next re-check and destabilizes resume.

### 3. Plan
Set the row status to `planning`. Fetch the issue (`gh issue view <N>`). Write
`issue-<N>.plan.md` (template in Ledger format → issue-<N>.plan.md), copying acceptance criteria
verbatim. Lighter for research/docs.

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
  now; no user-facing story required (state "internal tooling, no release-visible change" if so).

**Discharge cheap falsifiers at plan time — don't just state them.** If a story's falsifier is
checkable *before* code (a grep / corpus / prevalence pass), RUN it now, or escalate; a
stated-but-unrun falsifier is not sufficient. This is the load-bearing step: a cheap corpus pass
has caught a misdirected feature in this project's history — but only post-hoc, which is exactly the
argument for discharging it now. Defer discharge only when the check genuinely requires the built
feature.

**Source-fidelity check (any externally-cited justification).** If the issue's rationale leans on
an external source — an automated research-scout candidate feed, a linked article, a postmortem —
confirm the source actually supports the generalization the issue makes: **locus** (does the
incident occur on the surface this feature inspects?), **evidence base** (n, scope, whether the
source itself generalizes), and **current relevance** (already fixed upstream? version-specific?).
An issue that extrapolates past what its source establishes is misdirected regardless of
implementation quality — escalate rather than build.

**When you can't articulate it, escalate — don't build.** If you cannot state a credible user
*and* a checkable falsifier, route the issue to `SCOPE_AGENT` BEFORE implementing; do not
proceed on a plan whose value story doesn't hold.

### 4. Architect gate (conditional)
If any `ARCHITECT_TRIGGERS` condition fires OR you are unsure about the design, invoke the
`DESIGN_AGENT` with the plan; address `blocking`/`important` concerns before coding. Skip for docs
and trivial research.

### 5. Human gate (conditional — every mode)
The plan gate is **conditional in every mode** — `mode:` gates the merge gate only (step 11), never
this one. It is **value-first**: present the step-3 value framing (user-story map / value statement)
alongside the approach, and treat a **non-credible value story — no plausible user, or no
checkable falsifier — as itself a reason to STOP**, not just ambiguous ACs. Present the plan and
STOP for approval when: the value story doesn't hold; acceptance criteria are ambiguous; the
change is risky/irreversible; SCOPE/DESIGN agents disagree or punt; or you are otherwise unsure.
Otherwise proceed (note "auto-approved" + why in the journal). Route scope/value questions to
`SCOPE_AGENT` and design questions to `DESIGN_AGENT` BEFORE escalating to the human. On approval
(human or auto), advance the row to `plan-approved`.

### 6. Implement (you, the parent thread)
Advance the row to `implementing`. Create the branch (`BRANCH_FMT`). Implement code + tests +
docs per the plan. TDD where it fits (write failing tests, commit, do not modify tests later).
Run `LINT_CMD`, `TYPE_CMD`, `TEST_CMD` until green. Do NOT stage unrelated pre-existing
working-tree changes.

### 7. Verify done (independent, fresh context)
Run the AC-verifier (below): a fresh check that the diff satisfies EVERY acceptance
criterion — verify state, not your claim. If gaps, fix and re-verify (max 2 rounds, else
escalate).

### 8. Commit + PR
Commit with correct `COMMIT_CONV` scope. Open the PR; **replicate `PR_TEMPLATE` fully** in
the body; make the Security-review choice up front. Advance the row to `in-pr` and record the
PR number. Wait for CI; fix until green.

### 9. Code review
Advance the row to `in-review`. Run `CODE_REVIEW` on the diff. Implement viable findings;
decline others with a one-line rationale; **verify recs were applied**. Bounded to 2 rounds —
contested findings escalate to the human, do not loop. Commit fixes.

### 10. Security review (by route)
Run `SECURITY_REVIEW` per the routing in `loop.config.md` (the local-skill-vs-label choice and any
host-repo Git incantation are project specifics; this engine only fixes the gate's position and
that findings ≥ the project's confidence bar are addressed):
- A `.claude/`-only (tooling-only) change → the **local** review path.
- Otherwise, if a sensitive surface is touched → the **labeled** review path, applied ONLY now
  (dev-complete). Skip for docs/no-surface changes.

### 11. Merge
Read the run `mode` and `graduated-routes` from the `queue.md` header. The merge gate is the
**only** gate `mode` changes (step 5 is conditional in every mode). A row is **auto-merge-eligible**
only when ALL of these hold:
- `mode: escalation-only`, AND
- the row's Route is listed in the header's `graduated-routes` field, AND
- the change produces **no release-artifact bump**, or ≤ patch where `RELEASE_SCHEME` defines a
  version scheme — a `docs`/`chore` change, or any change in a project with no release cycle,
  produces no bump, which qualifies, AND
- the row is **not** `hold`, AND
- none of the always-escalate conditions apply: a `feat:`/breaking change, a risky/irreversible
  change, a touched security surface, or a contested review finding.

**Default-deny:** if route graduation or any always-escalate condition is uncertain, the row is
**not** auto-merge-eligible — fall back to the human merge gate.

If the row is **not** auto-merge-eligible — which includes *every* row under `mode: calibration`
(the default) and any `hold` row — STOP and ask the human before merging; never auto-merge.
**If the human holds the merge (now or in any later invocation),
WRITE the hold to the row before stopping** — set Status `hold` (record the reason in Notes) so
it persists across `/clear`; resume (step 0.3), step 1, and this gate all key on Status `hold` and
honor it until the human clears it (restoring the row's prior status). When the row **is**
auto-merge-eligible (or the human has approved), and CI + security are green AND the row is not
`hold`:
merge via `MERGE_METHOD` with an explicit `--subject` carrying the correct `COMMIT_CONV` scope,
`--delete-branch`. Confirm the issue closed.

### 12. Journal + stop
Append the iteration block to `progress.md`, including a `- Budget:` line (Ledger format →
progress.md):
`subagent-runs=<n>` · `gate-rounds=architect=<a>,code-review=<c>,ac-verify=<v>` ·
`wall-clock=<elapsed, includes gate-wait — not a cap input>` · `tokens=deferred` (computed
post-hoc from the loop's own JSONL by an out-of-band analyzer, not inside the skill; the named
slot keeps the line forward-stable).
Set the `queue.md` row to `done` (or `blocked`/`deferred` with reason); note newly-unblocked
issues. The ledger is gitignored — do NOT commit it (Ledger format → lifecycle). STOP. (Driver
re-invokes with fresh context for the next issue.)

### Escalation rubric (when unsure)
Scope/priority/requirements — including any plan whose value story lacks a credible user or a
checkable falsifier (step 3) — → `SCOPE_AGENT`, before implementing. Design/implementation →
`DESIGN_AGENT`. Escalate to the HUMAN only when those disagree/punt, ACs are unresolvable, an
action is destructive/irreversible, a review finding is contested, or the same step failed twice.

### Guardrails
One PR at a time (no stacked PRs). **Stuck = the same error SIGNATURE recurs** — grep the FULL
`progress.md` (not just the tail) for the signature: an identical CI failure, or the same
tool+args failing again — NOT merely re-entering a status (a legitimate `/clear`-resume
re-enters `implementing` and must not be flagged). On a genuine repeat: stop, escalate, mark
`blocked`, move on. Respect any iteration/budget cap (`iteration-cap:`/`subagent-cap:` in the
`queue.md` header): checked at iteration start (step 1) against the ledger — **advisory in manual
re-invoke (journaled + surfaced, not gating), halted by the driver**.

### Tool surface — and what you must NOT do
This skill intentionally runs with the full session toolset (no `allowed-tools` restriction):
an orchestrator needs Write/Edit, Bash(git+gh+tests), Agent (`SCOPE_AGENT`/`DESIGN_AGENT`/
AC-verifier), and the built-in review skills. With that power come hard limits — never force-push;
never bypass failing CI (no admin-merge, never merge red); only `--delete-branch` the PR's own
branch; never `git add` unrelated pre-existing working-tree changes; never edit the
user-global `SCOPE_AGENT`/`DESIGN_AGENT` definitions. The C1 append-only guard and the
human/merge gates are the enforced backstops; the rest of this list is your contract.

---

## Ledger format

Create `LEDGER_ROOT/<run-slug>/` (e.g. `.claude/loop/<run-slug>/`) containing three artifacts.
The orchestrator is the only writer except where noted.

### `queue.md` — work list (authoritative status)
Dependency-ordered. One row per issue. **`Route` and `Status` are separate columns** (a row
can be Route `research`, Status `blocked`). **Pipeline statuses** — advanced by the
orchestrator as the issue moves through the pipeline, so an interrupted run leaves a non-terminal
status resume keys on (see Resume): `queued → routed → planning → plan-approved → implementing →
in-pr → in-review`. **Terminal statuses** — the run converges when every row is terminal:
`done`; `deferred` (Route `stub-defer`); `blocked` (an unmet in-run dependency, or
`blocked: too-large` awaiting a split). **Two non-terminal, resting statuses** sit outside both
the pipeline and the terminal set: **`hold`** — a durable, human-set merge-hold that survives
`/clear`; and **`parked`** — a row whose work is gated on an **external event** (a release cut, a
dogfood window — *not* an in-run dependency), with the awaited condition in Notes as
`awaiting: <condition>`. Both retain their Route and are released only by the human (a `hold` by
clearing the hold at the merge gate; a `parked` row by explicit un-park, step 0.1 — which
flips it back to `routed`). While any `hold` **or `parked`** row remains the run is NOT complete
(Convergence distinguishes the resting `RUN PARKED` state from terminal `RUN COMPLETE`), but neither
blocks selecting other queued work (steps 0–1).

**Curated-subset invariant.** The queue built at init (see Initialization) is the authoritative
work set; `BACKLOG_SOURCE` membership may drift afterward, and that drift is **surfaced to the
human once, never auto-applied** — neither auto-added on join nor auto-ejected on leave (step-1
roster reconciliation). A corollary is a Notes discipline: **write a `parked`/`blocked` row's Notes
as the durable curation DECISION** (`awaiting: <external condition>`, `deliberately out of <run> at
init (curation)`, `kept: out-of-<run> roster (curation)`), **never the mutable live evidence** ("not
in roster", "no PR yet") — the latter is contradicted by a later live re-check and destabilizes
resume.

The header carries a `mode:` field that gates **the merge gate only** — it does
**not** change the plan gate, which is conditional in *every* mode (step 5: the
plan gate stops only on ambiguous ACs, risk/irreversibility, agent disagreement, or genuine
uncertainty — never merely because of `mode:`). The two modes:
- **`calibration`** (default) — the human approves **every** merge; the loop never auto-merges
  (step 11). Plan gate conditional.
- **`escalation-only`** — the human loosens the **merge gate per route**: a route the human has
  *graduated* auto-merges when CI + AC-verifier + review are green and the change produces no
  release-artifact bump (or ≤ patch where `RELEASE_SCHEME` defines one — a `docs`/`chore` change,
  or any change with no release cycle, qualifies). *Which* routes are currently graduated is a
  mutable human decision, recorded per-run in the `graduated-routes:` header and in the project
  decision log — never frozen into this mechanism definition (the lesson: graduation *state* is
  evidence, not a rule). The human merge gate is **retained** for every non-graduated route and,
  regardless of route, for any of: a `feat:`/breaking change, a risky/irreversible change, a touched
  security surface, a contested review finding, or a `hold` row — **and, by default-deny, whenever
  route graduation or any always-escalate condition is uncertain, fall back to the human merge
  gate.** Plan gate conditional (unchanged). Loosening to `escalation-only` presupposes the
  calibration prerequisites are met (these pinned mode semantics, plus per-iteration budget
  journaling — the `- Budget:` record and `iteration-cap:`/`subagent-cap:` fields below); it cannot
  run headless.

The set of graduated routes is recorded in a `graduated-routes:` header field beside `mode:`
(default `none`; e.g. `graduated-routes: docs, research`). Under `mode: calibration` it is inert.
*Which* routes graduate and the criteria for promoting one are out of scope here; this field only
gives the merge gate (step 11) a place to read the human's decision from.

The header also carries two **budget caps** (both default `none` = uncapped):
`iteration-cap:` (max **issues per run** — in this engine one "iteration" = one issue) and
`subagent-cap:` (max **subagent runs per iteration**). The orchestrator checks them at iteration
start (step 1) as a **retrospective circuit-breaker** against the ledger — it does not watch
its own spend mid-turn (that is why token/cost is deferred, below). Cumulative iterations are
counted as the **distinct issues at a terminal status** (`done`/`deferred`/`blocked`) in
`queue.md` — the authoritative status file — never by counting `progress.md` blocks, since a
`/clear`-resume re-enters an iteration and would double-count. `subagent-cap` is enforced by
reading the **prior** iteration's journaled `- Budget:` line: if it breached, halt before
starting the next. On breach the behavior is **advisory in manual re-invoke** (journal + surface
it and proceed — the human who invoked is the budget authority) and **halting under the driver**
(see Convergence). The caps bound `escalation-only`'s runaway-consumption risk; bad-merge risk is
already covered by the default-deny/always-escalate machinery above.

```markdown
# Loop run: <run-slug>
_mode: calibration_
_graduated-routes: none_
_iteration-cap: none_       # max issues per run; none = uncapped
_subagent-cap: none_        # max subagent runs per iteration; none = uncapped
_Last updated: <ISO8601 by orchestrator>_

| # | Issue | Route | Status | Depends on | PR | Notes |
|---|-------|-------|--------|-----------|----|----|
| 1 | #<a> precondition fix | code | done | — | #<pr> | precondition |
| 2 | #<b> probe | research | queued | — | — | first in epic #<epic> |
| 3 | #<c> follow-on | research | blocked | #<b> | — | needs #<b> findings |
| 4 | #<d> stub | stub-defer | deferred | — | — | not implementation-ready |
| 5 | #<e> post-release re-measure | code | parked | — | — | awaiting: <external condition> |
```

### `progress.md` — append-only journal (survives /clear + compaction)
The orchestrator APPENDS one block per iteration (and per gate decision). Never rewritten.
This is the audit trail and the resume anchor.

```markdown
## <ISO8601> — #<N> (research) — iteration start
- Selected: #<N> (highest-priority unblocked).
- Route: research (probe; no test-coverage gate).
- Plan: issue-<N>.plan.md written.
- Architect: skipped (research scaffolding, no shared-interface impact).
- Human gate: plan auto-approved (route=research, low ambiguity).
- Implemented: <path>; recorded findings in <path>.
- AC-verify: 3/3 acceptance criteria met.
- PR: #<pr> (chore scope). CI: green.
- Code-review: 0 findings. Security: n/a (no deps added).
- Budget: subagent-runs=3 · gate-rounds=architect=0,code-review=1,ac-verify=1 · wall-clock=18m · tokens=deferred
- Merged: squash #<pr>. Issue #<N> closed.
- Next: #<M> now unblocked.
```

The `- Budget:` line is the per-iteration cost record. Fields:
- **`subagent-runs`** — the proxy cost signal the orchestrator can count for free. Its blind spot:
  parent-thread token burn (a long implement step spawns no subagent yet can be the largest
  consumer) is invisible to run-count — which is precisely what the deferred `tokens` field
  eventually fixes.
- **`gate-rounds`** — architect / code-review / ac-verify round counts (feeds review-thrash
  detection downstream).
- **`wall-clock`** — elapsed time including human gate-wait; recorded for the dogfood corpus, **not
  a cap input** (an iteration that waited overnight for approval is not "expensive").
- **`tokens=deferred`** — a reserved, named slot. Per-iteration token/cost is computed **post-hoc
  from the loop's own JSONL by an out-of-band analyzer** (the loop JSONL is a first-class corpus),
  not inside the skill (the orchestrator can't cleanly slice its live session mid-turn). A future
  SDK driver backfills it via usage callbacks — keeping the slot named now makes that a backfill,
  not a format change.

### `issue-<N>.plan.md` — per-issue plan (architect-reviewed, human-approved)
```markdown
# Plan: #<N> — <title>
**Route:** <code|research|docs>  **Branch:** <BRANCH_FMT>

## Value framing (route-scaled — see step 3)
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
<which ARCHITECT_TRIGGERS fired, or "none">

## Risks / open questions for human
<empty if none>
```

### Lifecycle & commit policy
- **Init:** orchestrator creates the dir + `queue.md` from `BACKLOG_SOURCE` (see Initialization).
- **Per iteration:** update one `queue.md` row through its statuses; append `progress.md`;
  write/update `issue-<N>.plan.md`.
- **Commit policy — gitignore the ledger** (`LEDGER_ROOT/` is added to `.gitignore`). It is
  local working state: it survives `/clear`/compaction on disk, but is **never committed**.
  This is deliberate — committing it has no legal landing spot under a `main`-is-PR-only,
  no-stacked-PRs repo (folding ledger commits into an issue's squash-merged PR would pollute that
  PR's scope). Resolves the branch-protection collision.
- **Dogfood corpus harvest:** read the JSONL + `progress.md` from the working tree directly; if a
  versioned audit trail is later wanted, snapshot the ledger into a dedicated `docs:` PR on demand
  — do not stream per-iteration ledger commits.
- **No git/ledger divergence:** because the ledger is uncommitted, resume (see Resume) reconciles
  the on-disk ledger against *live* git/PR state (branch exists? PR open? CI status?), which
  is the source of truth — not a possibly-stale commit.

---

## Router — classification procedure
Set the row's **Route** (the semantic kind) and its **initial Status** *separately* — they are
distinct columns (Ledger format → queue.md), so a dependency-blocked research issue is Route
`research` / Status `blocked`, not Route `blocked`.

**Route**, from labels + body signals per `SOURCE_LAYOUT`, in order:
1. Issue body says explicitly "NOT implementation-ready" / is a stub (per `SOURCE_LAYOUT`'s
   stub-defer marker) → `stub-defer`.
2. The project's docs label + change confined to docs/markdown (per `SOURCE_LAYOUT`) → `docs`.
3. The project's research label or a `*-discovery` epic, throwaway scaffolding, "artifact under
   study is data" (per `SOURCE_LAYOUT`) → `research` (no test-coverage gate; placement outside the
   package; no runtime-dep leakage into the package).
4. Otherwise (a bug/enhancement touching the package source) → `code` (full pipeline).

**Initial Status:** Route `stub-defer` → `deferred` (terminal). Else if the row's work is gated on
an **external event** (a release cut, a dogfood window — not an in-run issue) → `parked` with Notes
`awaiting: <condition>` (non-terminal, resting; released only by explicit human un-park, step 0/1).
Else if any `Depends on` issue is not `done` → `blocked` (record the dep in Notes; the semantic
Route is retained so the row resumes as that route once the dependency clears, step 1). Else →
`routed`.

---

## AC-verifier
Default: **compose existing tools**, don't mint an agent.
1. After implementation, spawn a fresh subagent with ONLY: the issue's acceptance criteria
   (verbatim) + `git diff main...HEAD`. Prompt: *"For each acceptance criterion, state
   met/not-met with the file:line or test that satisfies it. Verify the diff actually does
   this; do not assume. Return a checklist + overall done/not-done."*
2. For behavior that needs runtime proof, also run `VERIFY` (runs the app).
3. `CODE_REVIEW` (step 9) provides the adversarial bug pass.
Promote to a dedicated `ac-verifier` agent only if the composed approach proves too loose.

---

## Initialization procedure (new run)
1. Derive `<run-slug>` from `BACKLOG_SOURCE`: milestone → the milestone name; label → the label
   (slugified); `TODO.md` → its basename. `mkdir -p LEDGER_ROOT/<run-slug>`.
2. Enumerate `BACKLOG_SOURCE` (e.g. `gh issue list --milestone <run> --state open --json
   number,title,labels` for a milestone; `--label <name>` for a label/epic source).
3. For each issue: determine route (Router) and dependencies (parse "Depends on"/"blocked by"
   refs in the body; respect epic ordering notes). An epic *tracker* issue is not a work row —
   carry it terminal (`deferred`) if the source enumerates it (the loop does not close epics).
4. Topologically order by dependency, then by `PRIORITY_LABELS` (tiebreak issue-number asc).
   Write `queue.md` with header `mode: calibration` **unless the human has already graduated routes
   for this project** — check the decision log and, if so, init `mode: escalation-only` + the
   graduated `graduated-routes:` instead, so a prior graduation persists across runs rather than
   silently resetting to calibration. Step 11 reads `mode`/`graduated-routes` to gate **the merge
   gate**, per route; it never affects the plan gate. Also set `iteration-cap: none` and
   `subagent-cap: none` (the human sets them when loosening).
5. Append an "init" block to `progress.md`. (Ledger is gitignored — not committed.)

---

## Resume after `/clear` or compaction
The next invocation's step-0 resume (step 3) reads `queue.md` + tail of `progress.md` and, finding
any *interrupted* row (non-terminal and NOT `queued`/`routed`/`hold`/`parked`), finishes it before
selecting new work. A `hold` **or `parked`** row is **excluded** — a `hold` is a deliberate, durable
human merge-hold and a `parked` row is gated on an external event (Ledger format → queue.md),
neither an interruption; leave them (a `hold` until the human clears it at step 11; a `parked` row
until explicit un-park at step 0.1) and neither blocks other work. A run resting under a `RUN
PARKED` sentinel is likewise **not** an interrupted row — step 0 short-circuits it on the cheap
parked path and never enters this resume scan (safe: a valid PARKED state has no non-terminal
pipeline row). The on-disk ledger row status is only a **coarse anchor** (which stage); the **live
git/PR state is the source of truth** for the details (the ledger is uncommitted): for an in-flight
row, check whether its branch exists, whether a PR is open (or already merged), and the PR's CI
status, and resume at the matching pipeline stage — git wins on any conflict with a stale status.
Stages 4/7/10 need no distinct status because the surrounding statuses bracket them: a
`plan-approved` row re-enters at implement (step 6), so the architect/human gates are NOT re-run.
The one external-side-effect stage is the architect (step 4) — it posts a comment to the issue —
so on the rare resume of a `planning` row, check for an existing architect comment and skip
re-invoking if present (do not double-post). AC-verify (step 7) is side-effect-free; security (step
10) re-labeling is a no-op. **Working-tree reconciliation:** if a crashed prior attempt left
uncommitted changes, inspect them before proceeding — keep and continue if they match the plan, or
`git restore`/stash if they're partial/unrelated. A resumed `implementing` row is NOT "stuck" (stuck
keys on a repeated error signature, not status re-entry — see Guardrails).

---

## Routing table

| Route | Pipeline differences |
|-------|----------------------|
| `code` | full pipeline, all gates |
| `research` | lighter plan; **no test-coverage gate**; architect optional; security only if deps added; place outside the package source |
| `docs` | skip architect + security; light review; `docs:` scope |
| `stub-defer` | do NOT implement; journal why; leave in backlog (Status `deferred`) |

`blocked` and `parked` are **Status overlays, not Routes**: a row keeps its semantic Route (`code`/
`research`/`docs`) while resting on an unmet in-run dependency (`blocked`) or an external event
(`parked`). Skip it; a `blocked` row returns to selection when its dependency closes (steps 1 /
Router), a `parked` row when the human un-parks it (step 0.1).

**Generic mechanical discipline:** set the squash `--subject` scope **explicitly** (per
`COMMIT_CONV`), never inherited from the PR title. (Host-repo mechanical specifics — e.g. the
`.claude/`-only security path and the `origin/HEAD` incantation — live in `loop.config.md`.)

---

## Gates, convergence & resting states

Gate table:

| Gate | Who | When | Output |
|------|-----|------|--------|
| Plan | orchestrator | every issue | `issue-<N>.plan.md` |
| Architect | `DESIGN_AGENT` | `ARCHITECT_TRIGGERS` or unsure | issue comment |
| Human (plan) | user | only if uncertain/irreversible | approve/redirect |
| AC-verify | fresh subagent (+`VERIFY`) | every code/research issue | done/not-done + gaps |
| Code review | `CODE_REVIEW` | every code issue | findings → fixes |
| Security | `SECURITY_REVIEW` (local or label) | by route | clean/findings |
| Merge | user (calibration / non-graduated route) → orchestrator (auto: graduated routes) | CI+security green | `MERGE_METHOD` |

**Convergence & the resting states.** When nothing is selectable, step 1 classifies the run
into one of four outcomes (tested in order: hold → parked → complete → pending) and appends a
`progress.md` **run-state sentinel**; step 0 reads the **most recent** sentinel by append
order (last-wins, since the log is append-only) and acts only on it:
- **`RUN COMPLETE — <run-slug>`** (terminal) — every row is terminal (`done`/`deferred`/`blocked`).
  Summarizes counts + any blocked/deferred items; the orchestrator stops and reports, and a later
  re-invocation short-circuits without re-scanning.
- **`RUN PARKED — awaiting <condition>`** (resting, **non-terminal**) — all *workable* rows are
  terminal but ≥1 `parked` row awaits an external event (a release cut, a dogfood window). A
  re-invocation short-circuits (step 0): it runs only the cheap roster reconciliation
  + a `queue.md` selectability re-derivation, then re-reports parked **without** the expensive
  per-row live reconcile / resume — until the human explicitly releases a **named** condition
  (which flips only the `parked` rows awaiting *that* condition back to `routed`, appends a
  superseding `RUN RESUMED` sentinel, and leaves rows gated on other conditions parked) or a
  pulled-in joiner / cleared dep makes work selectable again. This is what stops a release-gated run
  from re-reaching a *new* conclusion on every re-fire (the "converged-pending-release"
  instability). Distinct from an *interrupted* row needing resume (see Resume): a valid PARKED state
  has no non-terminal pipeline row, so resume is safely skipped.
- **held / pending** (no sentinel) — a `hold` row needs the human now, or a row is still `blocked`
  on an open in-run dependency; the orchestrator reports and stops without a sentinel (the run is
  not complete and not cleanly parked).

**Guardrails:** iteration/budget caps live in the `queue.md` header (`iteration-cap:`/
`subagent-cap:`) and are checked at iteration start against the ledger — **advisory in manual
re-invoke (journaled + surfaced, not gating — the human who invoked is the budget authority),
halted by the driver**; one PR at a time; stuck-detection (repeated error signature) → escalate.
The C1 append-only guard hook protects append-only logs once the loop commits its own work.
