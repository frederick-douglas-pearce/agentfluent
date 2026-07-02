# Agent SDK session-format findings

**Status:** Final (epic [#517](https://github.com/frederick-douglas-pearce/agentfluent/issues/517) S4 deliverable, [#521](https://github.com/frederick-douglas-pearce/agentfluent/issues/521))
**Type:** Discovery findings — the durable, citable synthesis of the empirical probe. **Descriptive, not prescriptive:** it documents what the SDK format *is*; parser/feature changes are downstream of this epic (see [§8](#8-downstream-follow-ups-not-scoped-here)).
**Governing decision:** [D013](decisions.md) (main-session model-routing scope — AgentFluent owns SDK main sessions, not Claude Code interactive main sessions).
**Primary downstream consumer:** [#112](https://github.com/frederick-douglas-pearce/agentfluent/issues/112) (model-routing diagnostics for the main Agent SDK session).

This doc synthesizes the raw, byte-cited observations captured across the epic's
research stories (#518 probe, #522 representative agent, #519 corpus matrix, #530
nested subagents, #520 cross-walk). Those observations live in
`research/agent-sdk-probe/FINDINGS.md`; the corpus itself is gitignored under
`research/agent-sdk-probe/corpus/`. A reader here can answer the epic's questions
**without re-running the experiment**. Every claim below traces to a corpus
record already re-verified against the bytes in #520.

## Reproducibility — versions used

| Component | Value |
|-----------|-------|
| `claude-agent-sdk` (PyPI) | **0.2.106** |
| `claude` CLI (`version` field in trace) | **2.1.185** |
| SDK entrypoint (Python) | `query()` |
| Models observed (`ClaudeAgentOptions.model`) | `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` |
| Captured | 2026-06-22 |

> Format as of the versions above. The trace shape may drift with SDK/CLI
> upgrades — re-run the probe (`research/agent-sdk-probe/`) and re-record if
> either version changes. Any discriminator or field claim below is
> **version-pinned**, not an eternal truth. This mirrors the `CLAUDE.md`
> "Format as of 2026-04" caveat.

---

## 1. Where SDK sessions are written on disk

**Same location and layout as Claude Code:**
`~/.claude/projects/<cwd-slug>/<session-id>.jsonl`.

- The SDK derives the project slug from `cwd` exactly as Claude Code does
  (probe `cwd == research/agent-sdk-probe/` → slug
  `-home-…-agent-sdk-probe`), confirmed via the SDK's own
  `project_key_for_directory()` helper.
- The session is a single `<session-id>.jsonl` at the project-slug root; child
  artifacts are sibling directories under it: `<session-id>/subagents/` (subagent
  traces + `.meta.json` sidecars) and `<session-id>/tool-results/` (oversized
  output spill).
- **Implication:** AgentFluent's existing discovery (enumerate
  `~/.claude/projects/`) finds SDK sessions with **no path changes**. SDK
  sessions and Claude Code interactive sessions are **co-located** in the same
  project dirs — which is exactly why the discriminator (§2) is load-bearing.

*Cite: `corpus/manifest.json` `project_slug` + `source_jsonl`; on-disk
`f63d80f5-…/subagents/`, `6c141b2e-…/tool-results/be5p6qdq6.txt`.*

---

## 2. SDK-vs-Claude-Code-interactive discriminator (D013)

**A reliable intrinsic marker exists.** #112 does **not** need a `--scope`
heuristic or a user-supplied flag.

| Field (on `user`/`assistant` lines) | SDK (observed) | CC interactive | Discriminator? |
|---|---|---|---|
| **`entrypoint`** | `sdk-py` | `cli` | **YES — intrinsic, reliable** |
| `promptSource` (prompt lines only) | `sdk` | `typed` | YES — intrinsic, but prompt-lines-only |
| `userType` | `external` | `external` | NO — identical |
| `isSidechain` | `false` (main) / `true` (child) | `false` (main) | NO — main sessions match |

- **Primary discriminator: `entrypoint == "sdk-py"`.** Present on **119/119**
  `user`/`assistant` lines corpus-wide (main + child). Claude Code interactive
  sessions carry `entrypoint == "cli"` (verified in #518 across 3 real
  interactive sessions, 108–293 lines each, 100% `cli` — this CC value is not
  re-verifiable from the SDK-only corpus but was measured directly).
- `promptSource == "sdk"` (vs `"typed"`) corroborates but appears on user
  *prompt* lines only, so `entrypoint` is the field to key on.
- The `-py` suffix implies the TypeScript SDK likely emits `sdk-ts`
  (cheaply-observable inference; **not verified** — D001 is Python-only).
- **Version caveat:** phrase any #112 discriminator as
  `entrypoint == "sdk-py"` **for SDK ≥ 0.2.106 / CLI 2.1.185**, not as a
  permanent guarantee.

*Cite: 119× `entrypoint=sdk-py`, 0 other; `981cd27f…jsonl:3` `promptSource=sdk`.*

---

## 3. Main-session model / options metadata

- **Model, per assistant message:** each `assistant` line carries
  `message.model` set to the configured `ClaudeAgentOptions.model` — verified
  across **two** values (`claude-haiku-4-5-20251001` and `claude-sonnet-4-6`).
  AgentFluent's existing per-message model extraction
  (`SessionMessage.model`) works unchanged. This is the authoritative
  main-session model for #112.
- **No persisted options/init line.** There are **0 `system` lines corpus-wide**.
  The full options snapshot — `model`, `permissionMode`, `cwd`, `allowed_tools`,
  `mcp_servers`, loaded agents/skills/plugins, `apiKeySource` — is delivered at
  **runtime** via a `SystemMessage(subtype="init")` stream event and is **never
  written to the JSONL**. So the authoritative main-session model is read from
  the assistant messages, not from a header. If #112 (or later work) needs the
  non-model options, they are **runtime-only** today (the probe captured them
  out-of-band into `corpus/manifest.json` `init`).
- **Subagent (child) model — `toolUseResult.resolvedModel`.** On an `Agent`
  result, `toolUseResult` carries a **`resolvedModel`** field reporting the
  *child's* concrete model. In the corpus's divergence sample (a **sonnet** parent
  delegating to a **haiku** child), the model split is recoverable three
  independent ways: parent `message.model == claude-sonnet-4-6`; child trace
  `message.model == claude-haiku-4-5-20251001`; and
  `toolUseResult.resolvedModel == claude-haiku-4-5-20251001`. So #112 can verify a
  configured subagent model against `resolvedModel` **with no cross-file join**.
  (Caveat: `resolvedModel` is a superset field — see §4 — dropped by the current
  parser; it is not yet exposed on `ToolResultMetadata`.)
- **Environment inheritance (corpus-design note, not a format fact).** By default
  the SDK **inherits the developer's local `~/.claude` environment** (tools,
  subagents, skills, plugins, MCP servers show up in the init event). A "pure"
  isolated agent requires `setting_sources=[]` (reliable on Python SDK > 0.1.59;
  verified clean on 0.2.106). The corpus used for these findings was captured
  pure.

*Cite: `981cd27f…jsonl:8` `message.model`; `manifest.json` `init`; 0 `system`
lines; `f63d80f5…jsonl:10` `resolvedModel`.*

---

## 4. Parser-assumption gap list — holds / breaks / unknown

Each concrete assumption stated or implied by the `CLAUDE.md` "JSONL Data Format"
section, graded for SDK data with a corpus sample-record pointer. **Baseline
note:** `CLAUDE.md` already reflects the #528 sync (it documents the `.meta.json`
sidecar, the `tool-results/` spill, `persistedOutputPath`/`persistedOutputSize`,
and lists `ai-title, queue-operation, attachment, last-prompt` under "Types to
skip"). This grades against that current text.

| Assumption (`CLAUDE.md`) | Verdict | Evidence |
|---|---|---|
| `type:"user"` / `type:"assistant"` schema as documented | **holds** | `981cd27f…jsonl:8` assistant, `:3` user |
| `message.content` may be **string OR array** | **holds** | user lines corpus-wide: 11 string, 27 array |
| Top-level `type:"tool_result"` lines are **NOT** emitted (results are blocks inside `user` messages) | **holds** | 0 top-level `tool_result` lines; block form at `f63d80f5…jsonl:10` |
| `toolUseResult` (camelCase) attached to the **containing** user message carrying the `tool_result` block | **holds** | `f63d80f5…jsonl:10` — same line has the block + sibling `toolUseResult` |
| Agent delegation `tool_use.name == "Agent"` (with `subagent_type`) | **holds** | `f63d80f5…jsonl:9` `name=Agent`; init advertises `Task` — the `Task`→`Agent` alias caveat (below) applies to tools reading `init.tools` |
| `usage` extra keys absorbed by `extra="ignore"` | **holds** | extras `iterations, inference_geo, speed, server_tool_use, service_tier, cache_creation` present; `parse_session` runs clean |
| Subagents discovered via **flat** `<id>/subagents/agent-<agentId>.jsonl` (+ `.meta.json`) | **holds — at every depth** | single-level: `ac1c3a7f/subagents/` 2 siblings, `f63d80f5/subagents/` 1; multi-level flatness confirmed separately in #530 (no `subagents/<id>/subagents/` dirs ever) |
| `.meta.json` sidecar shape `{agentType, description, toolUseId}` | **holds** | all 3 sidecars carry exactly those keys |
| `agentId` links child file to `toolUseResult.agentId` | **holds** | `ac1c3a7f…jsonl:10,13` agentIds == the two child filenames |
| Large output spilled; `persistedOutputSize` = full byte count; `stdout` truncated to 30 000 | **holds** | `6c141b2e…jsonl:10` `persistedOutputSize=3388895` == spill bytes; `len(stdout)=30000` |
| "Types to skip" list is complete for SDK data | **holds** | the only non-user/assistant types observed (`ai-title, attachment, last-prompt, queue-operation`) are all in the baseline list; parser else-branch tolerates them |
| `toolUseResult.status == "success"` (per the `CLAUDE.md:260` example) | **breaks (value, doc-example only)** | observed `"completed"` on 3/3 Agent results; non-Agent tool results carry **no** `status` key. A one-word example fix, **not** a schema break |
| Rollup `toolUseResult` present on every subagent result | **breaks (depth ≥ 2)** | level-1 result carries the full rollup; a depth-≥2 spawn's `tool_result` carries **none** — only an inline `subagent_tokens:` text trailer (#530). Grandchild metrics must come from the grandchild's own trace |
| Fields consumed by the parser are a **closed set** | **unknown → superset (forward-compatible)** | SDK bytes add fields not in the baseline (§5). All are absorbed by `extra="ignore"` today; `resolvedModel` in particular is **present in bytes but dropped** — a downstream parser must opt in to surface it |

**No line type present in the SDK corpus is missing from the baseline "Types to
skip" list.** All four candidates (`queue-operation, attachment, last-prompt,
ai-title`) are already listed.

> **`Task`→`Agent` alias caveat (#522).** The runtime `SystemMessage(init)`
> advertises `Task` in its tools array, but the model emits `tool_use` blocks
> named **`Agent`**. The two are aliased (backwards-compat). Analysis reading
> `init.tools` must map `Task` → `Agent` when matching emitted blocks; anything
> keying on the *emitted* `tool_use.name` (as AgentFluent does) is already correct.

---

## 5. SDK-only / absent fields relative to the CC baseline

**Present in SDK bytes, NOT called out by the `CLAUDE.md` JSONL section**
(all absorbed by `extra="ignore"` — no crash, but not surfaced):

- `entrypoint` (`"sdk-py"`) — every user/assistant line (the §2 discriminator).
- `promptSource` (`"sdk"`) — user prompt lines only.
- `toolUseResult.resolvedModel` — concrete child model; on Agent results.
  **Not in the CC baseline and (per #522) not yet in the `claude-code-sessions`
  reference either.** The one SDK field #112 most wants that the parser does not
  yet expose.
- Claude Code threading/top-level fields the baseline does not enumerate:
  `userType, version, promptId, gitBranch, permissionMode, parentUuid, uuid,
  requestId`; child-trace extras `agentId, attributionAgent` (a self-label = the
  agent's own type, **not** a parent pointer), `sourceToolAssistantUUID`
  (intra-file threading).
- assistant `message` extras: `id, stop_reason, stop_sequence, stop_details,
  diagnostics`.
- `usage` extras: `cache_creation` (nested), `server_tool_use, service_tier,
  inference_geo, iterations, speed`.
- inline `<usage>subagent_tokens: N / tool_uses: N / duration_ms: N</usage>` text
  trailer inside the Agent `tool_result.content` — present at **all** levels (not
  a nesting signal; the level-1-only artifact is the sibling `toolUseResult`
  object, not the trailer).
- runtime-only `init` keys (never persisted to JSONL): `slash_commands,
  claude_code_version, output_style, apiKeySource, memory_paths, fast_mode_state,
  analytics_disabled, product_feedback_disabled`.

**Documented in `CLAUDE.md` but ABSENT from this corpus** (uncovered, not
contradicted — a richer or differently-configured agent would exercise them):

- line types `system, progress, hook_progress, bash_progress, create,
  file-history-snapshot` — 0 occurrences.
- `toolUseResult.status: "success"` — observed value is `"completed"` (§4).
- `persistedOutputPath`/`persistedOutputSize` — only in the one oversized-output
  run (`6c141b2e`); spill-only, as expected.

---

## 6. Committed fixtures

An anonymized SDK **main-session** fixture is committed at
[`tests/fixtures/sdk_session/`](../../tests/fixtures/sdk_session/) (main session +
one delegated child trace + `.meta.json` sidecar), locked by
`tests/unit/test_sdk_session_fixture.py`. It isolates the signals a main-session
consumer (#112) keys on, which no prior fixture carried together:

- the discriminator `entrypoint == "sdk-py"` on every line + `promptSource ==
  "sdk"` on the prompt line;
- per-assistant `message.model` == the configured (sonnet) main model;
- the **model-divergence** case — sonnet main → haiku child with
  `toolUseResult.resolvedModel == claude-haiku-4-5-20251001` (≠ the parent model);
- `toolUseResult.status == "completed"` (the observed value, not the doc's
  `"success"`);
- the 4-way `tool_use.id` ↔ `tool_result.tool_use_id` ↔ sidecar `toolUseId` ↔
  `toolUseResult.agentId` ↔ child filename ↔ child top-level `agentId` linkage.

The complementary **multi-level subagent** layout is already committed at
`tests/fixtures/nested_session/` (from #530). Together they cover the main-session
model signals and the nested-delegation layout.

**Anonymization approach.** Real SDK corpus `.jsonl` files embed the capturing
machine's absolute home path and dash-encoded project slug (the probe's
`manifest.json` flags every `.jsonl` as `contains_abs_paths: true`). Rather than
scrub captured files, the committed fixtures are **hand-crafted with synthetic
content** — secret-free by construction. This matches the existing
`tests/fixtures/` convention (e.g. `nested_session`) and takes on **no
dependency** on an external scrubber. (The `claude-code-sessions` `ccs-sanitize`
tool was validated against the corpus during the probe and remains available for
ad-hoc scrubbing of real captures, but agentfluent deliberately does not vendor
it: it is an unpublished sibling-repo CLI, and a local-path dependency would break
reproducibility for CI and other contributors.) A raw SDK corpus itself exists
(gitignored, `research/agent-sdk-probe/corpus/`) for anyone needing authentic
bytes locally.

---

## 7. #112 — answers to its three open questions

#112 is blocked on three questions; all three are now answered with real bytes.
A summary comment has been posted on #112 flagging that its **draft acceptance
criteria should be revisited** with these findings in hand.

1. **Are SDK sessions written to `~/.claude/projects/` or elsewhere?** —
   `~/.claude/projects/<slug>/<session-id>.jsonl`, co-located with interactive
   sessions; existing discovery needs no path changes (§1).
2. **Does the main session carry a distinguishing marker (to separate SDK runs
   from Claude Code interactive)?** — Yes: `entrypoint == "sdk-py"` (intrinsic,
   reliable, version-pinned); no `--scope` fallback needed (§2).
3. **Structure of the main-session model/options metadata?** — Model per
   assistant message at `message.model` == configured `ClaudeAgentOptions.model`;
   no persisted options/init line (options are runtime-only); `resolvedModel` on
   `toolUseResult` gives the concrete child model for subagent routing (§3).

**Bearing on #112's draft ACs:** "Detect Agent SDK sessions" is satisfiable via
the §2 discriminator (a small parser change to surface `entrypoint`); "apply
complexity classification / emit model-routing recs for `ClaudeAgentOptions.model`"
maps to the per-assistant `message.model` (§3); the subagent-vs-main distinction
its output must draw is directly supported by `isSidechain` + `resolvedModel`.

---

## 8. Downstream follow-ups (NOT scoped here)

Per the epic's Non-Goals, this findings doc is descriptive; the *fixes* are
downstream stories:

- **Parser: add SDK line types to `SKIP_TYPES`.** `queue-operation, attachment,
  last-prompt, ai-title` fall through to the debug-logged `else` branch today
  (graceful, low severity); adding them to the `SKIP_TYPES` frozenset in
  `core/session.py` makes the skip intentional. (Doc side already synced via
  #528; the code change is unticketed.)
- **Parser: surface `entrypoint`** so #112 can detect SDK sessions, and **surface
  `toolUseResult.resolvedModel`** (add to `ToolResultMetadata`) for child-model
  routing. Both are dropped today.
- **Multi-level trace-to-invocation linker** (#530 follow-up): cross-file
  `toolUseId` join + derived `parent_invocation_id`; settle the `totalTokens`
  inclusivity formula before double-counting.
- **`CLAUDE.md` doc nit:** the `status: "success"` example (`CLAUDE.md:260`) should
  read `"completed"` — a one-word fix folded into the #528 sync scope.

**Upstream (out of scope per #517; pointer only).** The `resolvedModel` field on
`toolUseResult` is not yet in the [`claude-code-sessions`](https://github.com/frederick-douglas-pearce/claude-code-sessions)
reference repo — a candidate for a brief format-watch issue there once these
findings stabilize. The nested "flat at all depths" layout is likewise worth
feeding back. Neither is scoped as a story here.

---

## 9. Epic #517 success-criteria map

Each epic success criterion is tickable from a section above:

| Epic #517 criterion | Answered in |
|---|---|
| Where SDK sessions are written on disk | §1 |
| Whether/how distinguishable from CC interactive (D013 discriminator) | §2 |
| Structure of SDK main-session model/options metadata | §3 |
| Point-by-point parser-assumptions hold vs. break | §4 |
| SDK corpus exists; anonymized fixtures committed (or documented reason) | §6 (corpus gitignored under `research/agent-sdk-probe/corpus/`; hand-crafted fixtures committed under `tests/fixtures/`) |
| #112's three open questions answered + #112 updated | §7 (+ comment posted on #112) |

---

## References

- **Empirical source:** `research/agent-sdk-probe/FINDINGS.md` (#518/#522/#519/#530/#520 raw observations, byte-cited).
- **Epic:** #517 · **Primary consumer:** #112 · **Governing decision:** D013 (`decisions.md`).
- **Baseline:** the "JSONL Data Format" section of `CLAUDE.md` ("Format as of 2026-04").
- **Upstream reference:** `claude-code-sessions` (`reference/data-dictionary.md`, `reference/subagent-traces.md`).
- **PRD:** `.claude/specs/prd-agent-sdk-discovery.md`.
