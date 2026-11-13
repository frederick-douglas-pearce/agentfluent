# Agent Portfolio Analysis — cron-accumulation review

**Date:** 2026-11-12
**Author:** Claude (Opus 5) with Fred Pearce
**Source:** the retained dogfood-runner state tree — `$XDG_STATE_HOME/agentfluent/dogfood/<slug>/snapshot-*.json` (14 generations) plus `cron.log` and `runs.jsonl`. **Not** a fresh `analyze` invocation; see "How this run differs".
**Window reviewed:** 2026-11-04 → 2026-11-11 (the runner's rolling `7d`), with the 14-generation snapshot history used for trend and precision counts
**Sessions in the latest window:** 157 across 7 project slugs
**agentfluent version:** v0.11.0 (editable install at `8c53cee`)
**Baseline:** [`2026-07-03-v10-dogfood/analysis.md`](../2026-07-03-v10-dogfood/analysis.md)
**Derived evidence:** [`evidence.md`](evidence.md) (raw snapshots deliberately not committed — see that file's header)

## How this run differs from the previous five

Every prior dogfood analysis was a **release-cut ritual**: one `agentfluent analyze --project agentfluent` over the whole corpus, run deliberately by a human at a version boundary. This one is the first review of the **S0 cron's accumulated output** (#590) — daily runs at 12:30 with a `7d` rolling window, live since ~2026-07-07, 119 synthesis runs logged, and **unexamined for roughly four months**.

That changes what is being evaluated. The questions here are not "does v0.11 ship clean" but:

1. **Is the cron actually running, and is anything broken?** (Yes; and no.)
2. **Is the daily narrative it produces trustworthy?** (No — this is the finding of the run.)
3. **Are the newly-created agents (`ba`, `marketer`) being picked up and used?** (Yes, both.)
4. **What has been silently wrong for four months while nobody read the output?**

The answer to (4) is the reason this document exists.

## Headline — three silent failures, all live for weeks

| Surface | What the tool reported | What was true | Precision |
|---|---|---|---|
| **Cost** | `$0.0000` for 6 of 7 projects | ≈$1,363 attributed in one 7-day window | 5 of 10 model ids unpriced |
| **`STUCK_PATTERN`** | 112 `critical` "no progress" signals | 0 genuine stalls | **0 / 112** |
| **`USER_CORRECTION`** | 33 human corrections | 0 human corrections | **0 / 33** |
| **Synthesis** | dated P0s with owners; per-project session counts | FP-derived P0s; two counts off by 23× and 28× | — |

The cron's **deterministic gate was green throughout, and correctly so** — analysis genuinely succeeded every run, no parse exceptions, `warnings: []`, `tier3_degraded: false`. The anti-false-green split (#590) did exactly its job. What does not exist is any check on whether the *numbers and findings* are plausible before they are rendered as costs, severities and dated action items. That gap is the thread connecting all four rows.

Filed as epic **#672** (`epic:reporting-trust`), children #664, #666, #667, #668, #669, #670.

## The marquee: a zero-precision signal drove the same P0 for seven consecutive days

Every synthesis from 2026-11-05 through 2026-11-11 led with a variant of:

> **P0** — Add explicit "no-progress exit" guards to `ba.md` and `pm.md` agent prompts — **Owner:** Fred — **Due:** EOD 2026-11-12

It was stable across all three rotation parent models, which is what made it look credible. It is a false positive, and acting on it would have degraded two correctly-behaving agents.

### What the signals actually were

| project | agent | signal | reality |
|---|---|---|---|
| claude-code-loop | `pm` | `Read` identical **12×** | paging `tool-results/mcp-github-search_issues-*.txt`; results at lines 72 → 270 → 1033 |
| us-presidential-vote-analysis | `ba` | `Read` identical **7×** | paging `tool-results/mcp-github-get_pull_request_files-*.txt`; a different file per page |

Both agents were correctly paginating an oversized MCP result that Claude Code had spilled to `tool-results/` — the exact mechanism `CLAUDE.md` documents under `persistedOutputPath`.

### Root cause

```python
# src/agentfluent/diagnostics/trace_signals.py:393
is_stuck = seq.attempts >= _STUCK_MIN_ATTEMPTS and all(
    c.input_summary == calls[0].input_summary for c in calls[1:]
)
```

`input_summary` is capped at `INPUT_SUMMARY_MAX_CHARS = 200` (`traces/models.py:27`). A `Read` of a spill path serializes to:

```
{"file_path": "/home/<user>/.claude/projects/<slug>/<session-id>/tool-results/mcp-github-search_issues-1793838775794.txt", "of
                                                                                                                            ^ severed at 200
```

The path consumes the entire budget, so the string is cut **immediately before `offset`** — the only parameter that varies between pages. Every page compares byte-identical.

The adjacent comment states the assumption that fails:

> `STUCK` stays ungated: it requires identical inputs, which drifting paging never matches, and an identical no-error repeat is a real missing-exit-condition loop.

**#581 closed this exact hole for `RETRY_LOOP` and deliberately left `STUCK` ungated on the strength of that sentence.** The assumption holds for short inputs and fails for any input whose varying parameter sits past byte 200 — which is the normal shape for spill paths. A fix in one detector did not generalize to its sibling because the sibling's exemption was reasoned from a property it did not actually have.

### Corpus-wide precision

Across all 112 `STUCK_PATTERN` signals in the 14 retained generations:

- **112 / 112** have `input_summary` sitting exactly at the 200-char cap.
- **111 / 112** show differing `result_summary` values — demonstrable forward progress.
- The one remaining signal has identical results but is a deliberate poll, not a stall:
  `for i in $(seq 1 58); do grep -q '^EXIT=' <logfile> && break; sleep 10; done`, repeated while waiting on a background run.

Effective precision: **0 / 112**. Filed as **#666** (`priority:high`).

### Blast radius beyond the signal

- **#600** (`prompt-fix: enforce stop/fallback …`) was justified partly on these fires. Its body already hedged — *"partly the #580 self-referential FP, partly a genuine gap on the unrelated `us-presidential-vote-analysis` corpus."* The unrelated-corpus half is also an FP. Re-scoped: `pm.md` dropped, `architect` tool-error core retained.
- **#415** proposes a fork-loop hint built *on top of* `STUCK_PATTERN`. Should not start until #666 lands.
- Any prompt change made on this evidence would have been unmeasurable anyway — the detector would keep firing regardless of what the prompt said.

## `USER_CORRECTION` — 0 / 33, and a recurrence of closed work

All 33 fires in the 2026-11-10 and 2026-11-11 snapshots are **machine-injected user-role messages**:

| count | injected shape | origin |
|---|---|---|
| 20 | `You are a senior security engineer conducting a focused security review…` | the security-review workflow prompt |
| 13 | `Another Claude session sent a message: <agent-message from="…">` / `[Subagent hand-back] …` | cross-session relay, subagent hand-back |
| **0** | anything attributable to a human | — |

The 3-tier guardrail in `quality_signals.py` gates on *what the preceding assistant message did* (write-tool present, question-only suppression) but never asks whether the user message came from a human. Long injected prompts reliably trip the soft tiers, because `_NEGATION_PATTERNS` / `_REDIRECTION_PATTERNS` / `_UNDO_PATTERNS` match common words (`\bno,?\s`, `\brevert\b`, `\bundo\b`) that appear incidentally in any long block of text.

**This is a recurrence, not a new bug.** #321 ("patterns matching system-injected text") and #330 ("fires on Claude Code skill-metadata text") both closed this class for the injection shapes known at the time. The shapes above are newer — the security-review workflow prompt, and the relay format introduced by multi-session work. Three rounds of pattern-level exclusion have not generalized, which argues for a structural provenance filter.

Note the existing `synthetic` concept does **not** cover it: `synthetic_message_count` counts `<synthetic>`-model **assistant** ghosts (`analytics/pipeline.py:127`). There is no classifier for programmatic **user**-role messages; one has to be built. Filed as **#667**.

The synthesis built a recurring "design decisions delegated before architecture review — missed pre-implementation gates" narrative on these counts, emitted as dated P2/P3 items. The underlying count was zero.

## Cost — the pricing allow-list zeroed most of the fleet

Six of seven projects reported `$0.0000`. Measured through the production path at the pinned `genai-prices==0.1.4`:

| model id | corpus rows | `get_pricing` | upstream | verdict |
|---|---|---|---|---|
| `claude-opus-5-5` | 245 | `None` → $0 | `in=5.0, out=25.0` | **silently zeroed** |
| `claude-sonnet-5-5` | 50 | `None` → $0 | `in=3.0, out=15.0` | **silently zeroed** |
| `claude-fable-5-1` | 34 | `None` → $0 | `in=10.0, out=50.0` | **silently zeroed** |
| `claude-haiku-5-5` | 12 | `None` → $0 | **`None`** | **needs a `_RESIDUAL`** |
| `claude-sonnet-5` | 8 | `None` → $0 | `in=3.0, out=15.0` | **silently zeroed** |
| `claude-opus-5` | 1 | OK | `in=5.0, out=25.0` | priced by #663 |
| `claude-opus-4-8` / `-4-7` / `claude-sonnet-4-6` / `claude-haiku-4-5-20251001` | 67 | OK | — | priced |

**Five of ten ids present resolve to `$0.00`; four are priceable upstream today**, blocked solely by the `_KNOWN_MODELS` allow-list consulted *before* upstream (`pricing.py:174-177`). This is #664, already filed from the #661 architect review — but filed when the instances were two stragglers (`claude-sonnet-5`, `claude-fable-5`). They are now the dominant models in the corpus. Priority raised `medium` → `high`; it is the first issue in the epic queue.

Attributing the 2026-11-11 window at upstream rates:

| project | reported | attributed |
|---|---|---|
| us-presidential-vote-analysis | $0.00 | ≈$1,005.67 |
| sportswear-esg-news-classifier | $0.00 | ≈$205.69 |
| github-pages | $0.00 | ≈$72.92 |
| claude-code-loop | $0.00 | ≈$72.14 |
| claude-code-sessions | $0.00 | ≈$6.95 |
| **total, one 7-day window** | **$0.00** | **≈$1,363** |

List-rate attribution, not an out-of-pocket bill — this work is subscription-billed, and #646 separately tracks that `totalTokens` is a context proxy rather than spend. The point is that the cost surface reported zero for essentially the whole fleet, `warnings[]` was empty, and the miss is logged at **DEBUG only**.

### The false-green it produced

The 2026-11-11 synthesis read the zeros as a **strength**:

> **Cache leverage exceptional:** 95.6% reuse on claude-code-loop, 94.5% on github-pages; **zero marginal API cost** despite high session volume

For a project with 3,169 API calls. A plausibility assertion as crude as "`api_call_count > 0` and `total_cost == 0` is impossible" would have caught this independently of the pricing fix. That is now an AC on **#668**.

### A trap for #664's option (ii)

Upstream resolution **prefix-matches within a model family**, so "upstream can price it" is not evidence the id exists:

```
_resolve_rates("claude-sonnet-5-9")  ->  UpstreamRates(input=3.0, output=15.0, ...)   # no such model
_resolve_rates("claude-opus-9-9")    ->  None
_resolve_rates("claude-haiku-5-5")   ->  None
```

#664's option (ii) — "price anything upstream covers, minus a deny-list" — adopted naively would price typos and hallucinated ids at plausible family rates, trading a loud `$0` for a quiet wrong number. Worse for a cost tool. If (ii) wins it needs an exact-match assertion, or a WARNING whenever the match is inexact. Recorded on #664 and #669.

This likely also explains a discrepancy: #664's body lists `claude-sonnet-5` at `input=2.0, output=10.0`; it resolves to `3.0/15.0` at the same pin today. A family match rather than an exact record would account for it — worth confirming, since AC1's golden-rate locks depend on which it is.

## Synthesis reliability — the first substantive #636 rotation finding

The main-model rotation (#636) exists so the corpus says something about how the diagnostics behave across model tiers. It has now produced its first real result, and it is about the *synthesis*, not the diagnostics.

The 2026-11-11 run (`main_model: claude-haiku-4-5`) reported in its own summary table:

| project | synthesis said | snapshot held |
|---|---|---|
| us-presidential-vote-analysis | 4 sessions | **92** |
| sportswear-esg-news-classifier | 1 session | **28** |
| claude-code-loop | 7 | 7 ✓ |
| github-pages | 17 | 17 ✓ |
| claude-code-sessions | 5 | 5 ✓ |
| agentfluent | 7 | 7 ✓ |
| claude-hook-validator | 1 | 1 ✓ |

The two it got wrong are the **two highest-volume repos in the corpus**, understated 23× and 28× and presented as near-idle. The 2026-11-09 (`claude-opus-4-8`) and 2026-11-10 (`claude-sonnet-4-6`) runs transcribed their counts correctly.

The same report also contains a self-contradiction no check caught: it claims six agents "consistently record 0 invocations" *including* `ba` and `pm`, and in the same breath that `ba` and `pm` are the agents stuck in retry loops.

**Conclusion:** per-project figures must be rendered mechanically from the snapshot rather than transcribed by the model, and a finding sourced from a low-precision signal must not be renderable as a P0 with an owner and a due date. Both are ACs on **#668**. The deterministic gate stays LLM-free; this adds deterministic scaffolding *around* the narrative.

## Agent configuration — the actual answer to "are my new agents working?"

The `agentfluent` slug's own snapshot reports 6 agents unused and the `github` MCP server unused. **That verdict is an artifact.** All 7 sessions in its window are the dogfood runner's *own* SDK sessions (`session_kind: sdk`, `entrypoint: sdk-py`, sole tool `Agent`, sole agent `snapshot-summarizer`) — no interactive work happened in the repo during the window, so the runner was measuring itself.

Fleet-wide over the same window:

| agent | invocations | where |
|---|---|---|
| `general-purpose` | 151 | vote-analysis 93, esg 40, github-pages 14, loop 3, sessions 1 |
| `architect` | 46 | vote-analysis 33, esg 6, github-pages 6, loop 1 |
| `ba` | 41 | vote-analysis 41 |
| `pm` | 8 | vote-analysis 7, loop 1 |
| `marketer` | 4 | loop 3, vote-analysis 1 |

Both agents created since the last review are scanned **and** actively invoked: `ba` (created 2026-10-11) 41 times, `marketer` (updated 2026-10-24) 4 times. Only `anthropic-research` and `candidate-verifier` are genuinely uninvoked anywhere — consistent with the research scout's migration to `claude-code-sessions` (#451, closed).

**Open question, not yet filed:** the self-referential window makes the `agentfluent` slug's config assessment misleading on any day with no interactive work. Excluding `session_kind: sdk` dogfood sessions from that slug, or assessing agent usage over a longer window than `7d`, would fix it. Noted at the end of #671.

## Findings that survived verification

Three, after discarding the FP classes above.

### `architect` rediscovers the 256 KB Read ceiling by hitting it

```
architect: retried tool 'Read' 2 times with different parameter shapes before succeeding.
  First attempt failed with: 'File content (424.5KB) exceeds maximum allowed size (256KB).
  Use offset and limit parameters to read specific portions...'
```

Two independent projects in one window — 424.5 KB in `us-presidential-vote-analysis`, 367.6 KB in `sportswear-esg-news-classifier`. Genuine true positive (`is_error=true` on the leading attempt). Routed into the **#600** re-scope, which already owns `architect.md` prompt work. Cheap to prevent: size a file before reading it whole.

### `general-purpose` fights worktree isolation — 5 to 17 Bash retries

```
general-purpose: retried tool 'Bash' 17 times with different parameter shapes before succeeding.
  First attempt failed with: 'This agent is isolated in the worktree
  /home/.../us_presidential_vote_analysis/.claude/worktrees/agent-<id>'
```

Recurs at 5, 6, 6, 8, 14 and 17 retries across `us-presidential-vote-analysis` sessions, plus one Bash `permission_failure`. Real friction and real token cost, but `general-purpose` is built-in (no prompt file) and the cause is that repo's worktree + permission setup. Recorded in **#671**; the transferable lesson is that worktree-isolated delegation needs the isolation boundary stated in the delegating prompt, or the agent rediscovers it a dozen times by trial and error.

### The secrets hook over-blocks on any mention of a credential filename

`architect` was denied a `Grep` whose *pattern* merely referenced dotenv config while auditing config loading — reading source files, not secrets. `bash_command_is_blocked` matches `CREDENTIAL_TOKEN_REGEX` against the **whole command string**, so any command that names a credential file is denied regardless of what it does.

Reproduced twice in **this** repo while writing this review: reading the hook's own source was denied (the `sed` command quoted the pattern the hook defines), and so was the heredoc writing #670's issue text. Both had to go through the `Write` tool.

The guard is deliberately fail-closed and that posture is right, but this particular check is close to orthogonal to the risk: the genuine leak path is a recursive content search whose *scope* contains a secret file — which never mentions the filename and is therefore unguarded. Filed as **#670** (`.claude/**` only, `chore:` scope).

## Issues filed and re-scoped

**Epic #672** — `epic:reporting-trust`, intended as a dev-loop `BACKLOG_SOURCE`.

| # | title | priority |
|---|---|---|
| #664 | pricing gate silently zeroes priceable models — **work first** | high (raised) |
| #666 | `STUCK_PATTERN` paginated-read false positives | high |
| #667 | `USER_CORRECTION` injected-message false positives | medium |
| #668 | dogfood synthesis plausibility gate | medium |
| #669 | scheduled model-tier/version watcher for pricing coverage | medium |
| #670 | `block_secret_reads` over-blocking | low |

**Deliberately outside the queue**, each carrying an explicit keep-out comment so it cannot drift in:

- **#600** — re-scoped. `pm.md` dropped (FP), `architect.md` retained and narrowed. User-global → no repo artifact (#559).
- **#671** — triage record for the non-repo-bound findings.

**Commented, not re-opened:** #82, whose re-scoped overlay-lever drift detector is a different axis from #669, and whose stated blocker **#80 is closed** — its `pricing.json` scraper deliverables predate genai-prices-as-SSOT (D045) and should be pruned when picked up.

## What this review proved

1. **The cron works and the gate works.** 119 runs, daily, no analysis failures, no stale state. The anti-false-green split held: the gate never went green on broken analysis. The problem was never in the part that was engineered for correctness.
2. **A deterministic gate on *process* does not protect *findings*.** Exit codes certify that analysis ran, not that what it found is real. Both zero-precision signals were `critical`/`warning` severity and both were faithfully promoted to prioritized, owner-assigned, dated recommendations. Nothing between a 0%-precision detector and a P0 with a due date.
3. **Precision fixes do not generalize by themselves.** `STUCK_PATTERN` was left exposed by #581 on an explicit, reasoned assumption that was false. `USER_CORRECTION` had the same class closed twice (#321, #330) and regressed on new injection shapes. Both argue for structural invariants over pattern-level patches — consistent with the standing concern about overfitting to a single corpus's vocabulary.
4. **Unread automation decays silently.** Four months of daily reports, each internally plausible, compounding three wrong conclusions. The output was never wrong in a way that broke anything — it was wrong in a way that read as insight. That is the failure mode worth designing against, and it is what #668 exists for.
5. **Cost is the least safe place to fail quietly.** `$0.00` is indistinguishable from "this was free", and it actively generated a congratulatory narrative about efficiency. For a tool whose value proposition is dollar figures, under-pricing must be loud — the inverse of the fail-safe direction used for detection signals.

**Next:** #664, then #666. No point re-reading AgentFluent's own output until #666 lands — until then every run will keep reporting the same false positive.
