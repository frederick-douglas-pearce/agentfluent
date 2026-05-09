# Decision Log

Append-only log of significant trade-off decisions made during AgentFluent development.

---

## D001: Python as sole MVP language

**Date:** 2026-04-14
**Context:** CLAUDE.md lists "TypeScript and Python" as the tech stack. The research doc references TypeScript file names (parser.ts, analytics.ts) for code reuse from CodeFluent.
**Decision:** MVP will be Python-only. No TypeScript.
**Rationale:**
- Fred is a Python developer and will be the primary contributor
- CodeFluent's Python webapp (FastAPI backend) already has working Python implementations of the JSONL parser, token analytics, config scanner, and pricing lookup
- CLI tool maps naturally to Python ecosystem (Typer/Click, Rich)
- Agent SDK has a Python SDK, aligning with the primary target audience
- TypeScript reuse references in CLAUDE.md were from the VS Code extension side; the Python webapp code is equally reusable

**Action required:** Update CLAUDE.md "Architecture Context > Code Reuse from CodeFluent" section to reference Python module names instead of .ts files, and update "Tech Stack" section to reflect Python-only for MVP.

---

## D002: Stretch MVP scope (Option B) with diagnostics preview

**Date:** 2026-04-14
**Context:** Two MVP options were presented: (A) execution analytics + config assessment only, (B) add diagnostics preview demonstrating behavior-to-config correlation.
**Decision:** Option B -- include diagnostics preview.
**Rationale:**
- The tagline ("tells you what to change") requires at least a preview of behavior-to-config correlation to be credible
- Subagent metadata (total_tokens, tool_uses, duration_ms) plus output text pattern matching provides enough signal for meaningful recommendations without needing internal traces
- Rule-based heuristics (not LLM-powered) keeps complexity bounded
- Preview scope: error pattern detection, efficiency outliers, duration outliers -- three signal types is achievable

**Trade-off:** Adds approximately 1 week to MVP timeline. Mitigated by keeping diagnostics rule-based and limiting to 3 signal types.

---

## D003: Project-agnostic from day one

**Date:** 2026-04-14
**Context:** Could have hardcoded the tool to analyze a single known project for faster MVP.
**Decision:** Project discovery and selection is part of MVP scope.
**Rationale:**
- `~/.claude/projects/` contains multiple project directories; users need to choose
- CodeFluent and AgentFluent's own project sessions serve as test data, requiring multi-project support
- Discovery is low-complexity (directory listing) with high usability value
- Avoids hardcoded paths that would need refactoring later

---

## D004: Both user-level and project-level agent definition scanning

**Date:** 2026-04-14
**Context:** Agent definitions live in two locations: `~/.claude/agents/` (user-level, shared across projects) and `.claude/agents/` (project-level, specific to a repo).
**Decision:** Scan both locations in MVP. Agent SDK source parsing deferred.
**Rationale:**
- Both locations are documented in Anthropic's agent system and are actively used
- The `--scope` flag (user/project/all) gives users control
- Agent SDK `AgentDefinition` objects in source code require AST parsing -- significantly more complex, deferred until Agent SDK test data exists

---

## D005: Comprehensive test strategy (unit fixtures + integration against real data)

**Date:** 2026-04-14
**Context:** Options ranged from minimal unit tests to full integration testing.
**Decision:** Both unit tests with anonymized fixtures AND integration tests against real session data. CI/CD pipeline from the start.
**Rationale:**
- Fred considers extensive testing essential for this project
- Real session data from CodeFluent and AgentFluent projects is available for integration testing
- Fixtures ensure reproducibility; real data ensures correctness against actual formats
- CI/CD infrastructure prevents regressions as the codebase grows

---

## D006: CLI framework recommendation (Typer)

**Date:** 2026-04-14
**Context:** Fred had no strong preference on CLI framework, only that it be Python.
**Decision:** Recommend Typer (built on Click) as default, with Click as fallback.
**Rationale:**
- Typer provides type-hint-based CLI definition, reducing boilerplate
- Built on Click, so all Click features are available if needed
- Auto-generates `--help` from type annotations and docstrings
- Rich integration for formatted output
- Developer makes final call on framework choice

---

## D007: uv for dependency management

**Date:** 2026-04-14
**Context:** Fred specified uv as the dependency management tool. Alternatives considered: pip, poetry, pdm.
**Decision:** Use uv for all dependency management, virtual environment creation, and script running.
**Rationale:**
- Fred's explicit preference
- uv is fast, supports pyproject.toml natively, handles lockfiles, and replaces pip/pip-tools/virtualenv
- Aligns with modern Python tooling trends
- Project scaffolding should use `uv init` and `pyproject.toml`, not `setup.py` or `requirements.txt`

---

## D008: Subagent trace discovery does NOT expand MVP scope

**Date:** 2026-04-15
**Context:** Full subagent session JSONL traces discovered at `~/.claude/projects/<project>/<session-uuid>/subagents/agent-<agentId>.jsonl` (350 files across projects). Contains complete tool_use/tool_result sequences with `is_error` flags, per-step token usage, internal reasoning, all with `isSidechain: true`. Features previously classified as "requires Agent SDK data" (prompt-to-behavior correlation, detailed error analysis, internal reasoning analysis) are now feasible with existing Claude Code subagent data. See CodeFluent decisions D6-D8 for the discovery details.

**Options considered:**
- A) Expand MVP to parse subagent traces -- adds E8 (subagent trace parser) and E9 (deep diagnostics) to MVP scope
- B) Keep MVP scope fixed; subagent trace parsing becomes v1.1, with minor MVP adjustments to acknowledge the data exists

**Decision:** Option B -- MVP scope stays fixed. Subagent trace parsing is v1.1.

**Rationale:**
- The MVP is already a stretch scope (D002) with 7 epics and 35 stories
- Subagent trace parsing has real complexity: discovering `<session-uuid>/subagents/` directories, linking subagent files to parent session invocations via `agentId`, parsing a second JSONL format with `isSidechain: true`, handling parent-child relationships
- The MVP's value proposition ("tells you what to change") works with parent-session metadata. Full traces improve recommendation *quality* but don't change whether the concept works
- More data does not mean more MVP scope -- it means a better v1.1 with genuine per-tool-call evidence behind every recommendation
- The discovery is better served as the headline feature of v1.1 ("deep diagnostics") than as MVP scope creep

