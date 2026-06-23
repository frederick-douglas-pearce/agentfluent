# Agent SDK probe (#518)

Throwaway research scaffolding for the **Agent SDK session data discovery** epic
([#517](https://github.com/frederick-douglas-pearce/agentfluent/issues/517)).
This is **not** part of the published `agentfluent` package.

## What this is

Three throwaway Claude Agent SDK scripts:

- **`probe.py`** (#518) -- a ~15-line hello-world: spins up an agent, makes
  **exactly one** tool call (a single `Read` of a synthetic, secret-free
  `fixture.txt`), and exits. Answered where the SDK writes sessions and what an
  SDK session looks like.
- **`agent.py`** (#522) -- the representative **data-generation** agent, chosen
  purely to maximize the JSONL **format surface** later stories inspect. Its
  *answer* is irrelevant; we grade the bytes it emits. Three variants:

  | Variant | What it exercises | Run |
  |---------|-------------------|-----|
  | `flat` | multi-tool (Glob/Grep/Read/Bash), multi-turn, one natural `is_error: true` | `uv run --group research python research/agent-sdk-probe/agent.py flat` |
  | `subagent` | forces a delegation -> `<id>/subagents/agent-*.jsonl` + `isSidechain` + `agentId` linkage | `... agent.py subagent` |
  | `large` | oversized tool result -> `<id>/tool-results/` spill subfolder | `... agent.py large` |
  | `nested` | `main -> delegator -> leaf` (middle agent granted the `Agent` tool) -> validates the **flat** multi-level `subagents/` layout + by-data linkage (#530) | `... agent.py nested` |

  Each run prints a human `RESULT ...` line and a machine-readable `RESULT_JSON
  {...}` line so `run_matrix.py` can build the config->file manifest mechanically.
  An optional `[model] [subagent_model]` (e.g. `agent.py subagent
  claude-sonnet-4-6 claude-haiku-4-5-20251001`) threads the model into both the
  main agent and the subagent, so the child's `toolUseResult.resolvedModel` is a
  recorded, controllable input. `agent.py` is a **pure** SDK agent
  (`setting_sources=[]`, no MCP, web tools disallowed) so its corpus is trivially
  anonymizable. The synthetic targets live in `sampledata/` (committed;
  secret-free).

- **`run_matrix.py`** (#519) -- the corpus matrix runner. Drives `agent.py` across
  a 3-run matrix (one axis toggled per run), copies each run's raw session
  file(s) into the gitignored `corpus/`, and writes `corpus/manifest.json` -- the
  config->file index #520 consumes.

  | Run | variant | main / subagent model | isolates |
  |-----|---------|-----------------------|----------|
  | a | `flat` | haiku | full tool_use / error surface |
  | b | `subagent` | **sonnet / haiku** | delegation + parent!=child model |
  | c | `flat` | sonnet | model recording (2nd model) |

  Run (b) is a deliberate model-divergence sample: the parent runs sonnet, the
  child runs haiku, and `toolUseResult.resolvedModel` reports the **child** model
  -- the high-value artifact for #112 model-routing. Each manifest entry carries
  the config snapshot, SDK/CLI versions, the runtime `init` event, and per-file
  `sha256` + a `contains_abs_paths` scrub flag for #521. A completeness
  post-condition fails the run if a `subagent` variant yields no child trace.

  ```bash
  uv run --group research python research/agent-sdk-probe/run_matrix.py
  ```

See [`FINDINGS.md`](./FINDINGS.md) for the recorded answers to #112's three open
questions (#518), the representative-agent format findings (#522: subagent layout,
parent->child linkage, large-output spill), and the corpus matrix findings (#519:
model divergence, manifest schema, the `ai-title` stale-snapshot type).

## Dependency isolation

The Claude Agent SDK is a **dev-only** dependency in the `research`
[PEP 735 dependency-group](https://peps.python.org/pep-0735/) in `pyproject.toml`.
Dependency-groups are never part of the published distribution, so
`pip install agentfluent` does **not** pull the SDK. Verified against the built
wheel's `Requires-Dist` (no `claude-agent-sdk` entry).

## Run

```bash
# #518 hello-world probe
uv run --group research python research/agent-sdk-probe/probe.py

# #522 representative agent -- one variant per run
uv run --group research python research/agent-sdk-probe/agent.py flat
uv run --group research python research/agent-sdk-probe/agent.py subagent
uv run --group research python research/agent-sdk-probe/agent.py large
uv run --group research python research/agent-sdk-probe/agent.py nested

# #519 full corpus matrix (runs agent.py x3, writes corpus/manifest.json)
uv run --group research python research/agent-sdk-probe/run_matrix.py
```

Requires an authenticated `claude` CLI on `PATH` (the SDK drives it under the
hood; `apiKeySource` was `none` in the captured runs -- it used the CLI's own
auth).

## Corpus is never committed

The SDK writes the raw session to `~/.claude/projects/<cwd-slug>/<id>.jsonl`.
Raw sessions may carry secrets (per the CLAUDE.md secrets policy), so any copy
landed under `research/agent-sdk-probe/corpus/` is **gitignored** and never
committed. `run_matrix.py`'s `corpus/manifest.json` is gitignored too -- it embeds
absolute filesystem paths. Only anonymized fixtures graduate to `tests/fixtures/`
-- that is downstream work
([#521](https://github.com/frederick-douglas-pearce/agentfluent/issues/521)), and
the manifest's per-file `contains_abs_paths` flag is the scrub worklist for it.
The probe's fixture is synthetic, so its corpus is trivially safe, but the
gitignore guard is unconditional.

## SDK version

Pinned in `FINDINGS.md`. Re-record if the SDK is upgraded -- the trace format may
drift (mirrors the CLAUDE.md "Format as of 2026-04" caveat).
