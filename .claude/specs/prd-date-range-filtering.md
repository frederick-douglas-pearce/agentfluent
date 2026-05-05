# PRD: Date/Time-Range Filtering for `analyze`, `list`, and `diff`

**Status:** Draft
**Date:** 2026-05-05
**Author:** PM Agent
**Decision log:** See `decisions.md` entries D024, D025.
**Epic:** `epic:date-range-filtering`
**Milestone:** v0.6.0

---

## 1. Problem Statement

AgentFluent has no way to scope analysis to a time window. After making a config change to an agent (e.g., fixing a misconfig surfaced by `agentfluent analyze --diagnostics`), the user cannot verify the fix by analyzing only post-change sessions. New sessions are diluted by the historical baseline.

This blocks two real workflows:

1. **Verification of config changes.** "Did my fix to pm.md actually reduce retry_loop signals?" requires comparing pre-change to post-change windows.
2. **Retroactive baseline generation.** `agentfluent diff` consumes pre-generated `analyze --json` files, but you can only generate a baseline proactively (before a change). There is no "give me a baseline as of yesterday" because `analyze` cannot be scoped to a time window.

Surfaced during a 2026-05-05 dogfooding session: after applying fixes from issues #291 and #292, the user wanted to re-run `analyze` scoped to only post-fix sessions. The existing `--latest N` flag is count-based, not time-based, and does not express "sessions since the fix."

### Why `--latest N` is insufficient

- It cannot express "sessions before a change" for baseline generation.
- When N is known, it requires the user to manually count sessions since the fix.
- It does not compose with the `diff` workflow: there is no way to produce "baseline as of 2 days ago" without knowing the exact session count at that time.

## 2. Goals

1. **Enable time-based scoping** on `analyze` so users can target specific temporal windows.
2. **Enable retroactive baseline generation** for `diff` by scoping `analyze --json` to a historical window.
3. **Propagate time filtering to `list`** so users can preview which sessions a window covers before committing to an expensive analysis.
4. **Compose cleanly** with existing flags (`--latest`, `--session`, `--agent`).
5. **Keep the implementation simple and fast** -- filtering happens at the session-loading layer, not deep in the analytics pipeline.

## 3. Non-Goals

- **Diff-level `--since`/`--until` flags.** The `diff` command compares two pre-generated files; adding time filtering there duplicates the `analyze` workflow and complicates the architecture. The `analyze` layer is the right place to scope time windows. (Revisit if workflow friction is observed.)
- **Sub-message-level analytics recomputation.** Metrics computed per-session (diagnostics signals, tool-sequence clustering) will not be recomputed on a partial session. See Section 7 for the partial-session policy.
- **Timezone conversion UI.** The CLI accepts and emits UTC. Users provide UTC or local times (auto-converted to UTC). No timezone management UI.
- **Persistent window presets** (e.g., named baselines, "default since"). Premature; evaluate after the basic filter ships.

## 4. Timestamp Dimension Decision

### The question

Which timestamp should `--since`/`--until` filter on?

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| A. Per-message timestamp | Filter individual messages within sessions | Maximally precise; inclusive of straddling sessions | Forces partial-session semantics everywhere; expensive (must parse every session to filter) |
| B. Session file mtime | Filter whole sessions by filesystem modification time | Fast (stat-only, no parsing); simple | mtime can be altered by file copies, syncs; doesn't reflect session start |
| C. First-message timestamp | Filter whole sessions by their earliest message | Reflects session start; deterministic from content | Requires parsing the first line of each file |
| D. Last-message timestamp | Filter by session end | Answers "sessions that were active during the window" | Counter-intuitive for `--since` ("since X" should mean "started since X") |

### Decision: Option C -- Session-level filtering on first-message timestamp

**Rationale:**

1. **Per-message (A) is architecturally expensive for the value it provides.** The analytics pipeline computes metrics per-session (token totals, tool patterns, diagnostics signals). Partial-session inclusion would require either (a) re-running the pipeline on a filtered message subset per session, or (b) accepting that metrics are "whole session" even when only some messages fall in the window. Option (b) defeats the purpose; option (a) is a large refactor of `analyze_session()`.

