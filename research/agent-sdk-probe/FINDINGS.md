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
