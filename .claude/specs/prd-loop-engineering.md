# Loop Engineering Harness — Implementation Spec

**Status:** Draft (2026-06-29) · **Owner:** Fred + Claude Code · **Pilot:** v0.10.0

> A generalized, semi-autonomous "loop engineering" harness that runs a project's dev
> workflow (plan → architect → implement → review → merge) as a loop over a backlog, with
> human gates only where the orchestrator is genuinely unsure.
>
> **Split as of #612 — this document is now the design rationale (the "why"), not the
> operative artifact.** The build-ready procedure, parameters, and semantics were extracted
> into three files, which are the single source of truth at runtime:
> - **`.claude/skills/release-loop/loop-engine.md`** — the generic engine: the numbered
>   pipeline, ledger format, router, AC-verifier, initialization, resume, routing table, and
>   gate/convergence/park-hold/budget semantics (former §6, §7.1, §7.3–§7.6, §8, §9).
> - **`.claude/loop.config.md`** — the per-project bindings: the Project Parameters table,
>   `ARCHITECT_TRIGGERS`, `SOURCE_LAYOUT`, and the host-repo security specifics (former §4.0,
>   §7.2, §7.3 signals, §7.1 step 10).
> - **`.claude/skills/release-loop/SKILL.md`** — the thin `/release-loop` entry point that
>   reads the config for bindings and the engine for logic.
>
> Sections **4, 6, 7, 8, 9** below are retained as **pointers** to those files (their content
> moved; keeping it here too would re-introduce the drift this split removed). Sections 1–3, 5,
> and 10–15 remain the rationale, calibration history, and roadmap. **Built-in tooling:**
> `/code-review`, `/security-review`, `/verify`, `/simplify`, `/review` are **Claude Code
> built-in skills** (not files to build); `/code-review` and `/security-review` were verified
> working in the 2026-06-29 session.

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

**Moved to `.claude/loop.config.md`** (as of #612). The Project Parameters table (former §4.0),
the workflow conventions (former §4.1), the design-shaping hard constraints (former §4.2), the
commit-scope rule (former §4.3), and the porting checklist (former §4.4) all live there now — it
is the single per-project surface a porting project edits. The generic engine
(`loop-engine.md`) references every parameter by name; `loop.config.md` binds each name to this
repo's value.

**Porting framing (unchanged in spirit):** a non-Python / non-`src/` / non-GitHub project edits
**only** `loop.config.md` — the four config sections (parameters, `ARCHITECT_TRIGGERS`,
`SOURCE_LAYOUT`, security routing) — never the engine. The former §4.4 "ship the spec with the
skill" step is obsolete: the runtime artifacts are now the skill + engine + config, and this spec
is no longer read at loop time.

---

## 5. Components to build

| # | Component | Path | Status |
|---|-----------|------|--------|
| C1 | Append-only guard hook | `.claude/hooks/guard_append_only.py` | **Done** (#500/PR #550) |
| C2 | State ledger convention | `.claude/loop/<run>/` | **Done** — format in `loop-engine.md` → Ledger format |
| C3 | `/release-loop` orchestrator skill | `.claude/skills/release-loop/{SKILL.md,loop-engine.md}` + `.claude/loop.config.md` | **Done** — thin skill + generic engine + config (#612) |
| C4 | Router (issue→route) | folded into C3 | **Done** — `loop-engine.md` → Router |
| C5 | AC-verifier | composed `CODE_REVIEW`+`VERIFY`+checklist prompt | **Done** — `loop-engine.md` → AC-verifier |

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

**Moved to `loop-engine.md` → Ledger format** (as of #612). The three-artifact ledger
(`queue.md` work list + status vocabulary; `progress.md` append-only journal + `- Budget:` line;
`issue-<N>.plan.md` template), the `mode`/`graduated-routes` merge-gate machinery, the budget
caps, and the gitignore-the-ledger lifecycle/commit policy all live there now. `LEDGER_ROOT` binds
to `.claude/loop/` in `loop.config.md`.

The C2 component still exists (the ledger is real, uncommitted working state under
`LEDGER_ROOT/<run-slug>/`); only its *definition* moved into the engine so the format has one
source of truth.

---

## 7. C3 — `/release-loop` orchestrator skill

**Moved to `.claude/skills/release-loop/SKILL.md` (thin entry) + `loop-engine.md` (the
procedure)** (as of #612). Previously this section embedded a byte-identical *mirror* of the live
skill — the drift that the split removes. There is now one copy of the procedure, in
`loop-engine.md`; `SKILL.md` is the thin `/release-loop` entry that reads the config for bindings
and the engine for logic; the CI guard (`tests/unit/test_loop_skill_drift.py`) no longer polices a
mirror but instead pins the load-bearing semantics in the engine and the engine's genericity.

The former subsections map as follows:
- **§7.1 skill body** → `loop-engine.md` → The pipeline (steps 0–12) + Escalation / Guardrails /
  Tool surface.
- **§7.2 architect triggers** (`ARCHITECT_TRIGGERS`) → `loop.config.md` (project-specific).
- **§7.3 router** → `loop-engine.md` → Router (procedure shape); the project signals
  (`SOURCE_LAYOUT`) → `loop.config.md`.
- **§7.4 AC-verifier** → `loop-engine.md` → AC-verifier (compose `CODE_REVIEW` + `VERIFY` + a
  fresh checklist subagent; no dedicated agent).
- **§7.5 initialization** → `loop-engine.md` → Initialization procedure.
- **§7.6 resume** → `loop-engine.md` → Resume after `/clear` or compaction.
- **§7.1 step 10 security routing** (the `.claude/`-only-vs-label choice + `git remote set-head`
  GitHub-ism) → `loop.config.md` → Security routing.

---

## 8. Routing rules & mechanical rules

**Moved to `loop-engine.md` → Routing table** (as of #612): the per-route pipeline differences,
the `blocked`/`parked` Status-overlay-not-Route distinction, and the generic squash-`--subject`
discipline. The host-repo mechanical specifics (the `.claude/`-only-vs-label security path, the
`origin/HEAD` incantation) live in `loop.config.md` → Security routing. Both mechanical rules were
learned in the #500 pilot (see §11).

---

## 9. Gates, escalation, convergence, guardrails

**Moved to `loop-engine.md` → Gates, convergence & resting states** (as of #612): the gate table,
the four-outcome convergence classifier (hold → parked → complete → pending), the `RUN COMPLETE` /
`RUN PARKED` / `RUN RESUMED` sentinel semantics, and the guardrails (budget caps, one-PR-at-a-time,
stuck-detection, the C1 guard hook). The escalation rubric lives in `loop-engine.md` → Escalation
rubric. §2's research principles above are the "why" behind these gates.

---

## 10. Build checklist (ordered — a fresh agent follows this)

1. **C1 guard hook** — done (#500). Verify present + wired; extend `APPEND_ONLY_FILES` if the
   project needs more protected logs.
2. **C2 ledger** — add `LEDGER_ROOT/` (`.claude/loop/`) to `.gitignore`; create the dir and
   write the three templates (`loop-engine.md` → Ledger format) as the run's seed (uncommitted).
3. **C3 skill** — the thin `SKILL.md` (entry) + generic `loop-engine.md` (procedure) + per-project
   `loop.config.md` (bindings) already exist (#612). Porting to a new project edits **only**
   `loop.config.md`.
4. **C4 router** — embodied in `loop-engine.md` → Router (signals in `loop.config.md`); no separate file.
5. **C5 AC-verifier** — embodied in `loop-engine.md` → AC-verifier; compose existing tools.
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
