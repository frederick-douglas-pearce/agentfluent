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

## D031: `agentfluent report` as a separate subcommand, not `analyze --format markdown`

**Date:** 2026-05-09
**Context:** Epic #351 ships a Markdown rendering of `analyze` output. Two surface shapes were possible: extend `analyze` with `--format markdown` (one command, two output formats) or introduce a new `report` subcommand that consumes an `analyze --json` envelope and re-renders it. Surfaced as OQ3 in `prd-v0.7.md`.

**Options considered:**
- A) `analyze --format markdown` — adds a third value to the existing `--format` flag.
- B) `agentfluent report snap.json` — new subcommand that ingests a saved envelope.

**Decision:** Option B. Ship `agentfluent report` as a separate command. Stable workflow: `agentfluent analyze --project P --json > snap.json && agentfluent report snap.json > out.md`.

**Rationale:**
- Decouples rendering from ingestion. Snapshots round-trip through file storage, PR comments, and CI artifact pipelines without re-running the JSONL parser + diagnostics pipeline — a 10x cost saving for the common "render this snapshot three different ways" workflow.
- Composability with v0.8 surfaces (`diff` envelopes, future formats) is one-line dispatch — adding a renderer for a new envelope command stays a one-line change in `_RENDERERS`.
- Avoids the `--format` flag accumulating render-time options (page breaks, section toggles, etc.) that don't belong on the analysis command.

**Reference:** `prd-v0.7.md` §5 OQ3; epic #351 body; issue #198 / #353.

---

## D032: `--session` auto-scopes the diagnostics pipeline

**Date:** 2026-05-09
**Context:** v0.6 `--session <uuid>` scoped token/cost metrics to the named session but rolled diagnostics across the full project, producing misleading `--session` + `--diagnostics` output. Epic #351 cleans this up.

**Decision:** When `--session` is provided, scope the entire pipeline — metrics, signals, recommendations, offload candidates, quality signals — to the named session. No new flag; same `--session` means the same scope across the whole envelope.

**Rationale:**
- The v0.6 split was a latent bug, not a feature. Users debugging a specific session expected single-session diagnostics; the rollup behavior surprised everyone who hit it during dogfood.
- Single-flag scope keeps the mental model simple: `--session foo` answers "what happened in foo?" without the user composing additional scope flags.
- The breaking-change risk is communicated via CHANGELOG prose (D029) rather than a major version bump, since 0.x already signals "expect breaking changes."

**Reference:** epic #351 body; issue #357 / #358 / #359 / #360.

---

## D033: built-in agents silently excluded from `unused_agent` signal

**Date:** 2026-05-09
**Context:** Epic #350 ships the `UNUSED_AGENT` signal to flag custom agents defined but never delegated to. Built-in agents (`Explore`, `Plan`, `general-purpose`, `pm`, `tester`, `architect`, `code-reviewer`, `security-review`) are always available regardless of project config; flagging them as "unused" would generate noise indistinguishable from a real config-effectiveness gap.

**Decision:** The `UNUSED_AGENT` signal only considers custom agent definitions in `~/.claude/agents/` and `.claude/agents/`. Built-ins are silently filtered out before the unused-detection pass.

**Rationale:**
- Built-ins can't be "removed" — they exist whether or not the project uses them. A signal that fires on every project for every unused built-in adds zero signal value.
- The user's actionable surface is custom agents only; that's where the recommendation engine has leverage to suggest removal or scope changes.
- Filter happens at signal extraction, not at the recommendation layer, so the JSON output is clean — no noisy entries to filter post-hoc.

**Reference:** epic #350 body; issue #346.

---

## D034: diagnostics-version drift in `diff` is warn-only (non-fatal)

**Date:** 2026-05-09
**Context:** Epic #349 stamps `diagnostics_version` on `analyze --json` envelopes so `agentfluent diff` can detect when baseline and current were generated by different AgentFluent releases. Without a warning, signal-count deltas conflate real behavior changes with detector-sensitivity changes between releases.

**Options considered:**
- A) Warn-only — print a non-fatal notice; diff continues.
- B) Hard-fail on version mismatch — require user to re-baseline or pass `--ignore-version-drift`.
- C) Add a `--strict` mode that opts into the hard-fail behavior.

**Decision:** Option A for v0.7. Print a clear warning when versions differ, run the diff normally. `--strict` mode (Option C) deferred to a future release; no decision yet on whether it's ever needed.

**Rationale:**
- Hard-fail blocks the common case (analyst-running-diff-against-an-old-baseline) for a problem they already know about. Most version drifts are tolerable — the user can read the warning and judge.
- `--strict` is a flag we'd have to maintain; defer until at least one user reports the warning isn't loud enough.
- The version stamp itself is the load-bearing change; the gating policy can evolve without an envelope schema change.

**Reference:** epic #349 body; issue #347.

---

## D035: LLM-call augmentation candidates — tracking discipline established

**Date:** 2026-05-18
**Context:** The `TOOL_ORCHESTRATION_CHAIN` signal (PRD `prd-advanced-tool-use-diagnostics.md`) uses a rule-based proxy to detect wasteful tool-call chains. The semantic sub-detection ("did this intermediate result affect the final output?") is the kind of judgment that an LLM call would handle well but that rules approximate poorly. Fred flagged that AgentFluent should start collecting these cases systematically so the project is ready when LLM-call augmentation becomes worth the cost.

**Decision:** Establish a running list of "future LLM-call augmentation candidates" as a section within the Advanced Tool Use Diagnostics PRD (Section 9). Each candidate records: the signal, the sub-detection an LLM would improve, what the rule-based version does, approximate cost/call, and expected precision/recall delta. The list starts with one entry (TOOL_ORCHESTRATION_CHAIN intermediate-result relevance) and grows as new candidates are identified.

**This is NOT a commitment to add LLM calls.** It is a tracking discipline that ensures:
- The project knows exactly where rule-based precision falls short
- Cost/benefit is quantified before implementation
- The decision to add an LLM call (if it ever happens) has a clear trigger: rule-based FP rate exceeds threshold AND infrastructure exists for optional LLM integration

**Format per candidate:**

| Field | Purpose |
|---|---|
| Signal | Which diagnostic signal benefits |
| Sub-detection | The specific classification step an LLM would perform |
| Rule-based approach | What the current heuristic does |
| LLM approach | What the LLM call would do |
| Approximate cost/call | Estimated token cost at cheapest suitable model |
| Expected precision delta | How much precision improves over rule-based |
| When to implement | Triggering conditions for actual implementation |

**Candidate #1:** `TOOL_ORCHESTRATION_CHAIN` — intermediate-result relevance classification. Rule-based: token-to-tool ratio proxy (estimated 60-70% precision). LLM-augmented: semantic relevance check (estimated 85-90% precision). Cost: ~$0.02-0.05/invocation at Haiku pricing. Trigger: dogfood confirms >30% FP rate AND AgentFluent has an opt-in LLM infrastructure layer.

