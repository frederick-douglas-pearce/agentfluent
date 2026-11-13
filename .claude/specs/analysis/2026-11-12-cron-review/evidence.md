# Derived evidence — 2026-11-12 cron-accumulation review

Companion to [`analysis.md`](analysis.md).

## Why there is no `analyze.json` here

Prior dogfood analyses committed the raw `analyze --json` output alongside the write-up. This review's raw inputs are the **dogfood runner's retained snapshot tree**, which is deliberately out-of-tree and must not be committed:

> Snapshots carry absolute paths and project data, and an in-tree state dir is one `.gitignore` slip from leaking that to a public repo.
> — `tools/dogfood_runner/README.md`

The tree also holds data for six unrelated projects (including a private client-adjacent repo) and totals ~115 MB across 20 slugs × 14 generations. So this file carries the **derived tables** instead: every number quoted in `analysis.md`, plus the commands to regenerate them from a live state tree.

Source tree at review time: `$XDG_STATE_HOME/agentfluent/dogfood/` — 20 slugs, 14 retained generations per active slug, newest `snapshot-20261111T203001Z.json`.

## Cron liveness

| | |
|---|---|
| Schedule | `30 12 * * *` — daily 12:30 local, `--window 7d` |
| Installed by | `tools/dogfood_runner/install-cron.sh` (tagged `# agentfluent-dogfood-runner (S0/#590)`) |
| Live since | ~2026-07-07 |
| Synthesis runs logged | 119 (`runs.jsonl`) |
| Last run before review | 2026-11-11 12:30, clean |
| Analysis failures in retained window | 0 |
| `warnings[]` | empty in every snapshot |
| `tier3_degraded` | `false` |

