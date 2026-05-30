# #402 — `FEAT_FIX_PROXIMITY` precision calibration

**Date:** 2026-05-29
**Corpus:** `agentfluent` repo, last 90 days (window: ~2026-02-28 → 2026-05-29)
**Sample size:** 34 of 34 pairs (full population; no sampling needed)
**Source data:** `analyze-output.json` in this directory; pair extract at `pairs.tsv`
**Reference precedent:** #321 (USER_CORRECTION ≤30% FP target → ≥70% precision)

## Rubric

Each pair is one feat commit + ≥1 fix commit within `DEFAULT_PROXIMITY_DAYS` (7d) sharing ≥1 file. Classified as:

- **TP (true positive)** — at least one fix in the pair addresses a defect the feat plausibly shipped. A reviewer working from the feat's diff could reasonably have surfaced the issue. The fix's scope conceptually overlaps the feat's scope (parser bugs in parser feats, schema bugs in feats consuming the schema, etc.).
- **FP (false positive)** — all fixes in the pair are either:
  - **coincidental file overlap** — fix touches a widely-used file the feat also touched, but addresses something unrelated to the feat's purpose, OR
  - **intentional iteration** — fix is the planned calibration / tuning outcome of the feat (e.g., calibration notebook feat → recalibration commit), OR
  - **unrelated chore** — cleanup commits (unused imports, doc edits) marked `fix:`.

**Tiebreak:** when ambiguous, count as TP only if the fix subject indicates the feat's domain (e.g., `fix(parser)` for `feat(parser)` lineage). Borderline cases lean FP, mirroring #321's conservative posture.

## Per-pair classifications

