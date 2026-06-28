# Claude cost model — AgentFluent pricing notes

**The canonical cost model now lives in claude-code-sessions:**
**[`reference/cost-model.md`](https://github.com/frederick-douglas-pearce/claude-code-sessions/blob/main/reference/cost-model.md)**

That document is the source of truth for every lever that turns token counts into dollars — the cache-write TTL split (5m vs 1h), per-request multipliers (fast mode, batch, priority, data residency, long-context), server-side tool surcharges, per-model rate tables, stacking rules, and a worked example — each mapped to the session-JSONL field that carries it. Per the family convention (format and cost reference live in claude-code-sessions; AgentFluent and CodeFluent link rather than duplicate), AgentFluent does not re-document the model here.

This file keeps only what is **AgentFluent-specific**: how our pricing implementation covers, or does not yet cover, those levers.

## genai-prices coverage and local overlay

AgentFluent prices sessions on top of [pydantic/genai-prices](https://github.com/pydantic/genai-prices) (MIT). For Anthropic it models only `input_mtok`, `output_mtok`, `cache_read_mtok`, a single `cache_write_mtok` (5m-equivalent), context-length `tiers`, and dated `constraint`s. The levers it does **not** model — which AgentFluent must supply via a local pricing overlay, and/or request upstream — are our coverage ledger:

| Gap | Cost impact for users | Local status | Upstream (genai-prices) |
|---|---|---|---|
| 1-hour cache write (2×) | **High** (commonly the dominant TTL) | Overlay — landed (#534): parser splits `usage.cache_creation` into 5m/1h, priced separately | [pydantic/genai-prices#295](https://github.com/pydantic/genai-prices/issues/295) (shape proposed, PR offered) |
| Fast mode premium rates | High *if used* | Overlay | [pydantic/genai-prices#429](https://github.com/pydantic/genai-prices/issues/429) (filed) |
| Batch (0.5×) / Priority tier | Medium | Overlay | [pydantic/genai-prices#429](https://github.com/pydantic/genai-prices/issues/429) (filed) |
| Data residency US (1.1×) | Low–Medium | Overlay | [pydantic/genai-prices#429](https://github.com/pydantic/genai-prices/issues/429) (filed) |
| Web search ($10 / 1k) | Medium *if used* | Overlay (counts present in JSONL) | [pydantic/genai-prices#288](https://github.com/pydantic/genai-prices/pull/288) (PR in flight — retire overlay once merged + pin bumped) |
| Code execution ($/hr) | Partial (duration not in single-session JSONL) | Document limitation; surface count | not modeled |

The rates, multipliers, and field mappings behind this table are in the canonical doc; this is only AgentFluent's coverage status against it. The **Upstream** column tracks getting each lever modeled in the shared dataset — as those land and we bump the pinned genai-prices slice, the matching local overlay can be retired.

## Reading cost diffs across the v0.10 upgrade

The 1-hour cache-write overlay (#534) lands in **v0.10**. Before it, AgentFluent billed *every* cache-write token at the 5-minute rate (1.25× base input); from v0.10 on, the portion the session JSONL marks as 1-hour writes is billed at 2× base. For Claude Code sessions 1h is commonly the dominant TTL, so **the reported dollar cost of an unchanged session rises after upgrading** — this is a correction, not a regression.

This matters for `agentfluent diff`: a baseline snapshot captured **before** v0.10 stores costs computed under the old all-5m rate. Diffing it against a snapshot taken **after** upgrading will show a positive cost delta on cache-heavy sessions that comes purely from the pricing fix, not from any change in agent behavior. To compare like-for-like, **re-capture your baseline after upgrading to v0.10** so both sides price 1h writes the same way. Token counts (which the diff also reports) are unaffected.

A follow-up will stamp the pricing-model version into snapshots so the diff can flag a cross-version comparison automatically (tracked in #543).

---

_Catalog relocation: the full lever catalog originally drafted here (#535) now lives in claude-code-sessions `reference/cost-model.md`. The 1-hour cache-write overlay is tracked in #534._
