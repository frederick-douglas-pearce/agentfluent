# PRD — Research Pipeline Phase 3: architect-first dispatch automation

**Status:** design draft, awaiting human review
**Authors:** Claude (parent thread) — empirical observations from manual C-001 / C-004 / C-006 dispatches
**Date:** 2026-05-22

## Background

The research pipeline (specs: `decisions.md` D028+; queue file: `.claude/specs/research/anthropic-feature-watch.md`) has four stages:

1. **Scout** — `anthropic-research` subagent finds candidates and appends to the queue.
2. **Verify** — `candidate-verifier` subagent premise-checks each candidate against the codebase + decisions log + GitHub issues.
3. **Approve** — human writes a `Decision (YYYY-MM-DD):` line under each Verification block.
4. **Dispatch** — `promote-candidates` skill (Phase 1+2) reads the queue and routes each candidate:
   - `pm-first` → invokes `pm` subagent ✅ implemented
   - `dismiss-as-duplicate` → comments on overlapping issue ✅ implemented
   - `needs-evidence` → files blocked-on-evidence stub ✅ implemented
   - `architect-first` → ❌ **Phase 3: not implemented (this PRD)**

Phase 3 closes the gap by automating the architect-first dispatch path that we've now run manually for 3 candidates (C-001 → #423, C-004 → #427, C-006 → #431+#433).

## Empirical basis — what the manual flow looked like

Across 3 manual dispatches, the architect-first flow consisted of:

1. **File stub epic** via `mcp__github__create_issue`. Body templates:
   - Source candidate ref (queue line range)
   - Upstream signal (CHANGELOG entry / postmortem URL)
   - Summary
   - AgentFluent relevance
   - Verification status (premise + dedup + route from verifier)
   - "What architect review needs to decide" — list of design questions
   - Decision line + status: stub
   - In-flight context (only when verifier flagged overlaps — C-004 case)
2. **Invoke `architect` via Agent.** Prompt includes:
   - Pointer to stub epic
   - Code touchpoints to read
   - In-flight issue numbers to read (when applicable)
   - Expected comment structure (`## Architect design review (YYYY-MM-DD)` + 6–12 numbered design questions)
   - Format: post a single comment via `mcp__github__add_issue_comment`
3. **Human review of architect comment** — pause point. Human reads architect's design and either:
   - Says "go pm" (default path)
   - Restructures: e.g., "architect recommended splitting into two epics — rename stub, file second stub, go pm on both" (C-006 case)
   - Defers: e.g., "block this on #X first before pm scopes" (no instances yet, but the policy hook exists)
