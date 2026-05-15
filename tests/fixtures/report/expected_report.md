# AgentFluent Report

## Summary

- **Project:** demo-agent-shop
- **Sessions analyzed:** 5
- **Window:** 2026-05-01T00:00:00+00:00 → 2026-05-08T00:00:00+00:00 (5 of 12 sessions)
- **Total cost (API rate):** $7.84
- **Total tokens:** 463,000 (input 24,500, output 8,200, cache creation 18,300, cache read 412,000)
- **AgentFluent version:** 0.7.0

## Token Metrics

| Model | Origin | Input | Output | Cache | Cost |
| :--- | :--- | ---: | ---: | ---: | ---: |
| claude-haiku-4-5 | subagent | 4,000 | 1,000 | 41,700 | $0.82 |
| claude-opus-4-7 | parent | 14,200 | 5,100 | 292,500 | $5.40 |
| claude-opus-4-7 | subagent | 6,300 | 2,100 | 96,100 | $1.62 |
| **Total** |  | **24,500** | **8,200** | **430,300** | **$7.84** |

## Agent Metrics

| Agent Type | Count | Tokens | Avg Tokens/Call | Duration |
| :--- | ---: | ---: | ---: | ---: |
| Explore (builtin) | 8 | 42,000 | 5,250 | 320.0s |
| pm | 6 | 78,000 | 13,000 | 510.0s |
| tester | 3 | 24,000 | 8,000 | 145.0s |
| **Total** | **17** |  |  |  |

Agent token share of session total: **38.5%**

## Diagnostics

**Top 3 priority fixes:**

1. **critical** · agent: `pm` · 4× · target: `model` · axis: \[cost]
2. **warning** · agent: `tester` · 3× · target: `tools` · axis: \[speed]
3. **info** · agent: `(global)` · 1× · target: `mcp_servers` · axis: \[cost]


### Critical (1)

- \[cost] (target: `model`, agent: `pm`, count: 4×) — Agent 'pm' runs Opus on tasks Sonnet handles cleanly; swap the model field to claude-sonnet-4-6.

### Warning (1)

- \[speed] (target: `tools`, agent: `tester`, count: 3×) — Tester retries Bash 4-5 times before giving up; tighten the description so it routes failing builds back to the parent.

### Info (1)

- \[cost] (target: `mcp_servers`, agent: `(global)`, count: 1×) — MCP server 'github' is configured but no agent invoked it during the analyzed window.

## Offload Candidates

| Name | Confidence | Cluster size | Tools | Est. savings |
| :--- | :--- | ---: | :--- | ---: |
| ts-bulk-edits | high | 14 | Read, Edit, Grep | $2.85 |

## Reproduction

```bash
agentfluent analyze --project demo-agent-shop --since 2026-05-01T00:00:00+00:00 --until 2026-05-08T00:00:00+00:00 --json
```

*Generated: 2026-05-15T14:30:00Z*
