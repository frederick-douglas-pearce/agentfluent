# #407 — `TOOL_ORCHESTRATION_CHAIN` precision calibration

**Date:** 2026-06-02
**Corpus:** `agentfluent` + `codefluent` dogfood sessions in `~/.claude/projects/`
**Population:** 195 matching invocations (predicate: `tool_uses >= 10` AND `tokens_per_tool_use > 2000`)
**Sample size:** 30 of 195, seeded (`random.seed(407)`), stratified across the four emitting agent types
**Reproducibility (scripts in this directory):** `export.py` (corpus → `detections.tsv`, mirrors the pipeline's per-session load + trace linking), `sample.py` (seeded stratified sample), `tune.py` (threshold simulation). `detections.tsv` is the full 195-row population.
**Reference precedent:** #402 (`FEAT_FIX_PROXIMITY`, structural analog)

## What the signal fires on

The Tier A proxy aggregates per agent **type** and emits when ≥3 invocations of that
type match. In this corpus exactly four types clear the gate:

| agent_type | matching invocations |
|---|---:|
| `architect` | 62 |
| `general-purpose` | 62 |
| `pm` | 37 |
| `explore` | 28 |
| *(below gate: `candidate-promoter` 2, `plan` 2, `anthropic-research` 1, `claude-code-guide` 1)* | |

All four emitting types are **reasoning / review / scoping** subagents. The one type
whose work most resembles a genuine tool-orchestration chain — `anthropic-research`
(WebFetch/WebSearch over many sources) — has a single matching invocation, below the
`_MIN_MATCHING_INVOCATIONS = 3` gate, so it never emits.

## Rubric

Each matching invocation is one subagent run that made many tool calls at a high
average token cost. Classified as:

- **TP (true positive)** — the intermediate tool results were *truly unnecessary in
  context*: a mechanical orchestration chain (loop → fetch → filter/aggregate) where
  the model only needs the final computed output, and the large intermediate payloads
  pass through context as plumbing. This is the case Programmatic Tool Calling
  (`allowed_callers: ["code_execution_20250825"]`) actually fixes.
- **FP (false positive)** — the agent *genuinely needed each intermediate result in
  context for reasoning*: it Read code/diffs/issues/PRDs specifically to form a
  judgment about that content (design review, code review, exploration, spec scoping).
  The tool result *is* the input to the model's reasoning, so it cannot be offloaded
  to a code-execution sandbox.

**Classification basis (per AC):** subagent trace when available (185/195 have one);
agent type + task description as context clues otherwise (10/195, all trace-missing
from session `08b03d83`, cf. #468). Three rows were **deep-read** from their full
traces as evidence anchors (tool-name breakdown + final-output dependency check).

## Deep-read evidence anchors

| # | agent | trace | finding |
|---|---|---|---|
| 80 | `architect` | `a77a221…` (22 calls: 13 Read, 6 Grep, 2 get_issue, 1 add_comment) | Final verdict explicitly cites file contents it Read — *"`len(invocations)` is invocation count, not session count"*, *"`Axis.COST` is correct"*, *"new `agent_audit.py` module is correct"*. Every Read feeds the design judgment. **FP.** |
| 53 | `general-purpose` | `a86990d…` (33 calls: 12 Read, 12 Edit, 9 Bash) | Implement-and-test loop: Reads inform Edits, Bash runs tests whose output drives the next fix. No disposable plumbing; the whole loop is the work. **FP.** |
| 160 | `explore` (simplify review) | `a4c6ddb…` (21 calls: 7 Read, 14 Bash) | Reads + `git diff`/grep outputs all feed a simplification review; final text is the judgment over exactly that content. **FP.** |

The structural reason generalizes: **`tokens_per_tool_use = total_tokens / tool_uses`,
where `total_tokens` is the invocation's *entire* token consumption (large cached
context re-sent each turn + reasoning output + tool I/O), not the size of intermediate
tool *results* specifically.** For context-heavy reasoning agents the ratio is high
regardless of whether any result-plumbing occurs. The proxy therefore measures
"token-heavy invocation," which is not the same thing as "orchestration chain."

## Per-detection classifications (sample of 30)

All rows classified **FP**. `[*]` = deep-read anchor.

| # | corpus | agent | calls | tok/call | turns | task | class |
|---|---|---|---:|---:|---:|---|---|
| 9 | af | architect | 28 | 2241 | (no trace) | design review C-004 (read code+issues → comment) | FP |
| 14 | af | architect | 20 | 3889 | (no trace) | review Phase 3 PRD → feedback comment | FP |
| 80 | af | architect | 22 | 2159 | 9 | review #346 plan `[*]` | FP |
| 88 | af | architect | 21 | 2920 | 16 | review #285 plan | FP |
| 99 | af | architect | 18 | 2846 | 9 | review #191 glossary plan | FP |
| 103 | af | architect | 24 | 4283 | 15 | review #269 plan | FP |
| 109 | af | architect | 24 | 3245 | 12 | review #271 plan | FP |
| 136 | af | architect | 23 | 2985 | 14 | review #454 threshold-split design | FP |
| 155 | af | architect | 22 | 3269 | 13 | review #172 plan | FP |
| 177 | cf | architect | 19 | 2891 | 11 | review #242 scoring-prompt plan | FP |
| 73 | af | explore | 29 | 2733 | 30 | map config/warning/discovery code | FP |
| 138 | af | explore | 26 | 2011 | 18 | map parent-thread tool-use extraction | FP |
| 160 | af | explore | 21 | 2418 | 13 | simplification review of #465 diff `[*]` | FP |
| 180 | cf | explore | 16 | 5016 | 10 | explore #218 data readiness | FP |
| 28 | af | general-purpose | 13 | 5944 | 14 | code review angle E (wrapper correctness) | FP |
| 39 | af | general-purpose | 11 | 6210 | 12 | code review angle D (language pitfalls) | FP |
| 40 | af | general-purpose | 22 | 3917 | 23 | code review angle E | FP |
| 53 | af | general-purpose | 33 | 2552 | 34 | implement #298 window metadata `[*]` | FP |
| 82 | af | general-purpose | 22 | 2377 | 23 | quality review of PR #369 | FP |
| 108 | af | general-purpose | 14 | 4263 | 9 | reuse review of #270 diff | FP |
| 176 | cf | general-purpose | 22 | 2509 | 9 | research code-review agents | FP |
| 179 | cf | general-purpose | 17 | 2058 | 13 | code quality review round 2 | FP |
| 194 | cf | general-purpose | 10 | 5387 | 5 | code reuse review (E2E tests) | FP |
| 195 | cf | general-purpose | 12 | 3730 | 5 | efficiency review (E2E tests) | FP |
| 15 | af | pm | 17 | 5113 | (no trace) | scope Phase 3 stories under #439 | FP |
| 70 | af | pm | 26 | 4574 | 19 | scope C-006b Track B stories under #433 | FP |
| 98 | af | pm | 43 | 3640 | 25 | scope v0.6 release (read context → PRD+backlog) | FP |
| 121 | af | pm | 36 | 3597 | 21 | scope quality-axis PRD into epic | FP |
| 164 | af | pm | 38 | 2786 | 23 | PM brief: model-turn metric | FP |
| 189 | cf | pm | 41 | 3492 | 14 | scope+prioritize v1.3 release | FP |

## Tally

- **TP**: 0
- **FP**: 30

**Baseline precision: 0 / 30 = 0%** — far below the 70% target, and below the PRD's
60–70% estimate. The metadata-only proxy has **no discriminating power** on this corpus:
it fires uniformly on token-heavy reasoning subagents whose intermediate results are
genuinely consumed.

Caveat on generalization: 30/195 sampled, but the four emitting types are homogeneous
(all reasoning/review/scoping) and the FP mode is *structural* to how the ratio is
computed, so we expect ~0% across the full 195, not just the sample.

## Dominant FP pattern (single mode)

**Token-heavy reasoning subagents.** Every emitting agent type (architect, explore,
general-purpose code/reuse/quality review, pm scoping) Reads code / diffs / issues /
PRDs and reasons over that content. The high `tokens_per_tool_use` reflects large
context windows and substantial reasoning output, **not** large disposable
intermediate tool results. There is no second FP mode to separate out — it's one mode.

## Tuning simulation (one round, per AC)

Does any threshold band isolate a TP subpopulation?

| tool_calls | tokens/call | min_inv | detections | emitting types |
|---:|---:|---:|---:|---|
| **10** | **2000** | **3** *(current)* | 189 | architect, general-purpose, pm, explore |
| 15 | 2000 | 3 | 149 | architect, general-purpose, pm, explore |
| 20 | 2000 | 3 | 92 | architect, pm, general-purpose, explore |
| 10 | 3000 | 3 | 121 | general-purpose, architect, pm, explore |
| 10 | 4000 | 3 | 45 | architect, pm, general-purpose, explore |
| 10 | 5000 | 3 | 17 | architect, pm, general-purpose, explore |
| 20 | 4000 | 5 | 13 | pm, architect |
| 10 | 7000 | 3 | 0 | *(none)* |

**No band isolates a non-reasoning subpopulation** — because none exists in the corpus.
Tightening only shrinks the (entirely-FP) population; the sole band that eliminates the
FPs (`tokens/call > 7000`) eliminates *every* detection, i.e. it disables the signal
rather than tuning it. **Threshold tuning cannot rescue precision here.** The limitation
is the proxy itself, not its constants.

## Recommendation

The AC's tuning lever does not apply (no threshold band reaches 70% while retaining
detections). The honest dispositions, in order of preference:

