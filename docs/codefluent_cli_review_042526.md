# AgentFluent CLI Review — codefluent integration, 2026-04-25

**Reviewer:** Claude (Opus 4.7, 1M context) running as the codefluent project's collaborator
**AgentFluent version:** 0.3.0 (built from `74833388` on 2026-04-25)
**Context:** Installed via `uv tool install --from git+https://github.com/frederick-douglas-pearce/agentfluent agentfluent`, then ran `list`, `analyze`, and `config-check` against the codefluent project (10 sessions, 96 subagent invocations, $574 in API spend).

This is end-user feedback from the *first* serious use of the tool. It is structured as compliments, then issues, then feature gaps, then a prioritized punch list. Every gripe is paired with a suggestion. Every claim cites the actual command or output that prompted it.

---

## What works really well

**The 30-second time-to-insight is the magic.** From `uv tool install` to a 2.8 MB JSON of structured recommendations took under a minute, with no API key prompt, no auth dance, no config file. That speed is the product. Protect it.

**Severity + observation/reason/action is the right schema.** Reading `aggregated_recommendations` once was enough to understand both the data and the framework. The split between *what was observed*, *why it matters*, and *what to change* maps directly to how a human reasons about a finding. codefluent has filed an issue (#277) to adopt this exact schema for its own recommendation surface.

**Built-in vs. custom agent awareness shows real product judgment.** When agentfluent flagged 15 `tool_error_sequence` events on `general-purpose`, the recommendation didn't say "edit your prompt" — it said *"Built-in agent — prompt is not user-editable. Consider: (a) a custom wrapper subagent that narrows the task scope, (b) tightening the delegation prompt passed to this agent, or (c) rerouting this task to a different agent."* That's three actionable paths matched to the actual constraint. Most analytics tools would emit the raw signal and stop.

**Aggregation is excellent.** 128 raw recommendations collapsed cleanly into 24 distinct rows, with `count` and `signal_types` preserved. Dedup by `(agent_type, target, signal_types)` is the right grouping key.

**`config-check` static rubric is well-calibrated.** `architect` 100/100 and `pm` 95/100 (one INFO about error handling) felt accurate when I read the prompts. The static analysis caught what was statically checkable; the runtime analysis caught what wasn't (pm's token outlier on opus-4-6). Different tools doing different jobs.

**Cache-efficiency in `token_metrics` is right at the top.** `cache_efficiency: 98.4` is exactly the single number a reader cares about; surfacing it without hunting is good design.

**Cost breakdown by model.** `by_model` showing opus-4-6 ($537) vs opus-4-7 ($37) made it instantly clear which model dominated this project's spend.

**Examples in every subcommand's `--help`.** Big quality-of-life win. The `agentfluent analyze --project codefluent --format json | jq '.data.token_metrics.total_cost'` example told me both the right output flag and the right jq path in one line.

---

## CLI ergonomics — things that actively bit me

### `--json` is rejected; only `--format json` works

Both my first attempts failed identically:

```
$ agentfluent analyze --project codefluent --json > /tmp/af-analyze.json
No such option: --json Did you mean --session?
$ agentfluent config-check --scope user --json > /tmp/af-config.json
No such option: --json Did you mean --format?
```

This is the single biggest paper-cut. Suggestions, in order of preference:

1. Accept both — `--json` as an alias for `--format json`. Two extra lines per typer command.
2. If you keep `--format` only, mention it in the *short* help line (`--help` shows it but the rejection message could say "use --format json" rather than "did you mean --session?")
3. Update README example snippets to lead with `--format json` so muscle memory forms correctly.

### Parse warning printed mid-table on `list`

```
$ agentfluent list --project codefluent
Malformed JSON at a0e5f523-1191-45ca-97ba-3b40c64f57c0.jsonl:662
                             Sessions — codefluent
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳…
```

The warning lands on stdout (or interleaves with stderr), the exit code is 0, and the table renders fine. Three small fixes:

- Prefix as `WARNING:` so it's clearly non-fatal.
- Send to stderr unambiguously, before the table.
- Optionally include line context: which session UUID, what was the line content (truncated). Right now I know which file, but not whether to investigate or ignore.

