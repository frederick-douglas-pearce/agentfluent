# PRD Brief: Quality as a Third Axis of Agent Improvement Diagnostics

**Status:** Draft for PM scoping
**Date:** 2026-05-04
**Author:** Claude (in collaboration with Fred)
**Intended consumer:** PM agent — to be processed into an epic with stories, scoping decisions, and a v0.6+ scope-fit assessment.

---

## 1. Problem Statement

AgentFluent's diagnostics engine currently evaluates agent improvement opportunities along **two axes**:

1. **Cost** — token usage, cache efficiency, per-invocation token totals (drives offload-candidate detection, model-routing recommendations).
2. **Speed / efficiency** — duration, tool-use counts, retry density, churn (drives stuck-detection, duration outliers, efficiency flags).

A **third axis is missing: quality.** Quality is the dominant reason best-practice guidance recommends subagent delegation in the first place — independent context, narrower focus, and reduced parent-context contamination cause review-style subagents (architect, code-reviewer, security-review, tester) to catch design flaws and errors the parent would miss. The benefit shows up not as fewer tokens or faster wallclock — often the opposite — but as **fewer downstream defects, less rework, and lower follow-on session cost.**

Because the current recommendation engine cannot see this benefit, it systematically **under-recommends review-style subagents** and over-weights inline parent-thread work that looks "cheap" by token math but produces quality debt.

This is a credibility gap. A user following AgentFluent's current recommendations would diverge from current best practice on subagent usage, particularly for code-quality-sensitive workflows.

### Why this matters now

