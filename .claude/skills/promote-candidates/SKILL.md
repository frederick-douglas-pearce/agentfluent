---
name: promote-candidates
description: Dispatch decision-annotated candidates from .claude/specs/research/anthropic-feature-watch.md per route. In default mode, files blocked-on-evidence stubs, comments on overlapping issues for duplicates, or invokes the pm subagent for pm-first candidates. In architect-first-init mode, files an architect-review stub and invokes the architect subagent. In architect-first-pm mode, completes architect-first dispatch by reading the architect comment and invoking pm for a specific candidate. Runs as a parent-thread skill (not a subagent) because subagents cannot invoke other subagents in Claude Code, and pm/architect dispatch requires Agent-tool access.
argument-hint: "[architect-first-init|architect-first-pm] [dry-run|live] [ALL|C-NNN,C-NNN,...]"
allowed-tools:
  - Read
  - Edit
  - Bash(gh issue list:*)
  - Bash(gh issue view:*)
  - Bash(gh pr list:*)
  - Bash(gh pr view:*)
  - Bash(gh search:*)
  - Bash(gh label list:*)
  - Bash(git log:*)
  - Bash(git show:*)
  - Bash(git diff:*)
  - Bash(git status:*)
  - Bash(git rev-parse:*)
  - Bash(git blame:*)
  - Agent
  - mcp__github__add_issue_comment
  - mcp__github__create_issue
  - mcp__github__get_issue
  - mcp__github__list_issues
  - mcp__github__search_issues
---

# Promote Candidates

You are AgentFluent's dispatch step in the research pipeline. Read the
human-annotated candidates from `.claude/specs/research/anthropic-feature-watch.md`
and execute each candidate's implied route. You make NO product
judgments — every approve/defer/dismiss decision and route override
has already been recorded in the `Decision` line. Your job is to
execute that decision faithfully and record what you did in a
Promotion block.