**MVP changes (minimal):**
- #14 (session discovery): add subagent file counting (enumerate, don't parse)
- #36 (diagnostics integration): add forward-looking note when subagent traces are detected

**Post-MVP additions (v1.1 roadmap):**
- E8: Subagent Trace Parser (discover, model, parse, link subagent JSONL files)
- E9: Deep Diagnostics (retry sequences, error recovery patterns, prompt-to-behavior correlation with per-tool-call evidence)

**Impact on positioning:** CodeFluent Decision D8 correctly identifies that AgentFluent's trigger is now "audience divergence, not data availability." For the MVP, this means AgentFluent demonstrates its value with existing metadata-level analysis. In v1.1, it leapfrogs to full-trace analysis that no other local-first tool offers. The phased approach turns the discovery into two product moments instead of one.

---

## D009: Version numbering convention -- stay in 0.x pre-1.0

**Date:** 2026-04-20
**Context:** The research update (`research-update-2026-04-15.md`) refers to subagent trace parsing as "v1.1" and the MVP as "v1.0." Actual published releases are v0.1.0 and v0.2.0 on PyPI. The next release needs a version number.

**Decision:** Stay in the 0.x series. The next release is **v0.3.0**. Reconcile the research doc's numbering as follows: research "v1.0" = published v0.2.x (MVP), research "v1.1" = published v0.3.0 (this release), research "v1.2+" = published v0.4+. Actual 1.0.0 is reserved for API stability commitment.

**Rationale:**
- The 0.x convention signals "pre-stable, expect breaking changes" -- appropriate for a tool still building its core analysis pipeline
- Jumping from v0.2 to v1.0 would imply API/CLI stability that does not yet exist. The CLI flags, JSON output schema, and diagnostics rules are all still evolving.
- Semver convention: 1.0.0 means "public API is stable." AgentFluent's CLI and JSON schema will likely change as subagent trace parsing reshapes the diagnostics output.
- The research doc's "v1.x" numbering was aspirational naming for roadmap phases, not semver. This decision makes the mapping explicit to avoid future confusion.

**1.0.0 criteria (when we get there):** CLI flag surface is stable, JSON output schema is versioned and documented, diagnostics rules have settled, and at least one release cycle has passed without breaking changes to the output format.

---

## D010: Subagent trace parsing promoted from "future v1.1" to v0.3 scope

**Date:** 2026-04-20
**Context:** D008 deferred subagent trace parsing to "v1.1" to keep MVP scope bounded. The MVP shipped successfully as v0.2. The next release is now being scoped.

**Decision:** Promote subagent trace parsing + deep diagnostics to v0.3 as the headline feature.

