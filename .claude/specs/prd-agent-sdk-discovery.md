# PRD: Agent SDK Session Data Discovery

**Status:** Complete — epic #517 closed 2026-07-02 (all six children merged; findings in `agent-sdk-session-format-findings.md`)
**Date:** 2026-06-18
**Author:** PM Agent
**Type:** Discovery / research epic (deliverable is knowledge, not a shipped feature)
**Governing decision:** D013 in `decisions.md` (main-session model-routing scope)
**Primary downstream consumer:** #112 (model-routing diagnostics for main Agent SDK session)
**Findings:** `.claude/specs/agent-sdk-session-format-findings.md` (the durable S4 deliverable)

---

## 1. Problem

AgentFluent's stated primary target is developers building agents with the
**Claude Agent SDK** (Python and TS). Its analysis pipeline, however, is
validated almost entirely against **Claude Code** JSONL sessions. We have never
empirically inspected what session data the Agent SDK actually produces.

We do not know:

- **Where** SDK sessions are written -- `~/.claude/projects/` or somewhere else
  entirely (project-local dir, OS temp, a configurable path).
- **How** SDK sessions are structured -- whether they share the Claude Code
  JSONL schema documented in `CLAUDE.md` (the `type: "assistant"` /
  `type: "user"` + `toolUseResult` shape) or diverge.
- **Whether** an SDK session carries a distinguishing marker that lets us tell
  it apart from a Claude Code interactive session. This is the load-bearing
  question for D013: AgentFluent owns SDK main sessions (the main session *is*
  the configured agent) but explicitly does NOT own Claude Code interactive
  main sessions (human-driven, CodeFluent's territory). Without a reliable
  discriminator, AgentFluent cannot safely apply main-session diagnostics to
  the right sessions.
- **How** the main-session model / options metadata
  (`ClaudeAgentOptions.model` and friends) is represented in the trace, if at
  all.

This is a credibility gap: the tool's headline audience runs a code path the
tool has never seen. Every SDK-dependent feature on the roadmap (starting with
#112) is blocked on this unknown.

## 2. Why this is a discovery epic, not a feature epic

The deliverable is **knowledge + sample data**, not a shipped diagnostic. The
work answers questions; it does not implement parser changes or new signals.
Acceptance criteria therefore center on:

- A documented findings report answering #112's three open questions.
- A corpus of real SDK-generated session files captured for inspection.
- Anonymized sample fixtures committed for downstream test use (if feasible).
- An enumerated list of where the current parser's assumptions hold and where
  they break for SDK data.

Downstream feature work -- implementing #112, building an SDK-session
discriminator into the parser, adding SDK main-session model routing -- stays
**out of scope** for this epic. Those are sequenced after findings land and are
re-scoped (likely in a future release pass) with real data in hand.

## 3. Goals

1. Produce a minimal but real Agent SDK agent (Python) that exercises
   representative behavior to generate realistic session data.
2. Run that agent across a few configurations to produce an SDK session corpus
   for inspection.
3. Systematically compare SDK session data against the documented Claude Code
   format and produce a written findings doc + anonymized fixtures.
4. Answer #112's three blocking open questions definitively enough to unblock
   it and to revisit its draft acceptance criteria.

## 4. Non-Goals

- Implementing #112 (SDK main-session model-routing diagnostics) -- this epic
  unblocks it; it does not build it.
- Modifying the production parser to detect or special-case SDK sessions --
  parser-assumption *gaps* are documented here; the *fix* is a downstream story.
- Shipping any new CLI surface, flag, or diagnostic signal.
- TypeScript SDK agent (D001: Python-only). The TS SDK may produce a different
  trace shape; that is a separate, later question. Note it if cheaply
  observable, but do not build a TS agent.
- AST/source parsing of `AgentDefinition` / `ClaudeAgentOptions` from agent
  source code (D004 deferral) -- this epic inspects *runtime session output*,
  not config source.
- Contributing SDK-format findings upstream to the `claude-code-sessions`
  reference repo. Worth considering once findings stabilize, but explicitly not
  scoped as a story here.

## 5. In Scope -- Stories

Dependency-ordered. Story 3 depends on Stories 1 and 2.

| Story | Title | Type | Deps |
|-------|-------|------|------|
| S1 | Build a minimal Agent SDK agent (Python) for data generation | research | None |
| S2 | Run the SDK agent across configurations to generate a session corpus | research | S1 |
| S3 | Inspect & diff SDK vs Claude Code session data | research | S1, S2 |
| S4 | Write SDK-format findings doc + anonymized fixtures | documentation | S3 |

S3 and S4 are split deliberately: S3 is the empirical inspection (capture the
raw observations), S4 is the synthesis (the durable findings artifact + reusable
fixtures + the explicit #112 unblock). Splitting keeps the inspection honest
(record what is seen) separate from the writeup (decide what it means), and lets
S4's findings doc cite S3's raw observations.

## 6. Verifiability -- what "we understand the SDK format" means

The epic is done when a reader of the findings doc can answer, with evidence
from captured sessions:

1. Where SDK sessions are written on disk.
2. Whether and how an SDK session is distinguishable from a Claude Code
   interactive session (the D013 discriminator).
3. The structure of the SDK main-session model/options metadata.
4. A point-by-point statement of where the current parser's assumptions
   (per the `CLAUDE.md` JSONL section) hold vs. break for SDK data.

Plus: an SDK session corpus exists, and anonymized fixtures are committed (or a
documented reason why anonymization was infeasible).

## 7. Open questions / assumptions

- **Anonymization feasibility (S4).** Whether SDK session content can be safely
  anonymized into committable fixtures depends on what the SDK writes (prompts,
  tool I/O may contain sensitive strings). S4's AC allows "fixtures committed
  OR a documented reason they could not be." Flag to human if the corpus turns
  out to be hard to scrub.
- **Discriminator reliability.** It is possible the SDK writes sessions
  indistinguishable from Claude Code interactive sessions. If so, that is itself
  a critical finding (it would force #112 to rely on heuristics or a
  user-supplied scope flag rather than an intrinsic marker). The epic surfaces
  this rather than assuming a clean marker exists.
- **SDK version pinning.** Findings should record the SDK version used, since
  the format may evolve (mirroring the `CLAUDE.md` "Format as of 2026-04"
  caveat). Assumed: capture the version in the findings doc.
- **Milestone.** Intentionally unmilestoned per human instruction -- a separate
  v0.10 scoping pass will decide placement.
