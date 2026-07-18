# Token accounting in Claude Code session JSONL

**Status:** research finding, confirmed against live corpus 2026-07-18.
**Audience:** any project that derives token or cost numbers from Claude Code
session JSONL — AgentFluent, [CodeFluent][cf], [claude-code-sessions][ccs], or
third parties reading the same files.
**Tracking:** AgentFluent [#646][i646]; decision record [D056][d056].

[cf]: https://github.com/frederick-douglas-pearce/codefluent
[ccs]: https://github.com/frederick-douglas-pearce/claude-code-sessions
[i646]: https://github.com/frederick-douglas-pearce/agentfluent/issues/646
[d056]: ../.claude/specs/decisions.md

---

## TL;DR for maintainers of other projects

Three independent defects, in descending order of subtlety:

1. **`toolUseResult.totalTokens` is a single-turn context-size snapshot**, not
   cumulative spend. Summing it understates processed tokens by a **median 5.8x**
   and dollar cost by **~15x** on measured sessions. (§1, §2)
2. **Streaming chunks repeat `message.id`.** Summing every `assistant` line's
   `usage` without deduplicating **double-counts by ~2x**. (§3)
3. **Orphan / depth-≥2 traces get silently dropped**, hiding **~30%** of all
   subagent tokens. (§4)

If your code does any of these, you have a bug:

```python
# BROKEN (§1) — sums a per-turn snapshot as if it were cumulative
total = sum(inv["toolUseResult"]["totalTokens"] for inv in invocations)

# BROKEN (§1) — prices a non-spend quantity
cost = rollup["totalTokens"] * rate_per_token

# BROKEN (§1) — treats toolUseResult.usage as the invocation's usage
cost = price(rollup["usage"])          # it is ONE TURN's usage

# BROKEN (§3) — no dedup; streaming snapshots counted repeatedly
total = sum(sum(m["message"]["usage"].values()) for m in assistant_lines)
```

Correct source for an invocation's spend: the **child trace file**, deduplicated
by `message.id`, summed per turn. See [§6 Correct implementation](#6-correct-implementation).

---

## 1. The exact rule

Each subagent invocation produces a `toolUseResult` on the parent session's
`user` message. Two fields matter:

```jsonc
"toolUseResult": {
  "agentId": "af8069043d85b4450",
  "totalTokens": 78006,              // <-- the trap
  "usage": {
    "input_tokens": 2,
    "cache_creation_input_tokens": 681,
    "cache_read_input_tokens": 75353,
    "output_tokens": 1970
  }
}
```

**The rule, verified 691/691 exact (100%) on the live corpus:**

```
totalTokens == usage.input_tokens
             + usage.output_tokens
             + usage.cache_creation_input_tokens
             + usage.cache_read_input_tokens
```

And `usage` holds **one assistant turn's** usage — not an aggregate over the run.

| characterisation | match rate |
| --- | --- |
| `totalTokens` == sum of `usage` components | **691 / 691 (100%)** |
| `totalTokens` == the agent's **final** assistant turn | 582 / 691 (84.2%) |
| `totalTokens` == the **sum** of the agent's turns | 12 / 691 — 7 single-turn traces + 5 degenerate zero-token rollups |

The 84% "final turn" figure is the weaker, statistical characterisation. The
100% figure is **deterministic**, and it is the one to test against: the 16%
residual is simply cases where the turn snapshotted into `usage` is not the last
assistant line in the trace. A correct implementation has a hard invariant
available, not a heuristic.

> **Naming hazard.** A field's *name* is not its *semantics*. An SDK-supplied
> rollup called `totalTokens` invites exactly the reading the data refutes.
> Every consumer that summed it read "total" as cumulative.

### Why it is a context-size proxy

The Messages API is **stateless** — the full conversation history is re-sent on
every request ([official][msgapi]). So each turn's `usage` re-reports the whole
context it was given, and `cache_read_input_tokens` recurs turn over turn,
growing as the conversation grows.

Consequences, both directions:

- **Snapshotting one turn** (what `totalTokens` does) yields a *context size* —
  how much context the agent held at that moment.
- **Summing all turns** yields *tokens processed*, the correct basis for **cost**
  — you are genuinely billed per turn for re-read context, at the cheaper
  cache-read rate.

Neither is "tokens in the conversation." Both are legitimate numbers answering
different questions. `totalTokens` is only wrong when read as the second one.

### Why the trap is so well camouflaged

`totalTokens` is *numerically plausible*. Summed across a run, the **expensive**
components alone (`input + output + cache_creation`, excluding cache reads) come
to a **median 1.02x** of `totalTokens` (p90 1.21x).

That near-identity is structural, not coincidental: each context token is
cache-*written* roughly once over a run, so `Σ cache_creation` ≈ the final
context size ≈ `totalTokens`. The rollup therefore lands within a few percent of
a real and meaningful quantity — which is precisely why the error survived four
minor versions and propagated into published reference documentation (§8).

[msgapi]: https://platform.claude.com/docs/en/build-with-claude/working-with-messages

---

## 2. Magnitude

Corpus: `~/.claude/projects`, 2026-07-18. 1,047 subagent trace files carrying at
least one assistant turn; 691 linked to a parent rollup; 677 multi-turn. All
figures below use `message.id` deduplication (§3).

### Token quantities

Ratio of **real processed tokens** to the published `totalTokens`:

| statistic | all components | excluding `cache_read` |
| --- | ---: | ---: |
| minimum | 1.0x | — |
| **median** | **5.8x** | **1.02x** |
| p90 | 14.2x | 1.21x |
| maximum | 79.7x | 17.2x |

The right-hand column is the camouflage described in §1. The left-hand column is
what you are billed on — cache reads cost 0.1x, but there are a *lot* of them.

### Dollar cost — the number that matters

Six heaviest agent sessions. "Published" is AgentFluent's shipped
`estimated_total_cost_usd`; "true" prices each deduplicated per-turn usage at
that turn's real model rate:

| session | published | true | ratio | published token % | true token % |
| --- | ---: | ---: | ---: | ---: | ---: |
| `13607e2a` | $1.82 | $18.69 | 10.2x | 0.5% | 4.0% |
| `e175c382` | $1.29 | $17.28 | 13.4x | 0.8% | 5.8% |
| `b40dbafc` | $0.99 | $13.11 | 13.2x | 1.0% | 6.7% |
| `2b898766` | $1.32 | $32.52 | 24.6x | 0.9% | 20.4% |
| `2bdee3a4` | $0.92 | $13.05 | 14.1x | 2.6% | 24.4% |
| `c266bcf2` | $0.58 | $10.97 | 18.8x | 0.6% | 6.1% |
| **total** | **$6.94** | **$105.63** | **15.2x** | | |

**Direction is always understatement.** There is no configuration in which this
defect inflates a number.

Note the cost ratio (15x) exceeds the token ratio (5.8x). Two errors compound:
the numerator is a snapshot, *and* AgentFluent applies a **cache-diluted blended
rate** (`session_cost / session_total_tokens`, where the denominator counts cache
reads at full token weight but they cost 0.1x). A correct fix must address both
the quantity and the rate — see §6.

### Worked example — one invocation

Trace `agent-af8069043d85b4450.jsonl`, agent type `pm`, model
`claude-opus-4-8`, 35 deduplicated turns:

```
rollup totalTokens                    =        78,006
rollup usage {2, 681, 75353, 1970}    →        78,006   (exact, per §1)
the trace's final turn                =       357,933   (≠ rollup: one of the 16%)

child trace, summed over 35 turns:
  input + output + cache_creation     =     1,344,091
  cache_read                          =     4,873,272
  total processed                     =     6,217,363   (80x the rollup)

priced at real claude-opus-4-8 rates:
  cost implied by the rollup snapshot =         $0.09
  TRUE cost (35 per-turn usages)      =        $12.21   (134x)
```

78% of real spend here is `cache_read`, re-reported every turn — exactly why the
rollup looks stable and modest while true spend compounds. This invocation is
near the corpus maximum; the median is 5.8x on tokens and ~15x on dollars.

---

## 3. The streaming-dedup trap

**This one bit the author of this document**, and it invalidated the first pass
of every number above by ~2x. Documented here so nobody repeats it.

Claude Code writes **multiple `assistant` lines for the same logical turn** as a
response streams. They share a `message.id`; each carries a *snapshot* of `usage`
as it accumulates. Naively summing every assistant line therefore counts the same
turn several times.

Measured across all 1,047 trace files:

| | value |
| --- | ---: |
| assistant lines carrying `usage` | 23,119 |
| files containing duplicate `message.id` | **986 / 1,047 (94%)** |
| naive sum of all lines | 1,162,625,174 |
| deduplicated sum | 584,837,592 |
| **inflation from not deduplicating** | **1.99x** |

Also observed: `input_tokens` is frequently a small placeholder on non-final
chunks — `1` on 11,133 lines, `2` on 4,019, `3` on 2,300, `0` on 491. Do not read
`input_tokens` off an arbitrary chunk.

**Correct approach:** group assistant lines by `message.id`; within each group
keep the record with the **greatest `output_tokens`** (the most complete
snapshot) and take *all four* usage fields from that same record. Do not take a
per-field max across records — that mixes snapshots.

Both AgentFluent (`core/parser.py`, via `traces/parser.py`) and CodeFluent
(`extract_prompts.py:700-716`) already do this correctly in production. Verified:
the dedup method above matches AgentFluent's production parser on **80/80**
sampled traces, and the corpus total (584,837,592) independently matches
CodeFluent's measured 584,090,232 to within 0.13%.

---

## 4. Orphan and depth-≥2 traces

Independent of §1 and §3.

A subagent trace only contributes if it links to an invocation discovered in the
parent session. Non-linking traces are silently discarded (AgentFluent:
`analytics/pipeline.py:494`, *"Orphan traces (file exists, no matching
invocation) are debug-logged"*).

| | trace files | processed tokens |
| --- | ---: | ---: |
| linked to a parent rollup | 691 | 405,043,066 |
| **no parent rollup found** | **213** | **179,794,526** |
| total | 1,047 | 584,837,592 |

**30.7% of all subagent processed tokens sit in traces with no parent rollup.**
Two causes contribute, not separated by this measurement:

1. **Depth-≥2 spawns carry no rollup by design.** A level-1 subagent's result
   carries a full `toolUseResult`; a subagent spawned *by* a subagent produces a
   `tool_result` with **no** rollup — only an inline `subagent_tokens:` text
   trailer. Grandchild metrics exist only in the grandchild's own trace file.
   (See `agent-sdk-session-format-findings.md` §4.)
2. **Orphaned parents** — parent session rotated or deleted.

Either way the tokens are invisible. In AgentFluent this also means they are
missing from `token_metrics` — the session's **total cost is understated too**,
not just per-agent attribution.

**Anthropic documents this failure mode for the SDK.** From the Agent SDK
cost-tracking guide, on subagents:

> "Use `modelUsage`, or `model_usage` in Python, for whole-tree token accounting;
> the `usage` field **undercounts as soon as nesting occurs**."
> — [Track cost and usage][sdkcost]

That is the same defect in the SDK's own surface, officially acknowledged.

[sdkcost]: https://code.claude.com/docs/en/agent-sdk/cost-tracking

---

## 5. What official Anthropic documentation confirms

Because the JSONL format itself is undocumented, it matters which parts of this
analysis rest on official docs and which rest only on corpus measurement.

| claim | verdict | source |
| --- | --- | --- |
| `usage` fields are **per-request**, not cumulative | **CONFIRMED** | [Messages API][msgapi] |
| Full history is **re-sent every turn** (stateless API) | **CONFIRMED** | [Messages API][msgapi] |
| Cumulative cost = **sum over requests** | **CONFIRMED** | [SDK cost tracking][sdkcost] |
| Cache read 0.1x; write 1.25x (5m) / 2x (1h) | **CONFIRMED** | [Prompt caching][cache] |
| Nested/subagent `usage` **undercounts** | **CONFIRMED** | [SDK cost tracking][sdkcost] |
| `totalTokens` is **not** a Messages API field | **CONFIRMED** | [Messages API][msgapi] |
| `cache_read` **grows** across turns | **UNDOCUMENTED** | corpus only (§1) |
| `toolUseResult` / JSONL field semantics | **UNDOCUMENTED** | corpus only |

Key quotes:

> "The Messages API is stateless, which means that you always send the full
> conversational history to the API." — [Messages API][msgapi]

> "Each `query()` call within a session reports its own cost independently... The
> SDK does not provide a session-level total, so if your application makes
> multiple `query()` calls... accumulate the totals yourself."
> — [SDK cost tracking][sdkcost]

**Anthropic explicitly discourages parsing these files:**

> "The entry format is internal to Claude Code and changes between versions, so
> scripts that parse these files directly can break on any release."
> — [Manage sessions][sessions]

That is the correct posture for any project in this space to adopt publicly: the
§1 invariant is an *empirical* property of one captured format version, not a
contract. Assert it in tests so a format change fails loudly.

**Authoritative validation is available.** The [Usage & Cost Admin API][admin]
(`/v1/organizations/cost_report`, `/v1/organizations/usage_report/messages`)
returns real billed spend per workspace. Anthropic is explicit that client-side
figures are estimates:

> "The `total_cost_usd` and `costUSD` fields are client-side estimates, not
> authoritative billing data... For authoritative billing, use the Usage and Cost
> API." — [SDK cost tracking][sdkcost]

Reconciling a log-derived total against the cost report is the strongest
available check on any fix to §1–§4, and is worth doing before publishing
corrected numbers.

[cache]: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
[sessions]: https://code.claude.com/docs/en/sessions
[admin]: https://platform.claude.com/docs/en/manage-claude/usage-cost-api

---

## 6. Correct implementation

### Locating the child trace

```
~/.claude/projects/<project-slug>/
  <session-id>.jsonl                                  # parent
  <session-id>/subagents/
    agent-<agentId>.jsonl                             # child trace
    agent-<agentId>.meta.json                         # {agentType, description, toolUseId}
```

Join on `toolUseResult.agentId` → child filename, or use the `.meta.json`
sidecar's `toolUseId` for a direct `tool_use.id` → `agentId` map.

### Summing real spend, with dedup

```python
USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens")

def invocation_turns(trace_path):
    """Deduplicated per-turn usage for one subagent invocation. THIS is spend.

    Streaming emits several assistant lines per logical turn sharing a
    message.id; keep the most complete snapshot (greatest output_tokens)
    and take all four usage fields from that same record.
    """
    best, order = {}, []
    for line in open(trace_path):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        usage = msg.get("usage") or {}
        if not usage:
            continue
        mid = msg.get("id") or f"__anon{len(order)}"
        if mid not in best:
            order.append(mid)
            best[mid] = (usage, msg.get("model"))
        elif (usage.get("output_tokens") or 0) > (best[mid][0].get("output_tokens") or 0):
            best[mid] = (usage, msg.get("model"))
    return [best[mid] for mid in order]
```

Then price **per turn at that turn's own model rate** — not via a session blended
rate. Two reasons: a `haiku` subagent under an `opus` session is otherwise
misattributed, and a blended rate derived from a cache-inflated token denominator
is itself diluted (§2).

### Rules of thumb

1. **Never sum `totalTokens`.** Use it only as an explicitly-labeled context-size
   reading, or not at all.
2. **Never price `toolUseResult.usage`.** It is one turn.
3. **Always deduplicate by `message.id`** before summing per-turn usage (§3).
4. **Account for orphan traces**, or report coverage alongside the number (§4).
5. **Report coverage explicitly** when some invocations have traces and some do
   not — never silently blend real sums with snapshots. AgentFluent's
   `active_duration_invocation_count` is the existing pattern.
6. **Assert the §1 invariant in tests**, so a format change fails loudly rather
   than silently shifting your numbers.
7. **Reconcile against the [Admin cost report][admin]** before publishing
   corrected figures (§5).

---

## 7. Reproducing this

Self-contained, no project dependencies:

```python
import json, statistics
from pathlib import Path

K = ("input_tokens", "output_tokens",
     "cache_creation_input_tokens", "cache_read_input_tokens")
root = Path.home() / ".claude" / "projects"

def turns(path):                                  # dedup by message.id (§3)
    best, order = {}, []
    for line in path.open():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        m = d.get("message") or {}
        u = m.get("usage") or {}
        if not u:
            continue
        mid = m.get("id") or f"__a{len(order)}"
        if mid not in best:
            order.append(mid); best[mid] = u
        elif (u.get("output_tokens") or 0) > (best[mid].get("output_tokens") or 0):
            best[mid] = u
    return [sum(best[i].get(k) or 0 for k in K) for i in order]

rollups = {}
for f in root.glob("*/*.jsonl"):
    for line in f.open():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        r = d.get("toolUseResult")
        if isinstance(r, dict) and r.get("agentId"):
            rollups[r["agentId"]] = r

exact = final = summed = 0
ratios = []
for tf in root.glob("*/*/subagents/agent-*.jsonl"):
    t = turns(tf)
    r = rollups.get(tf.stem.removeprefix("agent-"))
    if not t or not r or not isinstance(r.get("totalTokens"), int) or r["totalTokens"] == 0:
        continue
    tt, usage = r["totalTokens"], r.get("usage") or {}
    exact  += tt == sum(usage.get(k) or 0 for k in K)   # expect 100%
    final  += tt == t[-1]                                # expect ~84%
    summed += tt == sum(t)                               # expect single-turn only
    ratios.append(sum(t) / tt)

ratios.sort()
print(f"n={len(ratios)}  ==sum(usage): {exact}  ==final: {final}  ==sum(turns): {summed}")
print(f"processed/totalTokens  median={statistics.median(ratios):.1f}x  max={ratios[-1]:.1f}x")
```

Verbatim output on the corpus described in §2:

```
n=686  ==sum(usage): 686  ==final: 577  ==sum(turns): 7
processed/totalTokens  median=5.8x  max=79.7x
```

`n=686` versus the 691 quoted elsewhere: this script skips the **5** rollups
whose `totalTokens` is `0` (they would divide by zero). All 5 are degenerate —
their traces sum to 0 tokens as well, which is also why `==sum(turns)` reads 7
here versus 12 in §1. Zero-valued rollups occur in the wild; guard for them.
Excluding them does not affect the `==sum(usage)` result, which is 100% either
way.

---

## 8. Cross-project review status

| project | §1 rollup defect | §3 dedup defect | §4 orphan defect |
| --- | --- | --- | --- |
| **AgentFluent** | **Affected** ([#646][i646], sites below) | clean — dedups in `core/parser.py` | **Affected** — 30.7% |
| **CodeFluent** | **Not affected** — never reads the rollup | clean — dedups by `message.id` | **Affected** — 30.2% |
| **claude-code-sessions** | **Documents the inverse error** | not addressed in docs | asymmetry documented correctly |

### AgentFluent — affected sites

- `analytics/agent_metrics.py:217-218` (per-type accumulation), `:250-254`
  (`blended_rate`), `:262` (`estimated_total_cost_usd`), `:267`, `:273-277`
  (`agent_token_percentage`)
- `analytics/pipeline.py:436` (`_merge_agent_metrics`), `:454-455`
  (`avg_tokens_per_tool_use`), `:461`, `:466-469`
- `diagnostics/tool_orchestration.py:130` → `:133` (`estimated_savings`)
- `diagnostics/model_routing.py:138` (median/outlier), `:187`
- `diagnostics/_complexity.py:231`
- `diagnostics/signals.py:313` → read back at `correlator.py:395`

### CodeFluent — clean on §1, affected by §4

**CodeFluent does not have the `totalTokens` bug and never did.** A repo-wide
grep for `toolUseResult` returns exactly one hit, and it is prose
(`codefluent/SECURITY.md:38`). Every `total_tokens` identifier is CodeFluent's
own locally-computed field, summed from per-turn `message.usage`
(`webapp/conversations.py:145` ← `:119-124`; `webapp/extract_prompts.py:528`).
`webapp/main.py:377,386,407,415` uses the camelCase name `entry["totalTokens"]`
purely for **ccusage output-format compatibility**, fed from the correct source —
confusingly named, correctly sourced.

`extract_prompts.py:652-745` (`scan_subagent_tokens`) already implements §6
exactly, including the §3 dedup. Mirrored in TypeScript at
`vscode-extension/src/parser.ts:735-810`. Pricing (`webapp/main.py:139-151`)
multiplies rates against those per-turn sums. Nothing rollup-derived reaches a
price multiplication anywhere.

**The §4 orphan defect is present.** `webapp/conversations.py:333-347` attributes
subagent tokens only to a conversation already carrying the matching
`session_id`; unmatched entries are silently discarded with no warning or
counter. On the same corpus: keying is **sound** (child `sessionId` == parent
directory name in 1047/1047, so the `:723` fallback never fires), but **61 of 246**
`subagents/` dirs have no parent `<session-id>.jsonl` anywhere — carrying
**176,545,872 of 584,090,232 subagent tokens = 30.2%**, dropped from every
CodeFluent token and cost number.

Affected: conversation `total_tokens` and `tokens_per_prompt`
(`conversations.py:352`), `cache_hit_rate` (`:354`), daily/monthly rollups and
`totalCost` (`main.py:349-416`), session cost column (`main.py:540`). Sites:
`webapp/conversations.py:333-347` and the identical
`vscode-extension/src/conversation.ts:409-423`.

*Fix:* return unattributed entries separately and surface them as an
"unattributed subagent usage" line — at minimum log a count so the loss is
visible.

*Latent, not live:* `conversations.py:326-329` iterates every project dir but
matches against a global `all_conversations` at `:332`, so a subagent map from
project A could in principle attach to a conversation in project B. UUID
collision makes this practically impossible. Same shape at
`conversation.ts:403-412`. Tidiness fix: scope the inner loop to conversations
whose `project_path_encoded` matches `project_dir.name`.

### claude-code-sessions — documents the inverse error

**This is the highest-impact remediation**, because this repo is the public
reference other projects paraphrase — and it currently teaches readers to make
the §1 mistake deliberately.

The root error: the docs assert `toolUseResult.usage` is **cumulative across the
subagent run**, and conclude that trace and rollup are "the same tokens reported
twice" so summing both **double-counts**. The truth is the inverse — the rollup
is one turn, and rollup-only aggregation **under**counts by ~5.8x.

Priority-ordered corrections (each currently steers a reader wrong):

1. **`reference/subagent-traces.md:342-347`** — the aggregation-patterns table
   *prescribes* "sum `toolUseResult.usage` on parent `user` lines" as the cheap
   path for total token consumption, and says trace-sum and rollup "should
   match." Both false.
2. **`reference/subagent-traces.md:175`** — advises *skipping* sidechain lines
   when reading the parent rollup, "otherwise you double-count." Following this
   produces the full undercount. Inverted advice.
3. **`posts/2026-06-04-...md:273`** — a copy-pasteable `jq` recipe emitting
   `tokens: .toolUseResult.totalTokens` per invocation, described as "the surface
   that powers any per-subagent diagnostic."
4. **`reference/data-dictionary.md:192`** — *"Total tokens consumed across the
   subagent's entire run."* The single most-copied line in the repo; AgentFluent's
   own `CLAUDE.md` paraphrases it.

Further sites: `subagent-traces.md:316,351,353,355` ("equal token counts" — the
strongest false claim; and `:355`'s mechanism is backwards — the rollup never
summed, it snapshotted); `data-dictionary.md:182,407,429,430`;
`tool-invocation.md:229,262,281`; `cost-model.md:50` (names the missing `model`
as the "one critical omission" — there are two, and this is the lesser).

**Already correct, no change needed:** the depth-≥2 no-rollup asymmetry, in all
three places it appears (`subagent-traces.md:136-141`, `data-dictionary.md:214`,
`tool-invocation.md:285`); and the `totalTokens == sum of the four usage fields`
identity, which the fixtures already encode as an invariant.

**Fixtures need a decision.** `fixtures/synthetic/anatomy-agent-invocation.jsonl`
sets `usage` to the *column sum* of the 8 turns in the paired trace fixture — a
~6.25x overstatement of a realistic rollup, teaching the wrong mental model. But
the generator notes state *"if you change either fixture's token numbers, change
both,"* and the cross-fixture sum invariant is narrated in a published post.
Re-cut both, or keep and annotate.

Two pieces of good news: `anatomy-subagent-trace.jsonl.generator.md:48` already
hedges that the four-field-sum formula is *"not pinned in the reference; this
fixture adopts it as a clear, reader-verifiable definition"* — that hedge can now
be promoted to **confirmed, 691/691**. And `subagent-traces.md:376` open
verification item 1 ("the exact `totalTokens` cross-level inclusivity rule") is
**closed** by this finding.

**Documentation gap:** the per-turn cache-recurrence mechanism (§1) appears
nowhere in `reference/` — only in an internal spec and a fixture generator note,
neither of which connects it to `totalTokens` semantics. That missing link is
what made the error invisible. It governs `message.usage` generally, so it
belongs canonically in `data-dictionary.md` near the existing usage pitfalls, not
only in the subagent doc.

Published posts need **errata, not silent edits** — notably
`posts/2026-06-24-token-accounting-is-harder-than-it-looks.md`, whose central
section ("The main event: the double-count") rests entirely on the false premise.
Ironically the true finding is a stronger version of that post's thesis: the trap
is not a 2x inflation but a ~6x deflation.

---

## 9. Open design questions

For [#646][i646]'s design pass — not settled here.

1. **Replace or coexist?** Does corrected spend replace `total_tokens` at the
   aggregates, or land beside it with the existing field retained as a deliberate
   context-size metric? `total_tokens` **has shipped**, so [D029][d056] governs.
2. **Mixed-coverage aggregates.** How should an aggregate behave when some
   invocations are trace-linked and some are not? (Recommendation: coverage-count
   the corrected subset, as `active_duration_invocation_count` already does.)
3. **Orphan traces** (§4) — attribute to the session, report separately, or
   surface as a coverage gap?
4. **Supersede or re-scope [#143][i143]?** Its premise — populate per-invocation
   input/output splits from `toolUseResult.usage` — is undercut by §1. As written
   it would replace one non-spend number with four non-spend numbers.
5. **Blended rate.** §2 shows the rate is diluted independently of the quantity.
   Per-turn per-model pricing fixes both; confirm that is in scope.

[i143]: https://github.com/frederick-douglas-pearce/agentfluent/issues/143

---

## Provenance

- **Corpus:** `~/.claude/projects`, measured 2026-07-18. 1,047 subagent trace
  files with ≥1 assistant turn; 691 linked rollups; 163 sessions analysed for the
  `agent_token_percentage` distribution (median 1.4%, max 31.6%, none >100%).
- **Format version:** confirmed against `claude-agent-sdk==0.2.106` / CLI
  `2.1.185`. Anthropic states this format is internal and may change on any
  release (§5) — re-run §7 before assuming these properties hold.
- **Cross-validation:** the §3 dedup matches AgentFluent's production parser on
  80/80 sampled traces; the corpus total matches CodeFluent's independently
  measured figure to within 0.13%.
- **Correction history:** the first pass of this analysis omitted §3 dedup and
  overstated all magnitudes by ~2x (median 12.5x rather than 5.8x, cost 28x
  rather than 15x). The §1 rule was unaffected. Numbers here are the corrected
  set; [#646][i646] carries the same correction.
- **Related:** [D056][d056], `#595` (trace linker),
  `agent-sdk-session-format-findings.md` §4, [`COST_MODEL.md`](COST_MODEL.md).