1. **Do not ship `TOOL_ORCHESTRATION_CHAIN` as a rule-only INFO signal in v0.9.**
   At 0% dogfood precision it is pure noise, and noise undermines the trust theme the
   diagnostics output depends on. Gate its emission off (keep the code + tests) pending
   Tier B or LLM augmentation. **This changes v0.9 signal scope → needs Fred/PM sign-off
   and a `decisions.md` entry.**
2. If it must ship, emit only with an explicit low-confidence caveat in the message and
   keep `INFO` severity — but (1) is cleaner.

This is **strong evidence for D035** (LLM-call augmentation candidate #1): the rule-based
baseline FP rate is ~100%, far above the 30% threshold that the PRD set as the bar for
"LLM augmentation would pay off." A viable signal needs trace-level inspection (compare
summed tool-result token size vs. final-output size — Tier B) or an LLM relevance
classifier; the metadata proxy alone cannot distinguish orchestration from reasoning.

## Follow-ups to file (pending Fred's disposition call)

- **Signal disposition issue** — gate Tier A emission off for v0.9; track Tier B /
  D035 LLM-augmentation as the path to a shippable version. Capture the
  "`anthropic-research` is the only plausibly-TP type but sits below the min-invocation
  gate" observation.
- **D035 candidate entry** (PRD §9) — annotate with this calibration's ~100% rule-based
  FP rate as the measured baseline LLM augmentation would improve upon.

## Forward note: automated-classifier candidate

Per the standing stance from #402/#274/#321: when the automated-classification capability
lands, evaluate a **classical ML path** (TF-IDF over task description + agent type +
tool-mix features → LR/GBT) before reaching for an LLM judge — determinism and zero
per-run cost fit "calibrate every release." For *this* signal specifically, the cleaner
fix is upstream (Tier B result-size measurement), since the FP mode is a feature-quality
problem — the proxy lacks the input it would need — not a classification problem the same
features can solve.
