# Claude Cost Model — Complete Lever Catalog

**Status:** Reference (living document)
**Pricing/format verified against:** Anthropic [pricing page](https://platform.claude.com/docs/en/about-claude/pricing) and local session JSONL on **2026-06-26**
**Scope:** First-party Claude API usage as recorded in Claude Code / Claude Agent SDK session JSONL (`~/.claude/projects/**/*.jsonl`). Cloud-platform (Bedrock/Vertex/AWS-CCU) and Managed-Agents billing are catalogued as out-of-scope (Section E).

> This document is a shared reference across the Claude-session tooling family
> (AgentFluent, CodeFluent, claude-code-sessions). It enumerates **every input
> that affects the dollar cost of a Claude request**, where (if anywhere) that
> input is observable in the session JSONL, and whether
> [pydantic/genai-prices](https://github.com/pydantic/genai-prices) — the chosen
> upstream pricing dataset — currently models it.
>
> **The format evolves.** Claude Code may add, rename, or restructure `usage`
> fields. Treat every field path below as "verified on the date above" and
> re-confirm against a current session before relying on it. `usage` parsing
> should ignore unknown fields, not assume presence.

---

## 0. Where cost data lives in the JSONL

Cost-relevant data is carried on **`type: "assistant"`** records. The shape
(fields relevant to cost only):

```jsonc
{
  "type": "assistant",
  "timestamp": "2026-06-26T...Z",          // used for date-aware pricing (effective rates)
  "message": {
    "model": "claude-opus-4-7",            // (B) selects the rate table
    "usage": {
      "input_tokens": 2741,                // (A) base input
      "output_tokens": 329,                // (A) output (incl. thinking tokens)
      "cache_read_input_tokens": 0,        // (A) cache hit, 0.1x
      "cache_creation_input_tokens": 19117,// (A) SUM of the two TTLs below
      "cache_creation": {                  // (A) the TTL split — REQUIRED to price cache writes correctly
        "ephemeral_5m_input_tokens": 0,    //     5-minute write, 1.25x
        "ephemeral_1h_input_tokens": 19117 //     1-hour write, 2x
      },
      "output_tokens_details": {           // (F) thinking-token breakdown (subset of output_tokens)
        "thinking_tokens": 0
      },
      "server_tool_use": {                 // (D) non-token tool surcharges (counts only)
        "web_search_requests": 0,          //     $10 / 1,000
        "web_fetch_requests": 0            //     free
        // "code_execution_requests": N    //     appears only when used; billed by container-hour (NOT here)
      },
      "service_tier": "standard",          // (C) "standard" | "priority" | "batch"
      "speed": "standard",                 // (C) "standard" | "fast"  (fast = fast-mode premium)
      "inference_geo": "not_available"     // (C) "global"(default) | "us"(1.1x) | "not_available" | ""
    }
  }
}
```

`message.usage.iterations[]` repeats the same per-message fields for multi-turn
internal iterations; the top-level `usage` is the billable rollup.

---

## A. Token-rate dimensions (per-token)

These are priced as `tokens × rate ÷ 1e6`. Rate depends on the model (Section B).

| Lever | Multiplier vs base input | JSONL location | genai-prices field |
|---|---|---|---|
| Base input | 1× | `usage.input_tokens` | ✅ `input_mtok` |
| Output | — (own rate) | `usage.output_tokens` | ✅ `output_mtok` |
| Cache read (hit) | 0.10× | `usage.cache_read_input_tokens` | ✅ `cache_read_mtok` |
| Cache write — **5 minute** TTL | 1.25× | `usage.cache_creation.ephemeral_5m_input_tokens` | ⚠️ single `cache_write_mtok` (= 5m value) |
| Cache write — **1 hour** TTL | 2× | `usage.cache_creation.ephemeral_1h_input_tokens` | ❌ **not modeled** |

Notes:
- `cache_creation_input_tokens` = `ephemeral_5m_input_tokens` + `ephemeral_1h_input_tokens`. Pricing the sum at a single rate **under-reports cost whenever 1h writes are present** (in Claude Code corpora, 1h is the *dominant* TTL — ~72% of messages). This is the only live mispricing found in the AgentFluent audit (tracked: agentfluent#534).
- Fallback when `usage.cache_creation` sub-object is absent (older sessions): treat the full `cache_creation_input_tokens` as 5m.
- Output tokens include extended-thinking tokens; see Section F.

### Base rates (USD per 1M tokens, verified 2026-06-26)

| Model | Base input | 5m write (1.25×) | 1h write (2×) | Cache hit (0.1×) | Output |
|---|---|---|---|---|---|
| Fable 5 | 10 | 12.50 | 20 | 1.00 | 50 |
| Opus 4.5 / 4.6 / 4.7 / 4.8 | 5 | 6.25 | 10 | 0.50 | 25 |
| Opus 4 / 4.1 *(retired/deprecated)* | 15 | 18.75 | 30 | 1.50 | 75 |
| Sonnet 4 / 4.5 / 4.6 | 3 | 3.75 | 6 | 0.30 | 15 |
| Haiku 4.5 | 1 | 1.25 | 2 | 0.10 | 5 |
| Haiku 3.5 *(retired)* | 0.80 | 1.00 | 1.60 | 0.08 | 4 |

---

## B. Model tier (selects the rate table)

| Lever | JSONL location | genai-prices |
|---|---|---|
| Model → rate table | `message.model` | ✅ `match` patterns per model |

Aliases (`opus`, `…[1m]` suffixes, dated variants like `claude-opus-4-5-20251101`)
must resolve to the canonical rate table. `<synthetic>` is a Claude Code
sentinel for internal messages — **skip it before pricing** (not a real API call).

---

## C. Per-request multipliers (stack on Section A)

| Lever | Effect | JSONL location | genai-prices |
|---|---|---|---|
| **Fast mode** | premium flat rates (below) | `usage.speed == "fast"` | ❌ not modeled |
| **Batch API** | 0.5× input & output | `usage.service_tier == "batch"` | ❌ not modeled |
| **Priority tier** | commitment pricing | `usage.service_tier == "priority"` | ❌ not modeled |
| **Data residency (US)** | 1.1× on *all* token categories | `usage.inference_geo == "us"` | ❌ not modeled |
| **Long-context (>200K)** | model-dependent premium tier | *derived*: `input + cache_read + cache_write` vs model context window | ✅ `tiers:[{start, price}]` |

Stacking rules (per pricing page):
- Fast mode applies across the full context window (incl. >200K) and **stacks with** prompt-caching multipliers and data residency; **not available with** Batch.
- Batch and prompt-caching discounts combine.
- Data residency 1.1× applies to input, output, cache writes, and cache reads (Opus 4.6 / Sonnet 4.6 and later only; earlier models reject the `inference_geo` param).

**Long-context status (important):** the current 1M-window models — Opus 4.6/4.7/4.8, Sonnet 4.6, Fable 5 — bill the **full window at standard (flat) pricing**; there is **no >200K premium** for them. genai-prices encodes the *historical* transition where applicable via a dated `constraint` (e.g. Opus 4.6 moved to flat-1M on 2026-03-13). A request only incurs a >200K premium on a model that actually has the tier at that date.

### Fast-mode rates (USD per 1M tokens)

| Model | Input | Output |
|---|---|---|
| Opus 4.6 / 4.7 | 30 | 150 |
| Opus 4.8 | 10 | 50 |

(Prompt-caching multipliers and data residency apply on top of fast-mode rates.)

---

## D. Server-side tool surcharges (non-token, additive)

These are **separate line items**, not token rates. The request *count* is in the
JSONL; the *cost* is not always reconstructable from a single session (see code
execution).

| Lever | Rate | JSONL location | Observable? | genai-prices |
|---|---|---|---|---|
| Web search | $10 / 1,000 searches | `usage.server_tool_use.web_search_requests` | ✅ fully | ❌ not modeled |
| Web fetch | free | `usage.server_tool_use.web_fetch_requests` | n/a (no cost) | n/a |
| Code execution | $0.05 / hour / container; **1,550 free hr/month**; 5-min minimum | `usage.server_tool_use.code_execution_requests` (count only) | ⚠️ **partial** — billed by container-*hour* against a monthly org-level free tier; per-session JSONL has the request count but **not the duration or the monthly aggregate** | ❌ not modeled |

> genai-prices has a per-request field (`requests_kcount`, used today for
> Perplexity) that *could* express the web-search surcharge, but it is **not
> populated for Anthropic**. Code-execution's hour-based, free-tiered billing has
> no representation.

---

## E. Out-of-scope-but-real (no first-party JSONL signal)

Catalogued for completeness; not observable in Claude Code / Agent SDK session
files and therefore out of scope for a session-JSONL cost estimator.

| Lever | Effect | Why out of scope |
|---|---|---|
| Bedrock / Vertex regional & multi-region endpoints | +10% over global | Partner-operated; billed by the cloud provider, not in first-party JSONL |
| Claude Platform on AWS | CCU conversion ($0.01/CCU) | Marketplace invoicing; not in JSONL |
| Managed Agents session runtime | $0.08 / session-hour | Managed-Agents product; not in Claude Code session JSONL |

---

## F. Count-affecting, NOT rate (do not apply as multipliers)

These change the **number of tokens**, which is already reflected in the
`usage.*_tokens` counts. **Do not** add them as price multipliers — that would
double-count.

| Factor | Effect | Already captured by |
|---|---|---|
| New tokenizer (Opus 4.7+) | up to +35% tokens for the same text | `usage.input_tokens` / `output_tokens` already reflect it |
| Tool-use system prompt + tool definitions | adds input tokens (per-model overhead table on pricing page) | `usage.input_tokens` |
| Extended thinking | billed as output tokens | `usage.output_tokens` (breakdown in `usage.output_tokens_details.thinking_tokens`) |

---

## G. Underivable from session data (stated limitation)

| Factor | Why |
|---|---|
| Volume / enterprise / negotiated discounts | Applied at the account/billing layer; never present in session JSONL. A list-price estimator reports **list price**, which may overstate an enterprise account's actual spend. |

---

## Coverage summary vs genai-prices

genai-prices (MIT) is the chosen upstream dataset. For Anthropic it currently
models **only**: `input_mtok`, `output_mtok`, `cache_read_mtok`,
`cache_write_mtok` (single, 5m-equivalent), context-length `tiers`, and
date/time `constraint`s. Everything below is a **gap we must supply locally**
(via the pricing overlay) and/or **request upstream**:

| Gap | Section | Live cost impact for users | Coverage path |
|---|---|---|---|
| 1-hour cache write (2×) | A | **High** (dominant TTL) | Local overlay now (agentfluent#534); request upstream |
| Fast mode premium rates | C | High *if used* | Local overlay; request upstream |
| Batch (0.5×) / Priority tier | C | Medium (batch ≠ interactive, but Agent SDK users may batch) | Local overlay; request upstream |
| Data residency US (1.1×) | C | Low–Medium | Local overlay; request upstream |
| Web search ($10/1k) | D | Medium *if used* | Local overlay (counts present); request upstream |
| Code execution ($/hr) | D | Partial — see Section D | Document limitation; surface count; request upstream |

Out-of-scope (E) and count-affecting (F) levers need **no rate modeling**; they
are documented so consumers neither attempt to price them nor double-count them.

---

## Worked example (correct cache-write handling)

A single Opus 4.7 request: `input_tokens=2741`, `output_tokens=329`,
`cache_read_input_tokens=0`, `cache_creation.ephemeral_5m_input_tokens=0`,
`cache_creation.ephemeral_1h_input_tokens=19117`, `speed=standard`,
`service_tier=standard`, `inference_geo` not US.

```
input  : 2741   × $5.00  / 1e6 = $0.0137050
output : 329    × $25.00 / 1e6 = $0.0082250
1h write:19117  × $10.00 / 1e6 = $0.1911700   ← priced at 2× base, NOT 1.25×
5m write:0      × $6.25  / 1e6 = $0.0000000
cache rd:0      × $0.50  / 1e6 = $0.0000000
                               -----------
                         total = $0.2131000
```

Pricing the 19,117 cache-write tokens at the 5m rate ($6.25) instead would report
$0.1936, a **$0.072 (-25% on cache-write cost)** under-report on this one request.
