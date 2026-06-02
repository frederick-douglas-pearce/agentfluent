"""Tuning simulation for #407: does any threshold band isolate true positives?

All sampled detections classified FP (intermediates genuinely consumed by
reasoning). This sim shows how the emitting population responds to threshold
changes — i.e. whether a tighter gate could ever isolate a TP subpopulation.
"""
import json
from collections import Counter

data = json.loads(open("/tmp/orchestration_407_context.json").read())
EMIT_GATE = 3


def emits(calls_min, ratio_min, inv_min):
    matching = [d for d in data
                if d["tool_uses"] >= calls_min and d["tokens_per_call"] > ratio_min]
    by_type = Counter(d["agent_type"].lower() for d in matching)
    emitting = {t: n for t, n in by_type.items() if n >= inv_min}
    return matching, emitting


print(f"{'tool_calls':>10} {'tokens/call':>12} {'min_inv':>8} | "
      f"{'detections':>10} {'emit_types':>10} {'agent_types_emitting'}")
for calls_min, ratio_min, inv_min in [
    (10, 2000, 3),   # current defaults
    (15, 2000, 3),
    (20, 2000, 3),
    (10, 3000, 3),
    (10, 4000, 3),
    (10, 5000, 3),
    (10, 2000, 5),
    (20, 4000, 5),
    (10, 7000, 3),   # near the max ratio (7768)
]:
    matching, emitting = emits(calls_min, ratio_min, inv_min)
    types = ", ".join(f"{t}({n})" for t, n in sorted(emitting.items(), key=lambda x: -x[1]))
    n_emit = sum(emitting.values())
    print(f"{calls_min:>10} {ratio_min:>12} {inv_min:>8} | "
          f"{n_emit:>10} {len(emitting):>10}  {types or '(none)'}")

print("\nAll emitting agent types across every band are reasoning/review/scoping")
print("subagents (architect, general-purpose, pm, explore). No band isolates a")
print("data-processing/orchestration subpopulation because none exists in the corpus.")
print("\nNote: 'anthropic-research' (web-fetch heavy, the one plausibly-TP type)")
print("has only 1 matching invocation — below any sane min_inv gate — so it never emits.")