2. **The primary workflow is "sessions after I made a config change."** Config changes happen between sessions, not mid-session. The user's mental model is "give me the sessions that started after my fix." First-message timestamp is the closest proxy for "when did this session begin."

3. **mtime is unreliable.** File copies, cloud sync (Dropbox, iCloud), and backup restores alter mtime. Content-derived timestamps are deterministic.

4. **First-message timestamp is cheap to extract.** `iter_raw_messages()` already yields messages in file order. Reading the first analytical message's timestamp requires parsing at most a few lines per file (skipping non-analytical types). This can be cached on `SessionInfo` during discovery.

5. **The "straddling session" edge case is rare and acceptable.** A session that spans a boundary (started before the cutoff, continued after) is included or excluded entirely based on its start time. For the primary "verify a fix" workflow, this is correct: a session that started before the fix was applied cannot verify the fix, even if it continued after. For the "retroactive baseline" workflow, slight overlap at boundaries is tolerable because `diff` reports deltas, not absolutes.

**Tradeoff acknowledged:** If a user runs a single multi-hour session, makes a config change mid-session, and wants to see only post-change behavior from that session, this approach cannot help. That user needs `--session <uuid>` plus manual inspection. This is an acceptable edge case; the solution is to end and restart sessions around config changes (which Claude Code already does naturally -- sessions rarely span config edits).

**Escalation to human:** If you later find that the per-message approach is needed (e.g., Agent SDK long-running daemon sessions that never restart), this decision should be revisited. The session-level approach is forward-compatible -- the `SessionInfo` model can later expose `first_message_timestamp` and `last_message_timestamp`, enabling a future `--include-straddling` flag without breaking existing behavior.

## 5. CLI Surface

### New flags on `analyze`

```
--since DATETIME   Include only sessions that started at or after this time.
--until DATETIME   Include only sessions that started before this time.
```

### New flags on `list --project <slug>`

Same `--since`/`--until` semantics. Allows users to preview which sessions fall in a window before running a full analysis.

### Accepted datetime formats

| Format | Example | Interpretation |
|--------|---------|----------------|
| ISO 8601 with timezone | `2026-05-05T12:00:00+00:00` | Exact |
| ISO 8601 without timezone | `2026-05-05T12:00:00` | Treated as local time, converted to UTC |
| Date only | `2026-05-05` | Start of day (00:00:00) in local time |
| Relative: `Nd` | `7d` | N days ago from now (UTC) |
| Relative: `Nh` | `12h` | N hours ago from now (UTC) |
| Relative: `Nm` | `30m` | N minutes ago from now (UTC) |

**No `yesterday`, `today`, `last week` keywords.** These are ambiguous across timezones and add parsing complexity without material value over `1d` and `0d`. Keep it simple.

**UTC default for display.** All timestamps in output (JSON, table) are UTC ISO 8601. Input without an explicit timezone is treated as the system's local timezone and converted to UTC for filtering.

### Interaction with existing flags

| Combination | Behavior |
|-------------|----------|
| `--since` + `--until` | Conjunction: sessions whose start time is in `[since, until)` |
| `--since` + `--latest N` | Apply `--since` first (filter to sessions in window), then take the N most recent within that window |
| `--until` + `--latest N` | Apply `--until` first, then take the N most recent before the cutoff |
| `--since` + `--session <uuid>` | Error: `--session` is an exact lookup; time filtering is incompatible. Emit a clear error message. |
| `--since` + `--agent <type>` | Orthogonal: `--since`/`--until` filter sessions, `--agent` filters invocations within those sessions. Both apply. |
| `--since` only | Open-ended: all sessions from `since` to now |
| `--until` only | Open-ended: all sessions from the earliest to `until` |

### Flag naming rationale

`--since`/`--until` chosen over alternatives:
- `--after`/`--before`: ambiguous (exclusive or inclusive?). `--since` is conventionally inclusive.
- `--from`/`--to`: `--from` conflicts with Python's `from` keyword in some tooling contexts; `--to` is less clear than `--until`.
- `--start`/`--end`: confusable with session start/end times.

