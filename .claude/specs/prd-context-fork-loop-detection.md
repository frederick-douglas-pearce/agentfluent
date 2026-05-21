# PRD: `context: fork` Infinite-Loop Detection in Skills

**Status:** Draft
**Date:** 2026-05-21
**Author:** PM Agent
**Source:** Feature-watch candidate C-005 (`.claude/specs/research/anthropic-feature-watch.md`)
**Decision log:** See `decisions.md` for related decisions.

---

## 1. Problem Statement

Claude Code v2.1.144 fixed an infinite loop where a skill using `context: fork` could re-invoke itself recursively. The fix prevents the runtime crash, but the underlying pattern -- a skill that references itself (directly or transitively) via `context: fork` -- remains a latent config hazard. If the skill's invocation logic changes or a new transitive chain forms, the same class of bug can recur. AgentFluent should detect both:

1. **Active symptoms** -- sessions already exhibiting runaway stuck behavior with extreme tool-use counts (detectable today from existing signals and metadata).
2. **Latent config risks** -- skill files whose `context: fork` declaration creates a self-referential invocation chain (detectable once skill file parsing exists).

## 2. Upstream Context

- **Fix:** Claude Code v2.1.144 (approx. May 2026) patched the recursive `context: fork` infinite loop.
- **Changelog:** https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md
- **Canonical recommendation copy:** "Skill X uses `context: fork` and appears in its own invocation chain."

## 3. Scope

### In Scope

**Story A (no dependency):** Diagnostic hint correlating existing `STUCK_PATTERN` signals with high `totalToolUseCount` metadata to surface a `context: fork` loop as a possible root cause. This is a correlator enhancement, not a new signal -- it adds a supplementary note to the existing `StuckPatternRule` recommendation when the metadata pattern suggests a fork-loop scenario.

