# PRD: AgentFluent v0.8 -- Sharpen the Signal

**Status:** Draft
**Date:** 2026-05-18 (updated 2026-05-25 -- #394 re-scope, see D038)
**Author:** PM Agent
**Decision log:** See `decisions.md` for key decisions referenced below.
**Backlog:** See `backlog-v0.8.md` for the full sequenced backlog.

---

## 1. Theme

**"Sharpen the signal."**

v0.7 completed the output layer: `agentfluent report` for shareable Markdown, `--session` for single-session post-mortems, `--git` for Tier 2 quality signals, and `unused_agent` for config-effectiveness diagnostics. The tool now detects, explains, and shares findings across all three diagnostics axes (cost, speed, quality) using two data sources (JSONL sessions, local git).

The v0.7 dogfood run (2026-05-17, `.claude/specs/analysis/2026-05-17-v07-dogfood-analysis.md`) confirmed the signals are firing -- and exposed where they mislead. Three concrete problems surfaced:

1. **Duration metrics lie for human-coupled agents.** pm's reported 33-min average duration includes user-wait time that `active_duration_ms` was supposed to exclude. Empirical investigation (2026-05-25) revealed two distinct causes: (a) ~20% of pm invocations have no subagent trace file on disk, causing silent fallback to wall-clock duration, and (b) moderate 1-4 min idle gaps fall below the detection threshold for trace-attached invocations. The originally hypothesized AskUserQuestion-anchored detection path cannot be built -- zero AskUserQuestion occurrences exist in the pm corpus. See D038 for the re-scope from #394 to #453 + #454.
2. **Retry noise drowns actionable signals.** Read retries account for 68% of all `retry_loop` signals (104 of 152), but Read retries are built-in-tool behavior with no agent-config fix. They crowd out the actionable Bash/MCP retries in the priority list (#395).
3. **Reviewer effectiveness is mislabeled.** A 31% `parent_acted` rate on architect's `reviewer_caught` signal looks like a problem, but ~50% of "not acted on" findings are deliberate rejections. The recommendation copy treats rejection as failure (#396).

These are not edge cases. They are the dominant signals in the dogfood corpus, and they all point the user in the wrong direction. Fixing them is the highest-leverage work for diagnostics credibility.

Alongside these fixes, v0.8 ships the **Tier 3 GitHub enrichment** designed in the v0.7 spike (#352). Two new quality-axis signals -- `CI_FAILURE_FIRST_PUSH` and `PR_REVIEW_COMMENT_DENSITY` -- bring the first external data source into the diagnostics pipeline. The infrastructure (`gh` CLI integration, file-backed cache, `--github` flag, consent UX) is designed for extension; two additional signals are deferred to v0.8.1+ per the spike's phased approach.

v0.8 = **tighten what we have + extend where we're strongest.**

One-line pitch: **"Fix the signals that mislead. Add the signals that prove quality."**

### Why this theme

The alternative was to broaden detection (tool-inventory diagnostics #371, new delegation patterns, webapp dashboard). That path was rejected because:

1. **Misleading signals are worse than missing signals.** A user who acts on a false "pm is slow, swap model" recommendation gets hurt. A user who doesn't get a "you have too many tools" signal just misses an optimization. The trust cost is asymmetric.

2. **Tier 3 is the natural follow-through on the quality axis.** v0.6 shipped Tier 1 (JSONL-only quality signals). v0.7 shipped Tier 2 (local git). Tier 3 (GitHub enrichment) is the next step in the planned progression (D015), and the spike deliverable (#352) resolved all design-blocking questions. Deferring Tier 3 to v0.9 would break the momentum of the quality-axis story.

3. **The dogfood fixes and Tier 3 reinforce each other.** #453/#454/#395/#396 make existing signals trustworthy. Tier 3 adds new signals that are inherently high-confidence (CI pass/fail is binary; review comments are human-generated). Together they raise the floor and the ceiling of diagnostics quality in one release.

## 2. Goals

1. **Fix duration measurement for human-coupled agents** by tagging no-trace invocations as unreliable (#453) and re-tuning idle-gap thresholds for moderate waits (#454)
2. **Reduce retry noise in priority rankings** by down-weighting built-in-tool retries (#395)
3. **Calibrate reviewer effectiveness interpretation** by introducing a healthy parent_acted band (#396)
4. **Validate feat_fix_proximity precision** with a ground-truth calibration check (#402)
5. **Ship Tier 3 GitHub enrichment infrastructure** -- `gh` CLI integration, file-backed cache, `--github` flag, consent UX (#399)
6. **Ship two Tier 3 quality signals** -- `CI_FAILURE_FIRST_PUSH` (#400) and `PR_REVIEW_COMMENT_DENSITY` (#401)
7. **Ship docs that reflect what shipped** (#390, #392)

## 3. Non-Goals

- LLM-powered analysis (stays rule-based)
- Auto-applying recommended fixes
- Webapp dashboard
- Cross-project aggregation
- Tier 3 post-merge issue references signal (deferred to v0.8.1 per spike)
- Tier 3 review-comment topic clustering (deferred to v0.8.2/v0.9 per spike)
- PAT fallback auth for `--github` (deferred to v0.8.1+ per spike)
- Config file layer (`~/.config/agentfluent/config.yaml`) -- skip for v0.8
- Tool-inventory diagnostics epic (#371-#375) -- PARKED, no relevance trigger hit
- ERROR_PATTERN FP reduction (#333) -- fits theme but lower priority than anchors; v0.8.1 candidate
- Negative recommendations ("remove this subagent") -- deferred per D020
- `agentfluent report` for `diff` output -- deferred from v0.7 OQ3, still deferred
- AskUserQuestion-anchored wait detection -- upstream gap (anthropics/claude-code#55240) means zero AskUserQuestion events in JSONL; cannot be built (D038)

## 4. In Scope -- 11 issues

### Stream A: Diagnostics Signal Quality (dogfood fixes)

Four independent stories addressing the dominant misleading signals from the v0.7 dogfood.

| # | Title | Effort | Priority | Deps |
|---|-------|--------|----------|------|
| #453 | Tag no-trace invocations as duration-unreliable | XS-S (~1 day) | high | None |
| #454 | Re-tune idle-gap thresholds for moderate user-coupled waits | M (2-3 days) | medium | Soft #453 |
| #395 | Down-weight `retry_loop` on built-in tools (Read dominates noise) | S (1-2 days) | medium | None |
| #396 | `reviewer_caught` parent_acted interpretation -- healthy-band gating | S-M (1-2 days) | medium | None |

Note: #394 (original AskUserQuestion-anchored approach) was closed and replaced by #453 + #454 after empirical investigation disproved its premise. See D038.

### Stream B: Tier 3 GitHub Enrichment

New external data source. Epic #398 with three child stories in a dependency chain.

| # | Title | Effort | Priority | Deps |
|---|-------|--------|----------|------|
| #399 | Tier 3 infrastructure: `gh` detection, cache, `--github` flag, consent UX | M-L (3-5 days) | high | None |
| #400 | `CI_FAILURE_FIRST_PUSH` signal implementation | M (2-3 days) | high | #399 |
| #401 | `PR_REVIEW_COMMENT_DENSITY` signal implementation | M (2-3 days) | high | #399 |

### Stream C: Signal Calibration

| # | Title | Effort | Priority | Deps |
|---|-------|--------|----------|------|
| #402 | `feat_fix_proximity` precision validation (v0.7 calibration check) | S-M (1-2 days) | medium | None |

### Stream D: Docs

| # | Title | Effort | Priority | Deps |
|---|-------|--------|----------|------|
| #392 | docs(changelog): tidy v0.7.0 manual breaking-changes section placement | XS (<1 day) | low | None |
| #390 | docs: catch up README + GLOSSARY + CHANGELOG for v0.8.0 | M (2-3 days) | required | All features |

### Stretch

| # | Title | Effort | Priority | Deps |
|---|-------|--------|----------|------|
| #333 | ERROR_PATTERN FP reduction on prose-heavy outputs | M (2-3 days) | low | None |

**Total in-scope: 11 issues (10 must-include + 1 stretch), ~19-27 dev days**

## 5. Open Questions / Decisions Needed

All four open questions resolved by user on 2026-05-18 -- recommendations accepted as written.

### OQ1: Should `--github` imply `--git`? -- **RESOLVED: (a) imply silently**

The Tier 3 infrastructure story (#399) proposes that `--github` implies `--git` because session-to-PR mapping relies on git commit data from Tier 2. This creates a coupling: a user who wants only GitHub signals also gets local git signals in their output. **Alternatives:** (a) `--github` implies `--git` silently -- simplest; (b) `--github` requires `--git` explicitly -- user knows what they're opting into; (c) `--github` runs Tier 3 independently, using timestamp heuristics for session-to-PR mapping instead of git data.

**Decision:** (a) imply silently. The coupling is real (session-to-PR needs git data), and forcing `--git --github` is ergonomically worse than a single flag. Document the implication in `--help`.

### OQ2: Does `--github` in CI/non-TTY require explicit consent? -- **RESOLVED: no, `--github` is consent**

The spike (Section 5) proposes that `--github` in non-TTY contexts is self-consenting. An alternative is requiring `--accept-github-tos` in non-TTY contexts. **Decision:** `--github` is consent in non-TTY. TTY still prompts on first run. Adding a second flag for CI adds friction for the primary early adopter (the project owner running in their own CI).

### OQ3: Should v0.8 include the CHANGELOG breaking-changes section restructuring (#392)? -- **RESOLVED: yes**

This is a docs chore from v0.7.0's release. It touches `release-please-config.json` which has CI implications. **Decision:** Include as a low-priority early win -- it's XS effort and prevents the pattern from compounding into v0.8.

### OQ4: Tier 3 dogfood scope -- agentfluent or CodeFluent? -- **RESOLVED: agentfluent**

Tier 3 needs a GitHub-hosted repo with PRs, CI, and review comments. AgentFluent qualifies. CodeFluent also qualifies and has a longer PR history. **Decision:** Dogfood against agentfluent first -- both for consistency with prior dogfood runs and because the user has been more active here recently. CodeFluent validation can follow if breadth is needed.

## 6. Dependencies

```
STREAM A (Diagnostics Signal Quality) -- mostly independent
[#453 tag no-trace] -- independent
[#454 threshold re-tuning] -- soft dep on #453 (dogfood validation only)
[#395 retry_loop down-weight] -- independent
[#396 reviewer_caught band] -- independent

STREAM B (Tier 3 GitHub Enrichment) -- sequential
[#399 infrastructure] --> [#400 CI_FAILURE_FIRST_PUSH]
                      --> [#401 PR_REVIEW_COMMENT_DENSITY]

STREAM C (Signal Calibration) -- independent
[#402 feat_fix_proximity calibration] -- independent

STREAM D (Docs) -- last
[#392 CHANGELOG tidy] -- independent (early win)
[#390 docs catch-up] -- after all features

STRETCH
[#333 ERROR_PATTERN FP] -- independent
```

### Cross-stream independence

Streams A, B, and C have zero cross-dependencies. They can be implemented in any order. Stream D (#390) depends on all features being final.

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `gh` CLI subprocess is flaky on some platforms (Windows, Docker) | Tier 3 doesn't work for a subset of users on day one | `gh` is the most common case; clear error messages guide users. PAT fallback in v0.8.1 covers CI/headless. |
| Session-to-PR mapping is imprecise (timestamp heuristics) | Tier 3 signals fire on wrong PRs | Use git commit SHAs + `gh api commits/{sha}/pulls` for precise mapping. Fall back to timestamp only when commit data is unavailable. |
| Idle-gap threshold re-tuning (#454) degrades `stuck_session` recall | Calibrated signal regresses | Hard constraint in #454 ACs: `stuck_session` recall must remain >= 100% on the original 12 stuck traces. If shared constants can't serve both signals, split into separate threshold pairs. If re-tuning proves too risky, #454 can slip to v0.8.1 -- #453 alone prevents silently misleading durations. |
| Tier 3 rate-limit degradation makes signals unreliable | Users don't trust partial output | `tier3_degraded: true` field in JSON envelope; visible warning in CLI output. Partial > nothing. |
| `feat_fix_proximity` precision check (#402) reveals poor precision | Signal needs rework | The story explicitly gates on precision results: if <70%, tune thresholds rather than ship uncalibrated. |

## 8. Success Criteria

v0.8 is successful when:

1. **pm's duration metric is honest.** `agentfluent analyze --project agentfluent` shows pm's average duration < 10 minutes (was 33 minutes in v0.7 dogfood). No-trace invocations are visually annotated as unreliable (#453). Reliable invocations have tighter idle-gap subtraction (#454). Combined effect: the table tells the truth.
2. **Retry noise is suppressed.** Read retries fall out of the top 5 priority fixes in the agentfluent dogfood corpus. Bash/MCP retries rank higher despite lower absolute counts.
3. **Reviewer effectiveness reads correctly.** architect's 31% parent_acted rate falls in the "healthy collaboration" band. The recommendation is INFO with "no action needed," not WARNING with "investigate."
4. **`feat_fix_proximity` precision is documented.** Calibration check completes with >=20 samples classified. If precision < 70%, one round of threshold tuning ships.
5. **`agentfluent analyze --github` produces Tier 3 signals.** `CI_FAILURE_FIRST_PUSH` and `PR_REVIEW_COMMENT_DENSITY` fire on the agentfluent repo when run with `--github`. Signals contribute to `axis_scores.quality`.
6. **Tier 3 degrades gracefully.** When rate-limited, the tool warns but continues with local-only signals. Exit code is 0.
7. **All new code has >80% test coverage.** No regressions.
8. **Docs reflect what shipped.** README, GLOSSARY, CHANGELOG all updated (#390).

## 9. Release Checklist

- [ ] #453 merged: tag no-trace invocations as duration-unreliable
- [ ] #454 merged: idle-gap threshold re-tuning (or explicitly deferred to v0.8.1 with documented rationale)
- [ ] #395 merged: retry_loop built-in-tool down-weight
- [ ] #396 merged: reviewer_caught healthy-band interpretation
- [ ] #402 merged: feat_fix_proximity precision validation
- [ ] #399 merged: Tier 3 infrastructure (gh, cache, --github, consent)
- [ ] #400 merged: CI_FAILURE_FIRST_PUSH signal
- [ ] #401 merged: PR_REVIEW_COMMENT_DENSITY signal
- [ ] #392 merged: CHANGELOG tidy
- [ ] #390 merged: docs catch-up
- [ ] Dogfood run: `agentfluent analyze --project agentfluent --diagnostics --git --github --json` produces clean output with Tier 3 signals
- [ ] Dogfood run: pm duration metric is honest (< 10 min avg on reliable invocations)
- [ ] Dogfood run: no-trace invocations visually annotated
- [ ] Dogfood run: Read retries no longer dominate priority fixes
- [ ] `uv run pytest --cov=agentfluent` passes with >80% coverage
- [ ] `uv run ruff check src/` clean
- [ ] `uv run mypy src/agentfluent/` clean
- [ ] CHANGELOG updated via release-please
- [ ] Version bump to 0.8.0