### Empty `agent_type` for cross-cutting findings looks like a bug

The MCP audit row came through as:

```json
{
  "agent_type": "",
  "target": "mcp",
  "severity": "warning",
  "signal_types": ["mcp_missing_server"],
  ...
}
```

When I grouped output by agent, the empty string showed up as a stray tab. Suggestions: use `null`, or a sentinel like `"<global>"` or `"(cross-cutting)"`. The current empty-string violates "type stability" — readers expect `agent_type` to always be a meaningful string.

### `list` table omits cost per session

The most actionable column for "which session should I dig into" is missing. Adding `Cost` (estimated, with explicit "estimate" footnote if needed) right next to `Subagents` would let users prioritize without having to run `analyze` first.

---

## Output / data fidelity

### `invocation_id: null` makes drill-down impossible

When I tried to investigate the 15 critical `tool_error_sequence` events for `general-purpose`, every contributing recommendation had:

```json
{
  "invocation_id": null,
  "observation": "Subagent 'general-purpose' had 3 consecutive tool errors."
}
```

There's no way to map a recommendation back to a specific session/invocation. This is the highest-impact data fidelity gap — it limits the tool to *aggregate* insight rather than *example-driven* insight. Either populate `invocation_id` or include `session_path` + `agent_id` so a user can grep into the trace and see what actually happened.

### Token-outlier reference points are ambiguous

> "Agent 'pm' has 32,071 tokens/tool_use, 2.5x above the 12,587 mean."

Across what population? All custom agents? All agents observed in this run? The agent type's own historical mean? One extra word would clarify ("2.5× the per-run mean across all agent types" or "2.5× the cohort mean"). Without it, the multiplier is hard to act on.

### `delegation_suggestions` returned `[]` with no explanation

```bash
$ jq '.data.diagnostics.delegation_suggestions | length' /tmp/af-analyze.json
0
```

I don't know whether this means (a) the clustering didn't find candidates, (b) `--min-cluster-size 5` wasn't met, or (c) `agentfluent[clustering]` wasn't installed. A one-line `notes` or `reason` field on empty results would solve this without breaking the schema.

### `representative_message` vs. `contributing_recommendations[0].message` duplication

Some entries had both. They were always identical (or close to it) in the data I saw. Consider documenting which is canonical, or deduplicating. It cost me a minute of "is one richer than the other?"

---

## Feature gaps I'd prioritize

These are *additions* I felt missing, not bugs.

### 1. Per-session diagnostics view

Right now diagnostics are aggregated across the whole project. I often want to ask "what went wrong in *this one session* that I'm debugging?" — `agentfluent analyze --project codefluent --session <uuid> --diagnostics --format json` runs but the aggregated_recommendations roll all sessions together. A `--scope session` flag (or per-session `diagnostics` subobject under `sessions[]`) would let users do post-mortems.

### 2. Markdown report export

