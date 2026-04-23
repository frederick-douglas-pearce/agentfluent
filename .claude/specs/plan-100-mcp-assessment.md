# Plan — Epic #100: MCP server config assessment

## Context

Stretch epic for v0.3 (`epic:mcp-assessment`, `stretch` labels). Covers 5 stories:

- **#116** Parse MCP tool names from session + trace data
- **#117** Discover configured MCP servers from config files
- **#118** Implement MCP audit rules (signals + correlator rule)
- **#119** Integrate into `analyze --diagnostics` pipeline
- **#120** Tests

Stories are tightly coupled — extraction, discovery, audit, and wiring form one feature broken into review-sized PRs. This plan covers the full epic so shared interfaces are decided once; each story ships as its own PR in sequence.

Per Architect C's review of #100: no new framework — reuse `DiagnosticSignal` + `CorrelationRule`. Two new `SignalType` values, one new rule class. Matches the shape of model-routing (#111).

## Architect review — corrections applied

Architect review of this plan (posted 2026-04-23 on issue #100) identified three blocking issues. Corrections folded below:

1. **Settings-file locations corrected.** MCP servers are stored in `~/.claude.json` and `.mcp.json`, NOT in `~/.claude/settings.json` or `.claude/settings.json`. Verified against a real `~/.claude.json` on this system: top-level `mcpServers` key exists. Also confirmed: each per-project entry under `~/.claude.json`'s `projects` dict has its own `mcpServers`, `enabledMcpjsonServers`, and `disabledMcpjsonServers` fields — giving us 3 effective scopes (user-global, project-local, project-shared).

2. **Regex replaced with `rfind("__")` split.** The `[^_]` tool-start gate silently drops legal tool names that begin with underscore. Split-based parsing is simpler and more robust.

3. **`_infer_project_dir` removed.** CLI already has `project_info.path` available; thread `project_dir` explicitly through `run_diagnostics` as a kwarg. No slug reversal needed.

Plan updated throughout to reflect these corrections. Story #116 and #117 AC need matching updates on GitHub.

## Scope

**Owns:**
- `src/agentfluent/diagnostics/mcp_assessment.py` (NEW, ~150 lines) — extraction + audit logic
- `src/agentfluent/config/mcp_discovery.py` (NEW, ~120 lines) — settings-file reader
- `src/agentfluent/diagnostics/models.py` — append 2 `SignalType` values
- `src/agentfluent/diagnostics/correlator.py` — add `McpAuditRule`, append to `RULES`
- `src/agentfluent/diagnostics/pipeline.py` — wire extraction + discovery + audit into `run_diagnostics`
- `src/agentfluent/core/paths.py` — no changes needed (already handles `--claude-config-dir` root)
- `tests/fixtures/mcp/` (NEW dir) — 4 settings files + 2 session fixtures
- `tests/unit/test_mcp_discovery.py` (NEW) — config reader tests
- `tests/unit/test_mcp_assessment.py` (NEW) — extraction + audit tests
- `tests/unit/test_correlator.py` — +3 tests for `McpAuditRule`
- `tests/unit/test_diagnostics_pipeline.py` — +3 wiring tests

**Deferred to doc issue #155:**
- README section mentioning MCP assessment
- Screenshot of the new output section

**Explicitly out of scope:**
- No new CLI flag (reuse existing `--claude-config-dir`, `--diagnostics`)
- No cost estimation (MCP tool calls have no pricing dimension)
- No recommendations to install an MCP server — only adjust configuration of what the user already has or had
- No per-server quality scoring (that would be a diagnostics v2 concern)

## Module layout

```
src/agentfluent/
├── config/
│   └── mcp_discovery.py        # NEW — reads settings files, returns McpServerConfig list
└── diagnostics/
    ├── mcp_assessment.py        # NEW — extract_mcp_usage + audit logic
    ├── models.py                # +2 SignalType values
    ├── correlator.py            # +McpAuditRule, extend RULES
    └── pipeline.py              # wire extraction + discovery + audit

tests/
├── fixtures/mcp/                # NEW dir
│   ├── settings_user.json       # user-scope with 2 servers
│   ├── settings_project.json    # project-scope adds 1 server, overrides 1
│   ├── mcp_project.json         # .mcp.json with 1 more server
│   ├── legacy_user.json         # ~/.claude.json fallback format
│   ├── session_mcp_github.jsonl # session using mcp__github__* tools
│   └── session_mcp_missing.jsonl # session with failed mcp__slack__* calls
├── unit/test_mcp_discovery.py   # NEW
├── unit/test_mcp_assessment.py  # NEW
├── unit/test_correlator.py      # +3 tests
└── unit/test_diagnostics_pipeline.py # +3 tests
```

Files are split by responsibility: `config/mcp_discovery.py` reads *configuration* files (same concern as `config/scanner.py`); `diagnostics/mcp_assessment.py` extracts *observed* usage and audits it (same concern as `diagnostics/model_routing.py`). Matches the existing one-concern-per-file convention.

## New `SignalType` enum values

Append to `SignalType` StrEnum (definition order = iteration order — appending is safe):

```python
MCP_UNUSED_SERVER = "mcp_unused_server"
MCP_MISSING_SERVER = "mcp_missing_server"
```

Both carry `agent_type = ""` — MCP servers aren't scoped to a single agent_type. Downstream rendering that groups signals by agent_type should treat empty string as a "global" bucket. Existing signal consumers already handle empty `agent_type` for aggregate-level signals.

## Data models

### `McpServerConfig` (config layer)

```python
class McpServerConfig(BaseModel):
    """A configured MCP server discovered from settings files."""

    server_name: str
    enabled: bool = True
    """False when `disabled: true` in settings. Disabled servers are
    excluded from the unused-server check — they're intentionally off."""

    configured_tools: list[str] | None = None
    """Allow-list of tools if specified; None means 'all tools from
    this server'. None is the common case."""

    source_file: Path
    """The file this server was declared in. User-facing
    recommendations cite this path so the user knows what to edit."""

    scope: Literal["user", "project_shared", "project_local"]
    """Precedence source. `user` = ~/.claude.json top-level;
    `project_shared` = .mcp.json at project root (team-committed);
    `project_local` = per-project mcpServers inside ~/.claude.json."""
```

Lives in `src/agentfluent/config/models.py` (alongside `AgentConfig`).

### `McpServerUsage` (assessment layer)

```python
class McpServerUsage(BaseModel):
    """Observed usage of an MCP server across analyzed sessions."""

    server_name: str
    total_calls: int
    unique_tools: list[str]
    """Sorted list — set coerced to list for deterministic JSON output."""

    error_count: int
    """Calls where the tool_result had is_error=True or the output
    matched an ERROR_REGEX keyword."""
```

Lives in `src/agentfluent/diagnostics/mcp_assessment.py` as a dataclass (internal — not exposed on `DiagnosticsResult`).

## Parsing + extraction (#116)

Story #116 specifies regex `mcp__(?P<server>[^_]+)__(?P<tool>.+)`, but that pattern fails on real server names with underscores (observed on this system: `mcp__claude_ai_Gmail__authenticate`). An earlier iteration of this plan proposed a corrected regex `mcp__(?P<server>.+?)__(?P<tool>[^_].*)`, but architect review flagged a data-loss bug: the `[^_]` tool-start gate silently drops legal tool names that begin with underscore (MCP tool names are server-author-defined, so `_internal_sync` is allowed).

**Corrected approach — last-delimiter split:**

```python
def parse_mcp_tool_name(name: str) -> tuple[str, str] | None:
    """Split an MCP tool name into (server, tool) components.

    Returns None when the name doesn't match the `mcp__<server>__<tool>`
    shape. Uses the last `__` occurrence as the server/tool boundary,
    so server names with internal underscores parse correctly
    (e.g., `mcp__claude_ai_Gmail__authenticate`).

    Limitation: a server name containing `__` (double underscore) is
    fundamentally ambiguous in this format. Documented as a known
    constraint; not handled.
    """
    if not name.startswith("mcp__"):
        return None
    rest = name[5:]  # strip "mcp__"
    idx = rest.rfind("__")
    if idx <= 0:
        return None
    server, tool = rest[:idx], rest[idx + 2:]
    if not server or not tool:
        return None
    return server, tool
```

Verified against real-world examples:
- `mcp__github__create_issue` → `("github", "create_issue")` ✓
- `mcp__claude_ai_Gmail__authenticate` → `("claude_ai_Gmail", "authenticate")` ✓
- `mcp__some_server___internal_sync` → `("some_server", "_internal_sync")` ✓ (correctly handles leading-underscore tool)
- `mcp__github__` → `None` (empty tool)
- `mcp_github_create_issue` → `None` (missing `__` prefix)
- Non-MCP tool `Bash` → `None`

No regex needed — the split approach is more robust and easier to test.

### `extract_mcp_usage` signature

```python
def extract_mcp_usage(
    invocations: list[AgentInvocation],
) -> dict[str, McpServerUsage]:
    """Aggregate observed MCP tool usage keyed by server_name.

    Walks each invocation's subagent trace (preferred — carries
    is_error directly) AND the invocation's message-level content
    blocks for any tool_use with name matching MCP_TOOL_REGEX.

    Non-MCP tool names are silently ignored. Returns empty dict when
    no MCP tools are found.

    Error counting: trace-path uses SubagentToolCall.is_error.
    Message-path scans the paired tool_result text for ERROR_REGEX
    keywords (same source as metadata-level ERROR_PATTERN detection).
    """
```

Walking invocations (not raw messages) lets us reuse the existing invocation→trace pairing. The message-level pass catches MCP calls made directly from the parent session when no subagent was involved.

Input shape clarification — #116's AC says the extractor takes `messages` and `traces`, but we actually want invocations as the unit (they already pair both). This is a small AC deviation; note it in the PR.

## Config discovery (#117)

### File locations and scopes (architect-corrected)

Per Claude Code's actual config model, verified against real `~/.claude.json` on this system:

| Scope | Source | Notes |
|-------|--------|-------|
| `user` | `~/.claude.json` top-level `mcpServers` | User-global servers |
| `project_local` | `~/.claude.json` `projects[<project_dir>].mcpServers` | Per-project overrides stored in user file |
| `project_shared` | `.mcp.json` at `project_dir` | Committed to repo, shared with team |

**Root overridable by `--claude-config-dir`**: `~/.claude.json` only. `.mcp.json` is always at `project_dir` regardless.

**Not MCP-relevant** (do NOT read):
- `~/.claude/settings.json` — hooks and other settings; no `mcpServers` key
- `.claude/settings.json` — project settings; no `mcpServers` key

### Precedence

Per Claude Code's documented resolution order (`local > project > user`):

```
project_local (~/.claude.json:projects[<p>].mcpServers)
  > project_shared (.mcp.json)
  > user (~/.claude.json:mcpServers)
```

When the same `server_name` appears in multiple sources, the canonical `McpServerConfig` record uses the winning source's fields; the `source_file` field points at the winning file.

### Project-shared gating via `enabledMcpjsonServers` / `disabledMcpjsonServers`

Each per-project entry in `~/.claude.json` can also carry:

- `disabledMcpjsonServers: list[str]` — server names from `.mcp.json` that are disabled for this user on this project. These MUST be honored — a disabled `.mcp.json` server should not flag as unused (user intentionally turned it off).
- `enabledMcpjsonServers: list[str]` — opt-in list. Semantics unclear from docs: could be "only these servers are enabled" (whitelist) or "these are pre-approved" (provenance marker). For v0.3, treat absence of this list as "all servers enabled" and presence of this list as a whitelist — if it turns out to be the provenance interpretation, we adjust later. **Track as an architect-worthy question for implementation time.**

For a `.mcp.json` server at audit time:
```
effective_enabled = (server_name in enabledMcpjsonServers if enabledMcpjsonServers else True)
                    AND (server_name not in disabledMcpjsonServers)
```

### File format summaries

**`~/.claude.json`** (large file; we read only two keys):
```json
{
  "mcpServers": { "<name>": { "type": "...", "command": "...", "args": [...] } },
  "projects": {
    "<absolute project path>": {
      "mcpServers": { "<name>": { ... } },
      "enabledMcpjsonServers": ["..."],
      "disabledMcpjsonServers": ["..."]
    }
  }
}
```

**`.mcp.json`** (project root):
```json
{
  "mcpServers": {
    "<name>": { "command": "...", "args": [...] }
  }
}
```

Per-server fields we care about:
- `disabled` (optional, default `false`): Note — observed real data does NOT include this field on `~/.claude.json` entries. Keep parser robust to its absence.
- `tools` (optional, default `null`): per-server allow-list if present. Not always set.
- `command`, `args`, `type`, `env`: ignored for audit purposes.

Other fields are ignored.

### `discover_mcp_servers` signature

```python
def discover_mcp_servers(
    claude_config_dir: Path | None,
    project_dir: Path | None,
) -> list[McpServerConfig]:
    """Discover configured MCP servers across user, project-shared,
    and project-local scopes. Returns one McpServerConfig per unique
    server_name, using precedence `project_local > project_shared >
    user` to pick the canonical record for duplicates.

    When `project_dir` is None, only user-scope is read (both
    project_local and project_shared require a project_dir). This
    matches the "analyze without --project" case where we don't know
    which project's files to consult.

    Missing files return an empty contribution (silent skip). Malformed
    JSON logs a warning and skips that file — same behavior as
    `scan_agents`.
    """
```

Internal helpers:

- `_read_user_mcp_servers(claude_json_path) -> list[McpServerConfig]` — reads top-level `mcpServers`, scope=`user`.
- `_read_project_local_mcp_servers(claude_json_path, project_dir) -> list[McpServerConfig]` — walks `projects[<project_dir>].mcpServers`, scope=`project_local`. Also returns the per-project `enabledMcpjsonServers` / `disabledMcpjsonServers` lists as a companion return so the driver can apply them to `.mcp.json` entries.
- `_read_project_shared_mcp_servers(project_dir, enabled_list, disabled_list) -> list[McpServerConfig]` — reads `.mcp.json`, applies the enabled/disabled gating lists from the project-local scope, scope=`project_shared`. Servers filtered out by the gating lists set `enabled=False` (not removed — they still appear in the config set so the missing-check knows they exist).

Driver composes:

```python
claude_json = (claude_config_dir or DEFAULT_CLAUDE_CONFIG_DIR) / "../.claude.json"
# (actually ~/.claude.json sits at home root, adjust path helper)

user_servers = _read_user_mcp_servers(claude_json)
project_local, enabled_list, disabled_list = (
    _read_project_local_mcp_servers(claude_json, project_dir)
    if project_dir else ([], None, [])
)
project_shared = (
    _read_project_shared_mcp_servers(project_dir, enabled_list, disabled_list)
    if project_dir else []
)
return _dedup_by_name_with_precedence(
    user_servers, project_shared, project_local,
)
```

Path helper: `~/.claude.json` is at the home root, not inside the `.claude/` subdirectory. Add a helper `claude_json_for(config_root: Path | None) -> Path` to `core/paths.py`:

```python
def claude_json_for(config_root: Path | None) -> Path:
    """Path to ~/.claude.json (the primary user config file).

    Lives at $HOME/.claude.json, parallel to the ~/.claude/ directory.
    When config_root is given (e.g., from --claude-config-dir), we
    resolve .claude.json relative to its parent — matches the pattern
    of overriding the entire Claude Code config hierarchy.
    """
    if config_root is None:
        return Path.home() / ".claude.json"
    # config_root points at e.g. /custom/.claude/, so .claude.json
    # sits at /custom/.claude.json — one directory up.
    return config_root.parent / ".claude.json"
```

**Architect-worthy question for implementation time**: what if `--claude-config-dir /custom/path/` is passed where `/custom/path/` has no parent semantics for `.claude.json`? Document behavior; possibly require the override to be a directory that has a sibling `.claude.json` file.

## Audit rules (#118)

### Two signal emitters

```python
def audit_mcp_servers(
    usage_by_server: dict[str, McpServerUsage],
    configured: list[McpServerConfig],
) -> list[DiagnosticSignal]:
    """Compare observed usage vs configured servers; emit signals."""
    signals: list[DiagnosticSignal] = []
    signals.extend(_detect_unused_servers(usage_by_server, configured))
    signals.extend(_detect_missing_servers(usage_by_server, configured))
    return signals
```

**`_detect_unused_servers`** — configured, enabled, but `total_calls == 0`:

```python
detail = {
    "server_name": server.server_name,
    "source_file": str(server.source_file),
    "configured_tools": server.configured_tools,
    "sessions_analyzed": len(invocations_in_scope),  # passed in
}
message = (
    f"MCP server '{server.server_name}' is configured in "
    f"{server.source_file} but has 0 tool calls across "
    f"{sessions_analyzed} analyzed sessions. "
    "Consider removing from mcpServers or marking as disabled."
)
severity = Severity.INFO
```

INFO severity — unused servers are advisory, not broken.

**`_detect_missing_servers`** — observed usage references a server name not in `configured`, AND at least one of those observed calls had `is_error=True`:

```python
detail = {
    "server_name": usage.server_name,
    "error_count": usage.error_count,
    "total_calls": usage.total_calls,
    "unique_tools": usage.unique_tools,
    "user_settings_path": str(user_settings_path),  # passed in
    "project_settings_path": str(project_settings_path),
}
message = (
    f"{usage.error_count} failed calls to mcp__{usage.server_name}__* "
    f"across {sessions_analyzed} sessions, but no '{usage.server_name}' "
    f"server configured. Add to .mcp.json or {user_settings_path}."
)
severity = Severity.WARNING
```

WARNING severity — failed calls indicate the user *wanted* this server and it's not there.

**Gate on error_count, not total_calls** — a non-configured server with 0 errors means the calls actually succeeded (mystery, but not broken). The failure case is the actionable one.

### `McpAuditRule`

Matches on both MCP signal types, routes target:

```python
class McpAuditRule:
    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type in (
            SignalType.MCP_UNUSED_SERVER,
            SignalType.MCP_MISSING_SERVER,
        )

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        # Action text is already in signal.message; recommendation
        # restructures it into observation/reason/action triples.
        ...
```

`target="mcp"` — new target string. Existing formatter already groups recommendations by target, so rendering is free (see "CLI rendering" section below).

Append to `RULES` in `correlator.py` after the model-routing rules:

```python
RULES: list[CorrelationRule] = [
    # ... existing rules ...
    ModelRoutingRule(),
    McpAuditRule(),
]
```

Order-within-target doesn't matter (each rule matches distinct signal types).

## Pipeline wiring (#119)

### `run_diagnostics` signature change

Add two keyword-only args with safe defaults — additive, no existing callers break:

```python
def run_diagnostics(
    invocations: list[AgentInvocation],
    *,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    claude_config_dir: Path | None = None,  # NEW
    project_dir: Path | None = None,        # NEW
) -> DiagnosticsResult:
```

The CLI call site (`src/agentfluent/cli/commands/analyze.py`) already has `project_info.path` — just pass it as `project_dir=project_info.path`. No slug reversal, no inference helper.

When `project_dir is None` (e.g., someone calls `run_diagnostics` programmatically without specifying a project), MCP discovery runs user-scope only. That degrades gracefully: user-global MCP servers are still audited; project-scoped servers aren't.

### Inside `run_diagnostics`

```python
# diagnostics/pipeline.py, after model_routing signal extraction
mcp_usage = extract_mcp_usage(invocations)
configured_mcp = discover_mcp_servers(
    claude_config_dir=claude_config_dir,
    project_dir=project_dir,
)

# Silent skip when neither side has content — no signals, no empty
# "MCP Assessment" section in rendered output.
if mcp_usage or configured_mcp:
    mcp_signals = audit_mcp_servers(
        mcp_usage,
        configured_mcp,
        sessions_analyzed=subagent_trace_count or len(invocations),
    )
    signals.extend(mcp_signals)
```

**Architect confirmed** the AND condition for silent-skip (`mcp_usage == {} AND configured == []`) is correct — either side being non-empty is actionable:

- `usage=={} AND configured!=[]` → unused servers should flag
- `usage!={} AND configured==[]` → missing servers should flag

## Disabled-server semantics

Two interactions:

1. **Unused check**: `disabled=true` servers are excluded (per story #118 AC). Enforced in `_detect_unused_servers` by `if not server.enabled: continue`.

2. **Missing check**: if a user *disabled* a server and then tries to use it, do we flag missing? **Yes** — `configured` for missing-check purposes is the full set regardless of enabled state. A disabled server that gets called is still "present in config" — the failure reason is different (disabled, not missing) but from the MCP layer we can't distinguish. If this becomes noisy, a follow-up ticket can add a third signal `MCP_DISABLED_SERVER_USED` with WARNING severity. Not in this plan.

## CLI rendering

**No new rendering code.** The existing table formatter (`cli/formatters/table.py`) groups `DiagnosticRecommendation` by `target`. Adding `target="mcp"` produces a new "MCP Configuration" section automatically, identical in structure to existing target groupings. Verify once during integration testing.

JSON envelope — `DiagnosticsResult.signals` and `.recommendations` are already serialized by Pydantic; new signal types serialize as their string values. No schema change required.

**Doc issue #155** covers the screenshot of the new section.

## Tests (#120)

### `tests/unit/test_mcp_discovery.py` (~11 tests)

Organized into classes by concern:

- **`TestReadUserScope`** (3): valid `~/.claude.json` with `mcpServers`, missing file (silent), malformed JSON (warn)
- **`TestReadProjectLocal`** (2): per-project `mcpServers` inside `~/.claude.json:projects[<path>]`; absent project entry returns empty
- **`TestReadProjectShared`** (3): valid `.mcp.json`, missing file (silent), `disabledMcpjsonServers` from project-local correctly sets `enabled=False` on the shared entry
- **`TestPrecedence`** (2): `project_local > project_shared > user` when same server_name appears in multiple scopes; `source_file` points at winning source
- **`TestConfigDirOverride`** (1): `--claude-config-dir` redirects `~/.claude.json` resolution; `.mcp.json` path is unaffected

### `tests/unit/test_mcp_assessment.py` (~12 tests)

- **`TestExtractMcpUsage`** (4): regex matches server-with-underscores, non-MCP tools skipped, is_error from trace propagates, error-pattern detection on message-level tool_result
- **`TestDetectUnusedServers`** (3): unused + enabled → signal, unused + disabled → no signal, used server → no signal
- **`TestDetectMissingServers`** (3): observed with errors + not configured → signal, observed without errors + not configured → no signal, observed + configured → no signal
- **`TestAuditIntegration`** (2): empty usage + empty configured → empty signal list; mixed inputs produce expected signals per server

### `tests/unit/test_correlator.py` — `TestMcpAuditRule` (3 tests)

- Matches MCP_UNUSED_SERVER → target="mcp", severity=INFO, references source_file
- Matches MCP_MISSING_SERVER → target="mcp", severity=WARNING, references project+user paths
- Non-MCP signal → no match

### `tests/unit/test_diagnostics_pipeline.py` — `TestMcpWiring` (3 tests)

- Invocations with MCP tools + configured servers → recommendations appear with target="mcp"
- Invocations without any MCP content → pipeline unchanged from pre-#100 (no empty section)
- Pipeline receives `claude_config_dir` kwarg and threads through to `discover_mcp_servers`

### Fixtures

```
tests/fixtures/mcp/
├── claude_user.json            # ~/.claude.json with top-level mcpServers
├── claude_with_project.json    # ~/.claude.json with projects[<path>] section
│                                 containing mcpServers + enabledMcpjsonServers
│                                 + disabledMcpjsonServers
├── mcp_project.json            # .mcp.json with 2 servers
├── mcp_project_disabled.json   # .mcp.json where one server is gated off
├── session_mcp_github.jsonl    # uses mcp__github__create_issue, all successful
└── session_mcp_missing.jsonl   # uses mcp__nonexistent__call, 3 failed
```

Sessions use the existing `tests/_builders.py` helpers to minimize boilerplate.

## Edge cases

- **Server name with underscores**: covered by regex fix (architect-flag candidate).
- **Tool name starting with underscore**: filtered out by `[^_]` gate — pathological, don't surface.
- **Server in config but never used, fewer than N sessions analyzed**: still flag (it's 0 calls regardless of denominator). The `sessions_analyzed` field in detail lets the user judge confidence.
- **Same server in 2 files both disabled**: treat as disabled. Precedence resolves the canonical record; `enabled` is copied from the winner.
- **Tools allow-list mismatch**: observed tool is in usage but not in `configured_tools` allow-list. **Not flagged in this plan** — the allow-list is a rarely-set field and interpreting it as "only these tools should be used" vs "advisory list" is ambiguous. Deferred.
- **Missing projects/ directory on `claude_config_dir` override**: `discover_mcp_servers` already handles missing files silently; this is just more of the same.
- **Invocations from multiple projects rolled into one analyze call**: project-scope settings will be ambiguous (which project's `.mcp.json`?). `_infer_project_dir` picks the first one seen; others' project-scope files are ignored. Not a v0.3 concern — `analyze --project X` is the canonical entry.
- **Settings file is an array instead of object**: `isinstance(data.get("mcpServers"), dict)` gate → skip with warn.
- **Empty `mcpServers: {}` → no contribution, silent**.

## Implementation order (story breakdown)

Each story = one PR. All stories land on feature branches `feature/<number>-<desc>`.

1. **#117** (discovery) first — pure config reading, no dependency on session analysis. Unlocks #118's correlator.
2. **#116** (extraction) second — pure session/trace reading, no dependency on discovery.
3. **#118** (audit rules) third — depends on both extractor and discovery interfaces.
4. **#119** (pipeline) fourth — depends on all three prior stories. Wires `run_diagnostics` call chain and threads `claude_config_dir` through.
5. **#120** (tests) is consolidated into each story's PR as tests land alongside code (standard pattern). The #120 ticket closes when all other stories merge, or can be re-scoped to "integration tests" if story-level unit tests aren't sufficient.

**Optional consolidation**: if all 4 code stories are small enough to land together, a single PR against `feature/100-mcp-assessment` is acceptable. Decide after #117 lands and the feature's actual size is known.

## Verification commands

```bash
uv run pytest tests/unit/test_mcp_discovery.py -v         # ~10 tests
uv run pytest tests/unit/test_mcp_assessment.py -v        # ~12 tests
uv run pytest tests/unit/test_correlator.py -v            # +3 new
uv run pytest tests/unit/test_diagnostics_pipeline.py -v  # +3 new
uv run pytest -m "not integration"                        # full suite (597 → ~625)
uv run mypy src/agentfluent/
uv run ruff check src/ tests/
# Dogfood: run against a real session with MCP tools
uv run agentfluent analyze --project agentfluent --diagnostics
```

## Definition of done

- `SignalType` has `MCP_UNUSED_SERVER` and `MCP_MISSING_SERVER` appended
- `config/mcp_discovery.py` reads all 4 file locations with correct precedence
- `diagnostics/mcp_assessment.py` extracts MCP usage and audits against config
- `McpAuditRule` in `correlator.py`, target="mcp"
- `run_diagnostics` wires discovery + audit with silent-skip when no MCP content
- `--claude-config-dir` override propagates correctly to user-scope settings paths
- ~28 new tests; mypy strict + ruff clean
- No regression in existing 597 tests
- Doc issue #155 lists MCP feature for README update

## Risks and traps

1. **Story #116 and #117 AC drift** — original acceptance criteria on both tickets were wrong (#116's regex excludes server names with underscores; #117 lists `settings.json` files that don't carry `mcpServers`). Both AC lists need updating on GitHub before implementation starts so they match this plan. Highlight in each PR body.
2. **`enabledMcpjsonServers` semantic ambiguity** — whether this is a whitelist or a provenance marker isn't clear from Claude Code docs. Plan treats presence as whitelist, absence as "all enabled." If this turns out wrong during implementation, adjust the `_read_project_shared_mcp_servers` helper and add a regression test.
3. **`~/.claude.json` path derivation under `--claude-config-dir`** — `.claude.json` sits at home root (`$HOME/.claude.json`), not inside the `.claude/` subdirectory. The override helper resolves it relative to the override's parent. If the user passes an override path with no parent `.claude.json` sibling, discovery gracefully returns empty user-scope.
4. **Disabled-server "used" scenario** — intentional scope cut. Follow-up ticket for `MCP_DISABLED_SERVER_USED` if it becomes common. Current behavior: disabled server that gets called produces no signal at all (not unused, not missing, not disabled-used) — preferable to a wrong signal.
5. **`scan_agents` and `discover_mcp_servers`** — read different files (`.claude/agents/*.md` vs `~/.claude.json` + `.mcp.json`), no conflict. No shared state between them.
6. **Claude Code config schema drift** — possible but unlikely in the near term. Plan is already defensive (tolerant of missing fields). If schemas change in a future Claude Code release, add regression tests using new real-data samples.
7. **SignalType enum insertion vs append** — must append. Same invariant as all previous enum additions.
8. **`McpServerUsage` as internal-only** — don't add to `DiagnosticsResult` unless a consumer actually needs it. Keeps the public contract lean; the `signals` list carries everything a user sees.
9. **`project_dir` can be None** — programmatic callers of `run_diagnostics` (e.g., tests) may not pass a project_dir. Discovery gracefully degrades to user-scope-only. Wiring tests cover both paths.

## Critical files

- **NEW:** `src/agentfluent/config/mcp_discovery.py`
- **NEW:** `src/agentfluent/diagnostics/mcp_assessment.py`
- **NEW:** `tests/unit/test_mcp_discovery.py`
- **NEW:** `tests/unit/test_mcp_assessment.py`
- **NEW:** `tests/fixtures/mcp/*` (6 fixture files)
- **Modified:** `src/agentfluent/diagnostics/models.py` (+2 `SignalType`)
- **Modified:** `src/agentfluent/config/models.py` (+McpServerConfig)
- **Modified:** `src/agentfluent/diagnostics/correlator.py` (+McpAuditRule, extend RULES)
- **Modified:** `src/agentfluent/diagnostics/pipeline.py` (wire extract + discovery + audit, add `claude_config_dir` kwarg)
- **Modified:** `tests/unit/test_correlator.py` (+TestMcpAuditRule)
- **Modified:** `tests/unit/test_diagnostics_pipeline.py` (+TestMcpWiring)
- **Read-only reference:** `src/agentfluent/config/scanner.py` (settings file read pattern)
- **Read-only reference:** `src/agentfluent/diagnostics/model_routing.py` (aggregate-signal wiring template)
- **Read-only reference:** `src/agentfluent/core/paths.py` (`claude_config_dir` resolution)