**Rationale:**
- Avoids the anti-pattern of adding LLM calls reactively without tracking why. Each candidate is justified by a specific precision gap.
- Keeps D002 (rule-based for MVP and beyond) intact while creating a structured path for evolution.
- The tracking cost is near-zero: a section in the PRD, updated when new candidates are identified.
- Quantifying cost/call now ensures future implementation decisions compare marginal precision gain against marginal API cost — not vibes.

**Reference:** `prd-advanced-tool-use-diagnostics.md` Section 9. D002 (rule-based constraint). Fred's directive: "start collecting examples where LLM API calls would have high impact."

---

## D036: Cache-anomaly recommendation target — `"platform"` (not `"model"`)

**Date:** 2026-05-22
**Context:** Epic #433 (C-006b) adds a `CACHE_ANOMALY` signal for detecting the April 2026 thinking-cache bug pattern. The architect's design proposed `target = "model"` on the correlator rule's recommendation but flagged it as a stretch: "there's no config change the user can make to fix a platform infrastructure bug." Architect left this as a PM judgment call, noting that a new `target = "platform"` value has "downstream implications for the CLI formatter's column rendering."

**Options considered:**
- A) `target = "model"` — reuse the closest existing target value; no new target string.
- B) `target = "platform"` — new target value that honestly communicates "this is a platform advisory, not a config change."

**Decision:** Option B — `target = "platform"`.

**Rationale:**
- **Semantic honesty.** Every existing `target` value names a user-editable config surface: `tools`, `prompt`, `model`, `mcp`, `description`, `subagent`. The user reads the Target column as "where to look / what to change." For a platform bug, the answer is "platform" — not "model." Using `"model"` would teach users that the Target column lies.
- **Zero formatter cost.** The architect's concern about "downstream implications for the CLI formatter's column rendering" was investigated and resolved: `table.py` renders `target` as a plain `escape(agg.target)` string (lines 377, 467). No enum, no branching, no hardcoded list. A new `"platform"` value displays correctly with zero code changes.
- **Aggregation and priority scoring are orthogonal.** `target` is not an input to `SIGNAL_AXIS_MAP`, `priority_score`, or `axis_scores`. The axis for `CACHE_ANOMALY` is `Axis.SPEED` (independent of target).
- **Establishes the advisory pattern.** This is the first recommendation where the user cannot fix the issue by editing their agent config. Future signals of this type (version deprecations, known platform regressions, API changes) will also need a non-config target. `"platform"` sets the precedent cleanly.
- **Low cost, high reversibility.** One new string value. If it turns out to confuse users in practice, changing to `"model"` later is a one-line edit with no schema break.

**Deferred:** Formalizing `target` as a `Literal` or `StrEnum` type. Today it is `str` on `DiagnosticRecommendation`. If more platform advisories accumulate, typing it is a separate cleanup story.

**Reference:** Epic #433 body (architect's open question); stories #435, #436, #438.

---

## D037: anthropic-research scout cadence — bi-weekly default + manual postmortem trigger

**Date:** 2026-05-24
**Context:** The `anthropic-research` subagent ([`.claude/agents/anthropic-research.md`](../../.claude/agents/anthropic-research.md)) surveys Anthropic engineering blog posts, news, Claude Code changelog, and Agent SDK changelogs to surface candidates for AgentFluent's roadmap. The agent description explicitly mentions "scheduled or manual research ticks" but the cadence was never specified. With Phase 3 of the research pipeline complete (Epic #439) and the cron-deployment work tracked in #451, picking a default cadence is the next gap.

**Research (upstream tempo data, gathered 2026-05-24):**

| Source | Tempo | Most recent activity |
|---|---|---|
| Claude Code `CHANGELOG.md` | **30 commits in 30 days** (~1/day in active periods); some clustering (May 21 had 4 commits) | last commit 2026-05-23 |
| Claude Code versions in current changelog | 35 distinct (v2.1.108–v2.1.150) | — |
| TypeScript Agent SDK `CHANGELOG.md` | ~9 recent versions visible (0.3.142–0.3.150) | active |
| Python Agent SDK `CHANGELOG.md` | ~6 recent versions visible (0.2.82–0.2.87) | less active |
| Anthropic Engineering blog | **1-2 articles/month** | postmortem published 2026-04-23 |
| Anthropic News | ~1-2/week, mostly non-technical announcements | continuous |

**Scout yield (single data point, 2026-05-20 manual run):** 32 sources reviewed → 8 candidates (~25% conversion).
- 27 Claude Code versions → 5 candidates (C-001 through C-005)
- 4 TS SDK versions → 2 candidates (C-007, C-008)
- 1 postmortem → 1 candidate (C-006)
- 9 news + 4 blog posts → 0 candidates (mostly partnerships, plan tiers, governance)

**Options considered:**

- A) **Weekly (every 7 days)** — captures ~7 Claude Code releases per pass; matches the busiest upstream source's tempo
- B) **Bi-weekly (every 14 days)** — captures ~14 Claude Code releases per pass; matches typical Anthropic sprint rhythm
- C) **Monthly (every 30 days)** — captures ~30 releases per pass; reduces operational overhead but produces a large per-pass backlog
- D) **Triggered (on changelog modification)** — GitHub webhook on the upstream `CHANGELOG.md` files fires the scout. Most responsive but very noisy without debouncing.
- E) **Reactive (RSS-only)** — fire scout only when engineering blog publishes. Catches postmortems but misses changelog drift entirely.

**Decision:** Option B (bi-weekly cron) as the default, plus a manual trigger exception for engineering postmortems.

**Rationale:**