4. **Invoke `pm` via Agent.** Prompt includes:
   - Pointer to stub epic
   - How to read architect comment (`gh issue view <n> --comments` since the MCP comment-list tool isn't always available)
   - Constraints from architect's design (deferrals, splits)
   - Open questions architect flagged for PM/verifier
5. **Annotate the queue.** Add Promotion block, flip Status to `promoted`.

Across the 3 dispatches:
- 0 PRDs were written (architect comment served as design doc in all cases)
- 1 case required structural restructuring (C-006 split into Track A/Track B)
- 2 cases generated verifier-bounce questions (C-001 `duration_ms` scope, C-006 `cache_read_input_tokens` observable)
- 1 case had in-flight overlapping issues (C-004 → #163/#171)
- 1 collateral event: external bot PR landed on the freshly scoped work (PR #432); now tracked separately in #434

## Design goals

1. **Match the manual flow's shape.** Don't redesign the workflow — automate the steps that are mechanical, preserve the human gate where judgment matters.
2. **Reuse existing infrastructure.** The `promote-candidates` skill already handles 3 routes; architect-first becomes the 4th.
3. **Keep architect-first idempotent.** Re-running the skill on a candidate that's already partway through (stub filed, architect commented, but pm not yet invoked) should be safe.
4. **Surface architect's structural recommendations to the human.** Splitting / merging / deferring decisions are not safe to automate; they need human judgment.

## Proposed contract

### Phase 3.A — extend the `promote-candidates` skill to handle `architect-first`

The existing skill already gates on the Decision line and routes per the verifier's `Suggested route` field. Add a fourth branch for `architect-first` that does **only the deterministic prefix** of the manual flow:

1. **File the stub epic** (`mcp__github__create_issue` with templated body).
2. **Invoke the architect subagent** via Agent.
3. **Capture the architect comment URL** and return it in the run summary.
4. **Annotate the queue** with a partial Promotion block:
   ```
   **Promotion (YYYY-MM-DD):** architect-first → stub epic #NNN; architect design comment on #NNN — awaiting human review before pm dispatch.
   ```
5. **Flip Status** to `architect-reviewed` (new status — see vocabulary section below).

The skill then **stops**. The candidate sits in `architect-reviewed` state until the human re-invokes the skill with explicit pm-dispatch intent (see Phase 3.B).

### Phase 3.B — pm-dispatch-after-architect

A second mode of the skill (`/promote-candidates pm-after-architect <C-NNN>` or similar) takes the architect-reviewed candidate and:

1. **Reads the architect comment** via `gh issue view`.
2. **Invokes pm** via Agent with the same prompt template the manual flow used.
3. **Captures pm's filed issue numbers + decision-log additions.**
4. **Updates the Promotion block** to the complete form:
   ```
   **Promotion (YYYY-MM-DD):** architect-first → stub epic #NNN; architect design comment; pm filed stories #NNN, #NNN, #NNN.
   ```
5. **Flips Status** to `promoted`.

### Phase 3.C — restructure mode (optional, low priority)

For the C-006 split case, the human currently does the restructure manually (rename stub, file second stub). The skill could add a `restructure-split` mode that takes `<original stub #> <Track A title> <Track B title>` and does the rename + new-stub-file in one step. **Recommend deferring this** until a second split occurs — we have one data point.

## Key design decisions (need human input)

### D1. Skill extension vs. new skill

**Option A:** Extend `promote-candidates` to handle architect-first as a 4th route. Pros: single entry point for all routes. Cons: skill becomes larger; the architect-first path needs the two-phase shape (stub-and-architect, then pm-after-architect) which is awkward inside the existing dispatch-per-route loop.

**Option B:** New skill `dispatch-architect-first` (or similar) dedicated to architect-first. Pros: cleaner two-phase contract; lets the existing skill remain a one-shot dispatcher. Cons: two skills to maintain; some logic duplication (queue parsing, candidate iteration, queue annotation).

**Recommendation:** Option A with a `mode` argument that distinguishes the two phases. Single skill, two modes:
- `mode=architect-first-init` (does stub + architect comment, stops)
- `mode=architect-first-pm` (does pm dispatch, completes)

### D2. Where the human pause happens

The manual flow paused after architect's comment for human review (we did this in all 3 dispatches before invoking pm). Two ways to encode this:

**Option A: Skill stops after architect comment.** Status flips to `architect-reviewed`. Human reads the comment, then explicitly re-invokes the skill in pm-after-architect mode. **Forces** human review.

**Option B: Skill auto-continues to pm by default; human can opt out.** Args like `--pause-after-architect` to stop after architect. **Defaults to no pause**, which is a footgun if architect recommends a split.

**Recommendation:** Option A. The pause is the safety mechanism. The cost of two skill invocations is small; the cost of pm scoping off a malformed architect comment is large.

### D3. New status vocabulary

Current statuses: `queued` (scout) → `verified`/`needs-evidence`/`duplicate` (verifier) → `promoted`/`dismissed` (dispatch).

Architect-first introduces an intermediate state: stub filed + architect commented, but pm not yet dispatched. Options:

**Option A:** Add `architect-reviewed`. Three-state architect-first flow: `verified` → `architect-reviewed` → `promoted`. Clear, explicit.

**Option B:** Keep `verified` until pm-dispatch completes, encode "architect comment exists" via Promotion block content. Two-state flow but status doesn't reflect intermediate progress. Harder to filter.

**Recommendation:** Option A. Filterable status is worth the schema cost.

### D4. Structural recommendation handling

When architect says "split into two epics," "fold this into existing in-flight issue," or "defer pending #X," the skill cannot safely auto-execute. Options:

**Option A: Detect-and-stop.** Skill scans architect comment for trigger phrases ("split", "fold", "defer") and stops with a clear "human action required" output even if `mode=architect-first-pm` was requested.

**Option B: Always stop after architect for human review.** No detection needed; the pause point is universal.

**Option C: Capture architect's structural recommendation as a structured field** in a new "Architect Recommendation" line in the Promotion block, and require human to either accept or override via a second Decision-style line before pm-dispatch mode runs.

**Recommendation:** Option B (the universal pause from D2 above handles this). If we ever see >5 dispatches where architect's structural call was trivial enough to auto-continue, revisit with Option A.

### D5. Stub epic body template

The 3 manual dispatches converged on a template. Embed it in the skill or keep it free-form?

**Option A:** Hard-coded template in the skill body — fields populated from candidate Verification block.
**Option B:** Read a template file from `.claude/specs/research/templates/` so non-skill consumers can use it too.

**Recommendation:** Option A for now. If we ever build a second dispatch path that needs the same template, externalize.

### D6. Architect prompt template

The 3 manual architect invocations had varying-quality prompts (the C-006 prompt was the cleanest because it explicitly said "first call is one-epic-or-two"). Hard-code architect prompt structure or keep free-form?

**Option A:** Hard-coded prompt template in the skill, parameterized by candidate body, code touchpoints from the Verification block, and any in-flight issue numbers from the Dedup field.
**Option B:** Free-form — let the skill construct the prompt ad-hoc.

**Recommendation:** Option A. Template includes:
- Inputs to read (stub body, source candidate range, code touchpoints, in-flight issues)
- Required comment header (`## Architect design review (YYYY-MM-DD)`)
- Required sections in the comment (location, mechanism, scope, output, fixtures, risks)
- What architect must NOT do (modify files, file issues, fold scope decisions silently)

## Open questions

1. **Should `architect-first-init` re-invocation be safe (idempotent)?** If the stub already exists for a candidate (e.g., the candidate's Promotion block has `→ stub #NNN`), should the skill skip stub-filing and re-invoke architect, or stop with "already in progress"?
2. **Should the architect's "open questions for verifier" be auto-detected** and bounced to verifier as a separate dispatch route? Two of three manual dispatches generated verifier bounces. The detection is structural (architect comment has a "Open questions for verifier" section); the skill could grep for it and emit a follow-up TODO.
3. **Should the pm prompt include the verifier-bounce status?** Currently the pm prompt says "Open question for verifier should resolve before #NNN ships, non-blocking for scoping." This is hard-coded per dispatch. Could be templated if it recurs.

## Out of scope

- Auto-handling of structural recommendations (split, fold, defer) — universal human pause covers this.
- Restructure-split mode (Phase 3.C) — defer until 2nd split occurs.
- Bot PR detection / awaiting-claim labels — tracked separately on #434.
- Phase 4: closing the loop (when a story implementation lands, AgentFluent's own self-analysis surfaces the resulting behavior change). Not pipeline-scope.

## Acceptance criteria for Phase 3 implementation

- `/promote-candidates architect-first-init <C-NNN>` files stub, invokes architect, annotates queue, flips Status to `architect-reviewed`. Idempotent: re-running doesn't double-file.
- `/promote-candidates architect-first-pm <C-NNN>` invokes pm with architect comment context, captures story numbers, updates Promotion block, flips Status to `promoted`.
- Both modes work end-to-end against a real candidate without parent-thread babysitting.
- New status `architect-reviewed` documented in the queue file schema header.
- Skill's `allowed-tools` extended to include `mcp__github__update_issue` (for any rename operations) and `mcp__github__get_issue_comments` (so it can read architect's comment without falling back to Bash).

## Recommended next steps after this PRD is approved

1. **Human review of D1–D6 design decisions.** This PRD captures recommended-and-rationale for each; user confirms or overrides.
2. **Open questions discussion** — particularly whether verifier-bounce auto-detection is worth building now or deferred.
3. **PM agent scopes stories** to implement Phase 3 per the approved design.
4. **Architect reviews the implementation plan** before coding starts (meta-dogfood: this is itself a pm-first candidate, not architect-first, since the design is now spec'd).
