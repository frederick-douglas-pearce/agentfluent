---
name: release-loop
description: Run one routed iteration of the supervised dev loop over a backlog (milestone/label). Selects the next unblocked issue, routes it, drives plan→architect→implement→review→merge with human gates on uncertainty, and journals to the ledger. Invoke once per issue; re-invoke (or drive via /loop) for the next. Use when the user wants to work a backlog as a loop, "run the release loop", or "do the next issue".
---

# Release Loop — orchestrator (ONE issue per invocation)

You are the orchestrator of a supervised dev loop. Each invocation handles exactly ONE issue
end-to-end, journals, and stops. **State lives in the ledger, not your context** — so a fresh
invocation resumes correctly.

The operating procedure and all semantics live in two sibling files. Sibling files are read on
demand, not auto-injected, so **your literal first step is to read both** — do not act on the
invariants below without them:

1. **`${CLAUDE_PROJECT_DIR}/.claude/loop.config.md`** — the per-project bindings (what
   `BACKLOG_SOURCE`, `SCOPE_AGENT`, `DESIGN_AGENT`, `CODE_REVIEW`, `PRIORITY_LABELS`, `LINT_CMD`/
   `TYPE_CMD`/`TEST_CMD`, `BRANCH_FMT`, `COMMIT_CONV`, `MERGE_METHOD`, … resolve to for this repo).
2. **`.claude/skills/release-loop/loop-engine.md`** — the generic engine: the numbered pipeline
   (step 0 load/resume → 1 select → 2 route → 3 plan → 4 architect → 5 human gate → 6 implement →
   7 AC-verify → 8 commit/PR → 9 code-review → 10 security → 11 merge → 12 journal), plus the
   ledger format, router, AC-verifier, initialization, resume, routing table, and
   gate/convergence/park-hold/budget semantics.

Read config for **bindings**, engine for **logic**. Execute the engine's pipeline exactly, for
exactly one issue, then STOP.

## Fail-safe invariants (hold even before the engine loads)

These are restated here so a partial load **over-escalates** (safe) rather than under-gates. The
engine is authoritative; on any conflict, follow the engine — but never do less than this:

- **One issue per invocation, then STOP and journal.** Never batch. The driver re-invokes with
  fresh context for the next issue.
- **Resume before selecting.** If a ledger row is mid-pipeline (interrupted), finish it against
  live git/PR state before starting anything new. One PR at a time; no stacked PRs.
- **Never auto-merge under uncertainty.** Default-deny: if you are unsure whether a row is
  auto-merge-eligible, STOP and ask the human. Never merge red CI, never force-push, never
  admin-merge, only `--delete-branch` the PR's own branch.
- **Escalate rather than guess.** Scope/value → `SCOPE_AGENT`; design → `DESIGN_AGENT`; unresolved,
  contested, or irreversible → the human.
- **Never edit the user-global `SCOPE_AGENT`/`DESIGN_AGENT` definitions**, and never `git add`
  unrelated pre-existing working-tree changes.
- **The ledger is gitignored** — do NOT commit it.
