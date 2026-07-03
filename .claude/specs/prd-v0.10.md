# PRD: AgentFluent v0.10 -- Meet the Primary Audience

**Status:** Draft
**Date:** 2026-06-19
**Author:** PM Agent
**Decision log:** See `decisions.md` (no new D-entries required for scoping; #517 governed by existing D013).
**Source dogfood:** `.claude/specs/analysis/2026-06-05-v09-dogfood/analysis.md` (candidates #510-#514).
**SDK epic spec:** `.claude/specs/prd-agent-sdk-discovery.md`.

---

## 1. Theme

**"Meet the primary audience."**

For nine releases AgentFluent has been validated against one data source it understands deeply: **Claude Code** JSONL sessions. Every signal, every metric, every calibration has been tuned on Claude Code traces -- almost all of them from a single developer's `~/.claude/projects/`. The v0.9 "Count Every Turn" release closed the last major metric gap (model turns are now visible at every level) and the v0.9 dogfood proved the surface ships clean: model turns populated, the three Advanced Tool Use signals fired-or-stayed-correctly-silent, `tier3_degraded: false`, no parse exceptions.

But the tool has a credibility gap it has never confronted: **its stated PRIMARY target -- developers building agents with the Claude Agent SDK -- runs a code path the tool has never seen.** We do not know where the SDK writes sessions, whether they share the documented Claude Code JSONL schema, or whether an SDK main session is even distinguishable from a Claude Code interactive session (the load-bearing D013 question). Every SDK-dependent feature on the roadmap, starting with #112, is blocked on this unknown.

v0.10 makes first contact with that audience. It is a **discovery-led release**: the marquee work is empirically learning what Agent SDK session data looks like (Epic #517), captured as real bytes on disk. Around that discovery, v0.10 lands the actionable calibration debt the v0.9 dogfood surfaced -- chiefly the `PARAMETER_RETRY` actionability fix (#510), which is the one genuinely actionable finding from the last dogfood and is load-bearing for the next one.

One-line pitch: **"Find out what the headline audience actually produces -- and land the calibration the last dogfood asked for."**

### Why this theme

The v0.9 dogfood (the fourth consecutive run on a Claude-Code-only corpus) has reached the limit of what a single-developer, built-in-tool-heavy corpus can teach us. Its sharpest finding -- that 92% of `PARAMETER_RETRY` fires recommend a fix the user can't apply because they target built-in tools -- is itself a signpost: **the signal's value proposition only pays off on custom SDK/MCP tool definitions, which this corpus doesn't contain.** The tool is increasingly well-calibrated for a corpus that is not its headline audience. Continuing to add Claude-Code-corpus precision work has diminishing returns until we know what the SDK corpus looks like.

The alternatives considered and rejected:

1. **Another diagnostics-precision release** (Tier B precision fixes #499/#492/#494, multi-contributor recalibration #459/#263/#482). Rejected as the *theme*: the strongest of these (#499 Tier B orchestration, #471 FEAT_FIX residuals) are explicitly blocked on a corpus that contains the patterns they target -- and three of them (#459, #263, #482) self-describe as "no work to do until multi-contributor data exists." Filing more single-corpus calibration is polishing a surface for the wrong audience. The one *actionable* precision item (#510) rides v0.10 regardless because it's load-bearing for the dogfood ritual.

2. **Pull the full SDK epic** (all of #517 -> #521, including the diff and findings synthesis). Rejected as over-scoped: the high-signal, low-effort front of the epic (the probe + corpus generation) de-risks everything downstream and answers #112's three open questions on its own. The diff/synthesis tail (#520/#521) is best done once the corpus exists and can be re-scoped with real bytes in hand. See §4 for the staged rationale.

3. **Implement the per-turn diagnostic ratios** (#469, carried from v0.9 as a stub). Deferred again -- still requires the dogfood-distribution analysis its own acceptance criteria demand, and v0.10's dogfood is the first run where post-#467 turn data is fully present. It stays a tracking stub.

## 2. Goals

1. **Make first empirical contact with Agent SDK session data** -- run a hello-world SDK probe (#518) to answer #112's three open questions (location, discriminator, options metadata), build a representative data-generation agent (#522), and generate a real SDK session corpus (#519).
2. **Land the one actionable v0.9 dogfood calibration finding** -- `PARAMETER_RETRY` actionability gate + message-template fix + built-in-tool deprioritization (#510). This is load-bearing for the v0.10 dogfood.
3. **Close the cheap dogfood-surfaced documentation and config-hygiene gaps** -- document `model_turns` vs `api_call_count` (#511), port the #479 prompt tightening to the `pm` agent (#512), re-measure #479 on a clean post-fix window (#513), investigate README documentation-thrash (#514).
4. **Eliminate a known data-loss risk in maintainer tooling** -- the pm agent's full-file `Write` clobbering append-only `decisions.md` (#500).
5. **Ship docs that reflect what shipped** -- catch-up issue (#504, auto-created).

## 3. Non-Goals

- LLM-powered analysis (stays rule-based; D035 tracks candidates).
- Auto-applying recommended fixes (D002).
- Webapp dashboard, cross-project aggregation, config file layer.
- **Implementing #112** (SDK main-session model routing) -- #517 *unblocks* it; v0.10 does not build it. #112 is re-scoped with real data after the corpus lands.
- **Modifying the production parser to detect/special-case SDK sessions** -- v0.10 documents where parser assumptions break for SDK data (downstream of the corpus); it does not fix them.
- **SDK diff + findings synthesis (#520, #521)** -- deferred to v0.11. The corpus must exist and stabilize first; the synthesis is re-scoped with bytes in hand. (See §4 for the staged rationale.)
- **Per-turn diagnostic ratios (#469)** -- stub only, carried again. Requires the v0.10 dogfood turn-distribution analysis its own AC demands.
- **`TOOL_ORCHESTRATION_CHAIN` Tier B (#499)** -- the corpus still has essentially no true positives for this signal (see #499 notes). Deferred until a corpus with genuine orchestration chains exists; the SDK corpus from #519 may become that corpus, which is a reason to sequence #499 *after* SDK discovery, not before.
- **`PARAMETER_RETRY` Tier B (#492), `TOOL_INVENTORY_OVERSIZED` built-ins (#494)** -- net-new signal *extensions*, not calibration debt. Both fire (or stay silent) correctly today; extending them is value-add, not v0.10-load-bearing. The SDK corpus is also the natural validation ground for #492's custom-tool case.
- **Multi-contributor recalibration (#459, #263, #482), FEAT_FIX residual FPs (#471)** -- all blocked on multi-author / wider-corpus data that does not yet exist. They self-describe as "no work until that condition is met." Tracked, not scheduled.
- `agentfluent report` for `diff` output -- deferred since v0.7, still deferred.

## 4. In Scope

### Stream A: Agent SDK Discovery (Epic #517, `epic:agent-sdk-discovery`)

The marquee. A research/discovery epic whose deliverable is **knowledge + sample data**, not a shipped feature. v0.10 pulls the **front three stories** -- the cheap, high-signal-per-effort work that de-risks the rest and answers #112's blocking questions. The diff (#520) and findings synthesis (#521) are deferred to v0.11.

| # | Title | Type | Priority | Deps |
|---|-------|------|----------|------|
| #517 | Epic: Agent SDK session data discovery | research (epic) | high | -- |
| #518 | S1a: Hello-world Agent SDK probe (locate & fingerprint sessions) | research | high | None -- **do first** |
| #522 | S1b: Build the representative Agent SDK data-generation agent | research | high | #518 |
| #519 | S2: Run the SDK agent across configurations to generate a corpus | research | medium | #522 |

**Staging rationale.** #518 is the single highest-signal-per-effort point in the entire backlog: a ~15-line script that answers all three of #112's open questions for almost no effort. #522 and #519 produce the corpus that every downstream SDK feature depends on. Stopping the v0.10 cut at "corpus exists" is deliberate -- the diff (#520) and the durable findings doc (#521) are synthesis work best done *with the bytes in hand*, and #521's anonymization feasibility is an open question (PRD §7) that the corpus itself will answer. Pulling them into v0.10 would either rush the synthesis or block the cut on it. Deferring them to v0.11 keeps v0.10 shippable while still delivering the load-bearing discovery.

**Out of this stream for v0.10:** #520 (diff SDK vs Claude Code), #521 (findings doc + fixtures). Both -> v0.11.

### Stream B: Dogfood Calibration & Hygiene (v0.9 dogfood follow-ups)

The actionable findings from the v0.9 dogfood, plus one data-loss fix. All independent.

| # | Title | Type | Priority | Deps |
|---|-------|------|----------|------|
| #510 | `PARAMETER_RETRY`: require is_error first attempt, fix "failed with" message, deprioritize built-in tools | bug | high | None |
| #500 | pm subagent clobbers append-only decisions.md via full-file Write (data-loss risk) | bug | high | None |
| #512 | Apply #479 get_issue/Read prompt tightening to the pm agent | enhancement | medium | None |
| #513 | Re-measure #479 architect prompt tightening at v0.10 dogfood | research | low | None (measured at dogfood) |
| #514 | Investigate README documentation-thrash | research | low | None |

**Note on #500:** Not a v0.9-dogfood candidate, but a HIGH-priority `bug` flagged during #407 work -- the pm agent's `Write` does full-file replacement and can silently destroy the append-only `decisions.md` (D001-D042 were nearly lost). In an autonomous/background flow that commits its own work, the D-history could be lost for real. It is cheap, durable, and directly protects the spec corpus this very release writes to. Pulled in on priority grounds.

**Note on #513:** Inherently a dogfood-time measurement, not a code change. It is listed in-scope as a release-checklist item -- the v0.10 dogfood is the clean post-#479 window the v0.9 analysis asked for. No PR; the deliverable is a measurement + comment.

### Stream C: Metric Documentation (v0.9 dogfood follow-up)

| # | Title | Type | Priority | Deps |
|---|-------|------|----------|------|
| #511 | Document `model_turns` vs `api_call_count` relationship + `<synthetic>` exclusion | documentation | medium | None |

A user already hit this confusion mid-development (a `user_correction` fired in-thread). Cheap, pre-empts recurring confusion. Kept distinct from the catch-up docs (#504) because it documents a *semantic relationship*, not a feature surface.

### Stream D: Docs

| # | Title | Type | Priority | Deps |
|---|-------|------|----------|------|
| #504 | docs: catch up README + GLOSSARY + CHANGELOG for v0.10.0 | documentation | required | All features |

Auto-created by the milestone Actions workflow. Always rides the release.

### Tracking-only (carried, not implemented)

| # | Title | Disposition |
|---|-------|-------------|
| #469 | analytics: per-turn diagnostic ratios (stub) | Carried tracking item. Assessed at v0.10 dogfood; ships or defers to v0.11 per its own AC. Same disposition v0.9 gave it. |

**Total in-scope: 12 working issues (3 SDK + 5 calibration/hygiene + 1 docs-semantic + 1 catch-up + 1 epic) + 1 tracking stub (#469) = 13 milestone issues.**

## 5. Sizing Sanity Check

| Release | Issues shipped |
|---------|----------------|
| v0.8 | 13 |
| v0.9 | 18 |
| **v0.10 (proposed)** | **13 (12 working + 1 tracking stub)** |

v0.10 sits at the low end of the recent band -- appropriate for a discovery-led release where the marquee stream is deliberately staged (front three stories of #517, not all five) and the rest is calibration/hygiene cleanup rather than new feature surface. The SDK stories are research-typed (no production test-coverage gate on the throwaway probe scaffolding), which keeps their effort low despite their strategic weight.

## 6. Dependencies

```
STREAM A (Agent SDK Discovery) -- sequential
[#517 epic]                          -- umbrella
[#518 hello-world probe]             -- no deps; DO FIRST (answers #112's 3 questions)
[#522 representative agent]          -- depends on #518 (design informed by probe findings)
[#519 corpus generation]            -- depends on #522 (variant 1 carried from #518)
        ... #520 (diff), #521 (findings) -> v0.11

STREAM B (Calibration & Hygiene) -- all independent
[#510 PARAMETER_RETRY actionability] -- independent
[#500 decisions.md clobber guard]    -- independent
[#512 pm prompt tightening]          -- independent
[#513 re-measure #479]               -- independent (dogfood-time)
[#514 README thrash investigation]   -- independent

STREAM C (Metric docs) -- independent
[#511 model_turns vs api_call_count] -- independent

STREAM D (Docs) -- last
[#504 catch-up]                      -- after all features
```

The only dependency chain is within Stream A: `#518 -> #522 -> #519`. Everything else is parallelizable. Stream A and Stream B can run fully concurrently.

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| SDK writes sessions indistinguishable from Claude Code interactive sessions | Forces #112 onto a `--scope` heuristic instead of an intrinsic marker | This is an *expected, important* finding, not a failure (#518 AC grades the discriminator by reliability). "No reliable marker" reshapes #112; it doesn't block v0.10. |
| SDK runtime dep leaks into the published `agentfluent` package | Ships an unwanted heavy dep to `pip install` users | #518/#522 AC require the SDK dep stay dev/optional, isolated from runtime. Probe lives in `research/`, not `src/`. |
| SDK corpus contains un-anonymizable sensitive strings | Blocks fixture commit (a v0.11 #521 concern) | #522 scopes the agent to no MCP/network/secret surface, so the corpus is trivially anonymizable. The hard anonymization call is deferred with #521 to v0.11. |
| #510 actionability gate over-suppresses real PARAMETER_RETRY TPs | Signal goes quiet on genuine custom-tool retries | #510 AC requires a unit test for the all-success paging sequence (must NOT fire) *and* retained genuine-error cases (must still fire). The gate is "first attempt is_error," which the dominant TP satisfies. |
| #500 guard (if a hook) blocks legitimate decisions.md rewrites | Maintainer can't restructure the log | Prefer the prompt-fix mitigation; if a hook, scope it to "rejects writes that *reduce* `## Dxxx` entry count," which legitimate appends never do. |
| Discovery stream produces no shippable user-facing change | v0.10 changelog looks thin to a `pip install` user | Expected and acceptable: v0.10 is a foundation release. The user-facing deltas (#510 signal fix, #511 docs) still ship; the SDK work is changelogged as research groundwork. Mirror v0.9's framing of research items. |

## 8. Success Criteria

v0.10 is successful when:

1. **The SDK probe has answered #112's three questions.** #518 records, with real bytes on disk: where the SDK writes sessions, whether/how an SDK session is distinguishable from a Claude Code interactive session (graded by reliability), and the structure of main-session options metadata. #112 is updated to reflect findings.
2. **A representative SDK agent exists and a corpus has been generated.** #522 produces an agent exercising multiple tool types, a natural `is_error`, and a subagent-delegation variant; #519 captures the run-matrix corpus with a config->file manifest.
3. **`PARAMETER_RETRY` is actionable.** #510: no fire on all-success paging sequences; no "failed with" message on non-errors; built-in-tool fires deprioritized/annotated. Verified on the v0.10 dogfood.
4. **The decisions.md data-loss risk is closed.** #500: append-only spec logs are protected against full-file-clobbering writes (prompt fix and/or defensive guard).
5. **The metric-relationship confusion is documented.** #511: GLOSSARY/metrics reference states the `model_turns` vs `api_call_count` relationship and `<synthetic>` exclusion behavior explicitly.
6. **The pm prompt carries the #479 tightening.** #512: pm.md has the get_issue-confirm and consolidate-Read instructions; re-measured at dogfood.
7. **The #479 re-measurement is recorded.** #513: architect get_issue/Read retries measured on a fully-post-#479 window, with a verdict (fix landed / no effect / inconclusive).
8. **README thrash is understood.** #514: a finding on whether release-note/roadmap prose inlined in README should link out to CHANGELOG/docs.
9. **All new production code has >80% test coverage.** No regressions. (SDK research scaffolding is exempt -- the artifact under study is the session data, not the probe.)
10. **Docs reflect what shipped.** #504: README, GLOSSARY, CHANGELOG updated.

## 9. Release Checklist

- [ ] #518 complete: hello-world probe run; #112's 3 questions answered + recorded; SDK version pinned
- [ ] #522 complete: representative data-generation agent built (multi-tool, natural error, subagent variant); run README
- [ ] #519 complete: SDK corpus generated across the run matrix; config->file manifest captured
- [ ] #112 updated with SDK discovery findings
- [ ] #510 merged: PARAMETER_RETRY actionability gate + message fix + built-in deprioritization
- [ ] #500 resolved: decisions.md clobber protection (prompt fix and/or hook)
- [ ] #511 merged: model_turns vs api_call_count documented
- [ ] #512 done: pm.md prompt tightening applied (user-global edit, tracked via issue)
- [ ] #513 done: #479 re-measured on clean post-fix window; verdict recorded
- [ ] #514 done: README documentation-thrash investigation finding posted
- [ ] #504 merged: docs catch-up for v0.10.0
- [ ] #469 assessed: dogfood turn-distribution analysis determines ship-or-defer for per-turn ratios
- [ ] Dogfood run: SDK probe findings reviewed; PARAMETER_RETRY actionability verified; #479/#512 retry deltas measured
- [ ] `uv run pytest --cov=agentfluent` passes with >80% coverage
- [ ] `uv run ruff check src/` clean
- [ ] `uv run mypy src/agentfluent/` clean
- [ ] CHANGELOG updated via release-please
- [ ] Version bump to 0.10.0

## 10. Shipped vs. planned (post-release addendum)

**Added 2026-07-02 during the #504 close-out.** The milestone grew after this PRD was drafted
(2026-06-19). The theme was reframed to lead with the shipped, pip-visible surface; the strategic
substance is unchanged. Recorded here (not as a `decisions.md` D-entry) because the change is
milestone-level scope growth, not a design decision — the release's design calls are already covered
by D044–D046.

**Theme.** Reframed from the PRD's **"Meet the Primary Audience"** to the shipped
**"Close the Hook Gap."** Rationale: prior release themes name the user-facing capability
("Count Every Turn," "Quality Axis: Tier 3"), and a `pip install` user's headline delta this release
is the recommendation engine reaching the **hooks** config surface — not the SDK discovery, which
ships knowledge + a corpus, not an invocable feature. The SDK-discovery stream remains the strategic
marquee and leads the forward-looking [`ROADMAP.md`](../../docs/ROADMAP.md) v0.10 entry; in the
CHANGELOG it is given a full, weighty paragraph (its only CHANGELOG home) framed as groundwork.

**Scope grew (not in the PRD's §4 in-scope table):**
- **Hook coverage diagnostics** — epic #423 (C-001): `HookFieldCoverage` + `hook_inspector` (#424),
  the `DurationOutlierRule` hook-coverage branch introducing the `target=hooks` surface (#425), and
  the `run_diagnostics` wiring (#426). Scoped into the milestone 2026-06-30.
- **Concrete target-model naming** (#170) and the **1-hour cache-write pricing fix** (#542).

**Pulled forward from v0.11:** #520 (SDK-vs-Claude-Code diff) and #521 (SDK-format findings doc +
anonymized fixtures) — the PRD §3/§4 explicitly deferred both to v0.11, but the front of epic #517
stabilized early enough to land them in v0.10. Epic #517 is functionally complete (all six children
merged); GitHub closure is the owner's call.

**Slipped to v0.11:** #513 (re-measure #479 — requires the shipped package) and #514 (README
documentation-thrash investigation, still open). #500 (decisions.md clobber guard) shipped as
planned. #469 (per-turn ratios) stays a carried tracking stub.
