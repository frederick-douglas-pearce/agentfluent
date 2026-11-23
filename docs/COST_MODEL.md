# Claude cost model — AgentFluent pricing notes

**The canonical cost model now lives in claude-code-sessions:**
**[`reference/cost-model.md`](https://github.com/frederick-douglas-pearce/claude-code-sessions/blob/main/reference/cost-model.md)**

That document is the source of truth for every lever that turns token counts into dollars — the cache-write TTL split (5m vs 1h), per-request multipliers (fast mode, batch, priority, data residency, long-context), server-side tool surcharges, per-model rate tables, stacking rules, and a worked example — each mapped to the session-JSONL field that carries it. Per the family convention (format and cost reference live in claude-code-sessions; AgentFluent and CodeFluent link rather than duplicate), AgentFluent does not re-document the model here.

This file keeps only what is **AgentFluent-specific**: how our pricing implementation covers, or does not yet cover, those levers.

## genai-prices coverage and local overlay

AgentFluent prices sessions on top of [pydantic/genai-prices](https://github.com/pydantic/genai-prices) (MIT). For Anthropic at the version pinned in `pyproject.toml` it models `input_mtok`, `output_mtok`, `cache_read_mtok`, `cache_write_mtok` (5m-equivalent), `cache_write_1h_mtok`, `web_searches_kcount`, context-length `tiers`, `price_variants` (batch and fast-mode rates, new at `0.2.3` — see #686), and dated `constraint`s. Two of those — `cache_write_1h_mtok` and `web_searches_kcount` — arrived with the `0.1.4` bump (#661); the others predate it. Their rows below therefore record **upstream** status rather than a retirement, and the two are at different stages locally: the 1h cache write has a landed local overlay (#534) that stays until #662 retires it onto the upstream key, while web search has **no local overlay yet** — the adapter does not read `web_searches_kcount`, so web-search cost is simply unpriced until #539. The levers it does **not** model — which AgentFluent must supply via a local pricing overlay, and/or request upstream — are our coverage ledger. Each lever plugs into the [base ⊕ overlay merge seam](#the-base--overlay-merge-seam-547) at one of three named plug points (its **Class** below):

| Gap | Class | Seam plug point | Cost impact for users | Local status | Upstream (genai-prices) |
|---|---|---|---|---|---|
| 1-hour cache write (2×) | A (rate-level) | `ModelPricing.__post_init__` (derived 2× input) | **High** (commonly the dominant TTL) | Overlay — landed (#534): parser splits `usage.cache_creation` into 5m/1h, priced separately | **Landed upstream** as `cache_write_1h_mtok` (populated on 25 of 27 Anthropic models at the current `0.2.3` pin; it arrived at `0.1.4`) — [pydantic/genai-prices#295](https://github.com/pydantic/genai-prices/issues/295). The local overlay **stays in place** until #662 retires it onto the upstream key (verified price-neutral: upstream is exactly 2× `input_mtok` on every model that carries the key). |
| Fast mode premium rates | A (rate-level) | `ModelPricing` rate table (#536) | High *if used* | Overlay — future (#536, blocked by #547) | **Landed upstream** at `0.2.3` as a `price_variants` entry (`when={'speed': 'fast'}`), on 5 Anthropic models, 4 of them curated — e.g. opus-5-5 fast $8/$40 vs base $4/$20. Reached via `get_prices(when, price_context=…)`, a **different seam** from the Class-A plug point above, so #536 needs re-scoping rather than a straight swap (#686). Not read today: the adapter passes no `price_context`. |
| Batch (0.5×) / Priority tier | B (multiplier) | `compute_cost(request_multipliers=…)` (#537) | Medium | Overlay — future (#537, blocked by #547) | **Landed upstream** at `0.2.3` as a `price_variants` entry (`when={'processing': 'batch'}`), on 16 of 27 Anthropic models at exactly 0.5× base, carrying a dated `StartDateConstraint(2026-10-14)`. Same seam caveat as fast mode — `price_context`, not the Class-B multiplier — so #537 needs re-scoping (#686). Priority tier is still unmodeled upstream. Not read today. |
| Data residency US (1.1×) | B (multiplier) | `compute_cost(request_multipliers=…)` (#538) | Low–Medium | Overlay — future (#538, blocked by #547) | [pydantic/genai-prices#429](https://github.com/pydantic/genai-prices/issues/429) (filed) |
| Web search ($10 / 1k) | C (surcharge) | `compute_cost(surcharge_usd=…)` (#539) | Medium *if used* | Overlay — future (#539, blocked by #547; counts present in JSONL) | **Landed upstream** as `web_searches_kcount` (populated on 22 of 27 Anthropic models at the current `0.2.3` pin; it arrived at `0.1.4`). Note it did **not** arrive via [pydantic/genai-prices#288](https://github.com/pydantic/genai-prices/pull/288), which was closed unmerged on 2026-08-27 and proposed a differently-named key; the shipped key reached the dataset by another route. There is **no local overlay to retire** here — `compute_cost(surcharge_usd=…)` has no production caller, so web-search cost is unpriced today. #539 is unchanged as of this writing and still scoped to building the overlay; re-scoping it onto the upstream key is that issue's call, not this row's. |
| Code execution ($/hr) | C (surcharge) | `compute_cost(surcharge_usd=…)` (future) | Partial (duration not in single-session JSONL) | Document limitation; surface count | not modeled |
| Context-length tiers | (base, per-request rate choice) | upstream `tiers` → `ModelPricing.context_tiers`, selected per request by `ModelPricing.for_prompt` | Medium *if a request exceeds the model's tier threshold* | **Modeled** (#682, closes #639) — see [Context-length tiers](#context-length-tiers-priced-per-request-682) below. The threshold is **per model**: `claude-haiku-5-5` is 5× above 100K at every date; sonnet-4-5 has a >200K tier at every date (upstream notes >200K requests now error, so only older ones reach it); opus-4-6 / sonnet-4-6 have a >200K tier for sessions dated before 2026-03-13. | modeled (`tiers`) |

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

From v0.12.0, `analyze --format json` stamps each snapshot with its cost model: the genai-prices version and a cost-method version that AgentFluent raises when it changes how cost is computed (#543; see [`cost_model`](GLOSSARY.md#cost_model)). `diff` compares the two stamps. When they differ, or a snapshot has no readable stamp (it predates v0.12.0, or the stamp is malformed), it prints a caveat above the cost tables, and `diff --quiet` marks its cost figure. The fix is the same: re-capture the baseline with the current release.

`diff` also reads each snapshot's [`cost_coverage`](GLOSSARY.md#cost_coverage) block (#713; see [`cost_coverage_status`](GLOSSARY.md#cost_coverage_status)). When either side has tokens with no price, agent invocations whose spend is unknown or unpriced, or trace files it could not parse — or has no readable coverage block at all, as every snapshot from before v0.12.0 does — the same caveat names that side and its gaps, and `diff --quiet` notes `cost coverage incomplete` or `cost coverage unknown`. A model id that went unpriced on one side otherwise reads as a cost drop. Neither caveat changes the exit code.

## Context-length tiers priced per request (#682)

Some models charge more for long prompts. From **v0.12.0**, AgentFluent prices each API request (one assistant message, or one subagent turn) on its own prompt-length tier, using the rule genai-prices applies:

- **Prompt size** = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` of that one request.
- A request is priced at a tier when its prompt size is **strictly greater than** the tier's threshold (100,000 → base; 100,001 → tier).
- **Every token of that request** — output and cache reads included — is priced at the tier rate.
- A tier is never selected from a sum: two 60K-prompt requests price at the base rate even though together they exceed 100K.

Two consequences for reading the numbers:

- A `by_model` row's cost is the sum of its requests' costs, so it **cannot be recomputed as row tokens × one rate** when any of its requests crossed a threshold.
- **Historical cost moves up** (D067): sessions with long requests on the models above now report what they were billed. `COST_METHOD_VERSION` is `2`, so `agentfluent diff` attributes the change to the cost method rather than to agent behavior.

**Known limitation — tiers are chosen at the session's date (#738).** Rates, base and tier, come from the session's first-message date, so every request in a session tiers against that date's table. A session that starts before 2026-03-13 and runs past it reports its later opus-4-6 / sonnet-4-6 requests over 200K at the tier rate, although they were billed flat. No session in the measured corpus does this (2026-11-22, 2,370 sessions); per-request dating is tracked in #738.

The model-switch savings and offload estimates price the same way, at both models. The offload estimate's delegated side tiers each request on the **original** request's full prompt size, not on its re-priced fresh input. Delegating doesn't shrink the context a request carries, and this reading gives the smaller claimed saving. Those estimates price at the latest rate rather than the session's date (#735), so a pre-2026-03-13 opus-4-6 request over 200K is tiered in `total_cost` but not in the savings figures.

## Date-aware base rates (session priced at the rate in effect when it ran)

From **v0.11**, AgentFluent resolves each session's base rate at the session's own date — the first-message timestamp is threaded through the cost path into genai-prices' dated `constraint`s (`get_pricing(model, timestamp=...)`, #546). The intent is that a historical session reports the cost it actually incurred, not today's rate.

**Base rates are date-invariant, by measurement.** As of the pinned `genai-prices` slice, **no model AgentFluent prices carries a dated _base-rate_ change**, so the base `(input, output, cache_write_5m, cache_read)` rates are identical at every date. The dated Anthropic constraints move two other things. The first is the >200K context tier of opus-4-6 / sonnet-4-6, which AgentFluent now prices (see [Context-length tiers](#context-length-tiers-priced-per-request-682)), so long requests in sessions dated before 2026-03-13 are restated upward. The second is the `price_variants` batch entries (#686), which the adapter does not read.

The wiring is a forward-compat guarantee: the day Anthropic ships a genuine dated base-rate change and it lands in the pinned dataset, historical sessions auto-price correctly with no code change. Two guard tests in `tests/unit/test_pricing.py` (`TestForwardCompatGuard`) **fail loudly** when that changes. One asserts that base rates don't move by date for every curated model. The other asserts that the tier table moves by date only for opus-4-6 and sonnet-4-6. Either failure is the signal to update the historical-cost note here.

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