`--since` uses inclusive semantics (>=), `--until` uses exclusive semantics (<). This is the half-open interval `[since, until)` convention used by `git log --since/--until` and most time-series tools.

## 6. JSON Output Schema Impact

### New metadata field

The `analyze --json` envelope gains a top-level `window` field in the `data` object:

```json
{
  "version": "2",
  "command": "analyze",
  "data": {
    "window": {
      "since": "2026-05-04T00:00:00+00:00",
      "until": "2026-05-05T00:00:00+00:00",
      "session_count_before_filter": 19,
      "session_count_after_filter": 4
    },
    "session_count": 4,
    "token_metrics": { ... },
    ...
  }
}
```

When no time filter is applied, `window` is `null` (not omitted -- explicit null signals "no filter applied").

### Impact on `diff`

The `diff` module (`loader.py`) does not require the `window` field -- it is not in `_REQUIRED_KEYS`. This means:
- Comparing a pre-window baseline (no `window` field) against a post-window current run works without error.
- The diff output can optionally display the windows of both inputs for context.
- No schema version bump required (`window` is additive; existing consumers ignore unknown keys).

The envelope schema version stays at `"2"`. The `window` field is purely additive metadata.

## 7. Partial-Session Policy

### The problem

Diagnostics signals (e.g., `RETRY_LOOP`, `STUCK_PATTERN`, `TOOL_ERROR_SEQUENCE`) and analytics metrics (token totals, tool counts) are computed per-session. If a session straddles a time boundary, should we recompute on a partial message subset?

### Decision: Whole-session semantics; no partial-session recomputation

A session is either **in** the window (based on first-message timestamp) or **out**. There is no partial inclusion. This means:

- Token totals for a "borderline" session include all messages in that session, even if some messages have timestamps outside the window.
- Diagnostics signals computed from that session reflect the full session's behavior.

**Why this is acceptable:**

1. The session is the natural unit of agent execution. A retry loop that starts before the boundary and continues after is still one behavioral event -- splitting it would lose the signal.
2. The first-message timestamp filter already answers the user's question: "which sessions started in this window?" If the session started in the window, its full behavior is relevant to the "how did things go after my change" question.
3. Implementation simplicity: no changes to `analyze_session()`, `compute_token_metrics()`, or any diagnostics rule.

**Documentation note:** The CLI `--help` text and the JSON `window` metadata should make clear that filtering is at session granularity, not message granularity. Example: "Sessions whose first message timestamp is at or after --since and before --until."

## 8. Implementation Architecture

### Layer 1: Timestamp extraction during discovery

Extend `SessionInfo` with a `first_message_timestamp: datetime | None` field. Populated during `discover_sessions()` by reading the first analytical message's timestamp from each JSONL file.

**Performance consideration:** This adds one `open()` + read-until-first-timestamp per session file during discovery. For the observed scale (19 sessions per project), this is negligible. For large projects (hundreds of sessions), the per-file cost is bounded by `iter_raw_messages()` stopping after the first valid timestamp (typically lines 1-5 of the file).

### Layer 2: Session filtering in the CLI command

Between discovery and `analyze_sessions()`, filter `session_infos` by comparing `first_message_timestamp` against the `--since`/`--until` bounds. This is a simple list comprehension at the same point where `--latest` is currently applied:

```python
# Current flow:
session_infos = project_info.sessions
if session: session_infos = [s for s in session_infos if s.filename == session]
if latest: session_infos = session_infos[:latest]

# After this feature:
session_infos = project_info.sessions
if session: session_infos = [s for s in session_infos if s.filename == session]
if since: session_infos = [s for s in session_infos if s.first_message_timestamp and s.first_message_timestamp >= since]
if until: session_infos = [s for s in session_infos if s.first_message_timestamp and s.first_message_timestamp < until]
if latest: session_infos = session_infos[:latest]
```

### Layer 3: Datetime parsing utility

