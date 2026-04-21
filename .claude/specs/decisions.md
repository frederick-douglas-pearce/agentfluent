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