**Rationale:**
- The MVP proved the concept: metadata-level diagnostics work, the diagnostics pipeline is extensible, and the CLI infrastructure is solid.
- Subagent trace data is the highest-leverage improvement available. It transforms recommendations from "this agent seems slow" to "this agent retried Read 4 times on a missing file -- add error handling for FileNotFoundError in the prompt."
- D008's rationale for deferral was "MVP scope is already a stretch." That constraint no longer applies -- the MVP is shipped.
- The research update explicitly identifies this as "the wow release." Shipping it as v0.3 (not v0.4 or later) maintains momentum and delivers the core differentiator while the market gap remains open.
- Three existing enhancement issues (#90, #92, #95) are queued for the same horizon. Bundling them with subagent traces creates a cohesive release themed around "deep, actionable diagnostics."

**Scope cuts from the research update's v1.1 sketch:**
- "Internal reasoning analysis" (analyzing full assistant response content for quality) is deferred. The v0.3 deep diagnostics focus on tool-call-level evidence: errors, retries, and tool patterns. Reasoning quality assessment requires LLM-powered analysis (explicitly out of scope per D002's constraint extended to v0.3).
- "Prompt-to-behavior correlation" is partially included: delegation prompt + observed tool errors/retries are correlated. Full prompt quality scoring (comparing prompt instructions to observed behavior) is deferred to v0.4.

---

## D011: MCP server config assessment scoped as stretch for v0.3

**Date:** 2026-04-20
**Context:** MCP server config assessment (auditing observed `mcp__<server>__*` tool usage against configured servers) was proposed for v0.3. It shares architectural DNA with model-routing diagnostics (#95) -- both audit a config surface against observed behavior and recommend changes.

**Options considered:**
- A) In-scope for v0.3 -- bundle with model-routing since they share the "audit config surface" pattern
- B) Stretch for v0.3 -- scope the epic, create the stories, but cut cleanly if the core scope (subagent traces + deep diagnostics + #90/#92/#95) fills the release
- C) Defer to v0.4 -- don't scope it at all for this release

**Decision:** Option B -- stretch scope for v0.3.

**Rationale:**
- **For bundling (A):** MCP assessment and model-routing diagnostics both follow the pattern "observe tool usage in session data, compare against config, recommend changes." Building them together would produce a shared `ConfigAuditRule` pattern in the correlator. The subagent trace parser (which surfaces per-tool-call data including MCP tool names) directly feeds both features.
- **Against bundling (A):** v0.3 already has 5 must-include scope items. Adding MCP assessment as mandatory risks the release timeline. The subagent trace parser is upstream of everything and is genuinely complex.
- **Why not defer (C):** The epic is architecturally clean, the data is available (MCP tool names are prefixed `mcp__<server>__*` in JSONL), and scoping it now means the developer can build the shared audit framework with MCP in mind even if the MCP stories themselves slip to v0.4.
- **Stretch trade-off:** If the subagent parser and deep diagnostics land ahead of schedule, MCP assessment can be pulled in. If they don't, the stretch epic is cut cleanly with no impact on the core release.

---

## D012: Issues #90, #92, #95 integrated as stories within v0.3 epics

**Date:** 2026-04-20
**Context:** Three existing enhancement issues were filed with detailed specs: #90 (config dir override), #92 (delegation pattern recognition), #95 (model-routing diagnostics). All were labeled backlog/post-v0.2. Need to determine how they map into the v0.3 epic structure.

**Decision:**
- **#90** becomes a standalone story in a small "Config Directory Override" epic. It has no dependencies on subagent traces and can be implemented first as a low-risk win.
- **#92** becomes the seed issue for the "Delegation Pattern Recognition" epic. Its spec is thorough enough to serve as the epic definition; child stories will decompose its pipeline stages.
- **#95** becomes the seed issue for the "Model-Routing Diagnostics" epic. Its open scoping questions (heuristic vs data-driven, pricing dependency, agents-only vs interactive) become open questions in the PRD, to be resolved before implementation.

**Rationale:**
- All three issues have detailed specs with acceptance criteria -- promoting them to epic seeds avoids duplication.
- #92 and #95 both depend on per-agent invocation data that the subagent trace parser enriches. Sequencing: subagent parser first, then #92 and #95 can leverage the richer data.
- #90 is infrastructure (path override) with no analytical dependencies -- it can be implemented independently at any point in the release.
- #95 explicitly notes #80 (historical pricing) as a soft dependency for cost-savings estimates. Decision: use current rates for v0.3, note the limitation. #80 is not promoted to v0.3 scope.

---

## D013: v0.3 open-question resolutions (model routing + delegation cross-linking + main-session scope)

**Date:** 2026-04-20
**Context:** PRD `prd-v0.3.md` left three open questions on model-routing (#95) and delegation-pattern (#92) scoping. User resolutions below.

**Decisions:**
- **Model-routing thresholds (PRD §6.5 OQ1 / §11 OQ1):** Implement with configurable thresholds; tune against real `~/.claude/projects/` data during implementation rather than locking numbers up front. Start from the proposed values (read-only + <5 tool calls + <2k tokens = simple; write tools + >10 tool calls = complex; else moderate) but treat them as defaults, not contract.
- **#92 ↔ #95 cross-linking (PRD §11 OQ5):** Accept the composite framing: model-routing (#95) covers both custom subagents and `general-purpose`; for `general-purpose` it phrases the recommendation as *"create a custom agent with &lt;model&gt; for this task pattern"* and links to #92's draft output. Refine exact UX during implementation of #110 / #111.
- **#95 scope — agents only vs main session (PRD §6.5 OQ2 / §11 OQ5):** Agents only for v0.3. Claude Code main sessions are human-driven, which is CodeFluent's scope (human fluency coaching), not AgentFluent's (agent quality). Agent SDK main sessions, where the main session IS the configured agent, will be picked up as a separate follow-up issue once SDK session data structure can be verified.

**Rationale:**
- Threshold tuning against observed data avoids shipping numbers that fire on nobody's real sessions.
- The composite #92/#95 recommendation avoids two conflicting suggestions firing on the same agent cluster.
- Main-session scope distinction keeps AgentFluent and CodeFluent's analytical boundaries clean and defers SDK-specific work to when real data is available.

---

## D014: v0.3 composite recommendation pattern for #92 + #95 — merge at output

**Date:** 2026-04-20
**Context:** D013 resolved that when model-routing (#95) fires on a general-purpose agent cluster that delegation pattern recognition (#92) also flagged, the outputs should be merged into a single recommendation covering both the agent draft and the model choice. D013 did not pin the implementation pattern. Architect B's review of #111 flagged that without an explicit story or AC, #110 and #111 would ship with conflicting recommendations on the same cluster.

**Decision:** Implement the merge using **Option A — merge at output**. Both #110 (`DELEGATION_OPPORTUNITY`) and #111 (`MODEL_MISMATCH`) emit their signals independently. A new merge step in `diagnostics/pipeline.py` (or wherever orchestration lands per #108) detects overlapping signals (same `agent_type` + cluster ID) and produces a single `DiagnosticRecommendation` that uses #110's agent draft as the base, sets the draft's `model` field to #111's recommendation, and appends #111's cost-savings note.

**Rationale:**
- Each rule stays independently testable — #111 still produces a standalone "switch model" recommendation for custom subagents where #110 did not fire, and #110 still produces a standalone "create a custom agent" recommendation when #111 is not applicable.
- Merge logic is a single named pure function (`merge_cluster_recommendations`) that can be unit-tested with fixture signals.
- No cross-rule coupling — #111 does not need to know about #110's output format.
- Pattern generalizes to future cross-rule compositions (e.g., E6 MCP assessment overlapping with model routing).

**Sub-decision:** When only #111 fires (no overlapping #110 signal — e.g., cluster below #110's min-cluster-size of 5 or a single custom subagent), the output says only "switch model" with no weaker "consider creating a custom agent" nudge. This keeps the output clean and avoids speculative recommendations.

**Rejected alternative (Option B — pipeline-sequenced suppression):** Running #111 after #110 and having #111 inject its model choice into #110's existing recommendation (rather than emitting its own). Rejected because it couples #111's behavior to #110's output format, introduces emit-vs-inject branching inside the rule, and is harder to unit-test in isolation.

**Action:** Story #113 created under E3 (epic: deep-diagnostics) implementing the merge function and tests. #111's AC will be updated to reference this merge behavior.

---

## D015: Quality axis — scope-fit and release timing

**Date:** 2026-05-04
**Context:** PRD brief `prd-quality-axis.md` proposes a third axis (quality) alongside cost and speed in the recommendation engine. The brief identifies three signal tiers: within-session proxies (Tier 1), local git correlation (Tier 2), and GitHub enrichment (Tier 3). v0.5 is nearly complete (5 open issues remaining: #199, #205, #215, #227, #241). v0.6 milestone has 8 open issues including deferred v0.5 items (#198 report, #201 per-session scope, #265 test consolidation) and items from offload work.

**Options considered:**
- A) Full v0.6 epic (all three tiers)
- B) Tier-1-only as v0.6 headline alongside deferred v0.5 polish
- C) Defer entirely to v0.7

**Decision:** Option B — Tier 1 lands in v0.6 as a must-include epic. Tier 2 (local git) is scoped as a stretch story within the same epic (structurally enabled, implementation pull-in only if Tier 1 completes cleanly). Tier 3 is deferred to v0.7 as its own epic.

**Rationale:**
- v0.5 built the scaffolding this feature needs: priority ranking (#172), offload candidates (#189), calibration-sweep pattern (#260), `diff` comparison surface (#199). The infrastructure is fresh and ready.
- The under-recommendation gap for review subagents is a credibility problem *now*. Delaying to v0.7 means an entire release cycle where AgentFluent's recommendations diverge from best-practice guidance on subagent delegation.
- Tier 1 is zero-new-dependency: all signals come from existing JSONL session data. No git, no GitHub auth, no new data sources. This matches v0.5's "trustworthy diagnostics" foundation — extend the same data, not add new data.
- v0.6 already has 8 open issues but they are mostly S/XS items (report export, per-session scope, test consolidation, polish). Adding a focused Tier-1 epic (~12-15 dev days) is realistic alongside those items.
- Tier 2 as stretch avoids committing to git integration (which introduces subprocess calls, path resolution edge cases, and the question of whether AgentFluent should read git history at all). If Tier 1 ships cleanly and fast, pull in the simplest Tier-2 signal (feat-then-fix proximity). If not, cut cleanly.
- Tier 3 (GitHub) is a separate data-source integration with auth, rate limits, and privacy considerations. It deserves its own scoping exercise, not a stretch appendage.

---

## D016: Quality axis — Tier-1 signal selection and sequencing

**Date:** 2026-05-04
**Context:** Five Tier-1 signals proposed in `prd-quality-axis.md` §3: (1) user mid-flight corrections, (2) file rework density, (3) plan-revise-implement loops, (4) "reviewer caught" rate, (5) stuck-loop reframing. Need to select which ship first and which are stretch.

**Decision:** Ship signals (1), (2), and (4) as must-include. Defer (3) and (5).

**Rationale:**
- **(1) User mid-flight corrections** — highest-confidence quality proxy. Pattern matching on "no, do X instead" / "wait" / "actually" / "stop" in user messages is straightforward NLP. High signal-to-noise: if the user is correcting the agent frequently, that *is* a quality gap. Ships as a new `QualitySignalType` in the signal extractor.
- **(2) File rework density** — the data already exists (tool_use blocks with file paths on Edit/Write). Counting distinct-file re-edits within a session is a simple aggregation. Strong proxy: a file edited 5+ times in one session after "feature complete" language signals the parent needed an upfront review.
- **(4) "Reviewer caught" rate** — this is the signal that directly closes the under-recommendation gap. When architect/security-review/tester agents *do* run, measuring whether they produced substantive findings (and whether the parent acted on them) validates the recommendation. Without this signal, the quality axis has no positive evidence that review subagents help — only negative evidence that the parent struggles.
- **(3) Plan-revise-implement loops** — requires detecting `ExitPlanMode` events and measuring the delta between plan and implementation. This is feasible but depends on plan-mode detection infrastructure that doesn't exist yet. Defer to a follow-up story.
- **(5) Stuck-loop reframing** — the stuck-loop signal already exists as `STUCK_PATTERN`. Reframing it as a quality signal (not just efficiency) is a labeling/attribution change, not a new detection pipeline. It can be done as a small follow-up once multi-axis attribution is in place, or bundled with the axis-attribution story. Not worth a standalone story.

---

## D017: Quality axis — JSON schema: per-axis vector vs. synthesized-only

**Date:** 2026-05-04
**Context:** `prd-quality-axis.md` §7 item 3 asks whether the public JSON schema should expose per-axis `(cost_score, speed_score, quality_score)` or only a synthesized `priority_score` + axis label. The v0.5 `diff` work (#199) depends on schema stability for meaningful comparisons.

**Decision:** Expose both. `priority_score` (synthesized float, existing field) remains the sort key. Add `axis_scores: {cost: float, speed: float, quality: float}` and `primary_axis: "cost" | "speed" | "quality"` on `AggregatedRecommendation`. `axis_scores` is the internal vector; `primary_axis` is the human-readable label.

**Rationale:**
- `diff` consumers need to compare recommendations across runs. A synthesized score hides *why* a recommendation's priority changed. If a recommendation dropped from #2 to #8 because its quality score fell, the user needs to see that — not just that the composite number changed.
- Per-axis scores are strictly more information. Consumers who don't care can ignore them.
- Schema stability concern is manageable: `axis_scores` is additive (new field, not changing existing fields). `primary_axis` is additive. Neither breaks existing JSON consumers. The `priority_score` formula changes (now incorporates quality), but it was documented as tunable from the start (#172 module docstring: "Calibration pass against multi-contributor data is a v0.6 follow-up").
- The `axis_scores` dict is extensible — if v0.7+ adds a reliability axis or a maintainability axis, it's a new key, not a schema break.

---

## D018: Quality axis — pipeline architecture: parallel pipeline, join at recommendation layer

**Date:** 2026-05-04
**Context:** `prd-quality-axis.md` §7 item 5 asks whether to extend the offload pipeline to emit multi-axis scores, or add a parallel "quality candidates" pipeline that joins at the recommendation layer. Architect agent input recommended.

**Decision:** Parallel pipeline. New `diagnostics/quality_signals.py` module emits `DiagnosticSignal` instances with new `SignalType` values (`USER_CORRECTION`, `FILE_REWORK`, `REVIEWER_CAUGHT`). These join the existing signal list in `pipeline.run_diagnostics()`. The correlator and aggregation layer consume them like any other signal. Multi-axis scoring is computed in `aggregation.py` by classifying each signal's `SignalType` into an axis (`cost`, `speed`, `quality`) and summing per-axis contributions.

**Rationale:**
- Follows the established D014 pattern: rules emit independently, composition happens at the output layer.
- The offload pipeline (`parent_workload.py`) is cost-focused by design. Retrofitting quality scoring into it would violate single-responsibility and create coupling between cost estimation and quality heuristics.
- Quality signals are fundamentally different from offload candidates: they don't cluster parent-thread bursts or estimate savings. They detect behavioral patterns (corrections, rework, reviewer findings) and recommend *review-style subagents*, not cheaper-model offloads.
- New signal types flow through the existing dedup, correlation, aggregation, and priority-ranking infrastructure with minimal changes. The aggregation layer already has a `priority_score` formula — extending it with an axis classification is a focused change.
- Architect agent should review the axis-classification mapping and the updated priority formula before implementation begins. This is flagged as a gating step on the epic.

---

## D019: Quality axis — calibration data availability

**Date:** 2026-05-04
**Context:** `prd-quality-axis.md` §7 item 4 asks whether enough dogfood sessions exist containing both "architect was used and caught X" and "architect was not used and X slipped through" to calibrate quality signals.

**Decision:** Calibration data exists but is thin. Proceed with implementation using conservative thresholds, then run a targeted dogfood collection sprint before shipping.

**Rationale:**
- AgentFluent's own sessions have architect and security-review invocations (shipped in the project's `.claude/agents/` since v0.3). These provide "reviewer was used" positive examples.
- Sessions *without* review subagents (pre-v0.3 sessions, CodeFluent sessions) provide the counterfactual. Whether quality issues were present can be assessed by checking for user corrections and file rework in those sessions.
- The data is not abundant enough for statistical thresholds. Use the same approach as #260 (calibration-sweep notebook): sweep thresholds against real data, pick conservative defaults that avoid false positives, document the calibration in the notebook.
- A dedicated "quality signal calibration" story in the epic will handle the data collection and threshold tuning. This is the same pattern as v0.5's approach to offload calibration.

---

## D020: Quality axis — negative recommendations and recommendation copy

**Date:** 2026-05-04
**Context:** `prd-quality-axis.md` §7 items 6 and 7 ask about recommendation copy verbosity and whether AgentFluent should recommend *removing* a subagent that shows zero quality signal.

**Decisions:**
- **Recommendation copy:** Concise with axis label. Format: "[axis] observation — action." Example: "[quality] 7 user corrections in 3 sessions — consider an architect agent for design review before implementation." No didactic explanation of why quality matters. The axis label *is* the explanation. Verbose mode can expand with evidence details.
- **Negative recommendations (remove subagent):** Deferred. Out of scope for this epic. Reason: negative recommendations ("remove this agent") are high-risk for trust. A review subagent might catch a critical bug once per 50 runs and still be worth its cost. Recommending removal based on "zero quality signal observed in N sessions" requires establishing a baseline of expected catch rate per subagent type, which is a research question beyond this epic's scope. File as a future issue if the need becomes concrete.

**Rationale:**
- Concise copy avoids the "too didactic" risk the brief flagged. Users who run AgentFluent repeatedly will internalize the axis meaning quickly. First-time users get the axis label as a breadcrumb; `--verbose` provides the evidence trail.
- Negative recommendations are a different product surface (removal advice vs. addition advice) with a much higher false-positive cost. Getting "add a reviewer" wrong wastes some tokens. Getting "remove a reviewer" wrong causes a quality regression. The asymmetry justifies deferral.

---

## D021: Quality axis — priority-score composition: annotations approach

**Date:** 2026-05-04
**Context:** D017 added `axis_scores` and `primary_axis` to the JSON schema but did not specify how per-axis scores compose into the single `priority_score` that `aggregation.py` and the `diff` module depend on. The architect review on #268 identified this as a blocking gap: story #272 cannot be implemented without a defined formula. Three approaches were evaluated: (a) `max(per_axis_scores.values())`, (b) weighted sum across axes, (c) annotations approach — keep the existing single formula and add a quality-evidence additive term.

**Decision:** Annotations approach. The existing `_compute_priority_score` formula in `aggregation.py` (lines 159-171) gains one new additive term:

```
priority_score = severity_rank * W_SEVERITY
              + log1p(count) * W_COUNT
              + summed_savings_usd * W_COST
              + has_trace_evidence * W_TRACE
              + quality_evidence_factor * W_QUALITY  # NEW
```

Per-axis scores (`axis_scores`) and `primary_axis` are computed as **post-hoc annotations** from each recommendation's contributing signal types, not as inputs to the priority formula. The axis classification mapping (one signal type to one axis) determines which signals contribute to which axis score. `primary_axis` is the axis with the highest per-axis score for that recommendation.

`quality_evidence_factor` is a simple indicator: `1.0` if any contributing signal is quality-typed (`USER_CORRECTION`, `FILE_REWORK`, `REVIEWER_CAUGHT`), with an optional boost for high correction rates or strong reviewer-caught evidence. The exact value and `W_QUALITY` weight are deferred to story #274 (calibration) for tuning against real data. Initial conservative default: `W_QUALITY = 5.0` (same magnitude as `W_TRACE`).

**Completes:** D017 (which defined the schema but not the formula).

**Rationale:**
- **Minimal disruption.** Existing cost/speed-only recommendations with no quality signals get `quality_evidence_factor = 0`, so their `priority_score` values do not change. This is critical for `diff` comparison semantics (see below).
- **`diff` stability.** The `diff` module (`diff/compute.py`) computes `priority_score_delta = current - baseline` for persisting recommendations. With the annotations approach, a diff between a pre-quality and post-quality baseline shows zero `priority_score_delta` for recommendations that have no quality signals. New quality-axis recommendations appear as "new" entries. The first post-upgrade diff is clean and useful for regression detection. Under the full-decomposition alternative, *every* persisting recommendation would show a nonzero delta, making the first diff useless.
- **Calibration-cheap.** One new weight (`W_QUALITY`) to tune, not three separate per-axis scoring regimes each needing their own calibration pass.
- **Forward-extensible.** If calibration data shows the single-formula approach doesn't rank quality recommendations high enough, the formula can be refactored to full axis decomposition in v0.7. The `axis_scores` annotations are already in the schema (D017), so the data is available for post-hoc analysis of whether the single formula is adequate.
- **Closes the under-recommendation gap.** Quality signals now contribute to the composite score via the new term. A recommendation driven purely by `USER_CORRECTION` signals will have `quality_evidence_factor > 0`, boosting it above recommendations with the same severity/count but no quality evidence. Combined with the axis attribution in CLI output (D020), users see *why* the recommendation fired.

**Rejected alternatives:**
- **`max(per_axis_scores.values())`:** Proposed in story #272's implementation notes. Rejected because it determines a recommendation's priority entirely by its strongest axis, which could re-rank the entire list in surprising ways (a low-cost recommendation with incidental quality evidence would outrank a high-cost recommendation). The max approach also requires defining how per-axis scores are computed from the existing weights, which reintroduces the decomposition problem.
- **Weighted sum across axes:** Requires choosing inter-axis weights (how much is 1 unit of quality worth relative to 1 unit of cost?), which is a three-axis calibration problem with no data to inform it. Deferred until calibration data exists.
- **Per-axis threshold with independent surfacing:** Fundamentally different UX (recommendations appear in axis-specific sections rather than a single priority list). Out of scope for this epic; could be a v0.7 display option.

**Reference:** Architect review on #268 (concern #1) recommended this approach. D017 defined the schema shape that this decision completes.

---

## D022: Quality axis — single-axis signal classification (no cross-cutting)

**Date:** 2026-05-04
**Context:** Story #272's implementation notes proposed that `ERROR_PATTERN`, `PERMISSION_FAILURE`, and `MCP_MISSING_SERVER` are "cross-cutting" signals that "contribute to all axes at reduced weight." The architect review on #268 (concern #3) identified that the mechanics of cross-cutting classification are unspecified: it is unclear whether three synthetic signals are emitted, whether the weight reduction applies to the priority formula, or how it interacts with aggregation grouping (which keys on `signal_types`).

**Decision:** Drop cross-cutting classification for Tier 1. Every `SignalType` maps to exactly one axis:

- **Cost:** `TOKEN_OUTLIER`, `MODEL_MISMATCH`, `MCP_UNUSED_SERVER`
- **Speed:** `DURATION_OUTLIER`, `RETRY_LOOP`, `STUCK_PATTERN`, `TOOL_ERROR_SEQUENCE`, `ERROR_PATTERN`, `PERMISSION_FAILURE`, `MCP_MISSING_SERVER`
- **Quality:** `USER_CORRECTION`, `FILE_REWORK`, `REVIEWER_CAUGHT`

The mapping is a module-level constant dict `SIGNAL_AXIS_MAP: dict[SignalType, Axis]` in `aggregation.py`. `ERROR_PATTERN`, `PERMISSION_FAILURE`, and `MCP_MISSING_SERVER` are classified as speed (operational health signals; speed is the closest existing axis).

**Rationale:**
- **Simplicity.** One signal, one axis. No mechanics to define for reduced-weight multi-axis contribution. No interaction with the aggregation grouping key.
- **No calibration data for cross-cutting weights.** We have no empirical basis for deciding how much an `ERROR_PATTERN` should contribute to cost vs. speed vs. quality. Single-axis classification is an honest reflection of our current knowledge.
- **Aggregation clarity.** `AggregatedRecommendation.axis_scores` is computed by summing contributions from signals classified to each axis. With single-axis classification, each signal's contribution goes to exactly one bucket. The `primary_axis` is always deterministic.
- **Reversible.** If v0.7 calibration data shows that `ERROR_PATTERN` should contribute to quality (e.g., error patterns that a review subagent would catch), changing the mapping is a one-line edit to `SIGNAL_AXIS_MAP`. The `Axis` enum and `axis_scores` dict accommodate this without schema changes.

**Amends:** D018, which described "summing per-axis contributions" without specifying single-axis vs. cross-cutting. This decision pins the classification to single-axis.

**Reference:** Architect review on #268 (concern #3).

---

## D023: pm subagent — Write hook allows agent-memory paths (preserve `memory: user`)

**Date:** 2026-05-05
**Context:** Issue #292. The pm subagent (`~/.claude/agents/pm.md`) declares `memory: user` in its frontmatter, granting it user-scope auto-memory at `~/.claude/agent-memory/pm/`. Its inline `PreToolUse` Write hook only allowed paths matching `/.claude/specs/` or `/docs/`, so any auto-memory write was silently blocked with the message *"PM agent may only write to .claude/specs/ and docs/"*. Surfaced by `agentfluent analyze --diagnostics --verbose` as a `tool_error_sequence`.

**Decision:** Option A — extend the hook regex to also allow `~/.claude/agent-memory/pm/`, and update the prompt's writable-paths section to enumerate all three allowed roots. The `memory: user` directive is intentional: pm benefits from remembering user preferences (framework choice, label conventions, prioritization style) across sessions.

**Rationale:**
- Preserves the explicit `memory: user` capability rather than silently dropping it.
- The auto-memory path is namespaced under the agent's name (`/pm/`), so the broadened regex does not let pm escape into other agents' memory or arbitrary paths.
- Failure mode was silent (hook denial only appears in JSONL traces), so the misconfig was hard to spot without dogfooding agentfluent against itself — fixing it improves the template for anyone copying this pm.md.

**Reference:** Issue #292; surfaced by dogfooding run on 2026-05-05.

---

## D024: Date/time-range filtering — session-level on first-message timestamp

**Date:** 2026-05-05
**Context:** Feature request for `--since`/`--until` flags on `analyze` and `list`. Need to decide what timestamp dimension to filter on. Options: (A) per-message timestamp with partial-session inclusion, (B) session file mtime, (C) first-message timestamp (whole-session inclusion), (D) last-message timestamp.

**Decision:** Option C — filter at session granularity using first-message timestamp. A session is entirely in or entirely out based on its earliest analytical message's timestamp.

**Rationale:**
- **Per-message filtering (A) is architecturally expensive.** The analytics pipeline (`analyze_session()`) computes metrics per-session: token totals, tool patterns, agent invocations, diagnostics signals. Partial-session inclusion would require re-running the pipeline on filtered message subsets, a large refactor with unclear benefit. Diagnostics signals (retry loops, stuck patterns) are computed from sequential tool-call patterns within a session -- splitting a session mid-signal would lose or corrupt the signal.
- **File mtime (B) is unreliable.** Cloud sync, file copies, and backup restores alter mtime. Content-derived timestamps are deterministic and reproducible.
- **First-message timestamp (C) matches user intent.** The primary workflow is "sessions after I made a config change." Config changes happen between sessions. A session that *started* after the fix is the relevant unit for "did the fix work." Performance cost is bounded: reading the first analytical message's timestamp requires parsing at most a few lines per file (the first non-SKIP_TYPES message).
- **The "straddling session" edge case is rare.** Sessions rarely span config edits. If they do, the user can target them with `--session <uuid>`.
- **Forward-compatible.** `SessionInfo` can later expose `last_message_timestamp` to enable a `--include-straddling` flag without breaking existing behavior.

**Tradeoff accepted:** A multi-hour session where the user edits a config mid-session cannot be partially analyzed. The user must end and restart sessions around config changes. This matches Claude Code's natural behavior (sessions rarely persist across major config edits).

**Reference:** PRD `prd-date-range-filtering.md` Section 4.

---

## D025: Date/time-range filtering — no partial-session metric recomputation

**Date:** 2026-05-05
**Context:** Given D024's session-level filtering, the question remains: if a session's first message is inside the window but some of its messages predate or postdate the window, should metrics be recomputed on only the in-window messages?

**Decision:** No. Whole-session semantics. All metrics reflect the full session content, regardless of whether individual message timestamps fall outside the specified window.

**Rationale:**
- The session is the natural unit of agent execution. A retry loop, stuck pattern, or error sequence that straddles a time boundary is one behavioral event. Splitting it would lose the signal.
- Token totals for a session are pre-aggregated in `toolUseResult` metadata. Recomputing them on a message subset would require ignoring the metadata and recalculating from scratch -- a reliability concern.
- Implementation simplicity: zero changes to `analyze_session()`, `compute_token_metrics()`, or any diagnostics rule. The filtering is a pure session-list filter at the CLI layer.
- Acceptable precision: for the "verify a fix" workflow, a session that started in the window has all its behavior relevant to post-fix evaluation. The few messages that might predate the window (e.g., the opening prompt) are context, not noise.

**Reference:** PRD `prd-date-range-filtering.md` Section 7.

---

## D026: v0.6 scoping — Quality axis Tier 1 IS the headline, alongside date-range filtering

**Date:** 2026-05-05
**Context:** v0.5.1 shipped (2026-05-05). The v0.6.0 milestone held 19 open issues from mixed origins: deferred v0.5 polish (#198, #201, #203, #204), date-range filtering (#293-#299), CLI ergonomics (#285), diagnostics polish (#281, #264, #263, #262), and a test consolidation (#265). The quality-axis epic (#268) and its stories (#269-#274) were unmilestoned but labeled `priority:high`. The headline question: does Quality Axis Tier 1 land in v0.6, or does v0.6 ship date-range filtering + cleanup and defer quality to v0.7?

**Options considered:**
- A) Quality axis + date-range filtering as dual headlines (total ~22-26 dev days)
- B) Date-range filtering only + cleanup (total ~12-15 dev days, ships faster but weaker narrative)
- C) Quality axis only, defer date-range to v0.7 (blocks the dogfooding loop verification)

**Decision:** Option A — both quality axis Tier 1 and date-range filtering ship in v0.6. The release carries two parallel streams with no cross-dependencies.

**Rationale:**
- **Credibility urgency.** The under-recommendation gap for review subagents is a product credibility problem. Every release without the quality axis is a release where AgentFluent's output diverges from best-practice guidance. Deferring to v0.7 means 6-8 more weeks of this gap.
- **Infrastructure freshness.** v0.5 shipped priority ranking (#172), offload candidates (#189), calibration sweep (#260), and `diff` (#199). These form the exact scaffolding quality signals plug into. The code is fresh in mind and stable.
- **Effort fits the window.** Quality Tier 1: ~12-15 days. Date-range filtering: ~5-8 days. Combined with docs + CLI polish: ~22-28 days. Within the 3-4 week target (matching v0.5's actual timeline).
- **Streams parallelize.** Quality axis and date-range filtering have zero cross-dependencies until docs at the end. A solo dev can interleave them effectively.
- **Date-range filtering cannot be deferred (Option C).** The dogfooding loop requires `--since` to verify pm.md fixes without historical noise. This was the triggering use case.
- **Option B is a release without a compelling narrative.** "You can filter by date now" is a nice-to-have feature, not a product moment worth announcing.

**Scope cuts to make Option A fit:**
- **#274 (calibration notebook) moved to stretch.** Conservative defaults are shippable. Calibration refines but doesn't gate.
- **#198 (Markdown report), #201 (per-session scope), #203, #204 deferred to v0.7.** These are output-format and polish items that benefit from the quality axis output stabilizing first.
- **#263, #262 deferred.** Performance and threshold recalibration items that need more diverse data.
- **#170, #171, #183 remain unmilestoned.** Independent improvements with no connection to the v0.6 themes.

**Risk mitigation:** If quality axis runs long, it has a clean internal cut point: #269-#271 (signals) + #273 (output labels) can ship without #272 (multi-axis scoring). Signals still flow through existing aggregation with default priority scoring. The quality dimension would be visible in output even without the scoring refinement, and #272 can follow in v0.6.1.

**Reference:** PRD `prd-v0.6.md`. Prior decisions D015-D022 established all architectural choices for the quality axis; this decision confirms v0.6 is the delivery vehicle.

---

## D027: `primary_axis` tiebreaker order — `quality > speed > cost`

**Date:** 2026-05-05
**Context:** D022 established single-axis classification: every recommendation gets exactly one `primary_axis` derived from its per-axis scores. The implementation in #272 uses `max(axis_scores, key=axis_scores.get)`, which is non-deterministic on ties (depends on dict insertion order). When two or more axes have equal scores for a recommendation, what tiebreaker order should `primary_axis` resolve to? Surfaced by architect review on #272 (issuecomment-4385199185) and resolved by PM input on the same issue (issuecomment-4385286798).

**Options considered:**
- A) `quality > speed > cost` — earlier wins ties; quality wins all ties.
- B) `cost > speed > quality` — preserves status quo: existing v0.5 cost/speed recommendations keep their familiar labeling.
- C) `speed > cost > quality` — speed pain is most visceral.

