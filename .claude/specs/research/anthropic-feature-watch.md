# Anthropic Feature Watch

**Purpose:** Queue of candidate features for AgentFluent's roadmap, sourced
from Anthropic announcements and ecosystem chatter. Maintained by the
`anthropic-research` and `candidate-verifier` subagents, the
`promote-candidates` skill, and a human review gate.

**Pipeline:**
1. `anthropic-research` subagent surveys upstream sources and appends candidates with `Status: queued`.
2. `candidate-verifier` subagent grounds each candidate's claims in the codebase, decisions log, and GitHub backlog, adds a Verification block, and flips Status to `verified`, `needs-evidence`, or `duplicate`.
3. The human reviews each annotated candidate and adds a `Decision` line: `approve`, `defer`, `dismiss`, or `override-route <route>`.
4. `promote-candidates` skill (invoked via `/promote-candidates`, runs in the parent thread) executes the decision — files issues, comments on overlaps, or invokes the `pm` subagent — and records the outcome in a Promotion block. Status flips to `promoted` or `dismissed`.

The dispatch step is a skill (not a subagent) because subagents cannot
invoke other subagents in Claude Code, and the `pm-first` route requires
invoking the `pm` subagent via the `Agent` tool. Skills run in the parent
thread, which has unrestricted Agent access. See
`.claude/skills/promote-candidates/SKILL.md` for the implementation.

---

## Schema

### Reviewed Sources entry

| Field | Required | Notes |
|---|---|---|
| Date | yes | YYYY-MM-DD when reviewed |
| URL | yes | Full URL |
| Title | yes | Article/post title |
| One-line takeaway | yes | What the source is about |
| Tag | yes | `candidate-added` / `not-actionable` / `already-covered` / `rejected-by-decision` |
| Candidate ref | conditional | If tag=candidate-added, the C-NNN id |

### Candidate entry

Each candidate is a block under `## Candidates Queue` that accumulates
annotations as it moves through the pipeline. Scout fields are written
once and never edited; later agents and the human append blocks below.

**Scout fields** (anthropic-research, append-only):

| Field | Required | Notes |
|---|---|---|
| ID | yes | `C-NNN`, monotonic |
| Title | yes | Short |
| Source | yes | URL + date |
| Added | yes | YYYY-MM-DD |
| Summary | yes | 2-3 sentences on the upstream feature |
| AgentFluent relevance | yes | Which of the 4 core features it touches + which data source signals it |
| Suggested shape | yes | New signal? Config scanner check? Analytics metric? Diff annotation? |
| Relevance strength | yes | `strong fit` / `moderate fit` / `speculative fit` |

**Verification block** (candidate-verifier, after scout fields):

```
**Verification (YYYY-MM-DD):**
- Premise check: confirmed | unconfirmed | partial — <evidence; file:line or issue#>
- Dedup check: no overlap | overlaps with #N (state) | covers decision D-NNN
- Suggested route: pm-first | architect-first | dismiss-as-duplicate — <reason>
- Notes: <optional 1-2 lines>
```

**Decision line** (human, after Verification — this is the human gate):

```
**Decision (YYYY-MM-DD):** <decision>
```

Where `<decision>` is one of:
- `approve` — execute the verifier's Suggested route
- `defer — <reason>` — leave the candidate for later (no action; Status unchanged)
- `dismiss — <reason>` — drop the candidate (no GitHub action; Status → `dismissed`)
- `override-route <route> — <reason>` — execute a different route than the verifier suggested

**Promotion block** (`promote-candidates` skill, after Decision):

```
**Promotion (YYYY-MM-DD):** <route> → <outcome>
```

Examples:
- `pm-first → filed epic #411, stories #412, #413`
- `dismiss-as-duplicate → commented on #164`
- `needs-evidence → filed #414 (blocked-on-evidence)`
- `dismiss → not worth tracking`

**Status line** (always last; reflects current state):

| Status | Set by | Meaning |
|---|---|---|
| `queued` | scout | initial; awaiting verification |
| `verified` | verifier | premise confirmed, awaiting human gate |
| `needs-evidence` | verifier | premise depends on unobserved data; human decides whether to track |
| `duplicate` | verifier | overlaps with existing issue or decision; awaiting human gate |
| `promoted` | `promote-candidates` skill | downstream action complete (issue filed, pm invoked, comment posted) |
| `dismissed` | `promote-candidates` skill | human chose to drop |