Main-model rotation (#636) confirmed cycling by date: 2026-11-09 `claude-opus-4-8`, 2026-11-10 `claude-sonnet-4-6`, 2026-11-11 `claude-haiku-4-5`.

## Latest window — per-project totals (2026-11-11 snapshot generation)

| project slug | sessions | reported cost | cache eff. | API calls | agents invoked |
|---|---|---|---|---|---|
| us-presidential-vote-analysis | 92 | $0.0000 | 97.2% | 3,169 | ba 41, architect 33, general-purpose 93, pm 7, marketer 1 |
| sportswear-esg-news-classifier | 28 | $0.0000 | 96.0% | 630 | architect 6, general-purpose 40 |
| github-pages-…-github-io | 17 | $0.0000 | 95.5% | 336 | architect 6, general-purpose 14 |
| agentfluent | 7 | $3.7064 | 62.3% | 23 | snapshot-summarizer 85 |
| claude-code-loop | 7 | $0.0000 | 96.2% | 414 | general-purpose 3, marketer 3, pm 1, architect 1 |
| claude-code-sessions | 5 | $0.0000 | 94.5% | 73 | general-purpose 1 |
| claude-hook-validator | 1 | $0.0000 | 0.0% | 0 | — |
| **total** | **157** | **$3.7064** | — | **4,645** | — |

`agentfluent` is the only slug reporting non-zero cost because the dogfood runner pins pre-5.5 models for synthesis; every other slug ran on `claude-opus-5-5` / `claude-sonnet-5-5`, which the pricing allow-list zeroes (#664).

The `agentfluent` slug's 7 sessions are **all** the runner's own: `session_kind: sdk`, `entrypoint: sdk-py`, `tool_frequency: {"Agent": N}`, `by_agent_type: {"snapshot-summarizer": N}`. Window filter: 244 sessions before, 7 after.

## Pricing coverage — all model ids in the corpus

Measured through the production path at `genai-prices==0.1.4`:

```python
from agentfluent.analytics.pricing import get_pricing
from agentfluent.analytics._genai_source import _resolve_rates
```

| model id | rows across all snapshots | `get_pricing` | `_resolve_rates` (upstream) | verdict |
|---|---|---|---|---|
| `claude-opus-5-5` | 245 | `None` → $0 | `in=5.0, out=25.0` | silently zeroed |
| `claude-sonnet-5-5` | 50 | `None` → $0 | `in=3.0, out=15.0` | silently zeroed |
| `claude-fable-5-1` | 34 | `None` → $0 | `in=10.0, out=50.0` | silently zeroed |
| `claude-haiku-4-5-20251001` | 28 | OK | `in=1.0, out=5.0` | priced |
| `claude-sonnet-4-6` | 14 | OK | `in=3.0, out=15.0` | priced |
| `claude-opus-4-8` | 14 | OK | `in=5.0, out=25.0` | priced |
| `claude-haiku-5-5` | 12 | `None` → $0 | **`None`** | needs `_RESIDUAL` |
| `claude-opus-4-7` | 11 | OK | `in=5.0, out=25.0` | priced |
| `claude-sonnet-5` | 8 | `None` → $0 | `in=3.0, out=15.0` | silently zeroed |
| `claude-opus-5` | 1 | OK | `in=5.0, out=25.0` | priced (#663) |

Curated `_KNOWN_MODELS` at review time (9 entries): `claude-haiku-4-5-20251001`, `claude-opus-4-5-20251101`, `claude-opus-4-6`, `claude-opus-4-7`, `claude-opus-4-8`, `claude-opus-5`, `claude-sonnet-4-20250514`, `claude-sonnet-4-5-20250929`, `claude-sonnet-4-6`.

### Upstream prefix-matching (bears on #664 option (ii), #669)

```
_resolve_rates("claude-sonnet-5")    ->  UpstreamRates(input=3.0, output=15.0, cache_write_5m=3.75, cache_read=0.3)
_resolve_rates("claude-sonnet-5-9")  ->  UpstreamRates(input=3.0, output=15.0, cache_write_5m=3.75, cache_read=0.3)   # no such model
_resolve_rates("claude-opus-9-9")    ->  None
_resolve_rates("claude-totally-fake-7") -> None
_resolve_rates("claude-haiku-5-5")   ->  None
_resolve_rates("claude-haiku-5")     ->  None
```

A fabricated `claude-sonnet-5-9` inherits sonnet rates. "Upstream can price it" is therefore not evidence an id is real. Note `claude-sonnet-5` resolving to `3.0/15.0` differs from #664's body (`2.0/10.0`) at the same pin — most likely a family match rather than an exact record.

### Attributed cost for the 2026-11-11 window

Computed as `(input×rate_in + output×rate_out + cache_creation×rate_cw + cache_read×rate_cr) / 1e6` per `by_model` row, using the upstream rates above:

| project | reported | attributed |
|---|---|---|
| us-presidential-vote-analysis | $0.00 | ≈$1,005.67 |
| sportswear-esg-news-classifier | $0.00 | ≈$205.69 |
| github-pages | $0.00 | ≈$72.92 |
| claude-code-loop | $0.00 | ≈$72.14 |
| claude-code-sessions | $0.00 | ≈$6.95 |
| claude-hook-validator | $0.00 | $0.00 |
| **total** | **$0.00** | **≈$1,362.37** |

List-rate attribution only — not an out-of-pocket bill (subscription-billed), and subject to #646's caveat that `totalTokens` is a context proxy rather than spend.

## `STUCK_PATTERN` precision — 0 / 112

Counted across every retained generation in every slug:

| | count |
|---|---|
| Total `STUCK_PATTERN` signals | **112** |
| …with `len(input_summary) >= 200` (at the truncation cap) | **112** |
| …and with >1 distinct `result_summary` (forward progress) | **111** |
| …with a single distinct result (stall candidate) | **1** |
| Genuine stalls after inspection | **0** |

Distinct (project, agent, tool) combinations that fired:

| first seen | project | agent | tool | `len(input_summary)` | distinct results | calls |
|---|---|---|---|---|---|---|
| 2026-10-29 | sportswear-esg | `general-purpose` | Bash | 200 | 5 | 5 |
| 2026-11-02 | sportswear-esg | `pm` | Read | 200 | 5 | 5 |
| 2026-11-03 | claude-code-loop | `general-purpose` | Bash | 200 | 4 | 4 |
| 2026-11-05 | claude-code-loop | `pm` | Read | 200 | 5 | 5 |
| 2026-10-29 | vote-analysis | `general-purpose` | Bash | 200 | **1** | 5 |
| 2026-11-06 | vote-analysis | `ba` | Read | 200 | 5 | 5 |

The single-distinct-result case is the one stall candidate; inspection shows a deliberate poll, not a stall:

```
input_summary: {"command": "for i in $(seq 1 58); do grep -q '^EXIT=' /tmp/.../issue-326.mutation-run-exact.log
                && break; sleep 10; done; cat /tmp/.../issue-326.mutation-run-exact.log", "description"
result (all 5 identical): "(Bash completed with no output)"
is_error flags: [False, False, False, False, False]
```

Representative false positive, showing truncation and progress together:

```
agent: pm   message: repeated tool 'Read' with identical input 12 times without progress
input_summary (all 12 identical, 200 chars):
  {"file_path": "/home/<user>/.claude/projects/<slug>/<session-id>/tool-results/
   mcp-github-search_issues-1793838775794.txt", "of        <- severed before `offset`
result_summary, call 6:  '72\t      "body": "**Epic:** #271 · **Model:** Opus 5.5 ...'
result_summary, call 7:  '270\t      "state": "open", ...'
result_summary, call 8:  '1033\t      "body": "**Epic:** #271 · **Model:** Fable ...'
```

## `USER_CORRECTION` precision — 0 / 33

Across the 2026-11-10 and 2026-11-11 generations:

| count | classification | marker |
|---|---|---|
| 20 | injected: security-review workflow prompt | `You are a senior security engineer conducting a focused security review` / `Review this change for security vulnerabilities.` |
| 13 | injected: cross-session relay / subagent hand-back | `Another Claude session sent a message: <agent-message from="…">` / `[Subagent hand-back] The text below is the final report of a…` |
| **0** | plausibly human | — |

Per project in the 7-day window: vote-analysis 12, sportswear-esg 5 (the remainder from the 2026-11-10 generation).

Confirmed absent from `src/agentfluent/diagnostics/quality_signals.py`: any filter on message provenance. `grep -n "synthetic\|is_synthetic\|is_meta\|isMeta"` → no matches. The 3-tier guardrail documented at `quality_signals.py:28-44` gates on the *preceding assistant message* only.

## Synthesis transcription accuracy by parent model

Session counts as stated in each run's own summary table, versus the snapshot each run had just written:

| project | 2026-11-09 (opus-4-8) | 2026-11-10 (sonnet-4-6) | 2026-11-11 (haiku-4-5) | actual (11-11) |
|---|---|---|---|---|
| us-presidential-vote-analysis | ✓ | ✓ | **4** | **92** |
| sportswear-esg-news-classifier | ✓ | ✓ | **1** | **28** |
| claude-code-loop | ✓ | ✓ | 7 ✓ | 7 |
| github-pages | ✓ | ✓ | 17 ✓ | 17 |
| claude-code-sessions | ✓ | ✓ | 5 ✓ | 5 |
| agentfluent | ✓ | ✓ | 7 ✓ | 7 |
| claude-hook-validator | ✓ | ✓ | 1 ✓ | 1 |

The 2026-11-11 report additionally asserts both that `ba` and `pm` have "0 invocations" and that they are the agents stuck in retry loops.

### The repeated P0, by run

| date | parent model | leading recommendation |
|---|---|---|
| 2026-11-05 | haiku-4-5 | "Add progress-check gates and max-retry fallbacks to pm.md and general-purpose delegation prompts" |
| 2026-11-06 | opus-4-8 | "Add explicit exit conditions / retry bounds to `pm.md` and `ba.md` — highest leverage" |
| 2026-11-07 | sonnet-4-6 | "Fix `pm` and `ba` exit conditions — confirmed across 3 repos" |
| 2026-11-08 | haiku-4-5 | "Both agents need explicit 'if retried N times, report failure and exit' logic" |
| 2026-11-09 | opus-4-8 | "Two repeated, fixable agent-definition bugs — missing retry-exit conditions in `ba`/`pm`" |
| 2026-11-10 | sonnet-4-6 | "Agents looping on identical calls with no exit condition, hitting three separate projects" |
| 2026-11-11 | haiku-4-5 | "**P0** — Add 'no-progress exit' guards to `ba.md`/`pm.md` — Owner: Fred — Due: EOD 2026-11-12" |

Model-independent, which is what made it look credible.

## Permission failures — 3 in the latest window

| project | agent | tool | verdict |
|---|---|---|---|
| sportswear-esg | `general-purpose` | Bash | hook working as designed — blocked a read of a committed config template |
| sportswear-esg | `architect` | Grep | **over-block** — pattern merely referenced dotenv config during a config audit (#670) |
| vote-analysis | `general-purpose` | Bash | flagged `'blocked'` but the detail's `result_summary` carries successful content; worth a second look, not pursued in this review |

## Regeneration

Against a live state tree (`$XDG_STATE_HOME/agentfluent/dogfood/`):

```bash
# cron liveness + schedule
crontab -l | grep agentfluent-dogfood-runner
wc -l "$XDG_STATE_HOME/agentfluent/dogfood/runs.jsonl"
tail -3 "$XDG_STATE_HOME/agentfluent/dogfood/runs.jsonl"

# per-project totals, agent usage, signal histogram  (iterate snapshot-*.json per slug)
#   data.session_count, data.token_metrics.{total_cost,cache_efficiency,api_call_count,by_model}
#   data.agent_metrics.by_agent_type[*].invocation_count
#   data.diagnostics.signals[*].signal_type

# STUCK_PATTERN precision: for each signal of that type,
#   len(detail.tool_calls[0].input_summary) >= 200            -> truncated
#   len({tc.result_summary[:80] for tc in detail.tool_calls}) -> >1 means forward progress

# USER_CORRECTION provenance: classify signal.message by injected-shape markers above

# pricing coverage
uv run python -c "
from agentfluent.analytics.pricing import get_pricing, get_known_models
from agentfluent.analytics._genai_source import _resolve_rates
for m in ['claude-opus-5-5','claude-sonnet-5-5','claude-fable-5-1','claude-haiku-5-5','claude-sonnet-5']:
    print(m, get_pricing(m), _resolve_rates(m))
print(get_known_models())
"
```

Paths in quoted snapshot excerpts above were scrubbed (`<user>`, `<slug>`, `<session-id>`) per the runner README's absolute-path caution.
