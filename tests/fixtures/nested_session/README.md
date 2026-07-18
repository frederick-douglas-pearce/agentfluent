# `nested_session` fixture — agent → subagent → subagent

A hand-crafted, secret-free fixture encoding a **two-level (nested) subagent**
chain as the Claude Agent SDK records it on disk. It is the anonymized companion
to the empirical probe in [#530](https://github.com/frederick-douglas-pearce/agentfluent/issues/530)
(verified live against SDK `0.2.106` / CLI `2.1.185`); see
`research/agent-sdk-probe/FINDINGS.md` for the full findings.

## Layout (this is the load-bearing shape)

```
nested-session-1.jsonl                              # main session
nested-session-1/subagents/
  agent-worker001.jsonl    + .meta.json             # LEVEL 1 (main delegated to it)
  agent-leaf0001.jsonl     + .meta.json             # LEVEL 2 (worker delegated to it)
```

Every subagent — **at every depth** — is a flat sibling under one `subagents/`
dir. There are **no** nested `subagents/<id>/subagents/` directories.

## What the fixture deliberately encodes

- **Flat layout**: `agent-worker001` (level 1) and `agent-leaf0001` (level 2) are
  siblings, not parent/child directories.
- **`.meta.json` sidecars**: each trace has an `{agentType, description, toolUseId}`
  sidecar. Discovery must ignore these (they are not trace files).
- **By-data parent linkage**: a subagent's `meta.toolUseId` is the `Agent`
  `tool_use` emitted in its **parent's** trace. `worker001.meta.toolUseId`
  (`toolu_main_to_worker`) is emitted in the main session; `leaf0001.meta.toolUseId`
  (`toolu_worker_to_leaf`) is emitted in **`agent-worker001.jsonl`** — that
  cross-file edge is what proves the depth-2 nesting. There is no depth marker
  and no stored parent pointer; the tree is reconstructed from this join.
- **Rollup metadata is top-level only**: the main session's `tool_result` for the
  worker carries a full `toolUseResult` rollup; the worker's `tool_result` for the
  leaf (depth 2) carries **none** — only an inline `subagent_tokens:` text trailer.
- **`totalTokens` semantics (D056, #595)**: the per-turn `usage` values mirror the
  live capture this fixture anonymizes, so both ratified facts hold *on the bytes*:
  - **exclusive of children** — the worker's rollup (`5495`) does not contain the
    leaf's `3925`;
  - **not cumulative spend** — `5495` is the worker's **final turn**
    (`4558, 4719, 5495`), while its turns *sum* to `14772`. Same for the leaf:
    final `3925`, sum `7480`.

  Do not "tidy" these into round numbers: the inequality is the property under
  test (`tests/unit/test_totaltokens_semantics.py`), and `cache_read` recurring
  across turns is why the sum overstates.

The worker also does its own `Read` before delegating (a realistic acts-and-
delegates middle agent), so the depth-2 "no rollup" property is not an artifact
of a degenerate delegate-only agent.

Consumed by `tests/unit/test_traces_nested_fixture.py`. The production
multi-level *linker* that turns this reconstruction into `AgentInvocation`
parent/child edges is downstream work (see #530 follow-ups).
