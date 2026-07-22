# Claude cost model — AgentFluent pricing notes

**The canonical cost model now lives in claude-code-sessions:**
**[`reference/cost-model.md`](https://github.com/frederick-douglas-pearce/claude-code-sessions/blob/main/reference/cost-model.md)**

That document is the source of truth for every lever that turns token counts into dollars — the cache-write TTL split (5m vs 1h), per-request multipliers (fast mode, batch, priority, data residency, long-context), server-side tool surcharges, per-model rate tables, stacking rules, and a worked example — each mapped to the session-JSONL field that carries it. Per the family convention (format and cost reference live in claude-code-sessions; AgentFluent and CodeFluent link rather than duplicate), AgentFluent does not re-document the model here.

This file keeps only what is **AgentFluent-specific**: how our pricing implementation covers, or does not yet cover, those levers.

## genai-prices coverage and local overlay

AgentFluent prices sessions on top of [pydantic/genai-prices](https://github.com/pydantic/genai-prices) (MIT). For Anthropic it models only `input_mtok`, `output_mtok`, `cache_read_mtok`, a single `cache_write_mtok` (5m-equivalent), context-length `tiers`, and dated `constraint`s. The levers it does **not** model — which AgentFluent must supply via a local pricing overlay, and/or request upstream — are our coverage ledger. Each lever plugs into the [base ⊕ overlay merge seam](#the-base--overlay-merge-seam-547) at one of three named plug points (its **Class** below):

| Gap | Class | Seam plug point | Cost impact for users | Local status | Upstream (genai-prices) |
|---|---|---|---|---|---|
| 1-hour cache write (2×) | A (rate-level) | `ModelPricing.__post_init__` (derived 2× input) | **High** (commonly the dominant TTL) | Overlay — landed (#534): parser splits `usage.cache_creation` into 5m/1h, priced separately | [pydantic/genai-prices#295](https://github.com/pydantic/genai-prices/issues/295) (shape proposed, PR offered) |
| Fast mode premium rates | A (rate-level) | `ModelPricing` rate table (#536) | High *if used* | Overlay — future (#536, blocked by #547) | [pydantic/genai-prices#429](https://github.com/pydantic/genai-prices/issues/429) (filed) |
| Batch (0.5×) / Priority tier | B (multiplier) | `compute_cost(request_multipliers=…)` (#537) | Medium | Overlay — future (#537, blocked by #547) | [pydantic/genai-prices#429](https://github.com/pydantic/genai-prices/issues/429) (filed) |
| Data residency US (1.1×) | B (multiplier) | `compute_cost(request_multipliers=…)` (#538) | Low–Medium | Overlay — future (#538, blocked by #547) | [pydantic/genai-prices#429](https://github.com/pydantic/genai-prices/issues/429) (filed) |
| Web search ($10 / 1k) | C (surcharge) | `compute_cost(surcharge_usd=…)` (#539) | Medium *if used* | Overlay — future (#539, blocked by #547; counts present in JSONL) | [pydantic/genai-prices#288](https://github.com/pydantic/genai-prices/pull/288) (PR in flight — retire overlay once merged + pin bumped) |
| Code execution ($/hr) | C (surcharge) | `compute_cost(surcharge_usd=…)` (future) | Partial (duration not in single-session JSONL) | Document limitation; surface count | not modeled |
| >200K context tier | (base, not overlay) | upstream `tiers` — adapter discards them | Medium *if a session exceeds 200K context* | **Gap** — the adapter takes `TieredPrices.base` and discards `tiers`, so >200K-context sessions are priced at the base tier (tracked in #639) | modeled (`tiers`) |

The rates, multipliers, and field mappings behind this table are in the canonical doc; this is only AgentFluent's coverage status against it. The **Upstream** column tracks getting each lever modeled in the shared dataset — as those land and we bump the pinned genai-prices slice, the matching local overlay can be retired at its single plug point (drop the class-A transform, or the class-B/C argument).

### The base ⊕ overlay merge seam (#547)

Every session's final per-request cost is `base (upstream) ⊕ overlay (local)`. genai-prices supplies only the **base** rate table (via the isolated `analytics/_genai_source.py` adapter — the only genai-prices contact point); the overlay levers above are the pieces it does not model. They are **not uniform** — they compose in three classes, in one fixed documented order:

```
final = ( (base ⊕ rate_overlays[A]) · tokens ) · Π(multipliers[B]) + Σ(surcharges[C])
```

| Class | What it does | Scope | Single plug point | Members |
|---|---|---|---|---|
| **A — rate-level** | transforms the per-token rate table *before* token×rate | per model + date | `ModelPricing` construction in `get_pricing` (e.g. `__post_init__` derives the 1h rate) | 1h cache write (#534, landed); fast mode (#536) |
| **B — per-request multiplier** | scales the token-derived dollar subtotal (multipliers commute) | per invocation | `compute_cost(request_multipliers=…)` | batch/priority (#537); data residency (#538) |
| **C — additive surcharge** | adds fixed dollars *after* the multipliers (a flat fee is never discounted by a batch 0.5×) | per invocation | `compute_cost(surcharge_usd=…)` | server-tool / web search (#539) |

`compute_cost` is the merge point (`analytics/pricing.py`); its class-B/C parameters default to no-op (empty multiplier product = `1.0`, zero surcharge), so a base-only session prices bit-identically to the pre-seam flat sum. "Is this lever upstream or overlay?" is a one-line answer per lever (the **Class**/**Plug point** columns above), and retiring an overlay when genai-prices models it upstream is a localized change at that single plug point. The ordering is load-bearing: class C is added strictly after class B.

## Reading cost diffs across the v0.10 upgrade

The 1-hour cache-write overlay (#534) lands in **v0.10**. Before it, AgentFluent billed *every* cache-write token at the 5-minute rate (1.25× base input); from v0.10 on, the portion the session JSONL marks as 1-hour writes is billed at 2× base. For Claude Code sessions 1h is commonly the dominant TTL, so **the reported dollar cost of an unchanged session rises after upgrading** — this is a correction, not a regression.

This matters for `agentfluent diff`: a baseline snapshot captured **before** v0.10 stores costs computed under the old all-5m rate. Diffing it against a snapshot taken **after** upgrading will show a positive cost delta on cache-heavy sessions that comes purely from the pricing fix, not from any change in agent behavior. To compare like-for-like, **re-capture your baseline after upgrading to v0.10** so both sides price 1h writes the same way. Token counts (which the diff also reports) are unaffected.

A follow-up will stamp the pricing-model version into snapshots so the diff can flag a cross-version comparison automatically (tracked in #543).

## Date-aware base rates (session priced at the rate in effect when it ran)

From **v0.11**, AgentFluent resolves each session's base rate at the session's own date — the first-message timestamp is threaded through the cost path into genai-prices' dated `constraint`s (`get_pricing(model, timestamp=...)`, #546). The intent is that a historical session reports the cost it actually incurred, not today's rate.

**This is presently date-invariant, by measurement.** As of the pinned `genai-prices` slice, **no model AgentFluent prices carries a dated _base-rate_ change** — the only dated Anthropic constraints move the >200K context tier (the gap above, #639), which the adapter discards, so the base `(input, output, cache_write_5m, cache_read)` rates are identical at every date. Analyzing an old session therefore yields the same dollar cost it would at "latest"; **no historical cost is restated today**, and no `agentfluent diff` cross-date delta arises from rate changes yet.

The wiring is a forward-compat guarantee: the day Anthropic ships a genuine dated base-rate change and it lands in the pinned dataset, historical sessions auto-price correctly with no code change. A guard test (`tests/`) asserts the current date-invariance per model and **fails loudly** if that ever stops holding — that failure is the signal to write the user-facing "historical costs now differ" note here and to activate #543's cross-date-delta caveat for `diff`.

## Rates, not quantities: the boundary of v0.11's cost work (D056)

v0.11 rebuilds the **rate** half of cost — the price per token: the genai-prices base ⊕ overlay seam (#547) and date-aware base-rate lookup (#546) above. It does **not** correct the **quantity** half, and the two must not be conflated.

Cost is `rate × quantity`. The rate side is now sourced from a maintained dataset; the quantity side — *how many tokens each agent actually burned* — has a known defect that v0.11 deliberately does not fix. `toolUseResult.totalTokens` is **not** cumulative token spend: it equals the agent's **final assistant turn** (`input + output + cache_creation + cache_read`), a context-size proxy that re-counts the cached prefix each turn. Settling that semantics is exactly what shipped in v0.11 (#595 PR A, **D056**); the correction that consumes it does not.

Concretely, for the current release:

- **Session-level `total_cost` and `total_tokens` are sound** for the tokens the pipeline actually sums (per-turn `usage`), now priced at corrected rates.
- **Per-agent token attribution and per-agent cost are not** claimed accurate. The aggregates that multiply a `totalTokens`-derived quantity by a rate (per-agent-type spend, `agent_token_percentage`) sum a final-turn proxy, not spend — they **understate** true per-agent burn, silently. See [`TOKEN_ACCOUNTING.md`](TOKEN_ACCOUNTING.md) for the field-by-field semantics.
- **Nested spend can be dropped entirely.** Orphan and depth-≥2 subagent traces that don't link to a discovered invocation are not folded into any aggregate (#648).

Both quantity corrections — per-agent attribution (#646) and orphan/depth-≥2 trace coverage (#648), enabled by the multi-level trace linker (#595 PR B) — are scoped to **v0.12**. Until they land, read a per-agent cost figure as a lower bound, not a settled number.

---

_Catalog relocation: the full lever catalog originally drafted here (#535) now lives in claude-code-sessions `reference/cost-model.md`. The 1-hour cache-write overlay is tracked in #534._