- Anthropic guidance, the Claude Code agent ecosystem, and community best practices increasingly frame subagent delegation around **independence and quality**, not just offload.
- AgentFluent's tagline — *"The tools that exist tell you what your agent did. This tool tells you what to change."* — is undermined if the recommendations miss the strongest reason to delegate.
- The v0.5 work on offload candidates (#189), priority ranking (#172), and `agentfluent diff` (#199) creates the right scaffolding to add a quality dimension on top: the recommendation engine, scoring, and comparison surfaces are now in place.

## 2. Proposed Direction

Extend the recommendation engine from a single priority score to a **multi-axis evaluation** — `cost`, `speed`, `quality` — where a subagent recommendation can fire if it wins on **any axis with sufficient evidence**, not only when token math dominates. The recommendation copy should explain *which axis* triggered it, so users see "consider an architect agent — this session shows N quality signals" rather than a generic delegation suggestion.

Quality signals are noisier and slower-feedback than cost/duration. The proposal is to layer them by data-source friction so we can ship value early and enrich progressively.

## 3. Signal Tiers

### Tier 1 — Within-session quality proxies (no extra data sources)

Detectable from existing JSONL session data. No new dependencies.

- **User mid-flight corrections.** Patterns like "no, do X instead", "wait, that's wrong", "stop", "actually, ...". Frequency = parent quality miss rate.
- **File rework density.** Same file edited N+ times within a single session, especially after a feature was declared "done." Within-session churn that a pre-implementation review could have prevented.
- **Plan→revise→implement loops.** ExitPlanMode followed by significant deltas before implementation. Suggests the parent benefits from forced reflection a subagent could provide consistently.
- **"Reviewer caught" rate when review subagents do run.** When architect / security-review / tester / code-reviewer subagents are invoked, count substantive findings (length, presence of "blocker" / "issue" / "concern" / "must" language) and check whether the parent's subsequent edits reflect them. High finding rate = strong evidence the parent needs that review consistently.
- **Stuck-loop detection reframed.** Long monotone tool sequences are often quality failures (lack of independent perspective), not just efficiency failures. The same signal can drive a quality-axis recommendation.

### Tier 2 — Local git correlation (low friction, no auth)

Reads `git log` and commit metadata for the project AgentFluent is analyzing. No remote calls.

- **Feature→fix proximity.** A `feat:` commit followed within N days by `fix:` commits touching the same files = quality miss in the original session. Correlate back to whether that session used review subagents.
- **Revert rate.** Commits reverted within a window = strong quality miss signal.
- **Re-touch decay.** How many sessions touch a file before edits to it settle. Long settle times = quality opportunity on initial implementation.

### Tier 3 — GitHub enrichment (opt-in, richest signal)

Requires GitHub auth (gh CLI or MCP). Off by default.

- **PR review comment density** on Claude-authored PRs.
- **CI-failure-on-first-push rate.** How often the first push to a PR fails CI.
- **Post-merge issue references.** Bugs filed shortly after merge.
- **Review-comment topic clustering.** If reviewers repeatedly flag the same kinds of things (error handling, security, naming), that's a *targeted* recommendation: "add a reviewer subagent specialized for X."

## 4. Recommendation Engine Changes

- **Multi-axis scoring.** Each recommendation candidate produces a vector `(cost_score, speed_score, quality_score)` plus a confidence per axis. A recommendation surfaces if any axis exceeds a threshold with adequate confidence.
- **Axis attribution in output.** The CLI/JSON output names which axis triggered each recommendation, so users understand whether they're being told to delegate to save tokens, save wallclock, or improve quality.
- **Quality-aware subagent suggestions.** When quality signals are strong, recommend specifically the *kind* of review subagent that would address them (architect for design issues; tester for missed edge cases; security-review for risk-laden surfaces).
- **Composition with offload candidates (#189).** The existing offload pipeline becomes one input among several. A burst that scores low on cost but high on quality (lots of corrections, file rework) can still surface — currently it would be filtered out.
- **Composition with priority ranking (#172).** Quality-axis findings need to plug into the unified priority list, not live in a separate table.

## 5. Goals

1. **Close the under-recommendation gap on review subagents** — measurable as: in dogfood runs of sessions known to have benefited from architect/reviewer use (or that *would have*), AgentFluent now surfaces the corresponding recommendation.
2. **Ship Tier 1 first.** Tier 1 alone is enough to start moving the needle and has zero new-data friction.
3. **Add axis attribution to all recommendation output paths** (CLI, JSON, eventually report/diff).
4. **Lay the groundwork for Tier 2/3** without making them blocking — local git enrichment should be additive, GitHub strictly opt-in.
5. **Calibrate against false positives.** Quality signals are noisy. Reuse the calibration-sweep pattern from the offload work (#260) to tune thresholds before shipping.

## 6. Non-Goals

- LLM-powered classification of "quality issues" (stays rule-based for now).
- Auto-applying recommended fixes.
- Webapp dashboard for quality dimensions (deferred along with the rest of the dashboard).
- Building a full PR-review-comment ingestion pipeline as part of this epic — Tier 3 is structurally enabled, but full GitHub ingestion is its own scoping exercise.
- Quality scoring of *individual subagent outputs* (e.g., "rate this architect review's quality"). Out of scope; this epic scores the parent's quality, not the reviewer's.

## 7. Open Questions for PM Scoping

1. **Fit and timing.** Is this a v0.6 epic, a v0.7 theme, or split across both? Tier 1 alone is plausibly a v0.6 inclusion alongside whatever else lands; Tiers 2/3 likely span releases.
2. **Tier-1 signal selection.** Of the five Tier-1 signals listed in §3, which are highest-leverage to ship first? "User mid-flight corrections" and "reviewer caught rate" feel like the strongest starters; PM to confirm or override based on backlog context.
3. **Multi-axis scoring shape.** Should the public JSON schema expose the per-axis vector, or only a synthesized priority + axis label? Schema-stability decision per the v0.5 `diff` work matters here.
4. **Calibration data.** Do we have enough dogfood sessions that contain both "architect was used and caught X" and "architect was not used and X slipped through" to calibrate? If not, scope a data-collection step.
5. **Relationship to existing offload-candidate pipeline.** Is the cleanest path to extend the offload pipeline to emit multi-axis scores, or to add a parallel "quality candidates" pipeline that joins at the recommendation layer? Architect agent input recommended before implementation.
6. **Recommendation copy.** How explicit should the output be about *why* quality matters? Risk: too didactic and users tune out; too terse and the recommendation looks unmotivated.
7. **Negative recommendations.** Should AgentFluent ever recommend *removing* a subagent if it shows zero quality signal and pure cost overhead? Adjacent to this epic but worth flagging.

## 8. Decisions Needed Before Implementation

- Tier-1 scope cut for the first epic deliverable (which signals; which output paths).
- Schema decision: per-axis scores in JSON or synthesized only.
- Default thresholds and calibration plan.
- Whether Tier 2 (local git) is in the same epic or a follow-on.
- Whether the architect agent should review the recommendation-engine refactor design before implementation begins (recommended: yes, given multi-axis scoring touches the core diagnostics interface).

## 9. Risks

- **Noisy signal → false positives → trust erosion.** Quality signals are inherently noisier than token math. Without careful calibration, AgentFluent could recommend architect agents for sessions where they'd add no value, undoing the trust gains from v0.5.
- **Recommendation overload.** Adding a third axis means more recommendations per run. Priority ranking (#172) helps, but we need to ensure the top-N stays curated rather than expanding.
- **Tier 2/3 scope creep.** GitHub enrichment is a tar pit if not bounded. Treat it as a separate spike.
- **Definition drift.** "Quality" can mean many things; the epic must define it operationally (= signals listed in §3) and resist expansion to subjective measures.

## 10. Inputs the PM Agent Should Consider

- Current diagnostics architecture: see `src/agentfluent/diagnostics/` (offload candidates, priority ranking, recommendations).
- Existing offload-candidate work: PRs #256, #258, #260, #266 — pattern to mirror for calibration.
- v0.5 PRD (`prd-v0.5.md`) for tone, structure, and the "trustworthy diagnostics" framing this epic must respect.
- Decision log (`decisions.md`) — D013 (model routing + delegation cross-linking), D014 (composite recommendation pattern) are precedents for multi-axis composition.
- Memory entry: under-delegation observation windows — this epic must not contaminate observation data by recommending delegation that wouldn't otherwise have happened during measurement runs.

---

**Suggested next step:** PM agent reads this brief, decides scope-fit (v0.6 vs split), drafts an epic issue with Tier-1 stories, and flags the architect-agent-review question (item 5 in §7) for resolution before implementation kicks off.
