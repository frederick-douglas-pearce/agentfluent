# Agent SDK dogfood-runner (S0 / #590)

A **repo-tracked Agent SDK agent** that runs AgentFluent's own **dogfood analysis**
over a bounded rolling window of the local corpus, driving the real `agentfluent`
CLI. It automates the manual post-release dogfood ritual and, because it runs as an
SDK agent, each run's own sessions feed the next cycle's corpus (self-reinforcing).

It doubles as the **canonical example Agent SDK agent** for this project — a
parent `query()` that fans out programmatically-defined subagents (`AgentDefinition`)
with per-agent model routing. If you want to see how AgentFluent expects an SDK
agent to be structured, read [`runner.py`](runner.py).

> Not part of the published `agentfluent` package — nothing under `tools/` ships in
> the PyPI wheel. This is maintainer/dogfood infrastructure (`chore:` scope).

## What it does each run

1. **Enumerate** active project-slugs (`agentfluent list --json`).
2. **Analyze** each slug over a **bounded rolling window** (`analyze --since <window>`),
   NOT the whole corpus — so diffs stay relevant and a spike against the recent
   baseline is an early "we just introduced a problem" warning (AgentFluent's own
   regression-detection value, pointed inward).
3. **Diff** each window against the previous run's snapshot (`agentfluent diff`),
   dogfooding the regression surface.
4. **Gate** on the real CLI exit codes (deterministic, no LLM in the loop).
5. **Synthesize** a narrative report: a parent-Opus `query()` fans out one
   Haiku subagent per slug. Each subagent runs **`agentfluent report`** on its
   snapshot (the tool's own deterministic interpreter of the JSON schema) and
   condenses the resulting Markdown — it never parses raw JSON itself, so it needs
   no data-dictionary/GLOSSARY context and can't misread AgentFluent-specific signal
   jargon. This dogfoods `report` too, and the parent stitches the per-slug
   condensations into a cross-project report. Best-effort — it never affects the gate.

### The anti-false-green split (architect review, #590)

The **pass/fail gate is deterministic** — [`cli_runner.py`](cli_runner.py) shells
out to the CLI in plain Python and reads `returncode` directly. An LLM is never on
the correctness path (it could transcribe an exit code wrong or silently
retry-and-report success). The exit-code mapping is **code-aware**, not `== 0`:

| CLI | exit | meaning | gate |
|-----|------|---------|------|
| `analyze` | 0 | ok, data present | green |
| `analyze` | 2 | NO_DATA — empty window for that slug | green (benign) |
| `analyze` | 1 | user/analysis error | **RED** |
| `diff` | 0 | no regression | green |
| `diff` | 3 | regression detected | surfaced as a finding (not a runner error) |

The process exit code reflects **analysis health only**: `1` if any analyze
errored (alert the maintainer the tool is broken), `0` otherwise. A detected
regression is a *successful* dogfood that found something — it is reported, not
treated as a runner failure.

The Haiku subagents synthesize the narrative only. Running parent-Opus /
child-Haiku is deliberate: it emits the model-divergence and nested-trace bytes
that the S5 trace linker (#595) and #112's model-routing signal consume, so the
runner dogfoods the exact v0.11 surfaces.

## Running it

```bash
# Full run (gate + SDK synthesis) — needs the `research` group + local Claude auth:
uv run --group research python -m tools.dogfood_runner.runner

# Gate only (no SDK; works without the research group):
uv run python -m tools.dogfood_runner.runner --no-synthesis

# Options:
#   --window 7d        rolling window passed to `analyze --since` (default 7d)
#   --fail-on critical regression severity threshold for `diff` (default warning)
#   --retention 14     snapshots kept per slug (default 14)
```

Run it **from the repo root** and via `-m` (not by file path) so the `tools.*`
package imports resolve.

## Snapshots

Each run writes one `analyze --json` snapshot per slug under
`$XDG_STATE_HOME/agentfluent/dogfood/<slug>/` (falling back to
`~/.local/state/...`) — a **user-global, out-of-tree** location. They are never
committed: snapshots carry absolute paths and project data, and an in-tree state
dir is one `.gitignore` slip from leaking that to a public repo. The next run diffs
the current window against the most recent snapshot.

## Scheduling (cron)

The DoD requires this to be **scheduled from day one**. It runs on **local cron**,
not a cloud/schedule-skill routine, for two reasons: the corpus is local
(`~/.claude/projects/`), which a cloud routine cannot see; and cron runs
unattended whenever the machine is on, with no live Claude session required. The
runner itself is scheduler-agnostic (a plain `uv run` entrypoint), so the
mechanism can change later without touching the runner. See `decisions.md` (D050).

```bash
# Install / refresh the daily entry (default 12:30 local — a midday time is more
# likely to fire than 3am; cron does not back-fill, and the overlapping window
# self-heals a missed day):
tools/dogfood_runner/install-cron.sh

# Custom schedule / uninstall:
DOGFOOD_CRON="0 13 * * *" tools/dogfood_runner/install-cron.sh
tools/dogfood_runner/install-cron.sh --uninstall
```

The default window is **7d** (see `DEFAULT_WINDOW`): robust to a sporadically-worked
corpus and to missed cron days (cron only fires when the machine is on, and the
window-over-window diff needs consecutive runs to overlap — a 7d window tolerates a
~6-day gap). Override with `DOGFOOD_WINDOW=5d tools/dogfood_runner/install-cron.sh`.

The cron entry bakes a **minimal** `PATH` — only the dirs where `uv`/`node`/`claude`
resolve at install time, plus the base dirs (baking the full interactive `PATH`
overflows crontab's line-length limit). The **narrative synthesis** additionally
needs local Claude auth (`ANTHROPIC_API_KEY`, or Claude Code credentials under
`~/.claude`) present in the cron environment — without it the deterministic gate
still runs and reports; only synthesis is skipped (logged to `cron.log`).

An event-based trigger (on merge to `main`) is a noted future enhancement, not part
of S0.

### Notes / limitations

- **Cross-version snapshots aren't diffed.** A release that bumps the `analyze
  --json` envelope version makes the next run's baseline incompatible; the runner
  detects the version mismatch and skips the diff (like a first run) rather than
  letting `diff`'s exit 1 spuriously red the gate. The window re-establishes on the
  following run.
- **Keep `--window` stable across runs.** Snapshots aren't keyed by window, so
  changing `--window` between runs diffs mismatched windows (a misleading but not
  red comparison). The cron uses a fixed window; only ad-hoc runs risk this.
- **Zero slugs is surfaced, not red.** An empty corpus is legitimate, so it stays
  exit 0 — but the report prints a `WARNING` so a misconfigured corpus path under
  cron is visible in `cron.log` rather than passing as a clean run.

## Layout

| File | Role |
|------|------|
| [`cli_runner.py`](cli_runner.py) | Deterministic, SDK-free core: CLI adapter, code-aware gate, snapshot orchestration. Unit-tested. |
| [`paths.py`](paths.py) | Snapshot state-dir + rotation. Unit-tested. |
| [`runner.py`](runner.py) | Entrypoint: gate + lazy-imported SDK narrative synthesis. |
| [`install-cron.sh`](install-cron.sh) | Idempotent local-cron installer. |

Tests: `tests/unit/tools/`.
