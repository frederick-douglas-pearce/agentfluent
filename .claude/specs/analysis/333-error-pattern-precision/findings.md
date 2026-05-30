# #333 — `ERROR_PATTERN` residual FP rate calibration

**Date:** 2026-05-30
**Corpus:** all projects under `~/.claude/projects/` (3 with `ERROR_PATTERN` hits: agentfluent, codefluent, classifier)
**Sample size:** 29 of 29 signals (full population pre-dedup; no sampling needed)
**Source data:** `sample.tsv` (raw extract), `labels.tsv` (with classifications), `script.py` (extractor)
**Reference precedent:** #281 (window-bound), #321 (USER_CORRECTION ≤30% FP target), #402 (FEAT_FIX_PROXIMITY ≥70% precision)

## Rubric

Each row is one `ERROR_PATTERN` keyword match in the 200-char leading window of an `AgentInvocation.output_text`. Classified as:

- **TP (true positive)** — the keyword surfaces a real error event the agent's run encountered: a system-emitted error message (`Agent type 'X' not found`), an actual tool-permission denial (`hook blocked writes`), an explicit failure reported in the agent's output as something that happened during execution.
- **FP (false positive)** — the keyword is topic-mention prose: the agent is discussing error-handling code, naming a dict key or identifier (`error_rates`, `_exit_with_error`, `conversations_with_errors`), reading back an issue or PR title (`Error Recovery Pattern Detection`), or describing failure modes hypothetically (`would type-error`). No actual error event occurred in the agent's run.
- **Borderline** — ambiguous; lean FP per #321 / #402 conservative posture.

**Labeling:** done by Claude (Opus 4.7) with 250-char snippet radius, single pass. No borderlines in this sample — the corpus cleanly separates into system error strings (TPs) and code-discussion prose (FPs).

## Headline

| Scheme | visible | TP | FP | precision | TPs silenced |
|---|---:|---:|---:|---:|---:|
| Current `_dedup_error_patterns` | 4 | 0 | 4 | **0%** | 4 |
| Hypothesis 5 (per-invocation trace gate) | 2 | 2 | 0 | **100%** | 2 (trace-covered) |
| H5 + min-match ≥ 2 | 0 | 0 | 0 | — | 4 (too aggressive) |

29 raw signals across 3 projects: agentfluent (16), codefluent (9), classifier (4). Trace breakdown: traced=27 (clean-trace subset=10), untraced=2.

## TPs (4)

| ID | project | agent_type | keyword | has_trace | description |
|---|---|---|---|---|---|
| S0004 | agentfluent | anthropic-research | not found | false | System "Agent type 'anthropic-research' not found" — agent didn't exist in `.claude/agents/` |
| S0005 | agentfluent | anthropic-research | blocked | true | Hook denied writes to `.claude/agent-memory/` (out of allow-list) |
| S0014 | agentfluent | pm | blocked | true | PM agent's hook denied writes outside `.claude/specs/` / `docs/` |
| S0026 | codefluent | architect | not found | false | System "Agent type 'architect' not found" — codefluent doesn't define an architect agent |