---

## Reviewed Sources

<!-- Append newest entries at the top of this section -->

| Date | URL | Title | Takeaway | Tag | Candidate ref |
|---|---|---|---|---|---|
| 2026-05-20 | https://raw.githubusercontent.com/anthropics/claude-agent-sdk-typescript/main/CHANGELOG.md | Claude Agent SDK (TypeScript) Changelog (v0.3.142–v0.3.145) | Three releases: `model_not_found` error type replacing `invalid_request` + new `api_error_status` field (v0.3.144); `TodoWrite` removed, replaced by `TaskCreate`/`TaskUpdate`/`TaskGet`/`TaskList` (v0.3.142); new `request_id`, `subagent_type`, `task_description` fields on SDK message types; peer-dep restructure (v0.3.143); Bun binary extraction helper (v0.3.144) | candidate-added | C-007, C-008 |
| 2026-05-20 | https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md | Claude Code Changelog (v2.1.119–2.1.145) | 27 releases covering new hook fields (`duration_ms`, `background_tasks`, `session_crons`), `agent_id`/`parent_agent_id` in OTEL spans, `alwaysLoad` MCP option, PostToolUse output replacement, agent frontmatter `mcpServers`, `context: fork` infinite-loop fix, `claude agents --json` | candidate-added | C-001, C-002, C-003, C-004, C-005 |
| 2026-05-20 | https://www.anthropic.com/engineering/april-23-postmortem | An update on recent Claude Code quality reports | Three production bugs (reasoning effort default, thinking-cache clear loop, verbosity prompt) degraded Claude Code quality Mar–Apr 2026; describes detection challenges and the new per-model eval discipline | candidate-added | C-006 |
| 2026-05-20 | https://www.anthropic.com/engineering/managed-agents | Scaling Managed Agents: Decoupling the brain from the hands | Architecture paper on decoupling harness/tools/session-log for resilient agents; session log as external durable state; `emitEvent`/`getEvents` interfaces | not-actionable | — |
| 2026-05-20 | https://www.anthropic.com/news/anthropic-acquires-stainless | Anthropic acquires Stainless | Stainless (SDK/CLI/MCP-server generator from OpenAPI specs) acquired; tightens SDK + MCP connector pipeline | not-actionable | — |
| 2026-05-20 | https://www.anthropic.com/news/finance-agents | Agents for financial services | Multi-agent templates with skills/connectors/subagents + async managed agents with audit logs and per-tool permissions; enterprise pattern announcement | not-actionable | — |
| 2026-05-20 | https://www.anthropic.com/news/widening-conversation-ai | Widening the conversation on frontier AI | Policy/governance piece; no technical content | not-actionable | — |
| 2026-05-20 | https://www.anthropic.com/news/anthropic-kpmg | KPMG integrates Claude across its workforce | Enterprise partnership announcement; no new APIs or signals | not-actionable | — |
| 2026-05-20 | https://www.anthropic.com/news/pwc-expanded-partnership | PwC is deploying Claude | Enterprise partnership announcement; no new APIs or signals | not-actionable | — |
| 2026-05-20 | https://www.anthropic.com/news/gates-foundation-partnership | Anthropic forms $200M partnership with Gates Foundation | Philanthropic partnership; no technical content | not-actionable | — |
| 2026-05-20 | https://www.anthropic.com/news/claude-for-small-business | Introducing Claude for Small Business | New plan tier; no new APIs or session-data signals | not-actionable | — |
| 2026-05-20 | https://www.anthropic.com/news/higher-limits-spacex | Higher usage limits for Claude and a compute deal with SpaceX | Usage limit increase and infrastructure deal; no new APIs | not-actionable | — |
| 2026-05-20 | https://www.anthropic.com/news/enterprise-ai-services-company | Building a new enterprise AI services company | Investment/services company announcement; no technical content | not-actionable | — |
| 2026-05-20 | https://www.anthropic.com/news/claude-for-creative-work | Claude for Creative Work | New product surface; no agent session data implications | not-actionable | — |
| 2026-05-20 | https://platform.claude.com/docs/en/release-notes/agent-sdk | Agent SDK release notes | URL returned 404; no content retrieved | not-actionable | — |

