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
`tool_use.name == "Agent"`, *not* on the init event's tool list (which advertises
`Task`). The probe allowed both names to avoid betting wrong.

**Backwards-compat aliasing, not a bug (verified).** With enforced permissions
(`permission_mode="default"`, `Task` allow-listed but *not* `Agent`), the
delegation runs with **zero permission denials** -- allow-listing the
*advertised* name (`Task`) permits the *emitted* `Agent`. So `Task`/`Agent` are
aliased, almost certainly for backwards compatibility (cf. the TS SDK's
`Options.toolAliases` and Python SDK request
`anthropics/claude-agent-sdk-python#980`). **Not worth an upstream bug report**;
recorded here only as a parser caveat (analysis tools that read `init.tools` must
map `Task` -> `Agent` when matching emitted `tool_use` blocks).

## Subagent layout -- SDK reproduces Claude Code's, plus a new sidecar

A forced delegation produced, under the parent session dir:

```
<session-id>/subagents/agent-<agentId>.jsonl        # full child trace
<session-id>/subagents/agent-<agentId>.meta.json    # NEW sidecar (see below)
```

- **Child trace matches CC:** `isSidechain: true`, `entrypoint: "sdk-py"`,
  `userType: "external"`; same `user`/`assistant` schema as the main session.
- **`.meta.json` sidecar** (`{"agentType","description","toolUseId"}`) -- a CC
  format evolution (not SDK-specific), **already documented in the
  `claude-code-sessions` reference** (`reference/subagent-traces.md`,
  `reference/data-dictionary.md`). It simply postdates agentfluent's CLAUDE.md
  JSONL snapshot, which should be synced (downstream item below). A parser can
  use it as a direct `toolUseId -> agentId` map without scanning the parent JSONL.
- **Parent->child linkage holds three ways** (capture all, per architect):
  - parent `tool_use.id` (`toolu_...`) == `tool_result.tool_use_id` == sidecar `toolUseId`
  - `toolUseResult.agentId` (e.g. `a561d5c531c5f37cb`) == the `agent-<agentId>.jsonl` filename
  - `toolUseResult` also carries `agentType`, `prompt`, `status`, `totalTokens`,
    `totalToolUseCount`, `totalDurationMs`, `toolStats`, `usage`, **plus a new
    `resolvedModel`** field (the concrete model the child ran).
  AgentFluent's existing `agentId` indexing works unchanged.

## Large tool result -- the `tool-results/` spill subfolder

Forcing an oversized Bash result (`seq 1 500000`, ~3.2 MB stdout) triggered the
spill layout **already documented in `claude-code-sessions`**
(`reference/data-dictionary.md`) but absent from agentfluent's CLAUDE.md snapshot
(sync it -- downstream item below):

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
  Fixtures must scrub these before committing. **Solved by the
  `claude-code-sessions` `ccs-sanitize` tool** -- validated against this corpus:
  it rewrote the home path *and* the dash-encoded project slug
  (`-home-<user>-...`) to `/home/user` (15 -> 0 username occurrences). #521
  fixtures run through `ccs-sanitize` rather than bespoke scrubbing.

## Parser-assumptions delta vs #518

Everything in #518's parser section still holds. New downstream items the
representative corpus surfaces (documented, not fixed -- per epic scope):

| Artifact | Where | In `claude-code-sessions`? | Parser action (downstream) |
|---|---|---|---|
| `agent-<id>.meta.json` sidecar | `<id>/subagents/` | yes (`subagent-traces.md`) | enumerate/skip; optional `toolUseId->agentId` shortcut |
| `tool-results/<rand>.txt` spill | `<id>/tool-results/` | yes (`data-dictionary.md`) | follow `persistedOutputPath` only if full content needed |
| `persistedOutputPath`/`persistedOutputSize` | `toolUseResult` | yes | use for output-size signals; absorbed by `extra="ignore"` today |
| `resolvedModel` | `toolUseResult` | **no (as of 2026-06-22)** | concrete child model; useful for #112 model routing |