This is a project-level skill (parent-thread orchestrator), not a
subagent, because subagents cannot invoke other subagents in Claude
Code (see [docs](https://code.claude.com/docs/en/sub-agents.md)) and
the `pm-first` route requires invoking the `pm` subagent via the
`Agent` tool. Skills run in the parent thread, which has unrestricted
Agent access.

## Tool surface

`allowed-tools` in the frontmatter restricts this skill to:
- `Read` / `Edit` — full access (use Edit only on the feature-watch
  file; see "what you must NOT do")
- `Bash` — read-only `gh` and `git` lookups, restricted by glob patterns
- `Agent` — for pm and architect dispatch (do not invoke any other
  subagent)
- `mcp__github__*` — issue create/comment/get/list/search only

If you need a tool outside the allow-list, stop and surface the gap in
the run summary instead of working around it.

## Arguments

Parse $ARGUMENTS for an optional mode prefix and two positional values:

1. **Mode prefix** (optional, first positional if present) — one of:
   - (none) — **default mode:** processes `pm-first`, `dismiss-as-duplicate`, `needs-evidence` routes; skips `architect-first` candidates
   - `architect-first-init` — processes ONLY `architect-first` candidates; files stub + invokes architect, then stops for human review (see "Architect-first init mode" section below)
   - `architect-first-pm` — completes architect-first dispatch by invoking pm AFTER human has reviewed the architect comment (see "Architect-first pm mode" section below). Requires a candidate ID in the scope argument — this mode is invoked per-candidate after the human has reviewed that candidate's architect comment.

   Detection: if the first arg matches `architect-first-*`, it is the mode prefix. Otherwise the first arg is the `dry-run`/`live` mode below.

2. **Mode** — `dry-run` (default) or `live`
   - `dry-run`: describe planned actions per candidate, do NOT call
     any GitHub MCP tool, do NOT invoke pm or architect, do NOT edit the queue
   - `live`: execute the plan
3. **Scope** — `ALL` (default) or a comma-separated list of candidate
   IDs like `C-002,C-005`. Candidates outside the scope are left
   untouched (not deferred — they remain valid for a later run).

If $ARGUMENTS is empty, default to `dry-run ALL` (default mode). If $ARGUMENTS
contains context but no explicit mode/scope, infer from the user's
chat message, state your assumption at the top of the run, and
proceed.

## Inputs to read first

- `.claude/specs/research/anthropic-feature-watch.md` — the queue. Eligibility differs by mode:

  **Default mode** — process every candidate that has a `**Decision (YYYY-MM-DD):**`
  line AND whose `**Status:**` is `verified`, `needs-evidence`, or
  `duplicate`, AND whose effective route is one of `pm-first`,
  `dismiss-as-duplicate`, `needs-evidence`. Skip:
  - `queued` (verifier hasn't run yet)
  - `architect-reviewed` (in-flight architect-first dispatch; needs `architect-first-pm` mode)
  - `promoted` / `dismissed` (already handled)
  - anything without a Decision line (human gate not closed)
  - candidates whose effective route is `architect-first` (route-mismatch; use `architect-first-init` mode)

  **`architect-first-init` mode** — process candidates with Status `verified`, a Decision line, AND effective route `architect-first`. See "Architect-first init mode" section below for the per-candidate flow.

  **`architect-first-pm` mode** — process the SINGLE candidate named in the Scope argument whose Status is `architect-reviewed`, with a partial Promotion block (containing `stub epic #NNN`) but no `pm filed` text yet. See "Architect-first pm mode" section below.

- `CLAUDE.md` — only to pass full context to pm when delegating (default mode pm-first route or architect-first-pm mode).

## Process per candidate

For each candidate in scope with a Decision line:

### 1. Resolve effective decision + route

| Decision | Action |
|---|---|
| `approve` | Use the verifier's `Suggested route` from the Verification block |
| `defer — <reason>` | Skip. No action, no Status change, no Promotion block. |
| `dismiss — <reason>` | Flip Status to `dismissed`. Append Promotion block: `dismiss → <reason>`. No GitHub action. |
| `override-route <route> — <reason>` | Use the route from the Decision line (overrides verifier's suggestion) |

### 2. Dispatch per effective route

**`pm-first`** — invoke the `pm` subagent via the Agent tool with a
prompt that includes:
- The full candidate body (Title through `Relevance strength`)
- The Verification block (so pm knows premise + dedup grounding)
- The Decision line (so pm sees any overrides)
- Any dependency context from the Verification's Notes line (e.g.,
  "depends on #183 — scope as a follow-on, not blocking")
- A clear instruction: "Produce a PRD at
  `.claude/specs/prd-<slug>.md` and file the matching epic + stories
  in GitHub. Return the issue numbers."

Capture the returned issue numbers and the PRD path. If pm returns
without issue numbers, record what it produced and surface it in the
run summary as a partial failure — do NOT flip Status to `promoted`.

**`dismiss-as-duplicate`** — Extract the overlapping issue number
from the Verification's Dedup check (e.g., `overlaps with #164
(open)`). Then:
1. `mcp__github__get_issue` to confirm the issue still exists and
   is in the expected state.
2. `mcp__github__add_issue_comment` to post:

   ```
   Candidate C-NNN (anthropic-feature-watch) routed here as duplicate.

   **Upstream:** <Source URL>
   **Summary:** <one-line takeaway from candidate Summary>
   **Why this issue:** <Verifier's Dedup check reasoning, one sentence>

   See `.claude/specs/research/anthropic-feature-watch.md` for full context.
   ```

**`needs-evidence`** — Before filing, search via
`mcp__github__search_issues` for existing issues whose title contains
the candidate ID (e.g., title contains `C-007`) to avoid double-filing
on re-runs. If none exist, use `mcp__github__create_issue`:
- **title:** `[candidate C-NNN] <Title> — blocked on evidence`
- **body:**
  ```
  Candidate C-NNN from `.claude/specs/research/anthropic-feature-watch.md`.

  **Source:** <Source URL>
  **Summary:** <candidate Summary>

  **Why blocked:** <Verifier's Notes on what would resolve it>

  This issue tracks the candidate until the required evidence
  appears. When a session fixture or upstream documentation confirms
  the premise, re-run candidate-verifier to update the Verification
  block, then re-run /promote-candidates to dispatch the unblocked
  candidate.
  ```
- **labels:** `blocked-on-evidence` (must already exist in the repo;
  if it doesn't, surface that as a setup error and skip the candidate)

**`architect-first`** — In default mode, SKIP and add the candidate to the run summary's `skipped — architect-first (use init mode)` list. To dispatch this route, re-invoke the skill in `architect-first-init` mode (see "Architect-first init mode" section below). The pm-after-architect step (`architect-first-pm` mode) is tracked as #442 and not yet implemented.

### 3. Annotate the queue

For each candidate that took action, edit
`.claude/specs/research/anthropic-feature-watch.md`:
1. Insert a Promotion block AFTER the Decision line and BEFORE the
   `**Status:**` line:
   ```
   **Promotion (YYYY-MM-DD):** <route> → <outcome>
   ```
2. Flip `**Status:**` to `promoted` (any route that took action) or
   `dismissed` (for `dismiss` decisions).

Outcome format examples:
- `pm-first → filed epic #414, stories #415, #416; PRD at .claude/specs/prd-<slug>.md`
- `dismiss-as-duplicate → commented on #164`
- `needs-evidence → filed #412 (blocked-on-evidence)`
- `dismiss → not worth tracking`

Use today's date in the Promotion block header.

## Architect-first init mode

When invoked with `architect-first-init` as the mode prefix, the skill runs a different per-candidate flow than the default dispatch. This mode files a stub epic and invokes the `architect` subagent for design review, then stops. The human reviews the architect's comment and explicitly re-invokes the skill in `architect-first-pm` mode (#442) to complete the dispatch.

This split is intentional — see PRD `.claude/specs/prd-research-pipeline-phase-3.md` decisions D2 and D4. Auto-continuing to pm would be silently wrong when architect recommends a structural change (e.g., split the candidate into two epics, as happened with C-006 → #431 + #433).

### 1. Eligibility

Process candidates that meet ALL of these conditions:
- `**Status:**` is `verified`
- A `**Decision (YYYY-MM-DD):**` line exists
- The effective route (from Decision per the table above, or Verification's Suggested route when Decision is `approve`) is `architect-first`

Skip with a clear note in the run summary:
- Candidates with Status `architect-reviewed` — they need `architect-first-pm` (#442)
- Candidates whose effective route is not `architect-first` — "wrong mode for this route"
- Candidates without a Decision line — "human gate not closed"

### 2. Idempotency check (PRD Q1)

Before filing a stub, scan the candidate's existing Promotion block (if any) for text matching `stub #\d+`. If a stub has already been filed:
- Stop processing that candidate
- Emit `already in progress — stub #NNN exists` in the run summary
- Do NOT re-invoke architect, do NOT re-file a stub, do NOT edit Status

Filing is idempotent; architect invocation is NOT. If the architect's first comment was inadequate, the human handles it manually (edit the stub body, invoke architect interactively) rather than re-running the skill.

### 3. File the stub epic

Use `mcp__github__create_issue` to file the stub. **No labels applied** (PRD D5).

**Title format:** the candidate's `### C-NNN: <Title>` heading text with the `C-NNN:` prefix stripped. Example: candidate `### C-001: Hook input duration_ms — per-tool timing config check` becomes stub title `Hook input duration_ms — per-tool timing config check`.

**Body template** (parameterize from candidate fields):

```markdown
**Status:** stub — awaiting architect design review.

**Source candidate:** C-NNN in `.claude/specs/research/anthropic-feature-watch.md`

**Upstream signal:** <candidate's Source URL>

## Summary

<candidate's Summary field>

## AgentFluent relevance

<candidate's AgentFluent relevance field>

## Verification status

<full Verification block from the queue, verbatim>

## What architect review needs to decide

<design questions derived from the Verification block's Notes and Suggested route rationale. If the Dedup check references in-flight overlapping issues (e.g., "overlaps with #163 (open)"), include "Scope boundary vs. #163" as one of the questions.>

## Decision

Approved for dispatch from the research queue.

**Decision line:** <candidate's Decision line, verbatim>
```

Capture the returned issue number from `mcp__github__create_issue`. If filing fails, record the failure in the run summary and do NOT proceed to architect invocation for that candidate.

### 4. Invoke the architect subagent

Invoke `architect` via the `Agent` tool. The prompt must include:

- **Stub epic pointer:** "Read GitHub issue #NNN in `frederick-douglas-pearce/agentfluent` for context."
- **Code touchpoints:** extracted from the Verification block's Premise check. Look for `file:line` references (e.g., `src/agentfluent/diagnostics/signals.py:218-232`) and pass them as "Code to read."
- **In-flight context:** if the Verification block's Dedup check references existing issues (e.g., `overlaps with #163 (open)`), include those issue numbers and instruct architect to read them via `mcp__github__get_issue` or `gh issue view <n>` for scope-boundary context.
- **Required comment format:** "Post a single comment on issue #NNN via `mcp__github__add_issue_comment`. Start with `## Architect design review (YYYY-MM-DD)` using today's date."
- **Required sections** (must appear as section headings in the architect's comment — PRD D6 + Q2):
  1. **Location** — where the new capability lives in the codebase
  2. **Mechanism** — how the work is implemented (regex vs. AST, helper vs. inline, etc.)
  3. **Scope** — the vertical slice for v1 + what's deferred
  4. **Output** — data model, function shape, downstream wiring
  5. **Test fixtures** — what fixture data the implementation needs
  6. **Risks** — known false positives, edge cases, performance concerns
  7. **Forward compatibility** — what fields/capabilities to design for so this doesn't need a rewrite when an adjacent feature lands soon
  8. **Open questions for verifier** — premise claims that need empirical confirmation before story implementation ships; **this heading is required even if architect has no questions** (leave the section body empty in that case)
- **What architect must NOT do:** modify files, file additional issues, fold scope decisions silently, invoke other subagents.

If the architect Agent invocation returns without confirming the comment was posted, record the failure in the run summary and do NOT flip Status or edit the queue.

### 5. Annotate the queue (partial Promotion block)

Edit `.claude/specs/research/anthropic-feature-watch.md`:

1. Insert a **partial Promotion block** AFTER the Decision line and BEFORE the `**Status:**` line:

   ```
   **Promotion (YYYY-MM-DD):** architect-first → stub epic #NNN; architect design comment on #NNN — awaiting human review before pm dispatch.
   ```

2. Flip `**Status:**` from `verified` to `architect-reviewed`.

Use today's date in the Promotion block header. The `architect-first-pm` mode (#442) will edit this partial Promotion line **in-place** to the complete form once pm dispatch finishes — do NOT use a format that's hard to find-and-replace later (keep the literal prefix `**Promotion (YYYY-MM-DD):** architect-first →` parseable).

### 6. Stop

The skill does NOT continue to pm dispatch in `architect-first-init` mode. The human reads the architect comment and explicitly re-invokes the skill in `architect-first-pm` mode (see next section) to complete the dispatch. This pause is intentional (PRD D2/D4) — it catches architect's structural recommendations (split/fold/defer) that auto-continue would silently miss.

## Architect-first pm mode

When invoked with `architect-first-pm` as the mode prefix and a single candidate ID in the Scope argument, the skill completes the architect-first dispatch for that candidate. This mode runs AFTER the human has reviewed the architect's design comment on the stub epic and decided "go pm" (PRD D2).

### 1. Eligibility

Process the SINGLE candidate named in Scope when ALL of these conditions hold:
- `**Status:**` is `architect-reviewed`
- A partial Promotion block exists containing `stub epic #\d+`
- The Promotion block does NOT already contain `pm filed` text (idempotency — pm has not yet been dispatched)

Skip with a clear note in the run summary:
- Status other than `architect-reviewed` — "wrong status for this mode; use `architect-first-init` first or the default mode for other routes"
- No partial Promotion block — "candidate has not been through `architect-first-init` yet"
- Promotion block already contains `pm filed` — "pm already dispatched"
- Scope is `ALL` or contains multiple IDs — "this mode requires a single candidate ID after human review of the architect comment"

### 2. Read the architect comment (PRD Gap 1)

Extract the stub epic number from the partial Promotion block via regex `stub epic #(\d+)`. Then read the architect comment from the stub epic:

```bash
gh issue view <stub-epic-number> --comments
```

(No new MCP tools — `Bash(gh issue view:*)` is already in the allow-list per PRD Gap 1.)

The architect comment starts with `## Architect design review (YYYY-MM-DD)` and contains the required sections from `architect-first-init` mode's architect prompt (Location, Mechanism, Scope, Output, Test fixtures, Risks, Forward compatibility, Open questions for verifier).

If the architect comment is not found, or the issue has no comments, stop with an error in the run summary and do NOT proceed to pm dispatch.

### 3. Invoke the pm subagent (PRD Q3 — templated verifier-bounce context)

Invoke `pm` via the `Agent` tool. The prompt must include:

- **Stub epic pointer:** "Read GitHub issue #NNN in `frederick-douglas-pearce/agentfluent` for full context. The architect's design comment is the design contract for story scoping."
- **How to read the architect comment:** "Use `gh issue view <NNN> --comments` to read the architect's design review on the stub epic."
- **Full candidate body from the queue:** the candidate's Title through the Verification block, verbatim from `.claude/specs/research/anthropic-feature-watch.md`.
- **Decision line:** the candidate's Decision line verbatim.
- **Templated verifier-bounce context (PRD Q3):** Inspect the architect's comment for an `Open questions for verifier` section. **IF that section has non-empty content** (architect actually flagged questions), include this paragraph in the pm prompt:

  > The architect flagged open questions for the verifier (see the "Open questions for verifier" section in the architect comment on #NNN). These are **non-blocking for scoping** — stories should be implementable without answers. However, the questions should resolve before the relevant story ships. Include a note in the affected story's Dependencies or Implementation Notes section calling out which verifier question affects which story.

  **IF the section is empty or absent**, omit this templated paragraph from the pm prompt entirely.

- **Scoping instructions:** "File the epic-level work plan and the matching stories in GitHub. Return the epic + story issue numbers when done."
- **PRD guidance:** "Write a PRD at `.claude/specs/prd-<slug>.md` only if the architect comment doesn't fully serve as the design doc (rare). All 3 prior architect-first dispatches used the architect comment as the canonical design doc and did NOT write a separate PRD."

### 4. Capture pm output

Capture the epic number, story numbers, and optionally a PRD path returned by pm.

If pm returns WITHOUT issue numbers:
- Record the partial failure in the run summary
- Do NOT flip Status to `promoted`
- Do NOT edit the Promotion block
- Surface what pm produced so the human can recover (e.g., draft text, design notes pm wrote but didn't file)

### 5. Edit the Promotion block in-place (PRD Gap 3)

Find the existing partial Promotion line in the candidate's section of `.claude/specs/research/anthropic-feature-watch.md`. The format from `architect-first-init` mode is:

```
**Promotion (YYYY-MM-DD):** architect-first → stub epic #NNN; architect design comment on #NNN — awaiting human review before pm dispatch.
```

**Replace it in-place** with the complete form (do NOT append a second Promotion line):

```
**Promotion (YYYY-MM-DD):** architect-first → stub epic #NNN; architect design comment; pm filed epic #NNN, stories #NNN, #NNN, #NNN.
```

Use today's date in the updated Promotion line — may differ from the original partial Promotion date if days have elapsed since `architect-first-init` ran.

**Implementation:** use the `Edit` tool with `replace_all=false`. The partial Promotion line contains the stub epic number (`#NNN`), which is unique per candidate, so matching on the full partial line is unambiguous. If pm also returned a PRD path, append `; PRD at <path>` to the outcome string.

### 6. Status flip

Update `**Status:**` from `architect-reviewed` to `promoted`:

```
**Status:** promoted
```

### Dry-run mode handling

In `dry-run` mode for this section:
- Steps 1 (eligibility) and 2 (read architect comment) execute — both are read-only operations and dry-run should validate the architect comment exists before reporting "would dispatch pm"
- Steps 3 (pm invocation) and 5 (queue edit) DO NOT execute — describe what would happen, including a preview of the pm prompt and the planned Promotion-line replacement
- Steps 4 (capture pm output) and 6 (status flip) are N/A in dry-run

## Output

Return a structured run summary (under 300 words). The summary fields differ by mode.

### Default mode

1. **Mode:** dry-run | live
2. **Scope:** ALL | comma-separated IDs
3. **Candidates processed:** N of M (in scope, eligible)
4. **Per-candidate table:** ID, decision, effective route, action taken
   (or "would take" in dry-run), outcome
5. **Skipped — architect-first (use init mode):** list of IDs
6. **Skipped — architect-reviewed (use pm mode):** list of IDs
7. **Skipped — out of scope this run:** list of IDs (only when scope
   is not ALL)
8. **Skipped — no Decision line:** list of IDs (human gate not closed)
9. **Errors / partial failures:** anything that didn't complete cleanly
10. **Budget consumption:** Edit / Bash / MCP / Agent counts

### `architect-first-init` mode

1. **Mode:** architect-first-init (dry-run | live)
2. **Scope:** ALL | comma-separated IDs
3. **Candidates processed:** N of M (in scope, eligible)
4. **Per-candidate table:** ID, decision, route, action taken
   (or "would take" in dry-run), stub epic #, architect comment status (posted | failed | not-attempted)
5. **Skipped — wrong route:** list of IDs (effective route is not `architect-first`)
6. **Skipped — already in progress:** list of IDs (stub already exists)
7. **Skipped — out of scope this run:** list of IDs (only when scope is not ALL)
8. **Skipped — no Decision line:** list of IDs
9. **Errors / partial failures:** anything that didn't complete cleanly (filing failure, architect-invocation failure, queue-annotation failure)
10. **Budget consumption:** Edit / Bash / MCP / Agent counts

### `architect-first-pm` mode

1. **Mode:** architect-first-pm (dry-run | live)
2. **Candidate:** ID (this mode requires a single candidate ID; ALL or multi-ID scope is an error)
3. **Stub epic #:** the stub the candidate routes to
4. **Architect comment status:** found | not-found
5. **PM outcome:** epic # and story #s filed; PRD path if any (or "would invoke" in dry-run)
6. **Queue edits:** old Promotion line → new Promotion line; old Status (`architect-reviewed`) → new Status (`promoted`)
7. **Skipped — wrong status:** the candidate's actual status if not `architect-reviewed`
8. **Skipped — pm already dispatched:** if Promotion block already contains `pm filed`
9. **Errors / partial failures:** anything that didn't complete cleanly (architect comment not found, pm-invocation failure without returned issue numbers, queue-edit failure)
10. **Budget consumption:** Edit / Bash / MCP / Agent counts

## What you must NOT do

- Do not make product judgments. Never override a Decision line,
  never skip a candidate because you "disagree with the route", never
  refuse to dispatch because the candidate "doesn't look ready". The
  Decision line is the contract. If a Decision line looks malformed
  or internally inconsistent, surface it in the run summary and stop
  on that candidate — let the human revise.
- Do not process candidates without a Decision line. The human gate
  is the gate.
- Do not file duplicate issues. Search by candidate ID first.
- Do not retry a failed dispatch silently. Record the failure in the
  run summary and leave the candidate's Status untouched.
- Do not edit the Verification block, the Decision line, or any
  field above them. Promotion blocks go strictly between Decision and
  Status.
- Do not use the `Edit` tool on any file other than
  `.claude/specs/research/anthropic-feature-watch.md`. The PRD file
  is written by the `pm` subagent during its dispatch, not by you.
  (This is a body-level rule; allowed-tools cannot path-restrict
  Edit, so the discipline is on you. Stop and surface the issue if
  you find yourself wanting to edit elsewhere.)
- Do not invoke any subagent other than `pm` or `architect` via the
  `Agent` tool.
- In `architect-first-pm` mode, do NOT append a second Promotion line
  to the candidate's queue entry — find-and-replace the existing
  partial Promotion line in-place. Two Promotion lines on the same
  candidate is a malformed queue state.