| # | sev | shared files | feat | fixes | class | rationale |
|---|-----|-------------:|------|-------|-------|-----------|
| 01 | W | 1 | core data models (`session.py`) | dedup token-counting; parse `toolUseResult` shape | **TP** | Both fixes address model gaps shipped in the feat (parser data shape, dedup logic). |
| 02 | W | 2 | parser + project discovery | dedup; parse `toolUseResult` | **TP** | Direct parser bugs in the feat's module. |
| 03 | W | 2 | agent invocation extractor | parse `toolUseResult` | **TP** | Direct extractor bug. |
| 04 | W | 5 | execution analytics pipeline | remove unused imports (chore); add opus-4-7 pricing + filter synthetic model | **TP** | Pricing fix addresses analytics-pipeline gap (model coverage). |
| 05 | W | 1 (CLAUDE.md) | security hooks | parse `toolUseResult` | **FP** | Shared file is doc-only; fix is parser bug unrelated to security hooks. |
| 06 | W | 4 | subagent trace parser | merge multi-fragment content blocks; route parse warnings to stderr | **TP** | Both fixes are parser bugs in the feat's module. |
| 07 | W | 2 | trace-level signal extraction (correlator+models) | `agent_type None instead of ""` schema | **TP** | Schema decision is part of the feat's correlator output; reviewer could have asked. |
| 08 | W | 3 | CLI integration of trace diagnostics | schema fix (touches `pipeline.py`) | **FP** | CLI integration ≠ schema design; fix is correlator/models scope, pipeline.py is incidental. |
| 09 | W | 6 | delegation clustering + draft generation | recalibrate cluster confidence thresholds; schema fix | **TP** | Schema fix relates to delegation-rule output. Recalibration alone would be FP; schema fix makes pair TP. |
| 10 | W | 4 | model-routing complexity + mismatch | recalibrate clusters; schema fix | **TP** | Same reasoning — schema fix is the feat's rule output. |
| 11 | W | 4 | model-mismatch cross-reference into delegation notes | recalibrate clusters; schema fix | **TP** | Same. |
| 12 | W | 1 | retain model on SubagentTrace | merge content blocks | **TP** | Direct trace-parser bug in the feat's module. |
| 13 | W | 2 | calibration notebook | recalibrate cluster confidence thresholds | **FP** | Calibration notebook *is* the tool; recalibration is the planned outcome, not a quality miss. |
| 14 | W | 3 | MCP tool usage extraction | default to `general-purpose` when missing `subagent_type`; schema fix | **TP** | Both fixes address gaps in MCP extraction. |
| 15 | W | 4 | MCP audit rules | schema fix (touches mcp_assessment + correlator + models) | **TP** | Schema fix is part of MCP rule output schema. |
| 16 | W | 1 | wire MCP audit into pipeline | schema fix (pipeline.py only) | **FP** | Wiring ≠ schema design; pipeline.py is incidental overlap. |
| 17 | W | 6 | aggregate duplicate recommendations | name signal type in aggregated prefix; schema fix | **TP** | Naming fix is aggregation-specific. |
| 18 | W | 4 | tailor recs for built-in agents | name signal type prefix; schema fix | **TP** | Naming fix is aggregation work this feat introduced. |
| 19 | W | 2 | surface copy-paste YAML subagent draft | schema fix | **FP** | YAML draft rendering ≠ correlator schema. |
| 20 | W | 1 | populate invocation_id on recommendations | filter PERMISSION_FAILURE FPs | **FP** | Different signal entirely; trace_signals.py incidental overlap. |
| 21 | I | 2 (md+yaml) | glossary YAML + explain CLI | PERMISSION_FAILURE FP filter | **FP** | Filter fix updated glossary (using new infra) — using the file ≠ fixing the feat. |
| 22 | I | 6 | subtract idle gaps from duration | PERMISSION_FAILURE filter; bound is_error regex (200ch); bound MCP regex; bound ERROR_REGEX | **TP** | At least the `bound is_error regex` fix addresses an outlier-detection signal shipped in the feat. |
| 23 | I | 4 | IQR-based outlier detection | PERMISSION_FAILURE; MCP regex; ERROR_REGEX windowing | **TP** | ERROR_REGEX windowing addresses noise in the outlier-detection signal. |
| 24 | W | 1 | parent-thread tool-burst extractor | bound MCP is_error regex | **FP** | Tool-burst ≠ MCP regex; `traces/parser.py` is incidental overlap. |
| 25 | W | 1 | priority ranking + Top-N summary | cache_efficiency color + relativize config paths | **FP** | UX fix on different signal; correlator.py incidental. |
| 26 | I | 2 | `agentfluent diff` CLI | cache_efficiency color; Origin column in diff Token Metrics | **FP** | Origin column is feature work labeled `fix:`; cache fix is UX polish on a different signal. |
| 27 | I | 4 | user-correction signal + quality_signals module | suffix-match REVIEWER_CAUGHT; tighten USER_CORRECTION; strip skill-metadata; cache_efficiency | **TP** | At least two fixes (`tighten USER_CORRECTION`, `strip skill-metadata`) address precision of the signal this feat shipped. |
| 28 | I | 1 | extract `first_message_timestamp` | extract text from list-shape `tool_result` content | **TP** | Direct parser bug in the feat's module. |
| 29 | I | 4 | file-rework density quality signal | REVIEWER_CAUGHT suffix-match; tighten USER_CORRECTION; strip skill-metadata; cache_efficiency | **FP** | All fixes target *different* signals (REVIEWER_CAUGHT, USER_CORRECTION); quality_signals.py overlap is incidental. |
| 30 | I | 4 | reviewer-caught signal + `_QualityRule` base | REVIEWER_CAUGHT suffix-match; tighten USER_CORRECTION; strip skill-metadata; cache_efficiency | **TP** | REVIEWER_CAUGHT suffix-match directly fixes the feat's signal. |
| 31 | I | 1 | axis labels on recommendations (diff_table.py) | cache_efficiency color; Origin column | **FP** | Both fixes are unrelated UX/feature work on diff_table.py. |
| 32 | W | 2 | quality signal calibration constants + notebook | REVIEWER_CAUGHT suffix-match; tighten USER_CORRECTION; strip skill-metadata | **FP** | Classic intentional iteration: the calibration-constants feat *introduced the framework*; the fixes are the calibration outcomes the framework produced. |
| 33 | I | 1 | propagate window + diagnostics-version into diff | Origin column in diff | **FP** | Origin column is feature work, not a quality miss in the feat. |
| 34 | I | 1 | `anthropic-research` subagent | drop `memory:project`, point Agent SDK at raw GitHub | **TP** | Direct fix to the subagent definition shipped in the feat. |

## Tally

- **TP**: 01, 02, 03, 04, 06, 07, 09, 10, 11, 12, 14, 15, 17, 18, 22, 23, 27, 28, 30, 34 = **20**
- **FP**: 05, 08, 13, 16, 19, 20, 21, 24, 25, 26, 29, 31, 32, 33 = **14**

**Baseline precision: 20 / 34 = 58.8%** — below the 70% target.

## Dominant FP patterns