The first three are already documented upstream; agentfluent's CLAUDE.md JSONL
snapshot is simply **stale** and should be synced. `resolvedModel` was **not
found** in `claude-code-sessions` -- a candidate for a brief issue there (or it
will surface on a routine format scan).

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
  in persisted-output references), now solved via `ccs-sanitize`.

## Upstream / cross-repo follow-ups

- **Sync agentfluent's CLAUDE.md JSONL snapshot** with the three artifacts above
  (subagent `.meta.json` sidecar, `tool-results/` spill, `persistedOutputPath`/
  `persistedOutputSize`) **plus the four line types missing from "Types to skip"**
  (`queue-operation`, `attachment`, `last-prompt`, `ai-title` -- see #519 below).
  Tracked in [#528](https://github.com/frederick-douglas-pearce/agentfluent/issues/528)
  so all CLAUDE.md updates land in one pass. Downstream doc task, not this epic.
- **`resolvedModel`** on `toolUseResult` is not yet in `claude-code-sessions` --
  candidate for a brief format-watch issue there.
- **Fixture anonymization (#521)** uses `claude-code-sessions`' `ccs-sanitize`
  CLI (validated here). It is a standalone tool invoked ad-hoc against captured
  corpus files; agentfluent does **not** take it as a packaged dependency (it is
  an unpublished sibling-repo CLI, and a local-path dep would break
  reproducibility for CI/other contributors).

---

# Corpus matrix findings (#519)

Captured by `run_matrix.py` on the **same versions pinned above**
(`claude-agent-sdk==0.2.106`, CLI `2.1.185`, captured 2026-06-22). The runner
drives `agent.py` across a 3-run matrix (one axis toggled per run -- the
cross-product is gold-plating, per architect review), copies each run's raw
session file(s) out of `~/.claude/projects/<slug>/` into the gitignored
`corpus/`, and writes `corpus/manifest.json` (the config->file index #520
consumes).

| run | variant  | main model | subagent model | isolates |
|-----|----------|------------|----------------|----------|
| a | `flat` | haiku | -- | full `tool_use` / error surface |
| b | `subagent` | **sonnet** | **haiku** | delegation + parent!=child model |
| c | `flat` | sonnet | -- | model recording (2nd model value) |

Satisfies the ACs: >=1 delegation run (b) + 2 without (a, c); 2 distinct models
(haiku, sonnet). Variant 1 (#518 hello-world) and the #522 `large` spill are not
re-run -- they are carried as `pre_existing` manifest entries so the manifest is
the single index of all SDK corpus.

## Model divergence is recorded three ways (the #112 artifact)

Run (b) ran a **sonnet parent delegating to a haiku child** -- a genuine
divergence sample. The split is recoverable from the bytes, three independent
ways:

- parent `message.model` on every assistant line == `claude-sonnet-4-6`
- child trace `message.model` == `claude-haiku-4-5-20251001`
- **`toolUseResult.resolvedModel` == the *child's* resolved model**
  (`claude-haiku-4-5-20251001`), i.e. `resolvedModel` reports what the *subagent*
  ran, not the parent. So #112 model-routing can verify a configured
  `subagent_model` against `resolvedModel` with no JSONL cross-referencing. The
  subagent model **must** be a threaded, recorded input for this to work -- it is
  (architect-flagged; `agent.py` no longer hardcodes the child model).

## Line type `ai-title` -- another stale-snapshot type, not a novel find

The richer multi-turn sessions surfaced a line type the #518 single-call probe
did not exercise: **`ai-title`** (`{"type","sessionId","aiTitle"}` -- the
auto-generated session title). Like the `.meta.json` sidecar and the
`tool-results/` spill, it is **already documented upstream in
`claude-code-sessions`** (`reference/data-dictionary.md`,
`reference/subagent-traces.md`) -- agentfluent's CLAUDE.md snapshot is simply
stale, not missing a new discovery. The same is true of the three #518 already
found absent from `SKIP_TYPES` (`queue-operation`, `attachment`, `last-prompt`):
all four are upstream-documented. All four:

- **do not crash the production parser** -- they fall through to the `else` branch
  and are debug-logged as "Unknown message type" (verified: `parse_session` ran on
  all three new sessions, 0 crashes).
- should be added to `SKIP_TYPES` (`session.py`) **and** to CLAUDE.md's
  "Types to skip" list. The CLAUDE.md doc edit is folded into
  [#528](https://github.com/frederick-douglas-pearce/agentfluent/issues/528); the
  `SKIP_TYPES` code change is a separate downstream parser item (unticketed, noted
  in #518).

**No *new persisted* `user`/`assistant` format fields** beyond what #522 already
recorded -- #519 widens line-type and model coverage, it does not surface new
`toolUseResult`/message schema.

## Manifest schema (`corpus/manifest.json`, gitignored)

Per architect review, each run entry carries enough to correlate format deltas to
inputs **and** to hand #521 a scrub worklist:

- `variant`, `main_model`, `subagent_model`, `session_id`
- `source_jsonl` (absolute) **and** `corpus_jsonl` (corpus-relative) -- both paths
- `sdk_version`, `cli_version` (read from the trace's `version` field), `prompt`,
  and the full `config` snapshot (`allowed_tools`, `disallowed_tools`,
  `setting_sources`, `permission_mode`, `max_turns`, `cwd`, `agents`)
- `subagent_files` / `tool_results_files` lists
- `files[]`: per-file `sha256`, `bytes`, `lines`, and a **`contains_abs_paths`**
  flag (true when the real home path or dash-slug appears in the bytes -- #521's
  mechanical scrub worklist). Observed: every `.jsonl` (main + child) is `true`;
  the `.meta.json` sidecar is `false`.
- `init`: the runtime-only `SystemMessage(init)` payload (keys include `tools`,
  `mcp_servers`, `agents`, `skills`, `plugins`, `permissionMode`, `cwd`,
  `apiKeySource`, `model`) -- the only place the non-model options surface, since
  they are **not** persisted to the JSONL (reaffirms #518 Q3). With
  `setting_sources=[]` the `agents`/`skills`/`plugins`/`mcp_servers` lists are
  empty -- the pure-SDK isolation holds on 0.2.106.

A **completeness post-condition** guards the copy: a `subagent` run that yields no
`subagents/*.jsonl` raises rather than writing a misleading (empty-child) manifest
entry. The copy runs after the subprocess exits, so the lazily-created sibling
dirs are fully flushed.

## Net result for the roadmap

- **#519 AC met:** raw corpus across the matrix exists under the gitignored
  `corpus/`; >=1 delegation + 2 non-delegation runs; 2 models; every file's
  on-disk location recorded; `manifest.json` maps config -> file for #520.
- **#520 (diff)** inherits the manifest as its correlation key and a confirmed
  model-divergence sample.
- **#521 (fixtures)** inherits the `contains_abs_paths` worklist (every `.jsonl`
  needs scrubbing; `ccs-sanitize` already validated in #522).
- **#528 (CLAUDE.md sync)** gains the four upstream-documented skip-types
  (`ai-title`, `queue-operation`, `attachment`, `last-prompt`) for the
  "Types to skip" list -- same stale-snapshot category as meta.json/spill.

---

# Nested (multi-level) subagent findings (#530)

Captured by `agent.py nested` on the **same versions pinned above**
(`claude-agent-sdk==0.2.106`, CLI `2.1.185`). The `nested` variant runs
`main -> delegator -> leaf-summarizer`: the middle agent is granted the
`Agent`/`Task` tool so it can itself delegate. Claude Code forbids subagents from
delegating, so this layout is **unobservable there** -- the SDK is the only way
to learn how a second-level trace is recorded. This resolves the last open layout
question (also `claude-code-sessions` `reference/subagent-traces.md` **open-item
#1**: flat-with-reconstruction vs nested `subagents/<id>/subagents/...`).

> **Validated against a realistic middle agent.** A first pass used a
> delegate-only middle agent; a second pass made the middle agent do its own
> `Grep`/`Bash`/`Read` work *before* delegating. Both passes gave the same
> conclusions, ruling out the "degenerate delegate-only agent" confound on
> finding 5.

## 1. The layout is FLAT (open-item #1: resolved)

Every subagent, **at every depth**, is a sibling file under one `subagents/` dir:

```
<session-id>/subagents/agent-<delegatorId>.jsonl   # level 1
<session-id>/subagents/agent-<delegatorId>.meta.json
<session-id>/subagents/agent-<leafId>.jsonl        # level 2 -- SAME folder
<session-id>/subagents/agent-<leafId>.meta.json
```

There are **no** nested `subagents/<agentId>/subagents/...` directories. This
confirms the production parser's existing flat, non-recursive
`discover_session_subagents()` is **correct** for nesting, not lucky -- a deeper
chain just yields more siblings. `sessionId` is shared across all levels;
`entrypoint: sdk-py` throughout.

## 2. Parent linkage is by-data, not by-path

The directory shape carries **no** depth information. The call tree is
reconstructed from the bytes:

- Each subagent's `.meta.json` sidecar carries `toolUseId` -- the `Agent`
  `tool_use` that spawned it.
- That `tool_use` is emitted **in the parent's trace**. The grandchild's
  spawning `toolUseId` was found in the *delegator's* trace file, not the main
  session. So: index `tool_use.id -> (containing_trace, agentId)` across **all**
  files, then resolve each child's `meta.toolUseId` into that index. Parent =
  "the agent whose trace emitted my spawning `tool_use`."
- `attributionAgent` is the agent's **own** type name (a self-label, not a parent
  pointer); `sourceToolAssistantUUID` / `parentUuid` are **intra-file** message
  threading only. None of them is a cross-file parent link.

> **Downstream linker note:** the existing single-level linker (#105) assumes
> parent == main session. A multi-level linker must do the cross-file
> `toolUseId` join above and gains an optional derived `parent_invocation_id`
> (None = root). Getting the join wrong silently flattens a 3-level tree to 2.

## 3. Rollup metadata (`toolUseResult`) is top-level only

The rich `toolUseResult` object (`totalTokens`, `totalToolUseCount`,
`resolvedModel`, ...) is attached **only** on the main session's user message
carrying a *level-1* result. At depth >= 2 the spawning `Agent` `tool_result`
block has **no** `toolUseResult` sibling -- only an inline `subagent_tokens: N`
text trailer. Clean same-session contrast (realistic-middle-agent pass):

| Spawn | Where the `tool_result` lives | `toolUseResult`? |
|---|---|---|
| main -> worker (level 1) | main `<session-id>.jsonl` | **yes** (`totalTokens=11486`, `totalToolUseCount=5`) |
| worker -> leaf (level 2) | `agent-<workerId>.jsonl` | **no** |

**Parser implication (downstream):** grandchild-level metrics cannot be read off
the parent's `toolUseResult` the way level-1 metrics can -- they must be derived
from the grandchild's own trace (or the inline trailer).

## 4. Counter / token semantics

- `totalToolUseCount` is **own-direct, not cumulative**: the worker reported `5`
  (its `Grep, Grep, Bash, Read, Agent` calls) and **excluded** the leaf's `Read`.
- `totalTokens` reads as **cumulative/inclusive** of descendants *directionally*
  (delegate-only pass: the middle agent did negligible direct work yet reported
  5495 vs the leaf's ~3925), but the figure differs from a raw usage sum (cache
  accounting). **Noted residual:** settle the exact inclusivity formula against
  this corpus before the multi-level linker double-counts tokens.

## Fixture

An anonymized, hand-crafted version of this layout is committed at
`tests/fixtures/nested_session/` (parent + 2 sibling traces + 2 `.meta.json`
sidecars) and locked by `tests/unit/test_traces_nested_fixture.py`. It encodes
findings 1-3 as executable assertions for the downstream linker work.

## Net result for the roadmap

- **Open-item #1 resolved:** SDK nested delegation records a **flat** layout;
  reconstruct the tree from `toolUseId`, never from path shape. Worth feeding
  back to the `claude-code-sessions` reference (upstream contribution is out of
  scope per #517).
- **Downstream (separate stories, not #517 -- discovery-only):** a multi-level
  trace-to-invocation linker (cross-file `toolUseId` join + `parent_invocation_id`
  + the token-inclusivity decision), and a regression test that
  `discover_session_subagents()` ignores `.meta.json` sidecars.
- The `.meta.json` sidecar itself is already in the #528 CLAUDE.md-sync scope
  (documented in the #522 section above) -- no new docs issue needed.

---

# SDK vs Claude Code -- structured comparison (#520)

The **consolidated, dimension-by-dimension cross-walk** of the SDK corpus against
the Claude Code baseline (the `CLAUDE.md` "JSONL Data Format" section). Where the
per-story sections above (#518/#522/#519/#530) recorded observations as they were
discovered, this section is the single structured artifact **S4 (#521) synthesizes
from**: every row is re-verified against the corpus bytes and cited to a specific
record (corpus-relative `filename:line` or field path). Same versions
(`claude-agent-sdk==0.2.106`, CLI `2.1.185`, captured 2026-06-22).

**Baseline note.** `CLAUDE.md`'s JSONL section already reflects the #528 sync -- it
documents the `.meta.json` sidecar, the `tool-results/` spill,
`persistedOutputPath`/`persistedOutputSize`, and lists `ai-title`,
`queue-operation`, `attachment`, `last-prompt` under "Types to skip." So several
items the earlier sections flagged as "stale-snapshot deltas" are now **in** the
baseline; this cross-walk grades against the current `CLAUDE.md` text (see the
"Refinements vs earlier sections" note below).

**Corpus scope for this diff.** Every claim was checked across all main `*.jsonl`
+ child traces via `jq`/`grep` (not restated from prose). Counts below are over
the full corpus (main + child `user`/`assistant` lines unless noted).

## 1. Per-dimension comparison

| Dimension | CC baseline (`CLAUDE.md`) | SDK observed | Verdict | Sample record |
|---|---|---|---|---|
| **File location / dir layout** | `~/.claude/projects/<slug>/<session-id>.jsonl`; child dirs `<id>/subagents/`, `<id>/tool-results/` | Identical: single `<session-id>.jsonl` at slug root; child dirs are siblings | **Same** | `manifest.json` `project_slug` + `source_jsonl`; on-disk `f63d80f5-.../subagents/`, `6c141b2e-.../tool-results/be5p6qdq6.txt` |
| **Message types (assistant/user)** | `assistant` (model+`tool_use`), `user` (prompt or `tool_result` blocks) | Both present, identical schema | **Same** | `981cd27f...jsonl:8` assistant; `:3` user |
| **Skip-types present** | Lists incl. `ai-title, queue-operation, attachment, last-prompt` | Corpus's only non-user/assistant types are exactly `ai-title, attachment, last-prompt, queue-operation` (18/30/13/16 occ.); `system, progress, create, file-history-snapshot` not exercised | **Same** (all 4 observed types are in the baseline list) | `981cd27f...jsonl:1` queue-operation, `:4` attachment, `:7` ai-title, `:24` last-prompt |
| **`toolUseResult` shape (camelCase)** | `status, prompt, agentId, agentType, totalDurationMs, totalTokens, totalToolUseCount, usage, toolStats` (+`persistedOutput*` on spill) | Same keys, camelCase, **+ `resolvedModel`** (concrete child model) not in baseline | **Differs (superset: +`resolvedModel`)** | `f63d80f5...jsonl:10` -- keys incl. `resolvedModel=claude-haiku-4-5-20251001` |
| **`toolUseResult.status` value** | example shows `"success"` (`CLAUDE.md:260`) | `"completed"` on all 3 Agent results; non-Agent tool results carry **no** `status` key | **Differs (doc-example nit)** | 3× `"completed"`, 17× absent (`f63d80f5...jsonl:10`, `ac1c3a7f...jsonl:10,13`) |
| **`isSidechain` present / semantics** | main `false`; subagent traces `true` | main `false`; child traces `true` | **Same** | main lines all `false`; `ac1c3a7f/.../agent-a29449fa7d1602e91.jsonl` all `true` |
| **Subagent trace layout + `agentId` linkage** | `<id>/subagents/agent-<agentId>.jsonl` + `.meta.json {agentType,description,toolUseId}`; `agentId` == `toolUseResult.agentId` | Exactly this; 4-way join verified: parent `tool_use.id` == `tool_result.tool_use_id` == sidecar `toolUseId` == `toolUseResult.agentId` == `agent-<agentId>.jsonl` filename == child top-level `agentId` | **Same** | `ac1c3a7f...jsonl:9` `tool_use.id=toolu_01CwHK...`; `:10` `agentId=a29449fa7d1602e91`; sidecar `agent-a29449fa7d1602e91.meta.json`; child file top-level `agentId=a29449fa7d1602e91` |
| **token/usage field shape** | `input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens` | All 4 present **+ extras**: `cache_creation` (nested), `server_tool_use`, `service_tier`, `inference_geo`, `iterations`, `speed` (absorbed by `extra="ignore"`) | **Differs (superset)** | `981cd27f...jsonl:8` usage block |
| **SDK-only markers (D013 discriminator)** | not documented | `entrypoint == "sdk-py"` on **119/119** user+assistant lines (main+child); `promptSource == "sdk"` on prompt lines; `version == "2.1.185"` | **SDK-only (new)** | 119× `entrypoint=sdk-py`, 0 other; `981cd27f...jsonl:3` `promptSource=sdk` |
| **Main-session model / options metadata** | `assistant.message.model` per message; no options header line | `message.model` per assistant line == configured model (haiku + sonnet both observed); **0 `system` lines corpus-wide** -- options are runtime-only (`SystemMessage(init)`, captured in `manifest.json` `init`) | **Same** (confirmed no persisted options header) | `981cd27f...jsonl:8` `message.model`; `manifest.json` `init`; 0 `system` lines |

## 2. Parser-assumption cross-walk -- holds / breaks / unknown

Each concrete assumption stated or implied by the `CLAUDE.md` JSONL section, graded
for SDK data with a sample-record pointer.

| Assumption (`CLAUDE.md`) | Verdict | Evidence |
|---|---|---|
| `message.content` may be **string OR array** | **holds** | user lines corpus-wide (main + child): 11 string, 27 array |
| Top-level `type:"tool_result"` lines are **NOT** emitted (results are blocks inside `user` messages) | **holds** | 0 top-level `tool_result` lines; block form at `f63d80f5...jsonl:10` |
| `toolUseResult` attached to the **containing** user message carrying the `tool_result` block | **holds** | `f63d80f5...jsonl:10` -- same line has the `tool_result` block + sibling `toolUseResult` |
| Agent delegation `tool_use.name == "Agent"` (with `subagent_type`) | **holds** | `f63d80f5...jsonl:9` `name=Agent`; init advertises `Task` (`manifest.json` `init.tools`) -- alias caveat from #522 still applies |
| `usage` extra keys absorbed by `extra="ignore"` | **holds** | extras present (`iterations, inference_geo, speed, server_tool_use, service_tier, cache_creation`); `parse_session` runs clean (#518/#519) |
| Subagents discovered via **flat** `<id>/subagents/` | **holds (single-level only)** | `ac1c3a7f/subagents/` 2 siblings, `f63d80f5/subagents/` 1; **no `nested` session in this corpus** -- #530's multi-level flatness is carried from its own capture, not re-verified here |
| `agentId` links child file to `toolUseResult.agentId` | **holds** | `ac1c3a7f...jsonl:10,13` agentIds match the two child filenames |
| "Types to skip" list is complete for SDK data | **holds** | all 4 observed non-user/assistant types are in the baseline list; parser else-branch tolerates them |
| Large output spilled; `persistedOutputSize` = full byte count, `stdout` truncated | **holds** | `6c141b2e...jsonl:10` `persistedOutputSize=3388895` == spill file bytes (3,388,895); `len(stdout)=30000` |
| `.meta.json` sidecar shape `{agentType,description,toolUseId}` | **holds** | all 3 sidecars carry exactly those keys |
| `toolUseResult.status` == `"success"` (per the doc example) | **breaks (value)** | observed `"completed"` (3/3 Agent results); minor doc-example fix, not a schema break |

**No line type present in SDK data is missing from the baseline "Types to skip"
list.** All four candidates (`queue-operation, attachment, last-prompt, ai-title`)
are already listed post-#528.

## 3. The three #112 open questions -- observed answers

**(a) On-disk location.** Same as Claude Code:
`~/.claude/projects/<cwd-slug>/<session-id>.jsonl`, co-located with interactive
sessions. Probe `cwd == research/agent-sdk-probe/` produced slug
`-home-...-agent-sdk-probe` identically; existing discovery needs **no path
changes**. *Cite: `manifest.json` `project_slug` + `source_jsonl`.*

**(b) SDK-vs-interactive discriminator.** `entrypoint == "sdk-py"` on **119/119**
user+assistant lines (main + child) -- intrinsic and reliable; #112 needs **no**
`--scope` heuristic. CC interactive carries `entrypoint == "cli"` (per #518 across
3 real sessions; not re-verifiable from this SDK-only corpus). Corroborating:
`promptSource == "sdk"` (prompt lines only). `userType` (`external`) and
`isSidechain` (`false`) are identical to CC and do **not** discriminate.
Version-specific -- phrase as `entrypoint == "sdk-py"` for SDK >= 0.2.106 / CLI
2.1.185. *Cite: 119× `entrypoint=sdk-py`; `981cd27f...jsonl:3` `promptSource=sdk`.*

**(c) Main-session model/options metadata.** Model surfaces per assistant message
at `message.model`, equal to the configured `ClaudeAgentOptions.model` (verified
across two model values, haiku + sonnet). **No persisted options/init line** -- 0
`system` lines corpus-wide; the full options snapshot (`tools, mcp_servers, agents,
skills, plugins, permissionMode, cwd, apiKeySource, model, ...`) is delivered at
runtime as `SystemMessage(subtype="init")` and captured out-of-band into
`manifest.json` `init`, never written to JSONL. *Cite: `981cd27f...jsonl:8`
`message.model`; `manifest.json` `init`; 0 `system` lines.*

## 4. SDK-only / absent fields relative to the CC baseline

**Present in SDK bytes, NOT in the `CLAUDE.md` JSONL section:**
- `entrypoint` (`"sdk-py"`) -- every user/assistant line. *(`981cd27f...jsonl:3`)*
- `promptSource` (`"sdk"`) -- user prompt lines only. *(`981cd27f...jsonl:3`)*
- `toolUseResult.resolvedModel` -- concrete child model; on Agent results; not in baseline and (per #522) not yet in `claude-code-sessions`. *(`f63d80f5...jsonl:10`)*
- Claude Code threading/top-level fields not called out by the baseline: `userType, version, promptId, gitBranch, permissionMode, parentUuid, uuid, requestId`. *(top-level keys, `981cd27f...jsonl:3`/`:8`)*
- assistant `message` extras: `id, stop_reason, stop_sequence, stop_details, diagnostics`. *(`981cd27f...jsonl:8`)*
- `usage` extras: `cache_creation` (nested), `server_tool_use, service_tier, inference_geo, iterations, speed`. *(`981cd27f...jsonl:8`)*
- child-trace top-level extras: `agentId, attributionAgent` (self-label = own agentType), `sourceToolAssistantUUID` (intra-file threading). *(`ac1c3a7f/.../agent-a29449fa7d1602e91.jsonl`)*
- inline `<usage>subagent_tokens: N / tool_uses: N / duration_ms: N</usage>` text trailer inside the Agent `tool_result.content` -- present at **level 1** here (see refinement 2). *(`f63d80f5...jsonl`, `ac1c3a7f...jsonl`)*
- runtime-only `init` keys (never persisted): `slash_commands, claude_code_version, output_style, apiKeySource, memory_paths, fast_mode_state, analytics_disabled, product_feedback_disabled`. *(`manifest.json` `init`)*

**Documented in `CLAUDE.md` but ABSENT from this corpus (uncovered, not contradicted):**
- line types `system, progress, hook_progress, bash_progress, create, file-history-snapshot` -- 0 occurrences (not exercised by these runs).
- `toolUseResult.status: "success"` -- observed value is `"completed"` (§2).
- `persistedOutputPath`/`persistedOutputSize` -- only in the one `large` run (`6c141b2e`); spill-only, expected.

## 5. Refinements vs the earlier per-story sections

Everything material in the #518/#522/#519/#530 sections checks out against the
bytes. Three refinements for S4/#521:

1. **The "Types-to-skip gap" is documentation-resolved.** The earlier sections
   flag `queue-operation, attachment, last-prompt, ai-title` as absent from
   `CLAUDE.md`'s skip list (tracked in #528). The **current** `CLAUDE.md` lists all
   four. What remains is only the runtime `SKIP_TYPES` frozenset code change (still
   valid, unticketed) -- no outstanding doc gap.
2. **The `subagent_tokens` inline trailer is not level-2-exclusive.** #530 frames
   it as the depth>=2 fallback. In this corpus it also appears in the **level-1**
   Agent `tool_result.content`, *alongside* a full `toolUseResult`. Refinement: the
   trailer lives in the tool_result content text at all levels; the level-1-only
   artifact is the sibling `toolUseResult` object, not the trailer -- a parser must
   not treat the trailer's presence as a nesting signal.
3. **The `nested` multi-level layout is not re-verifiable from this corpus.** No
   `nested`-variant session exists here (only single-level: `ac1c3a7f` 2 children,
   `f63d80f5` 1). #530's "flat at all depths" + cross-file `toolUseId` join claims
   are carried from its own separate capture; the single-level 4-way linkage they
   build on **is** confirmed here.

One doc nit for #521/#528: the `status: "success"` example value in `CLAUDE.md`
does not match the observed `"completed"` -- a one-word example fix, not a schema
change.

## Net result for the roadmap

- **#520 ACs met:** structured per-dimension comparison (§1) + per-assumption
  holds/breaks/unknown cross-walk (§2), each with a sample-record citation; the
  three #112 questions answered with evidence (§3); SDK-only/absent fields
  enumerated (§4); all observations cite specific #519-corpus records.
- **#521 (S4)** synthesizes the durable findings doc + anonymized fixtures from
  §§1-4 and inherits three concrete doc/parser follow-ups from §5 (`SKIP_TYPES`
  code change; `status` example fix; the level-1 `subagent_tokens` trailer note).
- **#112** stays unblocked: reliable intrinsic discriminator (`entrypoint ==
  "sdk-py"`), co-located location, per-assistant model + `resolvedModel` for
  child-model routing, options runtime-only.
