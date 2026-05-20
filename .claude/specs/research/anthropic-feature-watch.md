# Anthropic Feature Watch

**Purpose:** Queue of candidate features for AgentFluent's roadmap, sourced
from Anthropic announcements and ecosystem chatter. Maintained by the
`anthropic-research` subagent.

**Workflow:** subagent appends candidates here → human reviews on cadence
→ human says "spec out candidate C-NNN" → pm agent produces PRD/issues →
candidate status flips to `promoted` with the resulting issue/PR link.

---

## Schema

### Reviewed Sources entry

| Field | Required | Notes |
|---|---|---|
| Date | yes | YYYY-MM-DD when reviewed |
| URL | yes | Full URL |
| Title | yes | Article/post title |
| One-line takeaway | yes | What the source is about |
| Tag | yes | `candidate-added` / `not-actionable` / `already-covered` / `rejected-by-decision` |
| Candidate ref | conditional | If tag=candidate-added, the C-NNN id |

### Candidate entry

| Field | Required | Notes |
|---|---|---|
| ID | yes | `C-NNN`, monotonic |
| Title | yes | Short |
| Source | yes | URL + date |
| Added | yes | YYYY-MM-DD |
| Summary | yes | 2-3 sentences on the upstream feature |
| AgentFluent relevance | yes | Which of the 4 core features it touches + which data source signals it |
| Suggested shape | yes | New signal? Config scanner check? Analytics metric? Diff annotation? |
| Relevance strength | yes | `strong fit` / `moderate fit` / `speculative fit` |
| Status | yes | `queued` / `promoted` / `dismissed` / `duplicate` |
| Status notes | conditional | If promoted: linked issue/PRD. If dismissed: reason. If duplicate: existing issue/PRD. |

Candidates are append-only by the subagent. Status changes are made by
the human (or the pm agent on the human's instruction).

---

## Reviewed Sources

<!-- Append newest entries at the top of this section -->

_No sources reviewed yet._

---

## Candidates Queue

<!-- Append new candidates at the bottom. Status updates happen in place. -->

_No candidates queued yet._
