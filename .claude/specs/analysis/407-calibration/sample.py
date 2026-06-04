"""Stratified seeded sample of orchestration detections for #407 classification."""
import json
import random
from collections import defaultdict

data = json.loads(open("/tmp/orchestration_407_context.json").read())

# Restrict to the agent types that actually emit a signal (>=3 matching).
EMIT = {"architect", "general-purpose", "pm", "explore"}
pool = [d for d in data if d["agent_type"].lower() in EMIT]

by_type = defaultdict(list)
for d in pool:
    by_type[d["agent_type"].lower()].append(d)

# Proportional stratified sample, ~30 total, seeded for reproducibility.
random.seed(407)
TARGET = 30
total = len(pool)
sample = []
for t, items in by_type.items():
    k = max(3, round(TARGET * len(items) / total))
    k = min(k, len(items))
    sample.extend(random.sample(items, k))

sample.sort(key=lambda d: (d["agent_type"].lower(), d["idx"]))
print(f"pool={total} sample={len(sample)}")
for t in sorted(by_type):
    print(f"  {t}: pool={len(by_type[t])} sampled={sum(1 for s in sample if s['agent_type'].lower()==t)}")

for d in sample:
    tools = d.get("trace_tools")
    tools_str = ", ".join(f"{k}:{v}" for k, v in sorted((tools or {}).items(), key=lambda x:-x[1]))
    print("\n" + "=" * 78)
    print(f"[{d['idx']}] {d['agent_type']}  {d['corpus']}/{d['session']}  "
          f"calls={d['tool_uses']} tok={d['total_tokens']} ratio={d['tokens_per_call']:.0f} "
          f"trace={d['has_trace']} turns={d['model_turns']}")
    print(f"  TOOLS: {tools_str or '(no trace)'}")
    print(f"  DESC: {d['description']}")
    print(f"  PROMPT: {d['prompt_head'][:300]}")