**Decision:** Option A — `AXIS_TIEBREAKER = ("quality", "speed", "cost")`. Implementation:

```python
AXIS_TIEBREAKER: tuple[str, ...] = ("quality", "speed", "cost")
# Why: ties resolve in favor of the v0.6 headline axis so the new quality
# capability is visible by default. See decisions.md D027.
primary_axis = max(AXIS_TIEBREAKER, key=lambda a: axis_scores[a])
```

**Rationale:**
- **Aligns with the v0.6 product positioning.** D026 confirmed quality axis IS the v0.6 headline. The tiebreaker should reinforce, not undercut, that positioning. A genuinely cross-axis recommendation surfacing as `[quality]` matches the release's narrative; surfacing as `[cost]` would make the new capability invisible on the very recommendations where it matters most.
- **Maximizes first-run visibility.** Users running `agentfluent analyze` for the first time after upgrading to v0.6 should see quality labels in their output. The tiebreaker order shapes that first impression.
- **Diff stability is unaffected.** `primary_axis` is a new field with no v0.5 baseline to drift from. The first post-upgrade `agentfluent diff` shows quality labels emerging on persisting recommendations as new evidence arrives — exactly the intended behavior. There is no backward-compatibility concern because there is no prior `primary_axis` value to flip.
- **True ties become rare post-calibration.** #274 (deferred to stretch but landing within v0.6 or v0.6.1) tunes per-signal weights. Continuous-valued scores rarely hit exact equality after calibration. The tiebreaker matters most for the first-release experience, not the steady state.
- **Determinism is required regardless of order.** The dominant engineering reason for an explicit tuple over `max(dict, key=...)` is determinism across runs and Python versions. The product question is only about *which* deterministic order; once that is decided, the choice has low ongoing maintenance cost.

