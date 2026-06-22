# Agent SDK probe (#518)

Throwaway research scaffolding for the **Agent SDK session data discovery** epic
([#517](https://github.com/frederick-douglas-pearce/agentfluent/issues/517)).
This is **not** part of the published `agentfluent` package.

## What this is

A ~15-line hello-world Claude Agent SDK script (`probe.py`) that spins up an
agent, makes **exactly one** tool call (a single `Read` of a synthetic,
secret-free `fixture.txt`), and exits. The goal is not the agent -- it is to
learn, **with real bytes on disk**, where the SDK writes its session file and
what an SDK session looks like, before investing in the representative
data-generation agent ([#522](https://github.com/frederick-douglas-pearce/agentfluent/issues/522)).

See [`FINDINGS.md`](./FINDINGS.md) for the recorded answers to #112's three open
questions (location, discriminator, options metadata).

## Dependency isolation

The Claude Agent SDK is a **dev-only** dependency in the `research`
[PEP 735 dependency-group](https://peps.python.org/pep-0735/) in `pyproject.toml`.
Dependency-groups are never part of the published distribution, so
`pip install agentfluent` does **not** pull the SDK. Verified against the built
wheel's `Requires-Dist` (no `claude-agent-sdk` entry).

## Run

```bash
uv run --group research python research/agent-sdk-probe/probe.py
```

Requires an authenticated `claude` CLI on `PATH` (the SDK drives it under the
hood; `apiKeySource` was `none` in the captured run -- it used the CLI's own
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
