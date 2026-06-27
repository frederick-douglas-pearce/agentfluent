# Claude cost model — AgentFluent pricing notes

**The canonical cost model now lives in claude-code-sessions:**
**[`reference/cost-model.md`](https://github.com/frederick-douglas-pearce/claude-code-sessions/blob/main/reference/cost-model.md)**

That document is the source of truth for every lever that turns token counts into dollars — the cache-write TTL split (5m vs 1h), per-request multipliers (fast mode, batch, priority, data residency, long-context), server-side tool surcharges, per-model rate tables, stacking rules, and a worked example — each mapped to the session-JSONL field that carries it. Per the family convention (format and cost reference live in claude-code-sessions; AgentFluent and CodeFluent link rather than duplicate), AgentFluent does not re-document the model here.

This file keeps only what is **AgentFluent-specific**: how our pricing implementation covers, or does not yet cover, those levers.

## genai-prices coverage and local overlay

AgentFluent prices sessions on top of [pydantic/genai-prices](https://github.com/pydantic/genai-prices) (MIT). For Anthropic it models only `input_mtok`, `output_mtok`, `cache_read_mtok`, a single `cache_write_mtok` (5m-equivalent), context-length `tiers`, and dated `constraint`s. The levers it does **not** model — which AgentFluent must supply via a local pricing overlay, and/or request upstream — are our coverage ledger:

| Gap | Cost impact for users | Status |
|---|---|---|
| 1-hour cache write (2×) | **High** (commonly the dominant TTL) | Local overlay — #534 |
| Fast mode premium rates | High *if used* | Local overlay |
| Batch (0.5×) / Priority tier | Medium | Local overlay |
| Data residency US (1.1×) | Low–Medium | Local overlay |
| Web search ($10 / 1k) | Medium *if used* | Local overlay (counts present in JSONL) |
| Code execution ($/hr) | Partial (duration not in single-session JSONL) | Document limitation; surface count |

The rates, multipliers, and field mappings behind this table are in the canonical doc; this is only AgentFluent's coverage status against it.

---

_Catalog relocation: the full lever catalog originally drafted here (#535) now lives in claude-code-sessions `reference/cost-model.md`. The 1-hour cache-write overlay is tracked in #534._
