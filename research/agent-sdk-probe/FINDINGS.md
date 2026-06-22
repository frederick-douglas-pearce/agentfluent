# Agent SDK probe findings (#518)

Empirical answers to [#112](https://github.com/frederick-douglas-pearce/agentfluent/issues/112)'s
three open questions, captured from a real hello-world Agent SDK run. Feeds
S3/S4 ([#520](https://github.com/frederick-douglas-pearce/agentfluent/issues/520) /
[#521](https://github.com/frederick-douglas-pearce/agentfluent/issues/521)) and
the #112 update.

## Version pinning (reproducibility)

| Component | Value |
|-----------|-------|
| `claude-agent-sdk` (PyPI) | **0.2.106** |
| `claude` CLI (`claude_code_version` in trace) | **2.1.185** |
| SDK entrypoint (Python) | `query()` |
| Model set via `ClaudeAgentOptions.model` | `claude-haiku-4-5-20251001` |
| Captured | 2026-06-22 |

> Format as of the versions above. The trace shape may drift with SDK/CLI
> upgrades -- re-run the probe and re-record if either version changes.

## Q1 -- Location: where does the SDK write sessions?

**Same place as Claude Code:** `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`.

- The probe set `cwd` to `research/agent-sdk-probe/`. The SDK derived the project
  slug from that cwd exactly as Claude Code does
  (`-home-fdpearce-Documents-Projects-git-agentfluent-research-agent-sdk-probe`),
  confirmed via the SDK's own `project_key_for_directory()` helper.
- The session file is a single `<session-id>.jsonl` at the project-slug root --
  identical layout to a Claude Code interactive session.
- **Implication for AgentFluent:** existing discovery (`~/.claude/projects/`
  enumeration) finds SDK sessions with **no path changes**. SDK sessions and
  Claude Code interactive sessions are co-located in the same project dirs.

## Q2 -- Discriminator: can we tell an SDK session from a CC interactive session?

**Yes -- there is a reliable intrinsic marker.** `#112` does **not** need the
`--scope` heuristic fallback.

Grading (intrinsic field > heuristic > absent):

| Field (on every `user`/`assistant` line) | SDK (probe) | CC interactive | Discriminator? |
|---|---|---|---|
| **`entrypoint`** | `sdk-py` | `cli` | **YES -- intrinsic, reliable** |
| `promptSource` (on prompt lines) | `sdk` | `typed` | YES -- intrinsic (prompt lines only) |
| `userType` | `external` | `external` | NO -- identical |
| `isSidechain` | `false` | `false` (main) | NO -- main sessions match |

- **Primary discriminator: `entrypoint == "sdk-py"`.** Present on all 15
  `user`/`assistant` lines in the probe; Claude Code interactive sessions carry
  `entrypoint == "cli"` (verified across 3 real interactive sessions, 108-293
  lines each, 100% `cli`).
- The `-py` suffix implies the TS SDK likely emits `sdk-ts` (cheaply-observable
  inference per the epic's TS note; **not verified** -- D001 is Python-only).
- `promptSource == "sdk"` (vs `"typed"`) is a corroborating marker but only
  appears on user *prompt* lines, so `entrypoint` is the more robust field to
  key on.
- **Caveat:** value is version-specific. Phrase any #112 discriminator as
  "`entrypoint == "sdk-py"` for SDK >= 0.2.106 / CLI 2.1.185," not as an eternal
  truth.

## Q3 -- Options metadata: how does `ClaudeAgentOptions.model` surface?

- **Per assistant message:** each `assistant` line carries `message.model`, set
  to the exact `ClaudeAgentOptions.model` value (`claude-haiku-4-5-20251001`) --
  identical to how Claude Code records the model. AgentFluent's existing
  per-message model extraction works unchanged.
- **No persisted "options header" line.** The full options snapshot (model,
  `permissionMode`, `cwd`, `allowed_tools`, `mcp_servers`, loaded agents/skills,
  `apiKeySource`) is delivered at **runtime** via a `SystemMessage(subtype="init")`
  stream event -- it is **not** written to the JSONL as a `system` line. So the
  authoritative main-session model is read from the assistant messages, not a
  header. (If #112 ever needs the non-model options, they are runtime-only today.)
- The SDK **inherits the developer's local Claude Code environment** by default:
  the init event listed the user's full tool set, subagents, skills, plugins, and
  MCP servers. A "pure" SDK agent is not isolated from `~/.claude` unless
  `setting_sources` is constrained -- relevant context for #522's corpus design.

## Q4 -- Parser-assumptions: where do CLAUDE.md's JSONL assumptions hold vs break?

Ran the **production parser** (`agentfluent.core.parser.parse_session`) on the
captured SDK session.

**Holds:**
- `type: "user"` / `type: "assistant"` schema matches the documented shape.
- `toolUseResult` (camelCase) present on the user line carrying a `tool_result`
  block; parser attaches it to `SessionMessage.metadata` as documented.
- `message.usage` carries extra keys (`iterations`, `inference_geo`, `speed`,
  `server_tool_use`) -- absorbed harmlessly by `extra="ignore"`.
- Natural `is_error: true` tool result captured (agent's first `Read` used a
  wrong absolute path, self-corrected) -- exactly the error shape downstream
  signals key on.

**Breaks / gaps (documented, not fixed -- fix is downstream of this epic):**
- **Three line types are absent from `SKIP_TYPES`:** `queue-operation`,
  `attachment`, `last-prompt`. They are not in CLAUDE.md's "Types to skip" list.
  The parser does **not crash** -- they fall through to the `else` branch and are
  debug-logged as "Unknown message type" -- but they should be added to
  `SKIP_TYPES` (`session.py`) to make the skip intentional and silence debug
  noise. **Severity: low** (graceful degradation today).
- `attachment` lines appear interleaved with `user`/`assistant` (6 of 19 lines in
  this tiny session). A richer agent will emit more; confirm no downstream
  counter double-counts them once #522's corpus exists.

## Net result for the roadmap

- **#112 is unblocked.** All three questions answered with real bytes: location
  (co-located in `~/.claude/projects/`), discriminator
  (`entrypoint == "sdk-py"`, reliable intrinsic marker -- no `--scope` fallback
  needed), options metadata (model per-assistant-message; full options runtime-only).
- **#522 (representative agent)** can be designed with the format known: it will
  exercise multi-tool / multi-turn / subagent-delegation to validate the
  `toolUseResult.agentId` linkage and the `<id>/subagents/` layout (untested by
  this single-call probe).
- **Downstream parser story (unticketed):** add the three SDK line types to
  `SKIP_TYPES`, version-pinned to SDK 0.2.106.

---

# Representative-agent findings (#522)

Captured from `agent.py` (three variants) on the **same versions pinned above**
(`claude-agent-sdk==0.2.106`, CLI `2.1.185`, model `claude-haiku-4-5-20251001`).
The agent is a **pure** SDK agent: `setting_sources=[]`, `mcp_servers={}`,
`disallowed_tools=["WebFetch","WebSearch"]` -> corpus is trivially anonymizable.

> **`setting_sources=[]` caveat (architect review):** only reliably suppresses
> `~/.claude` env inheritance on Python SDK > 0.1.59 (older builds treated `[]`
> as "omitted"). Verified clean on 0.2.106 -- the init event showed no inherited
> subagents/skills/plugins/MCP. The env-*inheriting* representativeness run is a
> #519 config-matrix axis, deliberately not captured here.

## Delegation tool is `Agent`, not `Task` (and the init event mislabels it)

The probe's `SystemMessage(init)` advertised `Task` in its `tools` array, but the
model actually emitted `tool_use` blocks named **`Agent`** (with
`input.subagent_type`). `Agent` matches CLAUDE.md's documented delegation block,
so AgentFluent's existing assumption holds. **Takeaway:** key on the emitted
`tool_use.name == "Agent"`, *not* on the init event's tool list (which is stale
re: the Task->Agent rename). The probe allowed both names to avoid betting wrong.

## Subagent layout -- SDK reproduces Claude Code's, plus a new sidecar

A forced delegation produced, under the parent session dir:

```
<session-id>/subagents/agent-<agentId>.jsonl        # full child trace
<session-id>/subagents/agent-<agentId>.meta.json    # NEW sidecar (see below)
```

- **Child trace matches CC:** `isSidechain: true`, `entrypoint: "sdk-py"`,
  `userType: "external"`; same `user`/`assistant` schema as the main session.
- **`.meta.json` sidecar is new** vs the CLAUDE.md format snapshot:
  `{"agentType","description","toolUseId"}`. Per the human, this sidecar is a
  **recent Claude Code addition too** -- i.e. a CC format evolution, not
  SDK-specific. A future parser can use it as a direct `toolUseId -> agentId`
  map without scanning the parent JSONL.
- **Parent->child linkage holds three ways** (capture all, per architect):
  - parent `tool_use.id` (`toolu_...`) == `tool_result.tool_use_id` == sidecar `toolUseId`
  - `toolUseResult.agentId` (e.g. `a561d5c531c5f37cb`) == the `agent-<agentId>.jsonl` filename
  - `toolUseResult` also carries `agentType`, `prompt`, `status`, `totalTokens`,
    `totalToolUseCount`, `totalDurationMs`, `toolStats`, `usage`, **plus a new
    `resolvedModel`** field (the concrete model the child ran).
  AgentFluent's existing `agentId` indexing works unchanged.

## Large tool result -- a *new* `tool-results/` spill subfolder

Forcing an oversized Bash result (`seq 1 500000`, ~3.2 MB stdout) triggered a
spill the CLAUDE.md format snapshot does not document:

```
<session-id>/tool-results/<rand9>.txt    # full output verbatim (3,388,895 bytes)
```

- The main JSONL line does **not** inline the full output. Instead:
  - `tool_result.content` becomes a `<persisted-output>` block: a header
    (`Output too large (3.2MB). Full output saved to: <abs path>`) + a ~2 KB
    preview + `...`.
  - `toolUseResult.stdout` is **truncated to 30,000 chars**.
  - `toolUseResult` gains **`persistedOutputPath`** (absolute path to the spill
    file) and **`persistedOutputSize`** (full byte count).
- **Parser implication (downstream):** any content/token analysis that reads
  `tool_result.content` or `toolUseResult.stdout` sees only a truncated view of
  large results. To get the full bytes, follow `persistedOutputPath`. AgentFluent
  does not need full content for current metrics, but a signal that keys on tool
  *output size* must read `persistedOutputSize`, not `len(stdout)`.
- **#521 anonymization landmine (architect flagged, now concrete):**
  `persistedOutputPath` and the `<persisted-output>` header embed an **absolute
  filesystem path** (`/home/<user>/.claude/projects/<slug>/<id>/tool-results/...`).
  Fixtures must scrub these before committing.

## Parser-assumptions delta vs #518

Everything in #518's parser section still holds. New downstream items the
representative corpus surfaces (documented, not fixed -- per epic scope):

| New artifact | Where | Parser action (downstream) |
|---|---|---|
| `agent-<id>.meta.json` sidecar | `<id>/subagents/` | enumerate/skip; optional `toolUseId->agentId` shortcut |
| `tool-results/<rand>.txt` spill | `<id>/tool-results/` | follow `persistedOutputPath` only if full content needed |
| `persistedOutputPath`/`persistedOutputSize` | `toolUseResult` | use for output-size signals; absorbed by `extra="ignore"` today |
| `resolvedModel` | `toolUseResult` | concrete child model; useful for #112 model routing |

## Net result for the roadmap

- **#522 AC fully met:** multi-tool/multi-turn run with a natural `is_error`;
  forced subagent delegation with the `<id>/subagents/` layout + `agentId`
  linkage confirmed; large-output spill subfolder captured; no MCP/network/secret
  surface; SDK version pinned; variants documented in the README; SDK dep stays
  dev-only.
- **#519 (corpus matrix)** can drive `agent.py` repeatably -- each run emits a
  `RESULT ...` manifest line. Suggested added axes: an env-*inheriting* run
  (`setting_sources` populated) and a non-default `model`.
- **#520/#521 (diff + fixtures)** inherit a concrete, version-pinned list of
  format deltas (above) and the explicit anonymization landmine (absolute paths
  in persisted-output references).
