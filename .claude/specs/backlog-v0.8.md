# AgentFluent v0.8 Backlog

Ordered backlog for v0.8 (Sharpen the Signal). Issues are sequenced by dependency chain, not by issue number.

**Theme:** Fix the signals that mislead. Add the signals that prove quality.

**Milestone:** v0.8.0

---

## Triage Summary

| Disposition | Count | Issues |
|-------------|-------|--------|
| In scope (open) | 11 | #453, #454, #395, #396, #402, #399, #400, #401, #392, #390, #333 |
| Closed (superseded) | 1 | #394 (replaced by #453 + #454, see D038) |
| Total in milestone | 12 | (including parent epic #398) |

---

## Stream A: Diagnostics Signal Quality (dogfood fixes)

Four independent stories addressing the dominant misleading signals from the v0.7 dogfood analysis (2026-05-17). All four can be implemented in parallel (though #454 has a soft dependency on #453 for its dogfood validation AC).

### A1a. #453 -- Tag no-trace agent invocations as duration-unreliable

**Priority:** high
**Labels:** `bug`, `enhancement`, `priority:high`
**Sizing:** XS-S (~1 day)
**Dependencies:** None
**Status:** IN SCOPE
**Replaces:** #394 (Cause A -- see D038)

**Summary:** 6 of 31 pm invocations (~20%) have no subagent trace file on disk (all from one session). Without trace data, `active_duration_ms` returns `None` and the table silently falls back to wall-clock duration. Add a `duration_reliable` flag to `AgentInvocation` so consumers can distinguish real active durations from wall-clock fallbacks. Table formatter annotates unreliable durations. `duration_outlier` signal skips unreliable invocations.

**Key considerations:**
- `duration_reliable: bool` property on `AgentInvocation` -- True when trace exists, False otherwise
- Table formatter annotates (e.g., `~33m*` with footnote)
- `duration_outlier` gated: skip or down-weight to INFO for unreliable invocations
- JSON output includes `duration_reliable` per invocation
- No heuristic changes, no calibration risk

**Blocks:** Nothing (but #454's dogfood validation AC uses this flag)

---

### A1b. #454 -- Re-tune idle-gap thresholds for moderate user-coupled waits

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`
**Sizing:** M (2-3 days)
**Dependencies:** Soft dependency on #453 (for dogfood validation AC only)
**Status:** IN SCOPE
**Replaces:** #394 (Cause B -- see D038)

**Summary:** The idle-gap heuristic (`IDLE_GAP_K=10`, `IDLE_GAP_FLOOR_MS=300_000`) catches dramatic gaps but misses moderate 1-4 minute user-coupled waits. Re-run the calibration notebook against the v0.7+ corpus to find new constants that catch moderate gaps while maintaining 100% `stuck_session` recall. May require splitting idle-gap vs stuck-session thresholds.

**Key considerations:**
- Constants are shared with `stuck_session` signal -- calibrated to 100% recall on 12 stuck traces in `scripts/calibration/threshold_validation.ipynb` section 11
- Sweep `IDLE_GAP_K` (5, 7, 8, 10) and `IDLE_GAP_FLOOR_MS` (60k, 120k, 180k, 300k)
- If constants can't serve both signals, consider splitting into separate threshold pairs
- Dogfood target: pm avg duration < 10 min (reliable invocations only, per #453)

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

**Summary:** Auto-created when v0.8.0 milestone was opened. Update README (feature list, CLI flags, JSON example for `--github`), GLOSSARY (new terms: `ci_failure_first_push`, `pr_review_comment_density`, `tier3_degraded`, `duration_reliable`, updated `reviewer_caught`), CHANGELOG (prose expansion).

**Blocks:** Nothing

---

## Stream E: Additional Signal-Quality Fix

### E1. #333 -- ERROR_PATTERN FP reduction on prose-heavy outputs

**Priority:** low
**Labels:** `bug`, `priority:low`
**Sizing:** M (2-3 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Residual ERROR_PATTERN false-positive rate post-#281. ~10 of 11 leading-200 matches on the dogfood corpus are false positives (issue titles, code identifiers, schema field mentions). Five hypotheses in the issue; hypothesis 5 (suppress metadata fallback for trace-linked invocations) is the simplest scope-cut. Fits the "sharpen the signal" theme alongside the Stream A anchor fixes.

---

## Implementation Priority Order

### Wave 1 -- Dogfood fixes + Tier 3 infrastructure (start immediately, parallel)

All five are independent. Start them in parallel or interleave.

1. **#453** -- tag no-trace invocations as duration-unreliable (XS-S, no deps, quick win)
2. **#454** -- idle-gap threshold re-tuning (M, soft dep on #453 for validation)
3. **#395** -- retry_loop built-in-tool down-weight (S, no deps)
4. **#396** -- reviewer_caught healthy-band (S-M, no deps)
5. **#399** -- Tier 3 infrastructure (M-L, no deps, blocks Stream B)

### Wave 2 -- Tier 3 signals + calibration (days 5-12)

6. **#400** -- CI_FAILURE_FIRST_PUSH signal (M, depends on #399)
7. **#401** -- PR_REVIEW_COMMENT_DENSITY signal (M, depends on #399, parallel with #400)
8. **#402** -- feat_fix_proximity calibration (S-M, independent)
9. **#392** -- CHANGELOG tidy (XS, independent early win)

### Wave 3 -- Additional signal-quality fix

10. **#333** -- ERROR_PATTERN FP reduction (M, independent)

### Wave 4 -- Release prep (days 16-22)

11. **#390** -- Docs catch-up (M, depends on all features)
12. Dogfood validation runs (Tier 3 signals, duration fix, retry ranking)
13. Release prep (changelog, version bump, CI green)

---

## Ordered Backlog (flat view)

| Order | # | Title | In/Out | Priority | Deps | Stream |
|-------|---|-------|--------|----------|------|--------|
| 1 | #453 | Tag no-trace as duration-unreliable | IN | high | none | A |
| 2 | #454 | Idle-gap threshold re-tuning | IN | medium | soft #453 | A |
| 3 | #395 | retry_loop built-in down-weight | IN | medium | none | A |
| 4 | #396 | reviewer_caught healthy band | IN | medium | none | A |
| 5 | #399 | Tier 3 infrastructure | IN | high | none | B |
| 6 | #400 | CI_FAILURE_FIRST_PUSH signal | IN | high | #399 | B |
| 7 | #401 | PR_REVIEW_COMMENT_DENSITY signal | IN | high | #399 | B |
| 8 | #402 | feat_fix_proximity calibration | IN | medium | none | C |
| 9 | #392 | CHANGELOG tidy | IN | low | none | D |
| 10 | #333 | ERROR_PATTERN FP reduction | IN | low | none | E |
| 11 | #390 | Docs catch-up | IN | required | all features | D |

---

## Closed / Superseded

| # | Title | Disposition | Replaced by |
|---|-------|-------------|-------------|
| #394 | active_duration_ms non-trace agents (AskUserQuestion-anchored) | Closed -- premise disproved (D038) | #453 + #454 |

---

## Estimated Total

**11 open issues, ~20-29 dev days (3-4+ weeks)**

Net effect of the #394 split: one M issue replaced by one XS-S + one M. Total effort is similar but risk is better distributed -- the quick win (#453) ships independently of the calibration work (#454). If #454 threatens the timeline, it can slip to v0.8.1 without leaving users with silently misleading durations (because #453 tags them).

Streams A, B, C, and E are fully independent. Within Stream B, the infrastructure story (#399) blocks the two signal stories (#400, #401) which are independent of each other. A solo developer can start with Wave 1 (interleaving #453 with #399), then shift to Wave 2 (Tier 3 signals + calibration) once infrastructure lands.
