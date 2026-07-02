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
1. Identify the active run (most recent `LEDGER_ROOT/<run>/`). If it already carries a
   `RUN COMPLETE` sentinel (§9), report done and STOP — do not re-scan. If no run exists, ask
   the user which milestone/label to run, then INITIALIZE per §7.5 of the spec.
2. Read `queue.md` (note its `mode:` / `graduated-routes:` header and any `hold` rows) and the tail of `progress.md`.
3. **Resume before selecting (spec §7.6).** If any row sits in an *interrupted* status —
   non-terminal and NOT `queued`/`routed`/`hold` (i.e. `planning`/`plan-approved`/
   `implementing`/`in-pr`/`in-review`) — a prior iteration was cut off. Reconcile it against
   LIVE git/PR state as the source of truth — branch exists? PR open? already merged? CI
   status? — plus the working tree (status is only a coarse anchor; git wins on conflict),
   then re-enter the pipeline at the matching stage and FINISH that issue BEFORE selecting a
   new one. This is what makes "one PR at a time" hold across `/clear`/compaction. A `hold`
   row is NOT an interruption: skip it here, leave it held — it stays parked until the human
   releases the hold and does not block working other issues.

## 1. Select
**Budget cap (iteration start, retrospective).** Read `iteration-cap:` / `subagent-cap:` from the
`queue.md` header (both default `none` = uncapped). Cumulative iterations = the count of **distinct
issues at a terminal status** (`done`/`deferred`/`blocked`) in `queue.md` — never count
`progress.md` blocks (a `/clear`-resume re-enters an iteration and double-counts). Breach = that
count ≥ `iteration-cap`, OR the **prior** iteration's journaled `- Budget:` line (§12) shows
`subagent-runs` ≥ `subagent-cap`. On breach: **manual re-invoke is advisory** — journal + surface
it and proceed (the human who invoked is the budget authority); **the driver halts.** Inert while
both caps are `none`.

A row is **selectable** if its status is `queued`/`routed`, OR it is `blocked` on an unmet
dependency that has SINCE cleared (all its `Depends on` issues are now `done` — re-route it via
§2; this does NOT apply to a `blocked: too-large` park, which waits on a split). Among
selectable rows pick by `PRIORITY_LABELS` order, tiebreak issue-number ascending. If none are
selectable:
- If EVERY row is terminal (`done`/`deferred`/`blocked`), append the §9 `RUN COMPLETE —
  <run-slug>` sentinel to `progress.md` (counts + any blocked/deferred items) and STOP
  (convergence).
- Else if the only non-terminal rows are `hold`, report "<n> held — awaiting human
  merge-release" and STOP **without** the sentinel (the run is not complete).
- Else (rows still blocked on open in-run dependencies) report what's pending and STOP
  without the sentinel.
**Size guard:** before entering the pipeline, estimate scope from the issue body — if it
plausibly touches many files or spans multiple unrelated acceptance-criteria clusters (won't
fit one context window), mark it `blocked: too-large`, escalate to SCOPE_AGENT to split, and
go back to select. Aggressively offload reading/analysis to subagents (architect, AC-verifier)
within an iteration to conserve the parent's context.

## 2. Triage / route (if not already routed)
Run §7.3 to set the row's **Route** (`code`/`research`/`docs`/`stub-defer`) and its **initial
Status** (Route and Status are distinct — §6.1): `stub-defer` → Status `deferred` (terminal);
an unmet dependency → Status `blocked` (parked; record the dep, or `too-large`, in Notes — the
Route is retained so the row resumes as that route when the dependency clears, §1); otherwise →
Status `routed`. If the Status is `deferred` or `blocked`, journal why and go back to §1 — do
not implement.

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

**Source-fidelity check (candidates from `anthropic-feature-watch`).** If the issue traces to a
research-scout candidate citing an external article/postmortem, confirm the source actually
supports the generalization the issue makes — **locus** (does the incident occur on the surface
this feature inspects?), **evidence base** (n, scope, whether the source itself generalizes),
and **current relevance** (already fixed upstream? version-specific?). An issue that extrapolates
past what its source establishes is misdirected regardless of implementation quality. (The #437
lesson — decision D046.)

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