- **Bi-weekly matches Anthropic's apparent sprint rhythm.** The engineering blog publishes ~1-2/month, so bi-weekly catches all of it within a sprint cycle. The Claude Code changelog tempo (~1/day) is too fast to drive cadence directly; we'd produce sparse, repetitive passes if we ran weekly.
- **Manageable per-pass backlog.** 8 candidates per pass (from the 2026-05-20 data point) translates to 8 verifier + human review + dispatch cycles — feasible in a single batch session every two weeks. More frequent and each pass produces fewer candidates (the Reviewed Sources deny-list catches more); less frequent and the queue accumulates more candidates than a single review session can absorb cleanly.
- **Postmortems are time-sensitive and rare.** The April 23 postmortem alone produced C-006, AgentFluent's most interesting Behavior Diagnostics candidate to date. We don't want to wait up to 14 days to catch the next one. Manual trigger when a postmortem drops is the right shape. Future enhancement (per #451): RSS-watch on the engineering blog could automate this.
- **Bounded cost.** The scout has hard budget caps (14 WebFetch + 3 WebSearch + 10 Bash calls per run; documented in [`.claude/agents/anthropic-research.md`](../../.claude/agents/anthropic-research.md) line 83-87). Bi-weekly is cheap to operate.
- **Calibration window built in.** Cadence is a default, not a permanent contract. After 4-6 weeks (2-3 scout passes), real data on candidate yield and backlog should refine the choice. If we see "wish we'd known sooner" moments, tighten to weekly. If the queue stays empty, loosen to monthly. Tracking discipline: see #451 acceptance criteria.

**Calibration signals to watch (per-pass observability):**

| Signal | Action if observed |
|---|---|
| Per-pass candidate yield <2 for 2+ passes | Loosen to monthly (signal density too low for bi-weekly) |
| Per-pass candidate yield >12 for 2+ passes | Tighten to weekly (changelog volume outpaces scout) |
| Queue backlog (unprocessed candidates) >10 between human review sessions | Slow scout OR speed up human review batch |
| User-reported AgentFluent gap that scout would have caught earlier at higher cadence | Tighten cadence; file as occurrence note |

**Cron deployment is out of scope for this decision** — see #451 for the implementation tracking (Claude Code `schedule` skill vs. GitHub Actions vs. local cron). D037 picks the cadence; #451 picks the mechanism.

**Reference:** Issue #451 (cron deployment); `.claude/agents/anthropic-research.md`; `.claude/specs/research/anthropic-feature-watch.md` Reviewed Sources section (2026-05-20 scout pass data).

---
## D038: #394 re-scope -- split into #453 (tag no-trace) + #454 (threshold re-tuning)

**Date:** 2026-05-25
**Context:** #394 ("extend active_duration_ms to non-trace agents via AskUserQuestion-anchored wait detection") was the highest-priority story in v0.8 Stream A. Before implementation, an empirical investigation ran the existing extractor + trace parser against the agentfluent project corpus (25 sessions, 31 pm invocations). The investigation disproved the issue's premise and identified two distinct root causes for the inflated 33-min pm average.

**Findings:**
1. **AskUserQuestion does not appear in pm traces** -- zero occurrences across 31 invocations. The upstream gap (anthropics/claude-code#55240) is real. The "AskUserQuestion-anchored detection path" proposed in #394 and #230 cannot be built.
2. **The existing idle-gap heuristic works** for dramatic gaps (8h, 1.5h) but misses moderate 1-4 min user-coupled waits.
3. **Two distinct causes inflate the average:**
   - **Cause A:** 6/31 pm invocations (~20%) have no subagent trace file; `active_duration_ms` returns `None`; table silently falls back to wall-clock.
   - **Cause B:** Moderate idle gaps (1-4 min) fall below the `IDLE_GAP_K=10` / `IDLE_GAP_FLOOR_MS=300_000` threshold, which is shared with the `stuck_session` signal and calibrated to 100% recall on 12 stuck traces.

**Options considered:**
- A) Edit #394 in place with new title/body/ACs -- rejected because the original title, ACs, and comment thread all reference AskUserQuestion anchoring; overwriting creates a confusing audit trail.
- B) Close #394, open two replacement issues -- chosen. Preserves the diagnostic trail (original framing + empirical rebuttal + re-scope rationale all readable in one thread). Splits scope by risk profile.
- C) Close #394, open one combined replacement -- rejected because Cause A (XS-S, zero calibration risk) and Cause B (M, notebook re-run, calibration risk) have fundamentally different risk profiles and should not block each other.

**Decision:** Option B. Close #394 as superseded. Open:
- **#453** -- Tag no-trace invocations as duration-unreliable (Cause A). `priority:high`, XS-S, v0.8.0.
- **#454** -- Re-tune idle-gap thresholds for moderate user-coupled waits (Cause B). `priority:medium`, M, v0.8.0 (slippable to v0.8.1 if calibration threatens timeline).

**Rationale:**
- **Risk separation.** #453 has zero calibration risk and can ship immediately. #454 requires notebook re-validation and risks degrading `stuck_session` recall. Bundling them means the quick win waits for the risky work.
- **Audit trail.** Closing #394 with a pointer to replacements preserves the diagnostic journey. Future readers see: original hypothesis, empirical rebuttal, re-scope decision, and the two replacement issues -- all linked.
- **Milestone stability.** Both replacements stay in v0.8.0. #454 has an explicit deferral path to v0.8.1 if needed, documented in its ACs. #453 alone ensures users are never silently misled by wall-clock fallback durations.
- **Dogfood goal.** The pm avg < 10 min success criterion requires both issues. #453 prevents no-trace invocations from inflating the average. #454 catches moderate idle gaps in trace-attached invocations. Spec files updated to reflect that this goal depends on both.
- **AskUserQuestion explicitly moved to Non-Goals** in `prd-v0.8.md` -- it cannot be built until the upstream gap is resolved.

**Reference:** Issue #394 (closed); #453; #454. Empirical re-diagnosis: #394 comment (2026-05-25). `backlog-v0.8.md` and `prd-v0.8.md` updated to reflect the split.

---

## D038-A: #454 risk-model correction — phantom stuck_session coupling

**Date:** 2026-05-25
**Context:** Architect review on #454 ([comment](https://github.com/frederick-douglas-pearce/agentfluent/issues/454#issuecomment-4536983496)) identified that the "shared with `stuck_session`" coupling claim in D038, #454's body, `prd-v0.8.md` (Risks table row), and `backlog-v0.8.md` (A1b summary) does not match the actual code. Grep verification: `IDLE_GAP_K` and `IDLE_GAP_FLOOR_MS` are referenced only at `src/agentfluent/traces/parser.py:188` (definition + single use in `_compute_idle_gap_ms`). No `stuck_session` signal type exists in the codebase. The `STUCK_PATTERN` signal (`diagnostics/trace_signals.py:198`) uses a completely different mechanism: retry-attempt counting with identical input matching (`_STUCK_MIN_ATTEMPTS = 4`). It never reads the idle-gap constants.

**Where the phantom came from:** Notebook §11 (`scripts/calibration/threshold_validation.ipynb`) validates idle-gap detection against 12 "obviously-stuck" traces (span > 10min AND biggest_gap > 50% of span). The terminology "stuck traces" in the notebook is a regression-guard target for the idle-gap subtraction path -- not a separate signal's recall metric. The conflation propagated from #230's narrative into #394's framing, my own re-diagnosis comment on #394 (2026-05-25), PM's re-scope of #394, D038, and the v0.8 spec artifacts.

**Correction:**
- The 12-trace set in §11 is a regression guard for *subtraction quality on dramatic gaps*, not stuck-detection recall.
- `STUCK_PATTERN` recall is structurally unaffected by any change to `IDLE_GAP_K` / `IDLE_GAP_FLOOR_MS`.
- The "split into separate threshold pairs" mitigation in D038 and the v0.8 PRD is unnecessary; there is nothing to split.
- The "heuristic-shape change" alternative (median anchor vs absolute) is unnecessary; the floor is the binding constraint in the regime #454 targets.

**Decision:** #454 simplifies to approach (1) from the architect review: sweep `IDLE_GAP_K` and `IDLE_GAP_FLOOR_MS` downward (floor extended to 30_000ms because `gap > threshold` is strict), validate regression-guard on the 12-trace §11 set, validate positive detection on the moderate-gap pm invocations from the #394 re-diagnosis. Sizing revised M → S. `prd-v0.8.md` risks row, `backlog-v0.8.md` A1b, and #454 body all updated to remove the phantom coupling claim and reframe the validation target. D038's narrative remains historically accurate as the re-scope decision; D038-A documents the correction without rewriting the original entry (append-only log).