1. **Fan-out from broad-impact fix commits.** A single fix that touches widely-used files (`8f90cbf fix(schema)`, `fb4cd54 fix(cli,diagnostics)`, `552cbe7 fix(diff): Origin column`) gets paired with many unrelated feats in the proximity window. 8 of the 14 FPs (05, 08, 16, 19, 25, 26, 31, 33) are downstream of fan-out.
2. **Intentional calibration iteration.** A feat ships a calibration framework or initial thresholds; the planned-from-the-start retuning commits pair as fixes. 3 of the 14 FPs (13, 32, and partially 20, 24).
3. **Single-file coincidental overlap.** Pairs where exactly one file is shared between feat and fix — usually a high-traffic module like `parser.py`, `correlator.py`, or `pipeline.py`. The fix touches it for one reason; the feat touched it for another. 8 of the 14 FPs have only 1 shared file.

## Tuning options simulated

| Option | Rule change | Kept pairs | TP / FP | Precision | Recall loss |
|---|---|---:|---:|---:|---|
| Baseline | `feat ∩ fix files ≥ 1`, 7d window | 34 | 20 / 14 | **58.8%** | 0% |
| **A** | `feat ∩ fix files ≥ 2` | 23 | 16 / 7 | 69.6% | 4 TPs lost (01, 12, 28, 34) |
| **B** | A + exclude `.md` / `.yaml` / `.yml` from overlap count | 21 | 16 / 5 | **76.2%** ✓ | Same 4 TPs lost (#21 and #26 also drop, both FP) |
| C | B + proximity 7→5 days | 19 | 14 / 5 | 73.7% | 6 TPs lost (also #22, #23) |
| D | Cap fan-out: each fix matches ≤3 feats | hard to define ordering; not pursued | — | — | — |

**Option B is the recommended tuning.** Single defensible rule change (raise overlap minimum from 1 to 2, counting code files only), reaches ≥70%, retains the recall on the high-density TPs.

## Remaining FPs after Option B (5 pairs)

08, 13, 19, 29, 32. Patterns:
- **08, 19**: schema fix touching incidental files at high overlap count.
- **13, 32**: calibration intentional iteration. Both involve feats that ship a calibration framework / constants, then the planned recalibration commit pairs.
- **29**: cross-signal overlap (quality_signals.py shared by file-rework + USER_CORRECTION + REVIEWER_CAUGHT).

These are the patterns to file as a follow-up issue for targeted suppression. **Filed as [#471](https://github.com/frederick-douglas-pearce/agentfluent/issues/471) on 2026-05-29.**

## Recommendation

1. **Implement Option B** (raise `shared_files` minimum from 1 to 2, counting code files only — exclude `.md`/`.yaml`/`.yml` from the overlap calculation). Apply the `>=2` filter **per fix** inside the inner loop of `_find_feat_fix_pairs`, not on the accumulated `shared` set (architect review, 2026-05-29).
2. **Document calibration** in `git_signals.py`: baseline 58.8%, post-tuning 76.2%, methodology link to this file.
3. **File follow-up issue** for the residual FP patterns (intentional iteration detection; feature-work-labeled-as-fix detection).
4. **No GLOSSARY change** unless the user wants the precision number surfaced to consumers.

## Forward note: automated-classifier candidate (LLM judge OR classical ML)

This calibration round — and the precedents at #274 (`reviewer_caught`) and #321 (`user_correction`) — are a tagged candidate for **automated classification** once that capability lands on the AgentFluent roadmap. The work is structured: given a small evidence bundle (feat commit + fix commits + shared files) plus a rubric, emit a TP/FP label with rationale. The per-pair table above is suitable both as a few-shot prompt for an LLM judge **and** as a labeled training/eval set for a classical ML classifier.

Two automation paths to evaluate in parallel when this milestone is reached:

1. **Classical ML / NLP classifier** (TF-IDF over commit messages + file-path features, fed to logistic regression or gradient-boosted trees; or similar lightweight supervised model). **Benefits:** deterministic across runs, zero per-invocation API cost, fast enough to run on every release as a CI gate, no model-version drift, and on narrow text-classification tasks of this shape with a few hundred labeled examples they routinely match or beat few-shot LLM judges.
2. **LLM-as-a-judge.** **Benefits:** works from a rubric without requiring a labeled training set, handles genuinely subjective rubric clauses, and produces a human-readable rationale alongside each label. **Costs:** per-call API spend, non-determinism, and verdicts that can shift across model versions.

**Default stance for every LLM-judge candidate going forward:** evaluate the classical-ML path first, because the determinism and cost properties make it a better fit for "calibrate every release" usage. Reach for an LLM judge when the rubric is genuinely subjective, when labeled data is scarce, or as a fallback / tie-breaker on rows the classical model is uncertain about. Hybrid (classical model as first-pass filter, LLM judge on uncertain rows) is often the right answer.

Manual rounds remain valuable until either path is built, because they validate that the signal's *promise* matches its behavior and produce the labeled examples both paths need.
