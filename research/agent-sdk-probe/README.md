# Agent SDK probe (#518)

Throwaway research scaffolding for the **Agent SDK session data discovery** epic
([#517](https://github.com/frederick-douglas-pearce/agentfluent/issues/517)).
This is **not** part of the published `agentfluent` package.

## What this is

Two throwaway Claude Agent SDK scripts:

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

  Each run prints a machine-readable `RESULT variant=... session_id=... file=...`
  line so #519 can build its config->file manifest mechanically. `agent.py` is a
  **pure** SDK agent (`setting_sources=[]`, no MCP, web tools disallowed) so its
  corpus is trivially anonymizable. The synthetic targets live in `sampledata/`
  (committed; secret-free).

See [`FINDINGS.md`](./FINDINGS.md) for the recorded answers to #112's three open
questions (#518) and the representative-agent format findings (#522: subagent
layout, parent->child linkage, large-output spill).

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
```

Requires an authenticated `claude` CLI on `PATH` (the SDK drives it under the
hood; `apiKeySource` was `none` in the captured runs -- it used the CLI's own
auth).

## Corpus is never committed

The SDK writes the raw session to `~/.claude/projects/<cwd-slug>/<id>.jsonl`.
Raw sessions may carry secrets (per the CLAUDE.md secrets policy), so any copy
landed under `research/agent-sdk-probe/corpus/` is **gitignored** and never
committed. Only anonymized fixtures graduate to `tests/fixtures/` -- that is
downstream work ([#521](https://github.com/frederick-douglas-pearce/agentfluent/issues/521)),
not this story. The probe's fixture is synthetic, so its corpus is trivially
safe, but the gitignore guard is unconditional.

## SDK version

Pinned in `FINDINGS.md`. Re-record if the SDK is upgraded -- the trace format may
drift (mirrors the CLAUDE.md "Format as of 2026-04" caveat).