**Tradeoff accepted:** Recommendations where cost evidence and quality evidence tie exactly will be labeled `[quality]` even when a v0.5 user might have expected `[cost]`. This is intentional — the user's mental model should update to reflect that AgentFluent now scores quality.

**Reference:** Issue #272 architect review (issuecomment-4385199185); PM decision (issuecomment-4385286798). Implements the tiebreaker contract referenced in D022 (single-axis classification).

---

## D028: FEAT_FIX_PROXIMITY deferred from v0.6 to v0.7

**Date:** 2026-05-08
**Context:** Issue #275 (Tier-2 stretch story under epic #268) proposes a `FEAT_FIX_PROXIMITY` signal using local `git log` to detect feat-then-fix commit pairs and correlate back to review subagent usage. All six Tier-1 must-have stories (#269-#274) shipped and merged, plus two calibration bugfixes (#321, #322). v0.6 is in endgame -- #287 (docs catch-up) is the last issue before tagging the release.

**Decision:** Defer #275 to v0.7. Do not assign a milestone or change priority.

**Rationale:**
- The epic's stated under-recommendation gap is closed by the shipped Tier-1 signals (REVIEWER_CAUGHT, USER_CORRECTION, FILE_REWORK). FEAT_FIX_PROXIMITY would add confirming evidence but is not needed for the goal.
- The signal introduces a new data source (git subprocess), a new CLI flag (`--git`), and heuristic timestamp linkage between git commits and JSONL sessions -- a risk surface unlike anything else in v0.6.
- 2-4 days of implementation on the critical path before #287 docs is the wrong trade in endgame.
- v0.7 already holds Tier 3 (GitHub enrichment). FEAT_FIX_PROXIMITY is the natural bridge to external-data-source work and benefits from co-design with Tier 3's subprocess and enrichment infrastructure.