---

## Candidates Queue

<!-- Append new candidates at the bottom. Status updates happen in place. -->

---

### C-001: Hook input `duration_ms` — per-tool timing config check

**Source:** https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md — v2.1.119, approx. May 2026

**Added:** 2026-05-20

**Summary:** Claude Code v2.1.119 added `duration_ms` to hook input JSON for every tool execution. Hooks now receive the wall-clock time each tool call took, not just its success/failure. This means `.claude/hooks/` scripts can gate or log on slow tool calls without a separate timing layer.

**AgentFluent relevance:** Config Assessment — AgentFluent already scans `.claude/hooks/` for coverage gaps. A hook that could use `duration_ms` to detect slow tools but does not is a config gap: the agent is flying blind on per-tool latency. Also touches Behavior Diagnostics: if session JSONL shows repeated slow tool calls with no corresponding hook firing, the correlator can recommend adding a PostToolUse timing guard.

**Suggested shape:** Config scanner check — flag agents whose PostToolUse hooks do not reference `duration_ms` when the session data shows tool duration outliers (DURATION_OUTLIER signal already exists). New recommendation copy: "Add a PostToolUse hook that logs or gates on `duration_ms` to surface slow tool calls before they compound."

**Relevance strength:** strong fit

**Verification (2026-05-20):**
- Premise check: partial — `DURATION_OUTLIER` signal confirmed at `src/agentfluent/diagnostics/signals.py:218-232`. Config scanner (`src/agentfluent/config/scanner.py`) reads `hooks` frontmatter key (line 98) but has no hook-body analysis or `duration_ms` coverage audit. Claim that "AgentFluent already scans `.claude/hooks/` for coverage gaps" is unconfirmed — scanner inventories hooks dict but does not inspect hook scripts for field references.
- Dedup check: no overlap — no open or recently-closed issues cover hook-script content analysis or `duration_ms` config check.
- Suggested route: architect-first — touches existing config scanner module and requires defining what "hook references duration_ms" means at the script-inspection level; scope needs design before PM can spec.

**Decision (2026-05-21):** approve

**Promotion (2026-05-21):** architect-first → stub epic #423; architect design comment on #423; pm filed stories #424, #425, #426; no separate PRD (architect comment serves as design doc). Open question for verifier re: `duration_ms` field presence per tool type — non-blocking for #424, should resolve before #425 ships.

**Status:** promoted

---

### C-002: `background_tasks` and `session_crons` in Stop/SubagentStop hook input

**Source:** https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md — v2.1.145, approx. May 2026

**Added:** 2026-05-20

**Summary:** Stop and SubagentStop hooks now receive `background_tasks` and `session_crons` in their input JSON. This means stop hooks have full visibility into what background work was scheduled before the session ended — enabling hooks to block a stop if tasks are pending, or log the task inventory at shutdown.

**AgentFluent relevance:** Config Assessment — agents that use background sessions or crons but whose Stop hooks do not inspect `background_tasks`/`session_crons` may silently abandon in-flight work on stop. This is a new config surface to audit. Behavior Diagnostics — if an agent's session terminates with background tasks still pending (detectable if session JSONL shows a stop without task-completion entries), this is a signal worth flagging.

**Suggested shape:** Config scanner check — when an agent's `.claude/agents/*.md` or project config shows `crons:` or background session use, verify the Stop hook exists and its script references `background_tasks`. New recommendation: "Your agent uses crons/background tasks but the Stop hook doesn't inspect `background_tasks` — pending work may be silently abandoned at shutdown."

**Relevance strength:** moderate fit

**Verification (2026-05-20):**
- Premise check: unconfirmed — config scanner reads `hooks` dict from frontmatter (scanner.py:98) and `skills` list (line 99) but has no `crons:` frontmatter field in `AgentConfig` model (`src/agentfluent/config/models.py`). No fixture or session data contains `background_tasks`/`session_crons` fields to confirm they surface in JSONL. The Stop-hook inspection logic the candidate assumes does not exist.
- Dedup check: no overlap — no open or recently-closed issues cover background-task / cron hook auditing.
- Suggested route: needs-evidence — `crons:` is not in the current `AgentConfig` model; confirm whether `crons:` appears in real agent frontmatter and whether Stop hook input JSONL captures `background_tasks`. Route to `pm-first` once the frontmatter field and session data are confirmed.

