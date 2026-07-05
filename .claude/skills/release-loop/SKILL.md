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