**Reference:** Issue #454 architect review comment (2026-05-25). `prd-v0.8.md`, `backlog-v0.8.md`, #454 issue body updated.

---

## D039: `ERROR_PATTERN` metadata fallback — per-invocation trace gate, retire `_dedup_error_patterns`

**Date:** 2026-05-30
**Context:** #281 bounded `ERROR_REGEX` to a 200-char leading window, dropping raw match volume ~98% on the dogfood corpus. Its architect review flagged that the bounded set still has ~80%+ FP on code-discussion prose and proposed a follow-up (#333) to decide whether to suppress, gate, or replace the metadata fallback. The dedup pass `_dedup_error_patterns` (pipeline.py) was the existing partial mitigation: drop metadata `ERROR_PATTERN` for any agent_type that produced any trace-level signal anywhere in the analysis scope.

**Empirical calibration on full dogfood corpus** (29 ERROR_PATTERN signals across agentfluent, codefluent, classifier — `.claude/specs/analysis/333-error-pattern-precision/`):

| Scheme | visible | TP | FP | precision | TPs silenced |
|---|---:|---:|---:|---:|---:|
| Current `_dedup_error_patterns` | 4 | 0 | 4 | **0%** | 4 |
| Per-invocation trace gate (H5) | 2 | 2 | 0 | **100%** | 2 (trace-covered) |
| H5 + min-match ≥ 2 | 0 | 0 | 0 | — | 4 (too aggressive) |

**Findings:**
- The current `_dedup_error_patterns` has a recall bug. Operating at agent-type granularity, it silences "Agent type X not found" TPs when *another invocation* of the same agent_type produced trace signals. Per-invocation gating fixes this.
- Min-match ≥ 2 gate (the #281 architect's prior suggestion) inverts on this corpus: all 4 TPs are single-keyword system error strings; the gate silences 100% of TPs and only 16% of FPs.
- Per-invocation gate is a strict superset of the dedup's intent — every signal the dedup correctly drops would also be dropped by H5, since those are by definition traced invocations.

**Options considered:**
- A) Patch `_dedup_error_patterns` to also gate per-invocation. Rejected — reproduces the per-invocation check inside a separate function. Two places to maintain.
- B) Keep `_dedup_error_patterns` as belt-and-suspenders alongside H5. Rejected — its recall bug continues silencing real "not found" TPs.
- C) Per-invocation trace gate inside `_extract_error_signals`; delete `_dedup_error_patterns`. Chosen.
- D) Add min-match gate as additional precision layer. Rejected — corpus data shows it loses real TPs.

**Decision:** Option C. `_extract_error_signals` now skips invocations where `inv.trace is not None`; trace-level signals (`TOOL_ERROR_SEQUENCE`, `RETRY_LOOP`, `PERMISSION_FAILURE`) are the authoritative error source for traced invocations. `_dedup_error_patterns` retired. `compute_error_rate` untouched (denominator-normalized; FP-tolerant). No min-match gate.

**Rationale:**
- **Visible precision 0% → 100%** on the dogfood corpus; surfaces 2 previously-silenced TPs (`Agent type X not found`).
- **Coverage preserved at the recommendation layer** — the 2 hook-deny TPs that H5 silences are covered by the trace's `PERMISSION_FAILURE` / `TOOL_ERROR_SEQUENCE` signals, which drive specific correlator recommendations.
- **Load-bearing for post-v0.8 dogfood run** — Fred runs corpus analysis after each release; the previous 4-visible 0%-precision baseline would have undermined the v0.8 release demo.
- **Architect-approved** (#333 review comment, 2026-05-30). One IMPORTANT concern addressed: inline comment in `_extract_error_signals` documents the untraced-precision limitation and the planned next-layer defenses (anchored patterns, confidence field) deferred per #281's options.

**Known limitation:** untraced invocations have no precision backstop beyond the 200-char window. The corpus only contained 2 untraced ERROR_PATTERN signals (both TPs). When Agent SDK traffic grows in v0.8+, the untraced FP surface may surface a new class. Anchored-pattern detection (`^Error:`, `^Permission denied`) and per-signal confidence fields are the next-layer mitigations if needed.

**Reference:** Issue #333; calibration data at `.claude/specs/analysis/333-error-pattern-precision/`; architect review comment on #333 (2026-05-30); precedent in D027 (#281 bounded-window decision).

---

## D040: v0.9 scoping -- Model-turn integration as headline, Advanced Tool Use as complement

**Date:** 2026-05-30
**Context:** v0.8.0 shipped (2026-05-30). The v0.8 dogfood analysis (`.claude/specs/analysis/2026-05-30-v08-dogfood/analysis.md`) confirmed signal calibration landed and Tier 3 infrastructure works. Six model-turn integration issues (#465-#470) were filed under `epic:analytics` on 2026-05-27. Epic #403 (Advanced Tool Use diagnostics) was pre-scoped for v0.9 since 2026-05-18 with a full PRD (`prd-advanced-tool-use-diagnostics.md`). Additionally, the v0.8 dogfood surfaced five concrete follow-up issues (#477-#481). The scoping question: how to frame v0.9 and what to pair with the model-turn work.

**Options considered:**
- A) Model-turn integration only (~4-5 dev days) -- ships quickly but underwhelming narrative
- B) Model-turn + Advanced Tool Use diagnostics (~13-17 dev days) -- two complementary feature streams with synergy at `avg_tool_calls_per_turn`
- C) Model-turn + Advanced Tool Use + dogfood fixes (~17-26 dev days) -- adds the cheap trust-building items from the v0.8 analysis
- D) Model-turn + Advanced Tool Use + dogfood fixes + webapp dashboard -- overloaded, violates "right-size the release" norm

**Decision:** Option C. Model-turn integration is the headline (Stream A). Advanced Tool Use diagnostics is the complement (Stream B). Dogfood fixes are cheap insurance (Stream C). v0.9 theme: "Count Every Turn."

