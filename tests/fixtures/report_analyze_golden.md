# AgentFluent Report

## Summary

- **Project:** golden demo
- **Sessions analyzed:** 2
- **Window:** 2026-05-01T00:00:00Z → 2026-05-08T00:00:00Z (2 of 5 sessions)
- **Total cost (API rate):** $1.25
- **Total tokens:** 2,550 (input 1,200, output 450, cache creation 300, cache read 600)
- **AgentFluent version:** 0.7.0

## Token Metrics

| Model | Origin | Input | Output | Cache | Cost |
| :--- | :--- | ---: | ---: | ---: | ---: |
| claude-sonnet-4-6 | parent | 800 | 300 | 600 | $0.90 |
| claude-sonnet-4-6 | subagent | 400 | 150 | 300 | $0.35 |
| **Total** |  | **1,200** | **450** | **900** | **$1.25** |

## Agent Metrics

| Agent Type | Count | Tokens | Avg Tokens/Call | Duration |
| :--- | ---: | ---: | ---: | ---: |
| reviewer | 2 | 4,000 | 2,000 | 25.0s |
| **Total** | **2** |  |  |  |

Agent token share of session total: **35.5%**

## Diagnostics

**Top 1 priority fixes:**

1. **warning** · agent: `reviewer` · 2× · target: `allowed_tools` · axis: \[quality]


### Warning (1)

- \[quality] (target: `allowed_tools`, agent: `reviewer`, count: 2×) — Allow the reviewer to read changed files before commenting.

## Offload Candidates

| Name | Confidence | Cluster size | Tools | Est. savings |
| :--- | :--- | ---: | :--- | ---: |
| review-sweeps | high | 3 | Read, Grep | $0.75 |

## Reproduction

```bash
agentfluent analyze --project "golden demo" --since 2026-05-01T00:00:00Z --until 2026-05-08T00:00:00Z --json
```

*Generated: 2026-05-15T12:30:00Z*