**Decision (2026-05-21):** approve

**Promotion (2026-05-21):** needs-evidence → filed #412 (blocked-on-evidence)

**Status:** promoted

---

### C-003: `agent_id` and `parent_agent_id` in OTEL spans — subagent trace linking

**Source:** https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md — v2.1.145, approx. May 2026

**Added:** 2026-05-20

**Summary:** Claude Code v2.1.145 added `agent_id` and `parent_agent_id` attributes to `claude_code.tool` OpenTelemetry spans. This provides a structured parent-child linking field in the OTEL trace tree, complementing the `agentId` already present in JSONL `toolUseResult` metadata.

**AgentFluent relevance:** Execution Analytics and Behavior Diagnostics — AgentFluent currently links subagent JSONL files to parent sessions via `toolUseResult.agentId`. The new OTEL fields confirm Anthropic is standardizing on `agent_id`/`parent_agent_id` as the canonical identifiers for agent trace linking. If AgentFluent ever ingests OTEL export data (e.g., from an enterprise OTEL collector), it can join spans to JSONL records using these fields. More immediately, this is a signal that the `agentId` field in JSONL is a stable, first-class identifier — strengthening the v0.3 subagent trace linking design.

**Suggested shape:** Not a new signal — a validation of existing design. Actionable as a JSONL format drift monitor update (#164): confirm `agentId` remains stable and add a check that OTEL-exported sessions use the same value. Low-priority documentation note for the subagent trace parser.

**Relevance strength:** moderate fit

**Verification (2026-05-20):**
- Premise check: confirmed — `agentId` in `ToolResultMetadata` confirmed at `src/agentfluent/core/session.py:91`. The candidate correctly characterizes this as a design-validation finding rather than a new signal. Open issue #164 ("Add JSONL format drift monitoring skill") is the exact home for an `agentId` stability check.
- Dedup check: overlaps with #164 (open) — format drift monitor is the stated home for OTEL/agentId cross-validation.
- Suggested route: dismiss-as-duplicate — the actionable part (format drift monitor for agentId/OTEL alignment) belongs in #164. No standalone feature warranted.

**Decision (2026-05-21):** approve

**Promotion (2026-05-21):** dismiss-as-duplicate → commented on #164

**Status:** promoted

---

### C-004: Agent frontmatter `mcpServers` — new config surface for assessment

**Source:** https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md — v2.1.117, approx. May 2026

**Added:** 2026-05-20

**Summary:** Claude Code v2.1.117 added support for `mcpServers:` in agent frontmatter (`.claude/agents/*.md` files). MCP server configuration can now live directly in an agent definition rather than only in project-level or user-level `.mcp.json`. This means an agent can declare its own MCP dependencies inline.

**AgentFluent relevance:** Config Assessment — AgentFluent's MCP audit (epic D011, issues #163 #171) currently looks for MCP config in `.mcp.json` and `settings.json`. Agent-frontmatter `mcpServers` is an additional config location that the scanner must now cover to avoid false "missing MCP server" findings. If AgentFluent scans agent files but misses the `mcpServers` frontmatter key, it will report MCP gaps that the agent has already addressed inline.

**Suggested shape:** Config scanner update — extend the agent definition parser to extract `mcpServers:` from agent frontmatter YAML. Merge with project-level and user-level MCP configs when assessing whether an agent's observed MCP tool usage is covered by its declared config. This closes a config-coverage blind spot rather than adding a new signal.

**Relevance strength:** strong fit

**Verification (2026-05-20):**
- Premise check: confirmed — `AgentConfig.mcp_servers` field confirmed at `src/agentfluent/config/models.py:90`; scanner already reads `mcpServers` frontmatter key at `src/agentfluent/config/scanner.py:97`. The field is parsed today. The candidate's concern is that `McpServerConfig` discovery (issue #117, closed) and MCP audit logic need to merge agent-frontmatter `mcpServers` with project/user `.mcp.json` sources — a gap in the audit layer, not the scanner.
- Dedup check: overlaps with #163 (open) and #171 (open) — MCP audit signal work is active. #163 covers `MCP_DISABLED_SERVER_USED`; #171 covers verifying MCP audit rules fire. Frontmatter-source merging is the missing connector between the already-parsed field and the audit rules.
- Suggested route: architect-first — scanner already ingests the field; the gap is in MCP audit source-merging logic which touches #163/#171 in-flight work. Architect should confirm the merge point before PM specs.

**Decision (2026-05-21):** approve

**Promotion (2026-05-21):** architect-first → stub epic #427; architect design comment on #427 (kept separate from #163/#171); pm filed stories #428, #429, #430; cross-reference comment posted on #163; no separate PRD. #171 is soft prerequisite (audit-wiring investigation) — #428/#429 unblocked, #430 should wait on #171 confirmation.

**Status:** promoted

---

### C-005: `context: fork` infinite-loop detection in skills

**Source:** https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md — v2.1.144 (fix), approx. May 2026

**Added:** 2026-05-20

**Summary:** Claude Code v2.1.144 fixed an infinite loop where a skill using `context: fork` could re-invoke itself recursively. This was a real bug that caused runaway agent behavior before the fix. Even post-fix, the underlying pattern — a skill calling itself via fork — is a config hazard that can produce unexpected recursion if the invoking logic changes.

**AgentFluent relevance:** Config Assessment and Behavior Diagnostics — a skill with `context: fork` that invokes itself (either directly via `Skill(name)` or transitively through another skill) is a latent loop risk. In session data, this would manifest as an extreme `STUCK_PATTERN` or very high `totalToolUseCount` with repetitive skill-invocation tool calls. Before the fix, this was undetectable without trace analysis; after the fix, it is preventable via config scanning.

**Suggested shape:** Config scanner check — when parsing `.claude/skills/` or agent-frontmatter `skills:`, detect any skill that references itself (directly or through a chain) with `context: fork`. Flag as a config warning: "Skill `X` uses `context: fork` and appears in its own invocation chain — this was the root cause of a Claude Code v2.1.143 infinite loop." Also: if session data shows extreme tool-use counts (>50) with repetitive `Skill` tool calls, add a diagnostic hint referencing this pattern.

**Relevance strength:** moderate fit

**Verification (2026-05-20):**
- Premise check: partial — `AgentConfig.skills` field confirmed at `src/agentfluent/config/models.py:96`; `STUCK_PATTERN` signal confirmed in `src/agentfluent/diagnostics/models.py:57`. However, no skill file parsing or `context:` key inspection exists in the scanner today — `skills` is a list of skill names only. No `.claude/skills/` directory scanner exists. Detecting self-referential skill chains requires parsing skill files, which is currently out of scope.
- Dedup check: overlaps with #183 (open) — "delegation drafts: skill-aware provenance + actionability note" covers skill-scanner infrastructure. No dedicated fork-loop detection issue exists, but it depends on #183's skill scanner prerequisite.
- Suggested route: pm-first — once #183 ships the skill scanner, the fork-loop check is a small additive rule. PM can spec the check scoped as a follow-on story to #183.
- Notes: The STUCK_PATTERN + high tool-count session heuristic (no skill scanner needed) could ship as a standalone diagnostic note earlier if desired.

**Decision (2026-05-21):** approve

**Promotion (2026-05-21):** pm-first → filed epic #414, stories #415, #416; PRD at `.claude/specs/prd-context-fork-loop-detection.md`

**Status:** promoted

---

### C-006: Verbosity prompt degradation — system-prompt config signal

**Source:** https://www.anthropic.com/engineering/april-23-postmortem — 2026-04-23

**Added:** 2026-05-20

**Summary:** Anthropic's April 2026 postmortem revealed that a single system prompt instruction ("keep text between tool calls to ≤25 words; keep final responses to ≤100 words") degraded coding quality by 3% across all models, while the thinking-cache bug caused agents to appear forgetful and repetitive by continuously clearing reasoning history on every turn. Both were silent regressions undetected by standard monitoring until user reports accumulated.

**AgentFluent relevance:** Behavior Diagnostics and Regression Detection — this postmortem is direct evidence that system prompt verbosity constraints produce measurable quality regressions, and that thinking-cache bugs produce repetitive-behavior patterns detectable in session traces. For AgentFluent: (1) the verbosity-regression pattern suggests a new config assessment check — agents with extreme length-limiting instructions in their system prompts may be self-degrading their output quality; (2) the thinking-cache bug's behavioral fingerprint (repetitive tool calls, repetitive phrasing, unexpected cache misses inflating token costs) is exactly what AgentFluent's STUCK_PATTERN and TOKEN_OUTLIER signals exist to catch. The postmortem validates AgentFluent's core thesis and provides two concrete new signals.

**Suggested shape:** Two separate actionable items: (a) Config scanner check — scan agent system prompts for extreme word-count constraints (`≤N words`, `max N words`, `keep responses under N words`) that could degrade output quality; flag with a reference to this postmortem as evidence. (b) Behavior diagnostic signal — when session data shows high cache-miss rates alongside repetitive tool-call sequences (same tool, similar inputs, within the same session), emit a new `THINKING_CACHE_ANOMALY` signal or augment existing STUCK_PATTERN to note the cache-miss correlation. The JSONL `cache_read_input_tokens` field going unexpectedly to zero mid-session is the observable.

**Relevance strength:** strong fit

**Verification (2026-05-20):**
- Premise check: partial — `cache_read_input_tokens` is parsed and confirmed in `Usage` model at `src/agentfluent/core/session.py:23`. `ERROR_PATTERN` signal confirmed at `src/agentfluent/diagnostics/signals.py:74`. The verbosity-constraint scanner check (prompt body text scan) is additive to the existing `AgentConfig.prompt_body` field. The `THINKING_CACHE_ANOMALY` / cache-miss-goes-zero signal depends on per-session `cache_read_input_tokens` trajectory — no mid-session token-timeline tracking exists today.
- Dedup check: no overlap — no open or recently-closed issues cover prompt verbosity constraints or thinking-cache anomaly detection.
- Suggested route: architect-first — the two sub-items (prompt scan vs. session-timeline cache-miss signal) have different implementation homes (config scorer vs. diagnostics pipeline) and different data requirements. Splitting them cleanly needs design input before PM specs either.

**Decision (2026-05-21):** approve

**Promotion (2026-05-22):** architect-first → stub epic #431; architect recommended splitting the two sub-items (Track A: verbosity scanner, Track B: thinking-cache anomaly) — #431 renamed to Track A (C-006a), new stub #433 filed for Track B (C-006b); pm dispatch pending on both. Side observation: closed drive-by PR #432 (filed ~1h after #427's stories were scoped, before C-006 dispatch); tracking issue #434 logs occurrences + queued contributor-gating improvements.

**Status:** promoted

---

### C-007: `model_not_found` error type + `api_error_status` field — discrete model-config error signal

**Source:** https://raw.githubusercontent.com/anthropics/claude-agent-sdk-typescript/main/CHANGELOG.md — v0.3.144, approx. May 2026

**Added:** 2026-05-20

**Summary:** The TypeScript Agent SDK v0.3.144 introduced a discrete `error: 'model_not_found'` error type on assistant messages and `StopFailure` hooks, replacing the prior generic `'invalid_request'` error for model-unavailability failures. A companion `api_error_status` field was added to result messages to carry the HTTP status code alongside the error type.

**AgentFluent relevance:** Behavior Diagnostics and Config Assessment — AgentFluent's `ERROR_PATTERN` signal currently pattern-matches on error text strings in session data. A structured `model_not_found` error type, if surfaced in JSONL session output (as `"error": "model_not_found"` in tool_result content or assistant message fields), is a higher-confidence, lower-noise signal than text matching. It maps directly to an agent config problem: the `model:` frontmatter field or `ClaudeAgentOptions.model` specifies a model that is unavailable — a concrete recommendation (update the model field) follows immediately. This closes a known gap in the `MODEL_MISMATCH` signal, which currently focuses on cost/efficiency rather than hard availability failures.

**Suggested shape:** Two parts: (a) JSONL format drift monitor — verify whether `model_not_found` and `api_error_status` appear in session JSONL output for SDK-run agents, and if so, add them as parseable fields on `ToolResultMetadata` (currently uses `extra="ignore"`). (b) New signal or `ERROR_PATTERN` subtype — when a session shows `error: model_not_found`, emit a high-severity diagnostic: "Agent invocation failed with `model_not_found` — the configured model (`<model>`) is unavailable in this API context. Update the agent's `model:` field to a currently available model." This is a concrete, paste-ready fix rather than a probabilistic recommendation.

**Relevance strength:** strong fit

**Verification (2026-05-20):**
- Premise check: unconfirmed — `ToolResultMetadata` uses `extra="ignore"` (confirmed `src/agentfluent/core/session.py:86`) so unknown fields like `model_not_found` / `api_error_status` are silently dropped today. The candidate correctly identifies this gap. However, `model_not_found` / `api_error_status` appearing in Python-SDK-generated session JSONL is unconfirmed — the SDK change is TypeScript-only (v0.3.144) and it is unknown whether these fields appear in JSONL emitted by the Python SDK or Claude Code. Fixture `tests/fixtures/session_with_agent.jsonl` has no error fields.
- Dedup check: no overlap — no open or recently-closed issues cover structured `model_not_found` error parsing; #170 (open) covers model recommendation copy but not error-type parsing.
- Suggested route: needs-evidence — confirm whether `model_not_found` / `api_error_status` appear in actual Python-SDK or Claude Code JSONL (not just the TS SDK type definitions). Collect a session fixture showing the field before implementing. Route to `pm-first` once confirmed.

**Decision (2026-05-21):** approve

**Promotion (2026-05-21):** needs-evidence → filed #413 (blocked-on-evidence)

**Status:** promoted

---

### C-008: `TodoWrite` → Task tools rename — tool name normalization for analytics

**Source:** https://raw.githubusercontent.com/anthropics/claude-agent-sdk-typescript/main/CHANGELOG.md — v0.3.142, approx. May 2026

**Added:** 2026-05-20

**Summary:** The TypeScript Agent SDK v0.3.142 removed the deprecated `TodoWrite` tool and replaced it with four Task tools: `TaskCreate`, `TaskUpdate`, `TaskGet`, and `TaskList`. Sessions generated by SDK agents running v0.3.142+ will show `TaskCreate`/`TaskUpdate`/`TaskGet`/`TaskList` tool_use blocks where older sessions showed `TodoWrite`. The rename is not backward-compatible in the session data stream.

**AgentFluent relevance:** Execution Analytics — AgentFluent's tool pattern analytics (tool frequency, diversity, retry detection) key on tool names extracted from `tool_use` content blocks. Any analytics or diagnostics logic that checks for `TodoWrite` by name will silently miss usage in sessions generated by SDK v0.3.142+. Conversely, `TaskCreate`/`TaskUpdate`/`TaskGet`/`TaskList` will appear as unknown/new tools rather than being recognized as task-management tools. This is a data-continuity problem for diff comparisons across the version boundary.

**Suggested shape:** Analytics normalization update — add `TaskCreate`, `TaskUpdate`, `TaskGet`, and `TaskList` to the recognized tool taxonomy, classified as "task management" tools (same category as the old `TodoWrite`). In diff output, flag a known-rename annotation when a session baseline shows `TodoWrite` usage and the current run shows `TaskCreate`/`TaskUpdate` usage — this is not a regression but a tool rename. Also: if AgentFluent's config scanner checks for task-management tool coverage, update the check to recognize both the old and new tool names for backward compatibility with mixed-version session archives.

**Relevance strength:** moderate fit

**Verification (2026-05-20):**
- Premise check: confirmed — tool names are extracted from `tool_use` content blocks in the session parser and flow through `AgentInvocation` models; `AgentConfig.skills` field exists at `src/agentfluent/config/models.py:96`. The tool-taxonomy gap is real: no recognized tool set or "task-management" category exists in the codebase today. `TodoWrite` does not appear in any source file. `TaskCreate`/`TaskUpdate`/`TaskGet`/`TaskList` are similarly absent. The diff annotation concern is real for anyone comparing pre/post v0.3.142 TS SDK sessions.
- Dedup check: no overlap — no open or recently-closed issues cover tool name normalization or rename annotations.
- Suggested route: pm-first — clean additive scope: new tool category constant + diff annotation. No existing module conflicts. PM can spec independently.

**Decision (2026-05-21):** approve

**Promotion (2026-05-21):** pm-first → filed epic #418, stories #419, #420, #421, #422; PRD at .claude/specs/prd-tool-name-normalization.md

**Status:** promoted