**Rationale:**
- **Model-turn + ATU synergy.** `avg_tool_calls_per_turn` (computed in #467) is the exact denominator that makes `TOOL_ORCHESTRATION_CHAIN` (#406) interpretable. Shipping them together means the analytics and diagnostics tell a coherent story about agent efficiency.
- **Dogfood fixes are insurance, not scope creep.** #477 (remove tester, XS), #478 (docs, XS), #479 (prompt tightening, XS), #480 (active_duration in table, S), and #481 (cleanupPeriodDays, S-M) total ~3-4 dev days. They fix issues that misled the tool's own author during the v0.8 dogfood. Landing them before the next dogfood prevents repeat confusion.
- **Sizing is consistent with v0.7/v0.8.** v0.7 was ~18 issues, ~22-28 dev days, 3-4 weeks. v0.8 was 11 issues, ~20-29 dev days, 3-4+ weeks. v0.9 at 17 issues, ~17-26 dev days fits the same envelope.
- **Option A ships too thin.** Model-turn integration is 6 issues totaling ~4-5 dev days of actual implementation (one XS, two S, one S-M, one XS research, one stub). A release built only on surfacing an existing field would lack narrative weight.
- **Option D exceeds the envelope.** Adding a webapp dashboard or cross-project aggregation would push past 4 weeks and introduce a new technology surface (frontend framework, deployment) that doesn't pair with the CLI-focused turn+diagnostics work.

**Reference:** `prd-v0.9.md`; `backlog-v0.9.md`. v0.8 dogfood analysis at `.claude/specs/analysis/2026-05-30-v08-dogfood/analysis.md`.

---

## D041: #469 (per-turn diagnostic ratios) as stub -- defer implementation to dogfood validation

**Date:** 2026-05-30
**Context:** #469 proposes per-turn diagnostic ratios (`tool_errors_per_turn`, `retries_per_turn`, `cost_per_turn`) as new signal inputs. The denominators (#466, #467) ship in v0.9 Stream A. But the issue explicitly states "NOT implementation-ready" and "NOT a commitment to implement all three ratios." The question: should #469 be in v0.9 scope as a must-implement, a stretch, or a tracking item?

**Options considered:**
- A) Must-implement in v0.9 -- commit to shipping at least one per-turn ratio
- B) Stretch -- implement if time and data allow
- C) Tracking item only -- include in the milestone as a stub, assess at dogfood time

**Decision:** Option C. #469 is in the v0.9 milestone as a tracking item. Its disposition (implement vs. defer to v0.10) is decided at dogfood time based on two criteria: (1) do enough invocations have both turn data and diagnostic signals to make per-turn normalization useful, and (2) do the raw distributions suggest meaningful thresholds?

**Rationale:**
- **No data to set thresholds.** The model-turn fields don't exist in any production envelope yet. Until #465-#467 ship and a dogfood run produces real turn-count distributions, any threshold for "high tool_errors_per_turn" is guesswork.
- **Low cost to track, high cost to implement blind.** Leaving #469 as a stub costs nothing. Implementing it with guessed thresholds risks shipping a signal that fires incorrectly on day one, requiring immediate calibration work (the exact pattern v0.8's D039 corrected for ERROR_PATTERN).
- **The stub preserves the design intent.** #469's issue body documents the candidate ratios, their formulas, and the validation criteria. Future implementers have a complete spec to pick up.

**Reference:** #469 issue body ("What This Issue Is NOT" section). D002 (rule-based constraint), D019 (calibration data availability pattern).

---

## D042: Advanced Tool Use diagnostics kept at epic #403 scope -- no scope expansion for v0.9

**Date:** 2026-05-30
**Context:** Epic #403 (Advanced Tool Use diagnostics) was scoped on 2026-05-18 with four child stories (#404, #405, #406, #407). The PRD (`prd-advanced-tool-use-diagnostics.md`) also references follow-up items: #373 (tool description quality rubric), #374 (tool-schema token attribution), #375 (Tool Search regression in diff), and a Tier B trace-enhanced detection for TOOL_ORCHESTRATION_CHAIN. The question: should v0.9 pull in any of these follow-ups?

**Decision:** No. v0.9 scope for Advanced Tool Use diagnostics is exactly the four stories in epic #403 (#404, #405, #406, #407). Follow-ups remain parked/deferred.

