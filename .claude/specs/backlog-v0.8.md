# AgentFluent v0.8 Backlog

Ordered backlog for v0.8 (Sharpen the Signal). Issues are sequenced by dependency chain, not by issue number.

**Theme:** Fix the signals that mislead. Add the signals that prove quality.

**Milestone:** v0.8.0

---

## Triage Summary

| Disposition | Count | Issues |
|-------------|-------|--------|
| In scope (open) | 9 | #394, #395, #396, #402, #399, #400, #401, #392, #390 |
| Stretch | 1 | #333 |
| Total in milestone | 10 | (including parent epic #398) |

---

## Stream A: Diagnostics Signal Quality (dogfood fixes)

Three independent stories addressing the dominant misleading signals from the v0.7 dogfood analysis (2026-05-17). All three can be implemented in parallel.

### A1. #394 -- Extend `active_duration_ms` to non-trace agents (AskUserQuestion-anchored wait detection)

**Priority:** high
**Labels:** `bug`, `enhancement`, `priority:high`
**Sizing:** M (2-3 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** pm's `active_duration_ms` returns `None` when no subagent trace exists, causing the CLI table to fall back to wall-clock duration (33 min avg in dogfood). The original #230 spec called out the AskUserQuestion-anchored detection path but it was never implemented. Extend the computation to subtract AskUserQuestion wait gaps from wall-clock duration for parent-thread agents.

**Key considerations:**
- Option A (recommended by issue author): compute from parent-thread messages by detecting AskUserQuestion tool_result blocks between invocation start/end, summing gaps, subtracting from wall-clock
- Option B (pragmatic fallback): tag invocations as "may include user-wait time" without computing active duration
- Requires plumbing parent_messages into the AgentInvocation computation path
- `duration_outlier` signal must use active duration for AskUserQuestion-using agents

**Blocks:** Nothing

---

### A2. #395 -- Down-weight `retry_loop` on built-in tools

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`
**Sizing:** S (1-2 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Read retries are 68% of all retry_loop signals (104 of 152 in dogfood) but have no agent-config fix. Down-weight retries on built-in read-only tools (Read, Grep, Glob) in the priority scorer by a configurable factor (default 0.3). Bash retries remain at full weight (Bash is built-in but actionable).

**Key considerations:**
- Approach B from the issue: down-weight in priority_score, not a new SignalType
- Built-in noise list: Read, Grep, Glob (read-only tools)
- Bash stays at full weight despite being built-in
- No GLOSSARY or schema changes needed

**Blocks:** Nothing

---

### A3. #396 -- `reviewer_caught` parent_acted interpretation -- healthy-band gating

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`
**Sizing:** S-M (1-2 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** architect's 31% parent_acted rate is treated as a quality problem when ~50% of "not acted on" findings are deliberate rejections. Introduce a "healthy parent_acted band" (default 25-75%) that gates recommendation severity: within band = INFO ("healthy collaboration, no action needed"), below band = WARNING ("reviewer findings may be going unread"), above band = INFO ("high follow-through").

**Key considerations:**
- Approach A from the issue: healthy-band with configurable thresholds
- 25-75% default band based on dogfood observation (31% acted + ~50% legitimately rejected)
- Threshold constants documented in code
- GLOSSARY entry for reviewer_caught updated with band semantics

**Blocks:** Nothing

---

## Stream B: Tier 3 GitHub Enrichment -- Epic #398

New external data source. Sequential dependency chain: infrastructure -> signal implementations.

### B1. #399 -- Tier 3 infrastructure: `gh` detection, cache layer, `--github` flag, consent UX

**Priority:** high
**Labels:** `enhancement`, `epic:diagnostics`, `priority:high`
**Sizing:** M-L (3-5 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Shared infrastructure for all Tier 3 signals. `gh` CLI detection + auth validation. File-backed TTL cache at `~/.cache/agentfluent/github/` with two TTL tiers (7d for closed PRs, 15m for mutable data). `--github` flag on `analyze`. First-run consent prompt in TTY. Session-to-repo mapping via git remote inference. Graceful rate-limit degradation with `tier3_degraded` JSON envelope field.

**Key considerations:**
- No new Python dependencies -- `gh` via subprocess + stdlib JSON
- `--github` implies `--git` (OQ1 pending human decision)
- `--github` in non-TTY = self-consenting (OQ2 pending human decision)
- Consider `github/` subpackage for code organization
- Cache key includes auth user login to prevent cross-user leakage on shared machines

**Blocks:** #400, #401

---

### B2. #400 -- `CI_FAILURE_FIRST_PUSH` signal implementation

**Priority:** high
**Labels:** `enhancement`, `epic:diagnostics`, `priority:high`
**Sizing:** M (2-3 days)
**Dependencies:** #399 (Tier 3 infrastructure)
**Status:** IN SCOPE

**Summary:** New Tier 3 quality signal. Detects PRs where the first push failed CI. Direct quality-miss indicator: the agent shipped code that didn't pass automated checks. Uses `gh api` to fetch first commit SHA + combined CI status. Signal contributes to `axis_scores.quality` via existing aggregation.

**Key considerations:**
- Session-to-PR mapping via git commit SHAs + `gh api commits/{sha}/pulls`
- "First push" = earliest commit on the PR branch
- Combined status endpoint (not check-runs API) for simplicity
- Correlator recommendation: "Consider adding pre-commit validation to agent prompt or hooks"

**Blocks:** Nothing

---

### B3. #401 -- `PR_REVIEW_COMMENT_DENSITY` signal implementation

**Priority:** high
**Labels:** `enhancement`, `epic:diagnostics`, `priority:high`
**Sizing:** M (2-3 days)
**Dependencies:** #399 (Tier 3 infrastructure)
**Status:** IN SCOPE

**Summary:** New Tier 3 quality signal. Detects PRs with high review comment density (comments per line changed). Indicates human review effort that an agent-side review should have caught before PR creation. Default threshold: 0.1 (1 comment per 10 lines changed). Self-reviews excluded from count.

**Key considerations:**
- Density = review_comments / max(lines_changed, 1)
- Minimum lines_changed gate (e.g., 20 lines) to suppress noise on tiny PRs
- Severity escalation: INFO at 1x threshold, WARNING at 2x threshold
- Correlator recommendation: "Consider invoking architect or code-review agent before opening PRs"

**Blocks:** Nothing

---

## Stream C: Signal Calibration

### C1. #402 -- `feat_fix_proximity` precision validation (v0.7 calibration check)

**Priority:** medium
**Labels:** `enhancement`, `epic:diagnostics`, `priority:medium`
**Sizing:** S-M (1-2 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Ground-truth precision check for the FEAT_FIX_PROXIMITY signal (#275) shipped in v0.7. Sample >=20 of the 33 detections from the dogfood corpus, classify each as TP/FP, calculate precision. If >=70%, document and ship. If <70%, tune thresholds.

**Key considerations:**
- Lighter-weight than #274's calibration notebook -- manual + scripted hybrid
- Target: <=30% FP rate (matching #321's USER_CORRECTION precedent)
- Calibration result documented in code comments in `diagnostics/git_signals.py`
- If dominant FP pattern found, file follow-up issue

**Blocks:** Nothing

---

## Stream D: Docs

### D1. #392 -- docs(changelog): tidy v0.7.0 manual breaking-changes section placement

**Priority:** low
**Labels:** `documentation`, `chore`, `priority:low`
**Sizing:** XS (<1 day)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Restructure the v0.7.0 CHANGELOG section so breaking changes lead the release block rather than appearing as a separate section below the auto-generated feature list. Decision needed on approach (in-block restructuring vs. release-please-changelog-types config).

**Blocks:** Nothing

---

### D2. #390 -- docs: catch up README + GLOSSARY + CHANGELOG for v0.8.0

**Priority:** required-for-release
**Labels:** `documentation`, `enhancement`, `priority:medium`
**Sizing:** M (2-3 days)
**Dependencies:** All feature work complete (docs reflect what shipped)
**Status:** IN SCOPE

**Summary:** Auto-created when v0.8.0 milestone was opened. Update README (feature list, CLI flags, JSON example for `--github`), GLOSSARY (new terms: `ci_failure_first_push`, `pr_review_comment_density`, `tier3_degraded`, updated `reviewer_caught`), CHANGELOG (prose expansion).

**Blocks:** Nothing

---

## Stretch Scope

### S1. #333 -- ERROR_PATTERN FP reduction on prose-heavy outputs

**Priority:** low (within stretch)
**Labels:** `bug`, `priority:low`
**Sizing:** M (2-3 days)
**Dependencies:** None
**Status:** STRETCH

**Summary:** Residual ERROR_PATTERN false-positive rate post-#281. ~10 of 11 leading-200 matches on the dogfood corpus are false positives (issue titles, code identifiers, schema field mentions). Five hypotheses in the issue; hypothesis 5 (suppress metadata fallback for trace-linked invocations) is the simplest scope-cut. Fits the "sharpen the signal" theme but lower priority than the three anchor fixes.

**Why stretch:** The anchors (#394/#395/#396) address the three highest-volume misleading signals. #333 addresses a lower-volume FP class. Pull in if time allows after must-include scope completes.

---

## Implementation Priority Order

### Wave 1 -- Dogfood fixes + Tier 3 infrastructure (start immediately, parallel)

All four are independent. Start them in parallel or interleave.

1. **#394** -- active_duration_ms for non-trace agents (M, no deps, highest-impact dogfood fix)
2. **#395** -- retry_loop built-in-tool down-weight (S, no deps)
3. **#396** -- reviewer_caught healthy-band (S-M, no deps)
4. **#399** -- Tier 3 infrastructure (M-L, no deps, blocks Stream B)

### Wave 2 -- Tier 3 signals + calibration (days 5-12)

5. **#400** -- CI_FAILURE_FIRST_PUSH signal (M, depends on #399)
6. **#401** -- PR_REVIEW_COMMENT_DENSITY signal (M, depends on #399, parallel with #400)
7. **#402** -- feat_fix_proximity calibration (S-M, independent)
8. **#392** -- CHANGELOG tidy (XS, independent early win)

### Wave 3 -- Stretch (if time allows)

9. **#333** -- ERROR_PATTERN FP reduction (M, independent)

### Wave 4 -- Release prep (days 16-22)

10. **#390** -- Docs catch-up (M, depends on all features)
11. Dogfood validation runs (Tier 3 signals, duration fix, retry ranking)
12. Release prep (changelog, version bump, CI green)

---

## Ordered Backlog (flat view)

| Order | # | Title | In/Out | Priority | Deps | Stream |
|-------|---|-------|--------|----------|------|--------|
| 1 | #394 | active_duration_ms non-trace agents | IN | high | none | A |
| 2 | #395 | retry_loop built-in down-weight | IN | medium | none | A |
| 3 | #396 | reviewer_caught healthy band | IN | medium | none | A |
| 4 | #399 | Tier 3 infrastructure | IN | high | none | B |
| 5 | #400 | CI_FAILURE_FIRST_PUSH signal | IN | high | #399 | B |
| 6 | #401 | PR_REVIEW_COMMENT_DENSITY signal | IN | high | #399 | B |
| 7 | #402 | feat_fix_proximity calibration | IN | medium | none | C |
| 8 | #392 | CHANGELOG tidy | IN | low | none | D |
| 9 | #333 | ERROR_PATTERN FP reduction | STRETCH | low | none | -- |
| 10 | #390 | Docs catch-up | IN | required | all features | D |

---

## Estimated Total

**Must-include: 9 open issues, ~18-26 dev days (3-4 weeks)**
**With stretch: +1 issue, ~2-3 additional dev days**

Streams A, B, and C are fully independent. Within Stream B, the infrastructure story (#399) blocks the two signal stories (#400, #401) which are independent of each other. A solo developer can start with Wave 1 (interleaving #394 with #399), then shift to Wave 2 (Tier 3 signals + calibration) once infrastructure lands.