JSON is great for pipelines; tables are great for terminals; but for sharing findings with humans, Markdown is the universal currency. `agentfluent report --project codefluent > report.md` that emits the analyze output as a structured Markdown doc (severity sections, tables) would be hugely valuable. I just hand-wrote one of these for codefluent (issue #278) — would have saved me 20 minutes.

### 3. `agentfluent diff` is in your roadmap — raise its priority

Users will run `analyze` repeatedly. Showing whether `general-purpose retry_loop` count went from 34 → 12 between runs is the difference between "interesting one-shot tool" and "habit-forming tool." This is the single highest-ROI feature I'd push forward from the Future section.

### 4. "Top 3 most actionable" / TL;DR mode

The 24 aggregated rows × 110 occurrences are a lot to absorb. A `--top N` flag (or default summary header in non-`--quiet` mode) showing "Your three most pressing fixes are…" would help a first-time user not drown. The ranking signal could be `severity × count × is_actionable` (where `is_actionable` discounts built-in agents whose direct fix is impossible).

### 5. Cost-per-invocation field on each agent

```json
"by_agent_type": {
  "pm": {
    "invocation_count": 11,
    "total_tokens": 388408,
    "total_duration_ms": 1323299
    // missing: "total_cost_usd": ..., "avg_cost_per_invocation": ...
  }
}
```

Cost is the lingua franca. Surface it directly so users don't have to recompute from `total_tokens × by_model_pricing`.

### 6. Inline trace excerpts on critical findings

For a critical `stuck_pattern` observation like "repeated tool 'Bash' with identical input 4 times without progress," include the actual Bash command (truncated). One line of evidence beats five lines of abstract description.

### 7. Severity filter at the CLI layer

Right now I `jq '.data.diagnostics.aggregated_recommendations[] | select(.severity == "critical")'`. A native `--severity critical` (or `--min-severity warning`) would meet users where they live.

---

## Smaller polish

- **`--diagnostics` not on by default for `analyze`.** I almost missed it. The whole point of running analyze is to get diagnostics. Either flip the default and add `--no-diagnostics`, or rename the current default to a separate `analyze-tokens` subcommand.
- **`agentfluent list` doesn't accept `--format json`.** It only renders a table. Programmatic users need the same JSON discipline as the other subcommands.
- **README installation footprint.** The `agentfluent[clustering]` extra is mentioned but the consequence of not installing it (delegation suggestions silently empty) isn't. A README note: "If you want delegation clustering, install with `[clustering]`. Without it, `delegation_suggestions` will always return `[]`." would prevent confusion.
- **The `--claude-config-dir` global flag is well-thought-out** but easy to miss. Consider mentioning it once in each subcommand's `--help` epilogue when the Claude config path is non-default.

---

## Positioning and docs

The README's framing — *"If you write your own prompts each session, use CodeFluent. If your prompts live in `ClaudeAgentOptions`, `AgentDefinition`, or `.claude/agents/*.md` files, use AgentFluent."* — is excellent. It's clear, it's honest, and it acknowledges the sibling project without ceding ground. Keep it word-for-word.

The "no API key required" framing is real and worth keeping prominent at the top. It's the differentiator from anything else in this space.

The hooks adoption (`.claude/hooks/block_secret_reads.py`) is a great instance of cross-project hygiene — codefluent literally pulled them over and then improved them (we added `BLOCKED_PATH_SUFFIXES` for path-scoped blocks). Worth a periodic re-sync in both directions.

---

## Prioritized punch list

| Priority | Item | Type | Effort |
|---|---|---|---|
| P0 | Accept `--json` as alias for `--format json` | Bug | XS |
| P0 | Populate `invocation_id` on contributing recommendations | Data fidelity | S |
| P1 | Markdown report export (`agentfluent report`) | Feature | M |
| P1 | `agentfluent diff` (raise from Future) | Feature | M |
| P1 | Add `total_cost_usd` to `by_agent_type` | Output | XS |
| P1 | Per-session diagnostics scope | Feature | M |
| P2 | TL;DR / `--top N` mode | UX | S |
| P2 | Inline trace excerpts on critical findings | Output | S |
| P2 | `list` table: add cost column + JSON support | UX | S |
| P2 | `--severity critical` filter | UX | XS |
| P2 | Clarify token-outlier reference population | Docs | XS |
| P3 | Disambiguate parse warning on `list` | UX polish | XS |
| P3 | `agent_type: ""` → null or sentinel | Schema | XS |
| P3 | Document `[clustering]` extra consequence | Docs | XS |
| P3 | Reconcile `representative_message` vs. `contributing_recommendations[0].message` | Schema | XS |

---

## Closing

This is a serious tool. The schema design, the built-in/custom agent awareness, and the heuristic-only architecture are all correct calls. The biggest gaps are around (a) bridging from aggregate finding → concrete invocation, (b) presentation polish (Markdown export, TL;DR, inline evidence), and (c) one CLI alias that would have saved both my analyze runs from a needless retry.

Built in 30 days, shipping v0.3.0 with this much depth, with secret-handling hooks better than half the codebases I've seen — well done. Looking forward to v0.4.

— *Claude (codefluent's collaborator), 2026-04-25*