**Rationale:**
- **#373 (tool description quality rubric) has a research component.** It requires defining what makes a tool description "good" -- a judgment that benefits from LLM-call augmentation (D035 tracking discipline) rather than rule-based heuristics. Not v0.9 material.
- **#374 (tool-schema token attribution) is an analytics enhancement**, not a diagnostic. It computes how many tokens each tool's schema consumes in the context window. Useful but independent of the three ATU signals and not needed for them to ship.
- **#375 (Tool Search regression in diff) is blocked on #374.** If #374 doesn't ship, #375 can't ship.
- **Tier B trace-enhanced detection for TOOL_ORCHESTRATION_CHAIN** would improve precision but requires per-call payload size analysis from subagent traces. The Tier A metadata-only version is explicitly designed to ship first (60-70% estimated precision) with the calibration check (#407) gating the release. If #407 shows unacceptable precision, the response is threshold tuning, not a Tier B expansion mid-release.
- **Right-sizing.** v0.9 already has 17 issues across 4 streams. Adding ATU follow-ups pushes past the consistent 3-4 week envelope.

**Reference:** Epic #403 body ("Out of Scope" section); `prd-advanced-tool-use-diagnostics.md` Section 3 (Non-Goals).

---

## D043: TOOL_ORCHESTRATION_CHAIN ships live-with-caveat in v0.9 -- 0% dogfood precision is a corpus artifact; Tier B (#499) is the precision fix

**Date:** 2026-06-02
**Context:** The #407 calibration of the Tier A `TOOL_ORCHESTRATION_CHAIN` signal (#406) measured precision against the agentfluent + codefluent dogfood corpora: 195 matching invocations, a seeded stratified sample of 30, classified **0/30 true positives = 0% precision** -- below the 70% target and the PRD's 60-70% estimate. Root cause is structural, not a threshold miss: `tokens_per_tool_use` divides whole-invocation token burn (large cached context re-sent each turn + reasoning output) by tool count, **not** intermediate-tool-*result* size, so it fires uniformly on token-heavy reasoning/review/scoping subagents (architect, general-purpose, pm, explore) whose intermediates are genuinely consumed. The tuning simulation (the D042-anticipated response) confirmed no threshold band improves precision -- every band emits the same reasoning agents; the only band that drops the FPs drops all detections. See `.claude/specs/analysis/407-calibration/`.

**Decision:** Ship the signal **live in v0.9, emitting at `INFO` with an explicit low-confidence caveat** in its message (`_LOW_CONFIDENCE_CAVEAT` in `tool_orchestration.py`). It is **not** gated off. The architect's #407 review recommended gating off; that recommendation is **superseded** by this decision, as is D042's assumption that "the response is threshold tuning, not Tier B."

**Rationale:**
- **The 0% precision is a corpus artifact, not a broken signal.** Today's dogfood corpus contains only reasoning agents -- no agents that run genuine tool-orchestration chains, so there are no true positives to find. Fred expects to run agents that *will* generate real orchestration-chain TPs in future dogfood sessions; gating the signal off would mean those future TPs never surface.
- **The caveat manages present noise honestly** -- the signal is flagged a low-confidence lead, not asserted as a finding, and the caveat propagates into the recommendation observation so the fix text never presents the orchestration claim as fact.
- **Tier B is the precision fix, not a precondition for shipping.** Trace-level detection (summed tool-result tokens vs. final-output size -- deterministic, inputs already exist on ~185/195 invocations) directly measures the quantity the metadata proxy lacks. Filed as **#499** (post-v0.9). D035 (LLM relevance classifier) remains candidate #1 as the complement; the calibration's ~100% rule-based FP rate on this corpus is the measured baseline it would improve upon.
- **PRD §11 success criterion #3 (>=70% precision) is an accepted known limitation for v0.9** -- not met on the current corpus, tracked to Tier B (#499). It is explicitly *not* "deferred by removing the signal."

**Supersedes:** the architect's #407 gate-off recommendation; D042's tuning-not-Tier-B assumption. **Complements:** D035 (LLM-augmentation tracking).

**Reference:** `.claude/specs/analysis/407-calibration/calibration.md`; #407 (calibration) and its disposition comment; #499 (Tier B); #406 (signal); epic #403; PRD `prd-advanced-tool-use-diagnostics.md` §9 / §11.

---

## D044: model_turns excludes <synthetic> ghost responses; tally them separately (Option A)

**Date:** 2026-06-05
**Context:** The v0.9 `model_turns` metric (#465, #466) was implemented as a raw count of `type:"assistant"` messages. Dogfooding the #483 docs PR surfaced that this includes Claude Code's `<synthetic>`-model assistant messages -- locally fabricated filler (observed payload: `"No response requested."`, `stop_reason: stop_sequence`, zero usage) emitted to keep user/assistant alternation valid when a user-role turn needs no model reply (local slash-commands, `!`-bash output, hook injection, resume preambles). On the agentfluent dogfood corpus this made `model_turns` (e.g. 7412) exceed `api_call_count` (7387) by exactly the synthetic count (25), even though `api_call_count` already excludes synthetic (`tokens.py`: requires `usage is not None` AND `model not in SYNTHETIC_MODELS`). The model did not take a turn for a synthetic message, so counting it as one is wrong. Filed as #507 (release blocker for v0.9.0, which is unreleased).

**Decision:** Exclude `<synthetic>`-model assistant messages from `model_turns` and tally them separately. Specifically (**Option A**):
- `model_turns = assistant_message_count - synthetic_message_count` -- every assistant message the model actually produced, with `<synthetic>` ghosts netted out. Applied at both count sites: parent session (`pipeline.py`) and subagent trace (`traces/parser.py`).
- Keep `assistant_message_count` at its original all-inclusive meaning (it backs the integration invariant `message_count >= user + assistant` and is already in the JSON envelope).
- Add `synthetic_message_count` (per session) and a computed `total_synthetic_messages` (aggregate, top-level), surfaced on a "Synthetic responses" row in the Token Usage table and in JSON.
- Filter on the `SYNTHETIC_MODELS` sentinel, not on zero-token usage: a real turn always carries a real model name, so the sentinel is the robust discriminator.

**Option A vs Option B:** Option B was to define `model_turns` as exactly `api_call_count` (usage-bearing non-synthetic messages), collapsing the two metrics. **Rejected.** Under Option A the two metrics are equal in the common case but diverge on the (so-far-unobserved) edge case of a real-model assistant message carrying no `usage` block -- which counts as a model turn (the model responded) but not as an API call (no billing recorded). Keeping both metrics distinct surfaces that case if it ever occurs; collapsing them would hide it. Semantically, "model turn" = "the model produced a response," which is not the same predicate as "Claude Code recorded usage."

**Rationale:**
- **Correctness of the headline metric.** `model_turns` is v0.9's headline efficiency metric and the denominator of the per-agent ratios (`avg_tokens_per_turn`, `avg_tool_calls_per_turn`, `estimated_avg_cost_per_turn_usd`). Inflating it with zero-cost ghost turns biases every ratio down by the synthetic fraction.
- **Pre-ship, so no migration cost.** v0.9 is unreleased (release PR #486 open); fixing now means no user ever sees the inflated definition. `diff` reads turn fields with tolerant `.get(..., 0)`, and no pre-fix v0.9 baseline exists in any user's hands.
- **Blast radius is small.** The per-agent ratio chain is additive off `inv.model_turns -> trace.model_turns`, so the single `traces/parser.py` fix auto-corrects all rollups; `diff` needs no change.

**Follow-ups:** #508 (research) investigates the full taxonomy of `<synthetic>` scenarios and whether any sub-category warrants its own metric. The architect review on #507 endorsed Option A and this data-model shape (keep `assistant_message_count` all-inclusive, derive `model_turns` by subtraction) over redefining the field or storing `model_turns` directly.

**Reference:** #507 (fix), #508 (synthetic taxonomy research), #465/#466 (model-turn introduction), #467 (efficiency ratios); architect review comment on #507. Supersedes the "(one API round-trip)" phrasing in the #465/#466 docstrings and the original `model_turns` glossary entry.

---

## D045: genai-prices as the base-rate source; local overlay for unmodeled levers

**Date:** 2026-06-28
**Context:** Live pricing was a hand-maintained `_PRICING` dict (single current rate per
model, no time dependency). genai-prices (pydantic/genai-prices, MIT) had been named as the
chosen upstream in comments/docs and in epic #535's body, but was never made a dependency or
ticketed. The earlier DIY plan (#80/#81: build our own time-series pricing.json on CodeFluent's
shape) rested on a false premise — the "opus $15/$75 → $5/$25 historical drop" never happened
(Opus 4.5 launched at $5/$25).
**Decision:** Depend on genai-prices for Anthropic base rates AND native date-aware (effective-
dated constraint) lookup. Supply every cost lever genai-prices does not model — 1h cache write,
fast mode, batch/priority, data residency, server-tool surcharges — via a local overlay merged
at a single documented seam. Supersede #80 and #81; re-scope #82 from "scrape Anthropic into our
dict" to "keep the genai-prices pin current + detect upstream coverage of our overlay levers."
**Rationale:**
- genai-prices provides effective-dated pricing natively — the exact capability #80/#81 tried to
  hand-build, without the maintenance burden or the append-only discipline #80 required.
- The base ⊕ overlay split keeps AgentFluent's published cost bar (every lever priced or
  documented) honest while delegating the volatile base-rate data to a maintained MIT dataset.
- Owning a scraper (#82) over a hand dict is obviated once the dataset is upstream.
**Trade-off:** Adds a runtime dependency and couples AgentFluent to genai-prices' Anthropic
coverage/cadence; mitigated by the overlay (we can always supply a lever locally) and by tracking
upstream coverage so overlays retire as genai-prices catches up.
**Reference:** epic #535 (cost-lever coverage), #545/#546/#547 (Phase 1 substrate), #536/#539
(v0.11 overlay levers), #537/#538/#543 (v0.12), #82 (re-scoped), #80/#81 (superseded, closed),
#252 (folded into #545); spec `prd-pricing-genai-migration.md`.

---

## D046: Defer verbosity-constraint scanner (#437) — corpus prevalence is 0; re-enter only on evidence

**Date:** 2026-07-01
**Context:** #437 (PR #571, built + held at merge gate) shipped a regex scanner flagging ≤200-word
caps in the user's *own* agent prompts, citing Anthropic's April 2026 postmortem "3% coding
regression." A value re-review (`.claude/specs/value-review-437-verbosity.md`) found: (a) the
incident was in Anthropic's Claude Code **harness** system prompt — a surface the scanner cannot
see (locus mismatch); (b) evidence is n=1, internal, self-corrected, coding-specific, no
generalization claimed by the source; (c) v1 flags the common+correct case (scoped word caps on
summarizers/classifiers) and cannot distinguish the rare valid case (blanket caps on tool-using
agents); (d) v1 hands judgment back to the user, contradicting the "tells you what to change"
tagline; (e) the bare 3% citation is misapplied evidence that erodes recommendation credibility.
A read-only prevalence pass of the detector over the dogfood corpus (all `~/.claude/agents/` +
project `.claude/agents/` defs, 2026-07-01) found **0 blanket caps on tool-having agents**; the
corpus's only word-count constraints are all legitimate scoped output specs (marketer per-artifact
lengths; three research agents' "under 200 words" summaries) — the exact population v1 risks
mis-flagging.
**Decision:** DEFER, and the 0-prevalence result resolves the DEFER-vs-RESCOPE fork toward DEFER
(not immediate rescope). PR #571 closed **unmerged** (branch deleted; work preserved on the PR).
#437 closed as **misdirected-as-written** (not "kept open" — an older issue with architect-blessed
ACs written against the article, not a corpus). Correctly-scoped, evidence-gated successor filed as
**#572** (unmilestoned, backlog). Re-entry gate: build only if a corpus surfaces real blanket
response caps on agentic/tool-using prompts; if so, re-enter as RESCOPE-NARROW (tool-having gate +
scoped-field FP guard + mechanism-based copy, no bare 3% stat).
**Rationale:** The binding constraint is unproven prevalence, not implementation quality — and the
prevalence check returned 0. Cost asymmetry favors waiting: DEFER is reversible; shipping a
trust-damaging citation is not. A prevalence oracle already existed at zero marginal cost (the
dogfood corpus).
**Meta / process:** This effort was corrected only by a post-hoc user-story mapping requested after
implementation. Surfaced a release-loop gap → adding a user-focused value framing at the *planning*
gate so misdirected effort is caught before code (#573).
**Reference:** #437 (closed), #572 (successor), #431 (C-006a epic), PR #571 (closed unmerged),
spec `value-review-437-verbosity.md`, [postmortem](https://www.anthropic.com/engineering/april-23-postmortem).

---

## D047: Graduate `docs` + `research` to `escalation-only` auto-merge (release-loop)

**Date:** 2026-07-04
**Context:** The v0.10.0 release-loop run (14 issues to terminal, zero regressions, zero bad
merges) ran entirely under `mode: calibration` — the human approved every merge. The
retrospective (#562) established that all high-value human intervention landed **upstream** at
triage/plan; the calibration merge gate rubber-stamped ~11 of 14 rows, its one load-bearing
event being #437's hold → DEFER (a *value* catch on a `feat:`, the route that stays gated). The
`escalation-only` + `graduated-routes` semantics are pinned (§6.1 / §7.1 step 11, #563) and
per-iteration budget journaling is in place (§6.1 / §6.2, #565). The two evidence gaps that had
blocked graduation are now closed: `research` was driven **start-to-finish for the first time**
(#520/#521 — previously only *reconciled*), and the two red-path recovery paths auto-merge
relies on (§7.1 step 7 AC-verifier FAIL→re-verify, step 8 CI red→fix-until-green) were validated
by a controlled, architect-reviewed exercise (#583).
**Decision:** Graduate **`docs` and `research`** to auto-merge under `mode: escalation-only`. The
next run (v0.11.0) inits with `mode: escalation-only` + `graduated-routes: docs, research` (§7.5
now reads this decision at init so it persists across runs). A graduated-route row auto-merges
**only** when CI + AC-verifier + review are green AND the bump is ≤ patch AND it is not `hold`
AND none of the always-escalate conditions apply (`feat:`/breaking, risky/irreversible, security
surface, contested review finding); **default-deny** on any uncertainty (§6.1 / §7.1 step 11).
The **plan gate stays conditional/human in every mode** (unchanged — that is where the human
value landed). **`code`/`feat:` keeps the human merge gate**, pending its own per-route promotion
criteria (#562).
**Scope of the #583 evidence:** validates the step 7/8 *control flow* (loopback + fix-until-green),
NOT AC-verifier *sensitivity* (the induced gap was deliberately obvious). Now that the
docs/research human merge gate is off, sensitivity assurance — if wanted — comes from
spot-auditing the first N auto-merged rows (tracked in #562), not from #583. Residual risk is
low: docs/research don't break runtime, and the CI-green precondition still guards against
merging red.
**Rationale:** Graduate the part the evidence earned, not all-or-nothing. Docs/research are
no-bump, low-blast-radius, and cleared an independent AC-verifier + CI gate that carry without a
human. The human merge gate added value once in 14 tries — on a `feat:`. The flip removes ~half
the run's rubber-stamp merge gates while keeping human eyes where they paid off (triage/plan, and
every `code`/`feat:` merge). Cost asymmetry favors it: reversible (flip back to `calibration`),
and bad-merge risk stays covered by default-deny/always-escalate + budget caps (#565).
**Companion (not a blocker):** #584 (RUN PARKED sentinel + one-directional milestone-delta
surfacing) should land alongside autonomy — the "converged-pending-release" re-scan instability
compounds under a reduced re-fire cadence — but does not gate this flip.
**Reference:** #562 (retrospective umbrella), #563 (mode semantics), #565 (budget journaling),
#520/#521 (research E2E), #583 (red-path validation, closed), #584 (companion), #437 / D046 (the
load-bearing merge-gate hold), spec §6.1 / §7.1 step 11 / §7.5.

---

## D048: `RUN PARKED` resting state + bidirectional curated-subset invariant (release-loop)

**Date:** 2026-07-05
**Context:** Under D047 (docs+research graduated to `escalation-only` auto-merge) + a reduced
re-fire cadence (#562 Finding F), a release-loop run that has finished all *workable* rows but
retains rows gated on an **external event** (a release cut, a dogfood window) had no terminal
resting state. Skill §0 recognized only `RUN COMPLETE`; a "converged-pending-release" run therefore
re-ran select + live-reconcile against an ever-changing world (milestone membership, PR/CI status)
on **every** re-invocation and could reach a **new** conclusion each time. In the v0.10.0 run this
literally pulled #520/#521 in mid-stream, and milestone drift ran **both** directions (#514 left
scope; the #424/#425/#426 hook chain + #520/#521 joined) — each caught only by a manual human
cross-check. #584's parenthetical claim that the eject-on-leave half was "already in-skill via the
#514 block" was **refuted** on inspection (grep found no milestone-membership re-check anywhere);
both drift directions were manual.
**Decision:** Codify three things in the skill (§7.1) + its byte-identical spec mirror + prose
(§6.1/§7.3/§7.6/§8/§9), architect-reviewed (#584 comment thread):
1. **`parked` Status token** — a first-class **non-terminal, resting** status (modeled on `hold`,
   not a `blocked` substate) for a row gated on an external event, with the awaited condition in
   Notes as `awaiting: <condition>`. Chosen over a `blocked`+Notes-marker because the token leaves
   `blocked`'s terminal meaning untouched at every enumeration site (convergence, iteration-cap
   counting, resume predicate) and gives the future headless "park-and-continue" async-ask (§13/§14)
   a machine-clean state to enumerate.
2. **`RUN PARKED — awaiting <condition>` sentinel + short-circuit** — appended when the only
   non-terminal rows are `parked` (branch order hold → parked → complete → pending; parked tested
   BEFORE complete so a gated row is not swallowed as terminal, and requires `done`/`deferred`
   peers). §0 reads the **most recent** of `{RUN COMPLETE, RUN PARKED, RUN RESUMED}` (last-wins, the
   log being append-only) and, on `RUN PARKED`, takes a cheap path — milestone reconciliation +
   `queue.md` selectability re-derivation, **no** per-row live reconcile / resume — until the human
   **explicitly** un-parks a **named** condition (a concrete, **condition-scoped** mutation: flip
   only the `parked` rows awaiting that condition → `routed`, clear their marker, append `RUN
   RESUMED`; rows gated on other conditions stay parked, so a partial release never prematurely
   un-gates them — architect finding N1) or a pulled-in joiner / cleared dep makes work selectable.
   A bare `/loop` re-fire does not un-park.
3. **Bidirectional curated-subset invariant** — the init queue is authoritative; milestone drift is
   **surfaced to the human once, never auto-applied** (joiners via `- surfaced-join:` records +
   optional `queued` row on "pull in"; leavers via `- surfaced-leave:` + a `kept:`/`deferred`
   curation decision, with in-flight leavers finish-then-reconsider so no PR/branch is orphaned).
   Corollary Notes discipline: write a `parked`/`blocked` row's Notes as the durable **curation
   decision**, never the mutable live evidence (the v0.10.0 row-12 destabilizer).
**Scope:** `.claude/` loop-harness tooling only (`chore(loop):`, **no release milestone**, per
#559). No interaction with the D047 auto-merge path — `parked` rows never reach the merge gate;
PARKED is purely complementary (it suppresses the wasteful re-scans a reduced re-fire cadence would
amplify).
**Rationale:** A resting state that is idempotent under re-fire is the precondition for loosening
the human's presence (D047) without the run silently re-deciding scope each cycle. The token +
last-wins sentinel + explicit-only un-park make park/un-park a closed, deadlock-free cycle;
surface-once-never-auto keeps the human the sole curator of run scope.
**Forward:** sets up #559 idea-3 (eject the genuinely post-release tail — #504/#513 — into a
separate post-release run to reach a clean `RUN COMPLETE`) and the §13/§14 headless park-and-continue
async-ask, both reusing this resting-state machinery rather than a parallel one.
**Reference:** #584 (this work + architect review), D047 (companion — autonomy flip), #562 (Finding
F / retrospective umbrella), #559 (idea-3 + `.claude/` no-milestone convention), #514/#520/#521
(the observed bidirectional drift), spec §6.1 / §7.1 §0–§2 / §7.3 / §7.6 / §8 / §9.

---

## D049: v0.11 scoping — SDK ingestion as headline (full scope), #112 pulled in, partial pricing bump, dogfood-runner over scout-port

**Date:** 2026-07-06
**Context:** v0.10 closed the Agent SDK **discovery** epic (#517) — the durable deliverable was the descriptive findings doc (`agent-sdk-session-format-findings.md`), not a shipped capability. Its §8 enumerated the unticketed downstream parser follow-ups. The pre-existing v0.11.0 milestone held ~11 issues that were almost entirely the pricing/cost-lever work (#545/#546/#547/#536/#539), retry calibration (#580/#581), and dogfood/docs (#549/#514/#513/#469) — none of it the SDK ingestion Fred chose to prioritize for v0.11. Scoping questions: (1) does SDK ingestion land in v0.11 and at what depth; (2) does #112 (SDK main-session model routing) get pulled in or stay downstream; (3) how to resolve the SDK-vs-pricing milestone collision.

**Decisions:**
- **Full SDK scope in v0.11**, as the release headline ("Recognize the Primary Audience"). New epic `epic:agent-sdk-ingestion` (#589) with stories: **S0** repo-tracked Agent SDK **dogfood-runner** + cadence (#590, build first), **S1** surface `entrypoint` + sdk/cli/unknown classification (#591), **S2** SDK-vs-CC indicator in `analyze` — CLI badge + JSON emitting **both** `session_kind` and raw `entrypoint` (#592), **S3** surface `toolUseResult.resolvedModel` (#593), **S4** SDK line types → `SKIP_TYPES` + `status` doc nit (#594), **S5** multi-level trace-to-invocation linker with the `totalTokens` inclusivity/double-counting question settled as a named AC sub-task (#595).
- **#112 pulled INTO v0.11** as the first consumer of S1/S3 (not left downstream); its pre-discovery ACs revised per findings §7.
- **Partial pricing bump:** keep the pricing *foundation* (#545/#546/#547) in v0.11; move the two discretionary cost-levers #536 (fast-mode) + #539 (server-tool surcharges) to v0.12, where they consolidate under the existing `epic:cost-coverage` (#535) — so no epic is split.
- **Dogfood-runner (S0) chosen over a research-scout port** as the durable SDK-session source. Distinct from #522 (a synthetic matrix *generator* for discovery): S0 does *real work* (runs `agentfluent analyze` over a **bounded rolling window**, reusing the `--since` surface per D024/D025, and dovetails with `diff`), where session data is a byproduct. It is scheduled on a cadence from day one so SDK history accrues *during* the dev cycle.
- **Separate low-priority backlog item:** a competitive-landscape research agent (#596, no milestone), scoped tightly to competing tools so it does not overlap the anthropic-feature-watch pipeline.

**Rationale:**
- SDK ingestion is the direct sequel to v0.10's discovery and the load-bearing primitive that gates D013 correctness (main-session diagnostics must target SDK mains, not CC-interactive mains). Full scope was Fred's explicit call — comprehensiveness over a limited cut, "no rush" to prod — so the release also builds the first consumer (#112) and the heavy trace linker (S5) rather than deferring them.
- Full pricing bump would stall the #545 genai-prices foundation that v0.12's cost-lever epic builds on; full coexist leaves v0.11 with two competing marquees. Partial bump keeps the foundation moving while giving SDK the clean headline, and lands the discretionary levers where they thematically belong.
- The dogfood-runner is durable (dogfood is a permanent need, unlike the scout, which is migrating to `claude-code-sessions`), automates the manual post-release dogfood ritual, is self-reinforcing, and dogfoods the exact v0.11 surfaces (subagent trace linker + model routing). The bounded rolling window points AgentFluent's own regression-detection value proposition inward: a spike against a recent baseline is an early warning that the whole-corpus baseline would drown out.

**Reference:** epic #589 (S0 #590, S1 #591, S2 #592, S3 #593, S4 #594, S5 #595); #112 (pulled in, ACs revised); #536/#539 (bumped to v0.12 under #535); #596 (competitive-landscape agent, backlog); findings `agent-sdk-session-format-findings.md` §2/§3/§4/§7/§8; governing D013 (main-session scope), D001 (Python-only), D045 (pricing base+overlay), D024/D025 (date-range filtering); spec `prd-v0.11.md`.

---