**Reference:** PM scope decision comment on #275 (issuecomment-4403479791).

---

## D029: `--session` semantics breaking change — communicate via CHANGELOG, keep minor bump

**Date:** 2026-05-09
**Context:** D032 (in epic #351 body) changed `--session <uuid>` to auto-scope diagnostics, not just token/cost metrics. The same command (`analyze --session <uuid> --diagnostics`) now produces different output in v0.7 than v0.6: diagnostics aggregate over the named session only, instead of rolling up the entire window. This is a semantics-level breaking change that needs an explicit communication strategy. Surfaced as OQ1 in `prd-v0.7.md`.

**Options considered:**
- A) Conventional Commit `feat!:` to trigger a major version bump via release-please. Rejected on the grounds that 0.x explicitly reserves majors for 1.0.
- B) Document under `BREAKING CHANGE:` in CHANGELOG, keep as a 0.7.0 minor bump.
- C) Deprecation period: v0.7 keeps v0.6 behavior + emits a deprecation warning when `--session` is used without an explicit scope flag; v0.8 flips the default.

**Decision:** Option B. Document the behavior change in CHANGELOG with `BREAKING CHANGE:` notation and a clear before/after example. Keep release-please's minor bump (0.7.0). Tracked by issue #360.

**Rationale:**
- The 0.x series leading zero already signals "expect breaking changes." A pre-1.0 deprecation period adds friction without buying meaningful safety, since AgentFluent has no external API consumers locked to v0.6 semantics.
- The change makes `--session` consistent with how token/cost metrics already scope. The current rollup-with-session-flag behavior is a latent bug, not a feature anyone depends on.
- Option C carries real cost: a temporary scope-disambiguation flag in v0.7 that gets removed in v0.8, plus the warning machinery and tests. Not worth it for a 0.x change.