The TP set splits cleanly into two classes:
- **System "not found" errors** (S0004, S0026): single-match in window, always untraced (the agent didn't exist, so there was no run to trace).
- **Hook denials** (S0005, S0014): single-match in window, traced with `retry_loop` + `tool_error_sequence` (the hook deny showed up as `is_error=True` on the trace's tool_use, driving trace-level signals).

## FPs (25)

Dominant class is **code-discussion prose**: 18 of 25 FPs are Python/TypeScript identifiers (`_extract_error_signals`, `error_rates`, `conversations_with_errors`, `validation_failed`) appearing in code-review or implementation-plan output.

Secondary classes:
- **Issue / PR / section titles** (5): "Error Recovery Pattern Detection", "error-path API testing"
- **Hypothetical-error prose** (2): "would type-error", "TypeError issue" describing existing bug under review

No FP in the sample matched a leading-edge anchored pattern (`^Error:`, `^Permission denied`); confirming the architect's #281 observation that the precision gap is keyword-on-prose ambiguity, not window size.

## Finding 1 — current `_dedup_error_patterns` has a recall bug

`pipeline.py:152` drops metadata `ERROR_PATTERN` for any agent_type that produced **any** trace-level signal anywhere in the analysis scope. The dedup operates at agent-type granularity, but the TP class includes **invocations whose agent_type cannot have a trace at all** ("Agent type X not found" — the agent didn't run).

On this corpus:
- S0004 (`anthropic-research not found`) is silenced because S0005 (also `anthropic-research`, different session) produced trace signals.
- S0026 (`architect not found`) is silenced because S0021 (also `architect`) produced trace signals.

Both are real configuration issues the user should see. The cross-invocation suppression is wrong because the agent_type signal-presence doesn't generalize to invocations of the same name that never executed.

## Finding 2 — min-match gate is wrong for this corpus

The architect's #281 review proposed a min-match ≥ 2 threshold on top of the bounded window, on the assumption that single isolated keywords are noise and clustered keywords are real cascades. The data inverts this:

- All 4 TPs are **1-match** in the leading window. System error messages and hook denials are single-line: one error string, one keyword hit.
- 21 of 25 FPs are also 1-match (code identifiers tend to appear once).
- The remaining 4 FPs are 2-match — `errors`/`error` dict-key discussions that repeat the term.

A min-match ≥ 2 gate would silence 100% of TPs and only 16% of FPs on this corpus. Wrong direction.

The architect's intuition for the gate was sound for *runtime error cascades* (chained `Failed to retry. Operation aborted.`-style prose). But the dominant TP shape here is **system-emitted single-line messages**, where one keyword == one event by construction.

## Finding 3 — hypothesis 5 trades 2 TPs for 0 FPs, with coverage preserved

Per-invocation gate `if inv.trace is not None: continue` in `_extract_error_signals` silences 2 TPs (S0005, S0014, the hook-deny events). But:

- Both invocations' traces emit `retry_loop` + `tool_error_sequence` trace-level signals.
- Those signals drive specific correlator recommendations through `PermissionFailureRule` / `ToolErrorSequenceRule` paths.
- The metadata `ERROR_PATTERN` was the redundant fallback; trace signals already covered these.

So coverage isn't lost — the recommendation surface is preserved, just routed through the more specific trace-level path that the user wants anyway (per architect's #281 forward-compatibility argument).

The 2 untraced TPs that hypothesis 5 surfaces (S0004, S0026) are the user-visible win.

## Decision (for architect review)

**Recommend hypothesis 5 alone.** Implementation:

1. Inside `_extract_error_signals` (signals.py:103), skip invocations where `inv.trace is not None`. The trace is authoritative; metadata fallback is for trace-less invocations only.
2. **Remove `_dedup_error_patterns`** (pipeline.py:152) and its `signals = _dedup_error_patterns(signals)` call (pipeline.py:242). Per-invocation gating subsumes its purpose and fixes the cross-invocation TP-silencing bug.
3. **Do NOT add a min-match gate.** Corpus shows it silences 100% of TPs.
4. Tests: add cases that (a) traced invocation with error-keyword output emits no metadata signal, (b) traced invocation with clean trace AND error-keyword output emits no metadata signal (the case current dedup misses), (c) untraced invocation with error-keyword output emits the signal.

### Pipeline-level effect

- Visible `ERROR_PATTERN` signals on dogfood corpus: **4 → 2** (50% reduction).
- Visible signal precision: **0% → 100%**.
- Recall on "Agent type X not found" class: **0% → 100%** (currently silenced, becomes visible).
- Recall on hook-deny class: covered via trace signals (no regression at the recommendation level).

### Why not also keep `_dedup_error_patterns` as belt-and-suspenders

Two reasons:
- It silences real TPs (the recall bug). Keeping it means continuing to hide "Agent type not found" errors on dogfood-like corpora where any agent_type produced traces.
- Per-invocation gating is a strict superset of the dedup's intent: every signal `_dedup_error_patterns` correctly drops would also be dropped by hypothesis 5 (those are by definition traced invocations).

### Risks / open questions for architect

1. **Untraced + 1-match TPs in the wild beyond this corpus.** The 2 untraced TPs here are both `Agent type X not found` from `.claude/agents/`-driven Claude Code runs. Agent SDK calls (no linked trace files) may produce a wider FP class on this 1-match leading window. The corpus didn't surface any.
2. **`compute_error_rate` is untouched** by this proposal. It uses `iter_error_matches` for a numeric ratio normalized by `tool_uses`; per #281 the FP load there is acceptable. Confirm architect agrees no parallel change needed.
3. **Long-term:** the metadata fallback exists for Agent SDK calls without trace JSONL. As Agent SDK adopts session traces (Claude Code already does), this whole code path becomes vestigial. Hypothesis 5 keeps the helper simple and easy to delete later.

## Out of scope

- Replacing keyword-based detection with anchored patterns (`^Error:`, `^Permission denied`) — the #281 architect comment flagged this as a deeper precision improvement. Hypothesis 5 makes it unnecessary on this corpus; defer until a corpus with untraced FPs surfaces.
- Confidence-field metadata on `DiagnosticSignal` — architect #281 review option (b). Not needed given the precision delta we get from hypothesis 5 alone.
- Re-tuning `ERROR_DETECTION_WINDOW_CHARS` — independently calibrated per #281 / #241.

## References

- Parent: #333
- Calibration precedents: #281 (window-bound), #321 (≤30% FP target), #402 (≥70% precision target)
- Code: `src/agentfluent/diagnostics/signals.py:_extract_error_signals`, `src/agentfluent/diagnostics/pipeline.py:_dedup_error_patterns`
- Architect comment on #281 (proposed gate, confidence-field options): https://github.com/frederick-douglas-pearce/agentfluent/issues/281#issuecomment-4403984523
- Decision will be logged in `.claude/specs/decisions.md` post-implementation