**Story B (depends on #183):** Config scanner check that parses skill files, inspects the `context:` frontmatter key, and detects self-referential `context: fork` chains. Emits a config-assessment warning when found.

### Out of Scope

- Detecting transitive multi-skill chains (A forks B, B forks A). The first iteration covers direct self-reference only. Transitive detection is a future enhancement once the skill scanner has a dependency graph.
- Auto-fixing skill files.
- Detecting `context: fork` loops in Agent SDK programmatic definitions (`.py`/`.ts` source parsing is deferred per D004).
- New `SignalType` enum value -- Story A annotates an existing signal; Story B uses the config-assessment `ConfigRecommendation` model.

## 4. Design

### 4.1 Story A: STUCK_PATTERN + high-tool-count fork-loop hint

**Where it lives:** `diagnostics/correlator.py`, inside `StuckPatternRule.recommend()`.

**Trigger conditions (all must be true):**
1. A `STUCK_PATTERN` signal has fired for the invocation.
2. The parent `AgentInvocation.tool_uses` (from `toolUseResult.totalToolUseCount`) exceeds a high threshold (e.g., >= 50).
3. The stuck tool is `Agent` or `Skill` (the tool_name in the stuck sequence suggests a delegation/skill call).

**Behavior:** When all three conditions are met, the recommendation's `action` field is augmented with a supplementary note:

> "This stuck pattern combined with an extreme tool count (N calls) may indicate a `context: fork` self-invocation loop. Check whether the invoking skill calls itself via `context: fork`. See Claude Code v2.1.144 changelog for the upstream fix."

The existing recommendation structure (observation, reason, action) is preserved. The note is appended to the `action` string. No new fields are added to `DiagnosticRecommendation`.

**Thresholds:**
- `FORK_LOOP_HINT_MIN_TOOL_USES = 50` -- module-level constant in `correlator.py`, documented as tunable.
- Tool-name filter: `signal.detail.get("tool_name", "")` matches `"Agent"` or `"Skill"` (case-insensitive).

**Data availability:** `AgentInvocation.tool_uses` is already populated from `toolUseResult.totalToolUseCount`. `STUCK_PATTERN` signals carry `detail.tool_name`. No new data sources required.

### 4.2 Story B: Config scanner `context: fork` self-reference check

**Depends on:** #183 (skill scanner infrastructure -- skill file parsing, structured skill representation).

**Where it lives:** `config/scorer.py` (or the scoring module that #183 introduces for skills), consuming the structured skill representation from #183's skill scanner.

**Detection logic:**
1. For each parsed skill file, check whether the `context:` frontmatter key equals `fork`.
2. If `context: fork`, scan the skill's prompt body for references to the skill's own name (e.g., the skill name appears in a `Skill(name)` invocation pattern or as a tool-call target).
3. A direct self-reference match emits a `ConfigRecommendation`:
   - **dimension:** `"skill_safety"` (new scoring dimension, or grouped under an existing dimension per #183's design)
   - **severity:** `WARNING`
   - **message:** `"Skill '{name}' uses context: fork and appears in its own invocation chain -- this pattern caused an infinite loop fixed in Claude Code v2.1.144."`
   - **suggested_action:** `"Remove the self-reference from the skill body, or switch to context: none if forking is not required."`

**Self-reference detection heuristic:**
- Regex scan of the prompt body for the skill's own name as a word boundary match (case-insensitive).
- This is intentionally coarse for v1. False positives (mentioning the skill name in documentation text within the prompt) are acceptable at WARNING severity.

**Config model changes:** None. `AgentConfig.skills` already exists as `list[str]` (skill names). #183 will produce a richer `SkillConfig` (or similar) model that includes the parsed `context:` key and prompt body. Story B consumes that model.

## 5. Acceptance Criteria

### Story A: Fork-loop diagnostic hint

- [ ] Given a `STUCK_PATTERN` signal where `detail.tool_name` is `"Agent"` or `"Skill"` (case-insensitive) AND the parent invocation's `tool_uses >= 50`, then the recommendation `action` includes the fork-loop supplementary note
- [ ] Given a `STUCK_PATTERN` signal where `tool_uses < 50`, no fork-loop note is appended
- [ ] Given a `STUCK_PATTERN` signal where `tool_name` is `"Read"` (not a delegation tool), no fork-loop note is appended even if `tool_uses >= 50`
- [ ] The supplementary note references Claude Code v2.1.144
- [ ] Threshold constant `FORK_LOOP_HINT_MIN_TOOL_USES` is module-level and documented
- [ ] Unit tests cover all three conditions (fires, below threshold, wrong tool name)
- [ ] Built-in agent branching path (if applicable) also gets the hint

### Story B: Config scanner fork-loop check

- [ ] Given a skill file with `context: fork` in frontmatter AND a self-reference to the skill name in the prompt body, a `ConfigRecommendation` with severity WARNING is emitted
- [ ] Given a skill with `context: fork` but no self-reference, no recommendation is emitted
- [ ] Given a skill with `context: none` and a self-reference, no recommendation is emitted (fork is the hazardous mode)
- [ ] Recommendation message matches the canonical copy: "Skill '{name}' uses `context: fork` and appears in its own invocation chain"
- [ ] Recommendation references the upstream fix (v2.1.144)
- [ ] Unit tests with fixture skill files covering: fork + self-ref, fork + no self-ref, no-fork + self-ref, no context key
- [ ] JSON output includes the recommendation

## 6. Implementation Notes

### Story A
- The enhancement is entirely within `StuckPatternRule.recommend()` in `diagnostics/correlator.py`. The `config: AgentConfig | None` parameter gives access to the agent's config if matched, but the tool_uses threshold comes from `AgentInvocation` data available on the signal's parent invocation. The correlator currently receives the signal and config but not the invocation directly -- check whether `tool_uses` is accessible via `signal.detail` or needs to be threaded through.
- If `tool_uses` is not on `signal.detail`, the simplest path is to add it during signal emission in `trace_signals.py:_build_stuck_signal()`. The `SubagentTrace` is derived from the `AgentInvocation` whose `tool_uses` is populated from metadata.
- Alternatively, thread `tool_uses` as a detail key on `STUCK_PATTERN` signals at emission time.

### Story B
- Depends entirely on #183's skill scanner output shape. Do not begin until #183 merges and the `SkillConfig` (or equivalent) model is stable.
- The self-reference regex should use `re.compile(rf"\b{re.escape(skill_name)}\b", re.IGNORECASE)` to avoid substring false positives.
- Consider whether the check belongs in `config/scorer.py` as a new scoring dimension or as a standalone rule in a `config/skill_checks.py` module. Defer this decision to the developer/architect.

## 7. Dependencies

| Story | Depends on | Notes |
|---|---|---|
| A | None | Ships independently using existing infrastructure |
| B | #183 (skill scanner) | Blocked until skill file parsing + `context:` key extraction exist |

## 8. Sequencing

1. **Story A first** -- no dependencies, small scope (~0.5-1 day), immediate value for sessions already exhibiting the pattern.
2. **Story B after #183** -- requires skill scanner infrastructure. Scope ~1-2 days once #183 is merged.

## 9. References

- C-005 candidate: `.claude/specs/research/anthropic-feature-watch.md`
- Claude Code v2.1.144 changelog (fix for `context: fork` infinite loop)
- #183 -- delegation drafts: skill-aware provenance + actionability note (skill scanner prerequisite)
- `src/agentfluent/diagnostics/trace_signals.py` -- `STUCK_PATTERN` signal emission
- `src/agentfluent/diagnostics/correlator.py:StuckPatternRule` -- existing recommendation rule
- `src/agentfluent/config/models.py:AgentConfig.skills` -- current skill name list
- `src/agentfluent/diagnostics/models.py:SignalType.STUCK_PATTERN` -- signal enum