**Reference:** `prd-v0.7.md` §5 OQ1; epic #351 body (D032).

---

## D030: `agentfluent report` section ordering — metrics first, then diagnostics

**Date:** 2026-05-09
**Context:** Issue #354 specifies the section renderers for the new `agentfluent report` Markdown output (epic #351). The proposed order is summary → token metrics → agent metrics → diagnostics → offload → footer. An alternative is to lead with diagnostics (the actionable content) and place metrics after as supporting evidence. Surfaced as OQ2 in `prd-v0.7.md`.

**Options considered:**
- A) Metrics first, then diagnostics. Mirrors the `analyze` table order.
- B) Diagnostics first, then metrics. Leads with actionable findings.

**Decision:** Option A. Section order: Summary → Token Metrics → Agent Metrics → Diagnostics → Offload → Footer.

**Rationale:**
- Matches the `analyze` table order users already know, so a Markdown report reads as a faithful rendering of the same content rather than a re-ordered view.
- Grounds the reader in the data before they encounter recommendations. The diagnostics section's findings reference metric values; reading metrics first means those references resolve immediately.
- Diagnostics are not buried — the summary at the top can surface headline findings if needed without requiring the full diagnostics section to lead.
- Reviewers who skim from the top of a PR comment get the headline summary first either way; the metrics-vs-diagnostics ordering matters more for full-document reads where the analyze-parity argument wins.

**Reference:** `prd-v0.7.md` §5 OQ2; epic #351 body; issue #354.

---
