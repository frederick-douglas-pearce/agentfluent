"""Analytics pipeline orchestration.

Connects parser, extractor, and analytics modules into a single
analysis pipeline. Reusable by the CLI, future webapp, and tests.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field, computed_field

from agentfluent.agents.extractor import extract_agent_invocations
from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import (
    AgentMetrics,
    AgentTypeMetrics,
    _recompute_ratios,
    agent_token_percentage,
    compute_agent_metrics,
)
from agentfluent.analytics.pricing import SYNTHETIC_MODELS
from agentfluent.analytics.tokens import (
    ModelTokenBreakdown,
    TokenMetrics,
    TraceDedupStats,
    _aggregate_totals,
    compute_subagent_token_metrics,
    compute_token_metrics,
    counted_parent_message_ids,
    dedupe_trace_turns,
    fold_subagent_metrics_in,
    session_price_timestamp,
)
from agentfluent.analytics.tools import (
    ConcentrationEntry,
    ToolMetrics,
    compute_tool_metrics,
)
from agentfluent.analytics.trace_coverage import (
    TraceCoverage,
    compute_trace_coverage,
    merge_trace_coverage,
)
from agentfluent.config.models import EnvironmentWarning
from agentfluent.core.filtering import WindowMetadata
from agentfluent.core.parser import parse_session
from agentfluent.core.session import (
    SessionClass,
    SessionMessage,
    classify_entrypoint,
    select_entrypoint,
)
from agentfluent.diagnostics.mcp_assessment import (
    McpToolCall,
    extract_mcp_calls_from_messages,
)
from agentfluent.diagnostics.models import DiagnosticsResult
from agentfluent.traces.coverage import SessionTraces, collect_session_traces

logger = logging.getLogger(__name__)


class SessionAnalysis(BaseModel):
    """Complete analysis results for a single session."""

    session_path: Path
    token_metrics: TokenMetrics
    tool_metrics: ToolMetrics
    agent_metrics: AgentMetrics
    session_kind: SessionClass = "unknown"
    """How this session was produced: ``"sdk"`` (Agent SDK), ``"cli"``
    (Claude Code interactive), or ``"unknown"`` — from ``classify_session``
    on the parsed messages (#591 primitive). Persisted here (per-session,
    never at ``AnalysisResult`` level, since a run mixes co-located SDK and
    CLI sessions) so downstream diagnostics gate on it without re-deriving:
    #112's SDK main-session model-routing reads ``"sdk"``, and #592's
    analyze SDK badge rides the same field.

    **This name is public JSON API** — every non-``exclude`` field here is
    serialized wholesale by ``analyze --format json`` (D055). Named to match
    the ratified #592 AC / ``prd-v0.11.md`` §4."""

    entrypoint: str | None = None
    """The raw, verbatim runtime value this session's ``session_kind`` was
    derived from — ``"sdk-py"``, ``"cli"``, a future ``"sdk-ts"``, or an
    unrecognized value; ``None`` when no message carried one (#592 AC#2
    publishes BOTH).

    Stored, not computed: ``messages`` below is ``exclude=True``, so a
    computed property would work in-process yet silently yield ``None`` on
    every rehydrated envelope (``diff`` reads serialized JSON).

    Not redundant with ``session_kind``. Both come from
    ``select_entrypoint`` — same precedence, so they cannot contradict —
    but an *unrecognized* runtime classifies as ``"unknown"`` while this
    field still reports the value verbatim, turning entrypoint-vocabulary
    drift (``prd-v0.11.md`` §9) into a self-describing report rather than a
    dead end. Do not "simplify" it away."""
    invocations: list[AgentInvocation] = Field(default_factory=list)
    trace_coverage: TraceCoverage = Field(default_factory=TraceCoverage)
    """Subagent trace spend by cohort -- linked, nested, session-spawned,
    filtered, unparseable (#648 AC4). Stored, not computed: the traces it is
    built from are not serialized, and ``diff`` reads rehydrated JSON."""
    mcp_tool_calls: list[McpToolCall] = Field(default_factory=list)
    """Parent-session MCP tool calls (those made directly in the main
    session, outside any Agent delegation). Subagent-trace MCP calls
    are already captured on ``invocations[i].trace.tool_calls`` and
    not duplicated here."""

    messages: list[SessionMessage] = Field(default_factory=list, exclude=True)
    """Parsed parent-session messages. Retained so downstream
    diagnostics — currently only #189's parent-thread offload-candidate
    pipeline — can re-walk the session without re-parsing the JSONL.

    ``exclude=True`` keeps this field out of ``model_dump`` /
    ``model_dump_json`` output: the CLI's ``analyze --format json`` path
    serializes the full ``AnalysisResult``, and shipping every parsed
    message (including ``ToolUseBlock.input`` payloads — Edit/Write file
    contents, Bash output, Read results) would bloat the JSON envelope
    by orders of magnitude AND surface file contents the user never
    asked for in a CLI summary. In-memory access still works for
    diagnostics; serialization callers see only the pre-existing
    fields."""

    message_count: int = 0
    user_message_count: int = 0
    assistant_message_count: int = 0
    """All ``type:"assistant"`` messages in the session, INCLUDING
    Claude Code's ``<synthetic>`` ghost responses. Kept at its original
    all-inclusive meaning for backward compat (the integration invariant
    ``message_count >= user + assistant`` depends on it); ``model_turns``
    nets out the synthetic subset (#507)."""

    synthetic_message_count: int = 0
    """``<synthetic>``-model assistant messages -- Claude Code-fabricated
    filler (e.g. "No response requested.") emitted with zero usage and
    no API round-trip, to keep user/assistant alternation valid when a
    turn needs no model reply. Tallied separately and excluded from
    ``model_turns`` because the model did not actually turn (#507, D044).
    What populates this bucket is under investigation in #508."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def model_turns(self) -> int:
        """Number of model turns in this session (#465, corrected in #507).

        A *model turn* is one merged, **non-synthetic** assistant
        message. Fragment-merging in the parser may combine several raw
        JSONL lines into one logical assistant message, so this is the
        merged-message count, not the raw-line count. Distinct from
        ``tool_uses`` (actions within a turn) and tokens (cost per turn).

        Computed as ``assistant_message_count - synthetic_message_count``
        (D044, Option A): every assistant message the model actually
        produced, with Claude Code's ``<synthetic>`` ghost responses
        netted out. This equals ``api_call_count`` except on the (so far
        unobserved) edge case of a real-model response carrying no
        ``usage`` block -- see the ``model_turns`` glossary entry.
        Emitted in ``model_dump`` output as a computed field.
        """
        return self.assistant_message_count - self.synthetic_message_count


class AnalysisResult(BaseModel):
    """Aggregated analysis results across one or more sessions."""

    sessions: list[SessionAnalysis] = Field(default_factory=list)
    token_metrics: TokenMetrics = Field(default_factory=TokenMetrics)
    tool_metrics: ToolMetrics = Field(default_factory=ToolMetrics)
    agent_metrics: AgentMetrics = Field(default_factory=AgentMetrics)
    session_count: int = 0
    diagnostics: DiagnosticsResult | None = None
    window: WindowMetadata | None = None
    diagnostics_version: str | None = None
    """Package version that produced this envelope. Stamped by the CLI
    at emit time so ``diff`` can warn when baseline and current were
    analyzed with different rule sets / calibration constants (#347).
    ``None`` on legacy envelopes; populated as ``agentfluent.__version__``
    by :mod:`agentfluent.cli.commands.analyze`."""

    project_name: str | None = None
    """Display name of the analyzed project. Stamped by the CLI so
    ``report`` can render a standalone document (summary header,
    reproduction command in the footer) without needing the project
    re-specified at render time. Additive field — ``None`` on legacy
    envelopes; renderers fall back to ``"(unknown project)"``."""

    scope_session: str | None = None
    """Session filename when ``--session`` constrained the run, ``None``
    otherwise. Surfaced so consumers can verify scope at a glance: when
    set, every metric and diagnostic in this envelope reflects exactly
    one session. Stamped by :mod:`agentfluent.cli.commands.analyze` (#357)."""

    warnings: list[EnvironmentWarning] = Field(default_factory=list)
    """Non-fatal warnings about the analysis *environment* (not the
    agent's config) — e.g., Claude Code's ``cleanupPeriodDays`` silently
    truncating the session corpus (#481). Populated at discovery time by
    :mod:`agentfluent.cli.commands.analyze`; additive field, empty on
    legacy envelopes. Rendered as a banner above the normal output and
    carried in the JSON envelope."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_model_turns(self) -> int:
        """Aggregate model-turn count across all analyzed sessions (#465).

        Summed from each session's ``model_turns``. Recomputes from
        ``self.sessions`` rather than being stored, so it stays correct
        when an envelope is rehydrated from serialized JSON (the session
        list is always present) and is ``0`` for an empty result.

        Lives at the ``AnalysisResult`` top level of the JSON envelope --
        alongside ``session_count`` -- NOT nested under ``token_metrics``.
        #470 (diff integration) reads it from the top level."""
        return sum(s.model_turns for s in self.sessions)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trace_coverage(self) -> TraceCoverage:
        """Run-level ``trace_coverage``: each session's coverage summed (#648 AC4).

        Recomputed from ``self.sessions`` like ``total_model_turns``, so it
        survives JSON rehydration. This is the link-coverage count #676
        consumes."""
        return merge_trace_coverage([s.trace_coverage for s in self.sessions])

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_synthetic_messages(self) -> int:
        """Aggregate ``<synthetic>`` ghost-response count across all
        analyzed sessions (#507, D044).

        Mirrors ``total_model_turns``: summed from each session's
        ``synthetic_message_count``, recomputed-not-stored so it survives
        JSON rehydration, ``0`` for an empty result. Surfaced at the
        envelope top level (and in the Token Usage table) so the gap
        between ``api_call_count`` and ``model_turns`` is explained
        rather than mysterious. Additive field; ``diff`` ignores it."""
        return sum(s.synthetic_message_count for s in self.sessions)


def analyze_session(
    path: Path,
    *,
    agent_filter: str | None = None,
) -> SessionAnalysis:
    """Run the full analytics pipeline on a single session file.

    Args:
        path: Path to the .jsonl session file.
        agent_filter: If set, only include this agent type in agent metrics.

    Returns:
        SessionAnalysis with token, tool, and agent metrics.
    """
    messages = parse_session(path)

    # Price the session at the rate in effect when it ran (#546): derive the first-message
    # timestamp once and apply it to both parent and subagent cost rows for one consistent
    # session price date. None (no timestamped message) → latest rate.
    price_date = session_price_timestamp(messages)

    token_metrics = compute_token_metrics(messages, timestamp=price_date)
    tool_metrics = compute_tool_metrics(messages)

    all_invocations = extract_agent_invocations(messages)
    # Link, lineage and dedup run over EVERY invocation and every trace the
    # session owns, before `--agent` is applied: dedup precedence (which file
    # keeps a shared turn) must not depend on the filter, or a kept type's
    # spend would rise when an earlier fork is filtered out (#648, #701 review).
    session_traces = collect_session_traces(
        all_invocations, path, agent_filter=agent_filter,
    )
    dedup = _dedupe_session_trace_turns(messages, session_traces)
    invocations = [
        inv
        for inv in all_invocations
        if agent_filter is None or inv.agent_type.lower() == agent_filter.lower()
    ]
    mcp_tool_calls = extract_mcp_calls_from_messages(messages)

    # FOLD BY OWNERSHIP (#648 AC2). Session totals include every parseable
    # trace in this session's own `subagents/` dir except the ones `--agent`
    # excludes: depth-1, depth-2, depth->=3 and session-spawned (skill- and
    # workflow-run background agents). Ownership is a runtime invariant of the
    # layout, so the fold does not depend on the cohort labels, and it never
    # reads `trace.children` -- the child tree is display shape (#659 may
    # deepen it without moving a total). Before #648 only depth-1 traces were
    # folded and the rest were dropped from `total_cost`; this is the
    # deliberate act #595 PR B's non-folding gate deferred. Locked by
    # tests/unit/test_trace_coverage.py::TestFoldByOwnership.
    #
    # `by_agent_type` stays invocation-scoped: nested and session-spawned
    # spend shows in `trace_coverage`, not in any per-agent row.
    subagent_rows = compute_subagent_token_metrics(
        session_traces.fold_traces, timestamp=price_date,
    )
    token_metrics = fold_subagent_metrics_in(token_metrics, subagent_rows)
    trace_coverage = compute_trace_coverage(session_traces, dedup, timestamp=price_date)

    # Same `price_date` as the session fold above, so Σ per-agent cost reconciles
    # with the subagent-origin `by_model` rows (#646 AC2/AC8).
    agent_metrics = compute_agent_metrics(
        invocations,
        session_total_tokens=token_metrics.total_tokens,
        timestamp=price_date,
    )

    # Selected once, then classified: both published fields (#592) come from
    # this single raw value, so they cannot disagree.
    entrypoint = select_entrypoint(messages)

    return SessionAnalysis(
        session_path=path,
        token_metrics=token_metrics,
        tool_metrics=tool_metrics,
        agent_metrics=agent_metrics,
        session_kind=classify_entrypoint(entrypoint),
        entrypoint=entrypoint,
        invocations=invocations,
        trace_coverage=trace_coverage,
        mcp_tool_calls=mcp_tool_calls,
        messages=messages,
        message_count=len(messages),
        user_message_count=_count_type(messages, "user"),
        assistant_message_count=_count_type(messages, "assistant"),
        synthetic_message_count=_count_synthetic(messages),
    )


def analyze_sessions(
    paths: list[Path],
    *,
    agent_filter: str | None = None,
) -> AnalysisResult:
    """Run analytics across multiple session files and aggregate results.

    Calls analyze_session per file, then merges results mathematically
    rather than re-processing raw messages.

    Args:
        paths: List of .jsonl session file paths.
        agent_filter: If set, only include this agent type in agent metrics.

    Returns:
        AnalysisResult with per-session and aggregated metrics.
    """
    session_analyses = [analyze_session(p, agent_filter=agent_filter) for p in paths]

    if not session_analyses:
        return AnalysisResult()

    agg_token = _merge_token_metrics([s.token_metrics for s in session_analyses])
    agg_tool = _merge_tool_metrics([s.tool_metrics for s in session_analyses])
    agg_agent = _merge_agent_metrics(
        [s.agent_metrics for s in session_analyses],
        session_total_tokens=agg_token.total_tokens,
    )

    return AnalysisResult(
        sessions=session_analyses,
        token_metrics=agg_token,
        tool_metrics=agg_tool,
        agent_metrics=agg_agent,
        session_count=len(session_analyses),
    )


def _merge_token_metrics(metrics_list: list[TokenMetrics]) -> TokenMetrics:
    """Merge multiple TokenMetrics by summing per-(model, origin) breakdowns."""
    merged_models: dict[tuple[str, str], ModelTokenBreakdown] = {}
    api_call_count = 0

    for tm in metrics_list:
        api_call_count += tm.api_call_count
        for breakdown in tm.by_model:
            key = (breakdown.model, breakdown.origin)
            existing = merged_models.get(key)
            if existing is None:
                merged_models[key] = ModelTokenBreakdown(
                    model=breakdown.model,
                    input_tokens=breakdown.input_tokens,
                    output_tokens=breakdown.output_tokens,
                    cache_creation_input_tokens=breakdown.cache_creation_input_tokens,
                    cache_read_input_tokens=breakdown.cache_read_input_tokens,
                    cache_creation_5m_tokens=breakdown.cache_creation_5m_tokens,
                    cache_creation_1h_tokens=breakdown.cache_creation_1h_tokens,
                    cost=breakdown.cost,
                    origin=breakdown.origin,
                )
            else:
                existing.input_tokens += breakdown.input_tokens
                existing.output_tokens += breakdown.output_tokens
                existing.cache_creation_input_tokens += breakdown.cache_creation_input_tokens
                existing.cache_read_input_tokens += breakdown.cache_read_input_tokens
                existing.cache_creation_5m_tokens += breakdown.cache_creation_5m_tokens
                existing.cache_creation_1h_tokens += breakdown.cache_creation_1h_tokens
                existing.cost += breakdown.cost

    rows = list(merged_models.values())
    (
        total_input, total_output, total_cache_creation, total_cache_read,
        total_cost, cache_efficiency,
    ) = _aggregate_totals(rows)

    return TokenMetrics(
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_input_tokens=total_cache_creation,
        cache_read_input_tokens=total_cache_read,
        total_cost=total_cost,
        cache_efficiency=cache_efficiency,
        api_call_count=api_call_count,
        by_model=rows,
    )


def _merge_tool_metrics(metrics_list: list[ToolMetrics]) -> ToolMetrics:
    """Merge multiple ToolMetrics by summing frequency dicts."""
    merged_counts: dict[str, int] = {}
    for tm in metrics_list:
        for name, count in tm.tool_frequency.items():
            merged_counts[name] = merged_counts.get(name, 0) + count

    if not merged_counts:
        return ToolMetrics()

    sorted_tools = sorted(merged_counts.items(), key=lambda x: (-x[1], x[0]))
    sorted_freq = dict(sorted_tools)
    total = sum(merged_counts.values())

    concentration: list[ConcentrationEntry] = []
    cumulative = 0
    for i, (_name, count) in enumerate(sorted_tools, start=1):
        cumulative += count
        concentration.append(
            ConcentrationEntry(
                top_n=i,
                call_count=cumulative,
                percentage=round(cumulative / total * 100, 1),
            )
        )

    return ToolMetrics(
        tool_frequency=sorted_freq,
        unique_tool_count=len(merged_counts),
        total_tool_calls=total,
        concentration=concentration,
    )


def _merge_agent_metrics(
    metrics_list: list[AgentMetrics],
    session_total_tokens: int = 0,
) -> AgentMetrics:
    """Merge multiple AgentMetrics by summing per-type breakdowns."""
    merged: dict[str, AgentTypeMetrics] = {}

    for am in metrics_list:
        for key, m in am.by_agent_type.items():
            existing = merged.get(key)
            if existing is None:
                merged[key] = AgentTypeMetrics(
                    agent_type=m.agent_type,
                    is_builtin=m.is_builtin,
                    invocation_count=m.invocation_count,
                    total_tokens=m.total_tokens,
                    total_tool_uses=m.total_tool_uses,
                    total_duration_ms=m.total_duration_ms,
                    total_tool_uses_with_turns=m.total_tool_uses_with_turns,
                    total_tokens_with_tool_uses=m.total_tokens_with_tool_uses,
                    total_tool_uses_with_tokens=m.total_tool_uses_with_tokens,
                    total_duration_ms_with_tool_uses=m.total_duration_ms_with_tool_uses,
                    total_tool_uses_with_duration=m.total_tool_uses_with_duration,
                    estimated_total_cost_usd=m.estimated_total_cost_usd,
                    total_model_turns=m.total_model_turns,
                    invocations_with_turns=m.invocations_with_turns,
                    total_active_duration_ms=m.total_active_duration_ms,
                    total_wallclock_ms_trace_linked=m.total_wallclock_ms_trace_linked,
                    active_duration_invocation_count=m.active_duration_invocation_count,
                    spend_tokens=m.spend_tokens,
                    spend_invocation_count=m.spend_invocation_count,
                    priced_invocation_count=m.priced_invocation_count,
                    priced_model_turns=m.priced_model_turns,
                )
            else:
                existing.invocation_count += m.invocation_count
                existing.total_tokens += m.total_tokens
                existing.total_tool_uses += m.total_tool_uses
                existing.total_duration_ms += m.total_duration_ms
                existing.total_tool_uses_with_turns += m.total_tool_uses_with_turns
                existing.total_tokens_with_tool_uses += m.total_tokens_with_tool_uses
                existing.total_tool_uses_with_tokens += m.total_tool_uses_with_tokens
                existing.total_duration_ms_with_tool_uses += (
                    m.total_duration_ms_with_tool_uses
                )
                existing.total_tool_uses_with_duration += m.total_tool_uses_with_duration
                existing.estimated_total_cost_usd += m.estimated_total_cost_usd
                existing.total_model_turns += m.total_model_turns
                existing.invocations_with_turns += m.invocations_with_turns
                existing.total_active_duration_ms += m.total_active_duration_ms
                existing.total_wallclock_ms_trace_linked += m.total_wallclock_ms_trace_linked
                existing.active_duration_invocation_count += (
                    m.active_duration_invocation_count
                )
                existing.spend_tokens += m.spend_tokens
                existing.spend_invocation_count += m.spend_invocation_count
                existing.priced_invocation_count += m.priced_invocation_count
                existing.priced_model_turns += m.priced_model_turns

    # estimated_total_cost_usd and the spend/priced counts are plain sums: each
    # session priced its own traces at its own price date (#646). Tool-use
    # and turn ratios have to be recomputed because the per-unit ratio
    # changes when invocations from different sessions merge.
    for m in merged.values():
        _recompute_ratios(m)

    total_invocations = sum(m.invocation_count for m in merged.values())
    total_agent_tokens = sum(m.total_tokens for m in merged.values())
    total_agent_spend_tokens = sum(m.spend_tokens for m in merged.values())
    total_agent_duration = sum(m.total_duration_ms for m in merged.values())
    total_turns = sum(m.total_model_turns for m in merged.values())
    builtin_count = sum(m.invocation_count for m in merged.values() if m.is_builtin)
    custom_count = sum(m.invocation_count for m in merged.values() if not m.is_builtin)

    agent_token_pct = agent_token_percentage(total_agent_spend_tokens, session_total_tokens)

    return AgentMetrics(
        by_agent_type=merged,
        total_invocations=total_invocations,
        total_agent_tokens=total_agent_tokens,
        total_agent_spend_tokens=total_agent_spend_tokens,
        total_agent_duration_ms=total_agent_duration,
        builtin_invocations=builtin_count,
        custom_invocations=custom_count,
        agent_token_percentage=agent_token_pct,
        total_model_turns=total_turns,
    )


def _dedupe_session_trace_turns(
    messages: list[SessionMessage], traces: SessionTraces,
) -> TraceDedupStats:
    """Drop API turns counted more than once across this session's files (#701).

    Supplies what only the pipeline knows -- invocation order -- and leaves the
    precedence rule itself to ``tokens.dedupe_trace_turns``. Must run before
    anything reads spend: the session fold, ``compute_agent_metrics`` and
    ``trace_coverage`` all read ``trace_spend_usage``.

    Runs over every trace the session owns, **unfiltered** (``SessionTraces``
    carries both tiers before ``--agent`` is applied), so a type's spend is the
    same with and without ``--agent`` (#648).
    """
    return dedupe_trace_turns(
        counted_parent_message_ids(messages),
        traces.dedup_depth_one,
        traces.dedup_rest,
    )


def _count_type(messages: list[SessionMessage], msg_type: str) -> int:
    """Count messages of a given type."""
    return sum(1 for m in messages if m.type == msg_type)


def _count_synthetic(messages: list[SessionMessage]) -> int:
    """Count ``<synthetic>``-model assistant messages (#507).

    These are Claude Code-fabricated ghost responses (zero usage, no API
    round-trip) that are netted out of ``model_turns``. Filters on the
    ``SYNTHETIC_MODELS`` sentinel rather than zero-token usage: a real
    turn always carries a real model name, so the sentinel is the robust
    discriminator. Mirrors the same exclusion ``api_call_count`` applies
    in :mod:`agentfluent.analytics.tokens`."""
    return sum(
        1
        for m in messages
        if m.type == "assistant" and m.model in SYNTHETIC_MODELS
    )
