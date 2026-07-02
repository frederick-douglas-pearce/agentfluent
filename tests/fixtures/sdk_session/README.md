# `sdk_session` fixture — a clean Agent SDK **main session**

A hand-crafted, secret-free fixture of an **Agent SDK main session** as the SDK
records it on disk. It is the anonymized companion to the SDK session-format
discovery epic ([#517](https://github.com/frederick-douglas-pearce/agentfluent/issues/517),
findings synthesized in
[#521](https://github.com/frederick-douglas-pearce/agentfluent/issues/521); see
`.claude/specs/agent-sdk-session-format-findings.md` and the empirical probe at
`research/agent-sdk-probe/FINDINGS.md`).

**Verified against** SDK `claude-agent-sdk==0.2.106` / CLI `2.1.185`
(captured 2026-06-22). The trace shape may drift with SDK/CLI upgrades — re-run
the probe and re-record if either version changes.

Where the `nested_session` fixture (#530) exercises multi-level *subagent*
layout, this fixture isolates the signals a **main-session** consumer (#112,
model-routing diagnostics) keys on, which no other fixture carries together.

## Layout

```
sdk-main-1.jsonl                                   # main session (sonnet)
sdk-main-1/subagents/
  agent-child0000001.jsonl   + .meta.json          # the delegated child (haiku)
```

## What the fixture deliberately encodes (the load-bearing #112 signals)

- **The SDK-vs-CC-interactive discriminator (D013):** `entrypoint == "sdk-py"`
  on **every** `user`/`assistant` line (main + child). A Claude Code interactive
  session carries `entrypoint == "cli"`. This is the intrinsic, reliable marker —
  no `--scope` heuristic needed.
- **Corroborating marker:** `promptSource == "sdk"` on the user *prompt* line
  (present on prompt lines only; `entrypoint` is the more robust field to key on).
- **Main-session model:** each `assistant` line carries `message.model ==
  "claude-sonnet-4-6"` — the configured `ClaudeAgentOptions.model`, which the
  production parser exposes as `SessionMessage.model`. There is **no** persisted
  options/init line (real sessions deliver those at runtime via
  `SystemMessage(subtype="init")`, never to JSONL).
- **Model divergence — the "#112 artifact":** a **sonnet** main session delegates
  to a **haiku** child. `toolUseResult.resolvedModel == "claude-haiku-4-5-20251001"`
  reports the *child's* resolved model (≠ the parent's `message.model`), so
  model-routing can verify a configured subagent model with no cross-file join.
  Note `resolvedModel` is **not** in the CC baseline and is **dropped by the
  current parser** (`ToolResultMetadata` uses `extra="ignore"`) — it lives in the
  bytes here as a forward test-bed for the downstream #112 work.
- **`toolUseResult.status == "completed"`** — the observed value; the `CLAUDE.md`
  JSONL example's `"success"` is a doc-example nit, not the emitted value.
- **4-way linkage:** the main `tool_use.id` (`toolu_main_to_child`) ==
  `tool_result.tool_use_id` == the sidecar's `toolUseId` == `toolUseResult.agentId`
  (`child0000001`) == the `agent-child0000001.jsonl` filename == the child trace's
  top-level `agentId`.

## Anonymization

Real SDK corpus `.jsonl` files carry the capturing machine's absolute home path
and dash-encoded project slug (`contains_abs_paths: true` in the probe manifest).
This fixture is **hand-crafted with synthetic content**, so it is secret-free by
construction — no scrubbing tool (e.g. `ccs-sanitize`) is involved, and
agentfluent takes on no dependency on one.

Consumed by `tests/unit/test_sdk_session_fixture.py`.