A shared utility module (`core/timeutil.py` or similar) that:
- Parses ISO 8601 with/without timezone
- Parses date-only strings
- Parses relative strings (`7d`, `12h`, `30m`)
- Returns a timezone-aware `datetime` (UTC)
- Raises a clear error for unparseable input

### Layer 4: JSON output enrichment

`AnalysisResult` (or the CLI's JSON formatting path) includes the `window` metadata when time filtering was applied.

## 9. Diff Workflow Ergonomics

### The workflow today

```bash
# Before a config change:
agentfluent analyze --project myproject --json > baseline.json

# Make the config change...

# After some sessions:
agentfluent analyze --project myproject --json > current.json
agentfluent diff baseline.json current.json
```

**Problem:** Requires proactive baseline capture. If you forgot, there is no retroactive way.

### The workflow with `--since`/`--until`

```bash
# Retroactive baseline (sessions before the change):
agentfluent analyze --project myproject --until 2026-05-04T18:00:00 --json > baseline.json

# Current state (sessions after the change):
agentfluent analyze --project myproject --since 2026-05-04T18:00:00 --json > current.json

# Compare:
agentfluent diff baseline.json current.json
```

Or using relative time:

```bash
# Last 24h vs previous 24h:
agentfluent analyze --project myproject --since 48h --until 24h --json > baseline.json
agentfluent analyze --project myproject --since 24h --json > current.json
agentfluent diff baseline.json current.json
```

### Why `diff` does NOT need its own `--since`/`--until`

The `diff` command compares two pre-generated JSON files. Adding time filtering to `diff` would mean either:
- Re-running analysis internally (duplicating `analyze`), or
- Filtering the already-aggregated JSON (lossy and complex).

Neither is warranted. The two-step workflow (`analyze --since X --json > file; diff ...`) is explicit, composable, and scriptable. A future convenience wrapper (e.g., `agentfluent diff --project P --split-at 2026-05-04`) can be added later if friction is observed.

## 10. Scope and Sequencing

### Stories (priority order)

1. **Timestamp extraction in discovery** -- extend `SessionInfo`, populate during `discover_sessions()`
2. **Datetime parsing utility** -- `core/timeutil.py` with ISO 8601, date-only, and relative format support
3. **`list` time filtering** -- add `--since`/`--until` to `list --project`, wire up session filtering
4. **`analyze` time filtering** -- add `--since`/`--until` to `analyze`, wire up session filtering, flag interactions
5. **JSON `window` metadata** -- add `window` field to `analyze --json` output when time filtering is active
6. **Documentation and examples** -- update `--help` text, epilog examples, README if warranted

### Estimated effort

- Total: S-M (5-8 dev days)
- Heaviest story: #4 (`analyze` wiring + flag interaction logic + error messages)
- Lightest stories: #5 (JSON metadata) and #6 (docs)

## 11. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Sessions without timestamps (empty files, parse failures) | Filter logic treats `first_message_timestamp = None` as "exclude when any time filter is active." Clear warning in verbose mode. |
| Performance regression from reading first-message during discovery | Bounded: first-message read stops after first valid analytical message (typically <5 lines). For 19-session projects, adds <50ms. Benchmark in integration tests. |
| User confusion about UTC vs local time | Document clearly: input without timezone = local; all output = UTC. Print the resolved UTC window in verbose mode so users can verify. |
| Relative time (`7d`) is ambiguous across invocations | Relative is resolved at invocation time. JSON output includes the resolved absolute timestamps, so saved envelopes are self-documenting. |

## 12. Success Criteria

1. `agentfluent analyze --project P --since 2026-05-04 --json` produces output covering only sessions starting on or after 2026-05-04.
2. `agentfluent list --project P --since 2026-05-04` shows only the sessions in that window.
3. `agentfluent diff baseline.json current.json` works correctly when baseline and current were generated with different `--since`/`--until` values.
4. The dogfooding loop is closed: after fixing pm.md (issues #291, #292), `analyze --since <fix-date>` shows the fix's effect without dilution from historical sessions.
5. All existing tests continue to pass (no behavioral change when `--since`/`--until` are not provided).
