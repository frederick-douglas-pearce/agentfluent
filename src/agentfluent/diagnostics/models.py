"""Data models for diagnostics: signals, recommendations, and delegation drafts.

DiagnosticSignal represents an observed behavior pattern;
DiagnosticRecommendation maps that signal to an actionable config
change; DelegationSuggestion is the draft for a brand-new subagent
proposed by clustering recurring general-purpose delegations.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, computed_field

from agentfluent.config.models import Severity


class SignalType(StrEnum):
    """Types of behavior signals detected in agent invocations.

    Metadata-level signals (extracted from `AgentInvocation` fields):
    - `ERROR_PATTERN`, `TOKEN_OUTLIER`, `DURATION_OUTLIER`

    Trace-level signals (extracted from `SubagentTrace` evidence):
    - `TOOL_ERROR_SEQUENCE`, `RETRY_LOOP`, `PERMISSION_FAILURE`,
      `STUCK_PATTERN`

    Aggregate-level signals (extracted from per-agent-type rollups):
    - `MODEL_MISMATCH`

    MCP audit signals (configured-vs-observed MCP server usage):
    - `MCP_UNUSED_SERVER`, `MCP_MISSING_SERVER`
    """

    ERROR_PATTERN = "error_pattern"
    TOKEN_OUTLIER = "token_outlier"
    DURATION_OUTLIER = "duration_outlier"
    TOOL_ERROR_SEQUENCE = "tool_error_sequence"
    RETRY_LOOP = "retry_loop"
    PERMISSION_FAILURE = "permission_failure"
    STUCK_PATTERN = "stuck_pattern"
    MODEL_MISMATCH = "model_mismatch"
    MCP_UNUSED_SERVER = "mcp_unused_server"
    MCP_MISSING_SERVER = "mcp_missing_server"


class DiagnosticSignal(BaseModel):
    """A single behavior signal detected in agent invocation data."""

    signal_type: SignalType
    severity: Severity
    agent_type: str | None
    """Agent this signal is scoped to. ``None`` for cross-cutting signals
    that don't belong to a specific agent (e.g. MCP server audit findings,
    which apply project-wide). Per-agent signals always carry a name."""
    invocation_id: str | None = None
    """Source ``AgentInvocation.invocation_id`` for per-invocation
    signals; ``None`` for cross-cutting signals (MCP audit)."""
    message: str
    detail: dict[str, object] = Field(default_factory=dict)
    """Extensible detail dict for signal-specific data (keyword, snippet,
    actual_value, mean_value, etc.)."""


class DiagnosticRecommendation(BaseModel):
    """An actionable recommendation derived from behavior signals.

    Follows the pattern: [What was observed] + [Why it matters] + [What to change].
    """

    target: str
    """Config surface to change (e.g., 'tools', 'prompt', 'model', 'hooks')."""

    severity: Severity
    message: str
    """Human-readable recommendation following observation+reason+action pattern."""

    observation: str = ""
    """What was observed in the session data."""

    reason: str = ""
    """Why this matters."""

    action: str = ""
    """What to change in the config."""

    agent_type: str | None = None
    """Which agent this recommendation applies to. ``None`` for
    cross-cutting recommendations not scoped to any single agent (e.g.
    MCP server audit findings)."""

    invocation_id: str | None = None
    """Copied from the contributing ``DiagnosticSignal`` so consumers
    can drill from a recommendation back to a specific session /
    subagent trace."""

    config_file: str = ""
    """Path to the agent config file, if known."""

    signal_types: list[SignalType] = Field(default_factory=list)
    """Which signal types contributed to this recommendation."""

    is_builtin: bool = False
    """True when the agent is one of Claude Code's built-in types
    (Explore, general-purpose, Plan, etc. — see
    ``agents.models.BUILTIN_AGENT_TYPES``). Built-in agents have no
    user-editable prompt/tool/model config, so their recommendations
    use a different action template. Denormalized onto the model so
    JSON consumers don't need to re-derive via ``is_builtin_agent()``."""


class AggregatedRecommendation(BaseModel):
    """Aggregate of one or more ``DiagnosticRecommendation`` instances that
    share the same ``(agent_type, target, signal_types)`` shape.

    Produced by ``diagnostics.aggregation.aggregate_recommendations`` so the
    default Recommendations table can show distinct findings (with an
    occurrence count and metric range) instead of N near-identical rows.
    The raw per-invocation recommendations remain available on
    ``contributing_recommendations`` for ``--verbose`` and JSON output.
    """

    agent_type: str | None
    """``None`` for cross-cutting findings; renders as ``(global)`` in
    terminal output."""
    target: str
    severity: Severity
    signal_types: list[SignalType] = Field(default_factory=list)

    count: int = 1
    """Number of per-invocation recommendations merged into this row."""

    metric_range: str | None = None
    """Human-readable metric range for signal types that carry ratio data
    (TOKEN_OUTLIER, DURATION_OUTLIER). ``None`` for signal types that do
    not expose a comparable scalar (retry counts, permission failures)."""

    representative_message: str
    """The message shown in the default table. Verbatim copy of
    ``contributing_recommendations[0].message`` when ``count == 1``;
    a synthesized cluster summary
    (``"<signal_type>[ (range)]: <action>"``) when ``count > 1``."""

    is_builtin: bool = False
    """Mirrors ``DiagnosticRecommendation.is_builtin`` — built-in and
    custom agents never aggregate together because ``agent_type`` is in
    the grouping key, so this is constant across
    ``contributing_recommendations``."""

    contributing_recommendations: list[DiagnosticRecommendation] = Field(
        default_factory=list,
    )
    """Raw per-invocation recommendations merged into this row. Source
    of truth for the underlying signal text; carries the full
    observation/reason/action/signal_types from each source recommendation
    (not denormalized onto the aggregated row). ``--verbose`` re-renders
    this list as the unaggregated view."""


class DelegationSuggestion(BaseModel):
    """A draft subagent definition derived from a cluster of recurring
    ``general-purpose`` delegations.

    Produced by the delegation clustering pipeline in
    ``agentfluent.diagnostics.delegation``. Deduped suggestions (those
    already covered by an existing agent config) are retained in output
    with a populated ``dedup_note`` so the user sees what was suppressed
    and why, rather than having the signal silently dropped.
    """

    name: str
    """Kebab-case agent name synthesized from the cluster's top terms."""

    description: str
    """One-line description synthesized from top terms."""

    model: str
    """Recommended Claude model ID (haiku / sonnet / opus)."""

    tools: list[str] = Field(default_factory=list)
    """Filtered tool list for the draft's frontmatter — tools used in at
    least ``DEFAULT_TOOL_FREQUENCY_THRESHOLD`` (50%) of cluster members.
    The full observed union lives on ``tools_observed`` for reference.
    Empty when no traces were linked to the member invocations OR when
    no tool met the threshold (see ``tools_note``)."""

    tools_observed: list[str] = Field(default_factory=list)
    """Full union of tools observed across the cluster's subagent
    traces, before frequency filtering. Surfaced so users can widen the
    draft's ``tools`` list manually if the filter was too aggressive."""

    tools_note: str = ""
    """Diagnostic note about the ``tools`` field. Populated when no
    traces were linked (older sessions) OR when traces were linked but
    no tool met the frequency threshold — in the latter case, the note
    points the user at ``tools_observed`` to review what was filtered."""

    prompt_template: str
    """Draft prompt scaffold anchored on the cluster's top terms."""

    confidence: Literal["high", "medium", "low"]
    """Confidence tier based on cluster size + cohesion."""

    cluster_size: int
    """How many invocations formed this cluster."""

    cohesion_score: float
    """Mean pairwise cosine similarity within the cluster."""

    top_terms: list[str] = Field(default_factory=list)
    """Top TF-IDF terms that characterize the cluster."""

    dedup_note: str = ""
    """Non-empty when the draft overlaps an existing agent config above
    the similarity threshold. Holds the matched agent name + similarity."""

    matched_agent: str = ""
    """Name of the existing agent that deduped this draft (empty when
    not deduped). Exposed as a first-class field so cross-reference
    logic can look up the matched agent without parsing ``dedup_note``."""

    def render_body(self) -> str:
        """YAML frontmatter + tools_comment + ``---`` + prompt body.

        Shared by ``yaml_draft`` here and ``OffloadCandidate.yaml_draft``,
        which prepends a different preamble. Any frontmatter shape change
        stays in lockstep across both consumers — the whole reason this
        is one method instead of two.
        """
        frontmatter_data: dict[str, object] = {
            "description": self.description,
            "model": self.model,
            "tools": self.tools,
        }
        frontmatter = yaml.safe_dump(
            frontmatter_data, sort_keys=False, default_flow_style=False,
        ).rstrip()
        tools_comment = ""
        if not self.tools and self.tools_note:
            tools_comment = f"\n# tools: {self.tools_note}"
        return (
            "---\n" + frontmatter + tools_comment + "\n---\n\n" + self.prompt_template
        )

    # ``# type: ignore[prop-decorator]`` is the documented workaround for
    # mypy not reconciling ``@computed_field`` stacked on ``@property``
    # (upstream: pydantic/pydantic#6709).
    @computed_field  # type: ignore[prop-decorator]
    @property
    def yaml_draft(self) -> str:
        """Copy-paste-ready subagent definition block.

        Matches the shape a user would save to
        ``~/.claude/agents/<name>.md``: comment preamble with confidence
        + cluster context, YAML frontmatter (description, model, tools),
        ``---`` separator, prompt body. Low-confidence clusters get a
        REVIEW warning in the preamble so the caller doesn't paste them
        into production without vetting.
        """
        preamble: list[str] = [f"# Suggested agent: {self.name}"]
        if self.confidence == "low":
            preamble.append("# REVIEW BEFORE USE — low confidence cluster")
        preamble.append(
            f"# Confidence: {self.confidence} "
            f"({self.cluster_size} invocations, {self.cohesion_score:.2f} cohesion)",
        )
        if self.top_terms:
            preamble.append(f"# Top terms: {', '.join(self.top_terms)}")
        if self.dedup_note:
            preamble.append(f"# Note: {self.dedup_note}")
        return "\n".join(preamble) + "\n" + self.render_body()


class SkillScaffold(BaseModel):
    """v0.6 placeholder for a skill-scaffold draft.

    Intentionally empty in v0.5 — schema reservation only. ``OffloadCandidate``
    serializes ``skill_draft: null`` for every candidate this release. Once
    the skill scanner (#183) lands, fields will be added here additively;
    consumers that rely on ``skill_draft is None`` to mean "v0.5 didn't ship
    a skill" should switch to ``target_kind == 'skill'`` post-v0.6.

    ``extra="forbid"`` is the forward-compat tripwire: a v0.6-emitted JSON
    deserialized by a v0.5 consumer will raise instead of silently dropping
    fields, so the migration plan can't get bypassed by accident.
    """

    model_config = ConfigDict(extra="forbid")


class OffloadCandidate(BaseModel):
    """A parent-thread workload pattern that could move to a cheaper subagent.

    Produced by the parent-thread offload-candidate pipeline (#189) which
    clusters ``ToolBurst`` records on the parent (typically Opus) thread,
    estimates the cost delta against a cheaper alternative model, and
    synthesizes a copy-paste-ready subagent draft. Peer concept to
    ``DelegationSuggestion`` — that one comes from clustering existing
    ``general-purpose`` delegations; this one comes from clustering the
    parent thread's own tool-using turns.

    ``estimated_savings_usd`` is **signed**: negative means offloading
    would cost MORE than staying on the parent (cache is load-bearing for
    this pattern). Per architect review for #189-C, the sign is preserved
    rather than clamped — the negative case is the most actionable signal
    this feature can produce. ``cost_note`` is populated when negative
    with a CLI-render-ready warning.
    """

    name: str
    """Kebab-case suggested subagent name synthesized from top terms."""

    description: str
    """One-line description synthesized from top terms."""

    confidence: Literal["high", "medium", "low"]
    """Confidence tier based on cluster size + cohesion (same boundaries
    as ``DelegationSuggestion``)."""

    cluster_size: int
    """Number of bursts in the cluster."""

    cohesion_score: float
    """Mean pairwise cosine similarity within the cluster's TF-IDF vectors."""

    top_terms: list[str] = Field(default_factory=list)
    """Top TF-IDF terms characterizing the cluster."""

    tool_sequence_summary: list[str] = Field(default_factory=list)
    """Most common tool sequence across the cluster (in order, not deduped).
    Modal sequence by ``Counter`` of tool-name tuples; empty when cluster
    has no observed tool calls."""

    tools: list[str] = Field(default_factory=list)
    """Frequency-filtered tool list (mirrors ``subagent_draft.tools`` when
    a subagent draft is present). Surfaced flat so the CLI compact-table
    formatter doesn't reach across the ``subagent_draft`` boundary, which
    would silently fall through to ``[]`` for the v0.6 ``target_kind=skill``
    path. Other rendering surfaces (verbose YAML, JSON) read whichever
    field they prefer; both stay in sync at construction time."""

    tools_note: str = ""
    """Diagnostic note about the ``tools`` list, mirroring
    ``subagent_draft.tools_note``. Same surface-flat rationale as
    ``tools`` above."""

    estimated_parent_tokens: int = 0
    """Sum of total tokens used across cluster bursts on the parent thread."""

    estimated_parent_cost_usd: float = 0.0
    """Aggregate parent-thread cost (USD) of the cluster's bursts at the
    parent model's pricing. ``0.0`` when pricing was unavailable."""

    estimated_savings_usd: float = 0.0
    """Signed savings (USD) projected if the cluster's work moved to
    ``alternative_model``. NEGATIVE means offloading would cost more —
    see ``cost_note``. ``0.0`` when pricing unavailable."""

    parent_model: str = ""
    """Model id observed on the cluster's parent-thread bursts."""

    alternative_model: str
    """Recommended cheaper-tier model id (e.g., ``claude-sonnet-4-6``)."""

    cost_note: str = ""
    """Populated when ``estimated_savings_usd`` is negative — explains
    that offloading would increase cost (parent-thread cache is
    load-bearing) and recommends keeping the work on the parent."""

    target_kind: Literal["subagent", "skill"] = "subagent"
    """Always ``"subagent"`` in v0.5; ``"skill"`` reserved for v0.6 once
    skill scanner #183 lands."""

    subagent_draft: DelegationSuggestion | None = None
    """Populated when ``target_kind == "subagent"`` (always in v0.5).
    Reuses ``DelegationSuggestion`` so the YAML draft shape stays
    consistent between this feature and the existing delegation
    suggestions."""

    skill_draft: SkillScaffold | None = None
    """Always ``None`` in v0.5. Field shape reserved for v0.6."""

    matched_agent: str = ""
    """Name of an existing ``AgentConfig`` whose description overlaps this
    candidate's draft above the dedup threshold. Populated by sub-issue E."""

    dedup_note: str = ""
    """Non-empty when the candidate overlaps an existing agent. Populated
    by sub-issue E."""

    # ``# type: ignore[prop-decorator]`` mirrors ``DelegationSuggestion`` —
    # mypy + pydantic ``@computed_field`` interaction (pydantic/pydantic#6709).
    @computed_field  # type: ignore[prop-decorator]
    @property
    def yaml_draft(self) -> str:
        """Copy-paste-ready subagent block with offload-specific context.

        Builds an offload-flavored preamble (savings, parent → alt model,
        cost_note when negative) on top of the ``subagent_draft``'s
        frontmatter + prompt body. Does NOT delegate to
        ``DelegationSuggestion.yaml_draft`` — that one's preamble is
        about delegation patterns; this one needs to communicate the
        parent-thread offload framing and the dollar figure that
        motivates the change.
        """
        if self.subagent_draft is None:
            return ""

        preamble: list[str] = [
            f"# Suggested subagent (parent-thread offload candidate): {self.name}",
        ]
        if self.confidence == "low":
            preamble.append("# REVIEW BEFORE USE — low confidence cluster")
        preamble.append(
            f"# Confidence: {self.confidence} "
            f"({self.cluster_size} bursts, {self.cohesion_score:.2f} cohesion)",
        )
        if self.parent_model:
            preamble.append(
                f"# Parent model: {self.parent_model} "
                f"→ recommended: {self.alternative_model}",
            )
        else:
            preamble.append(f"# Recommended model: {self.alternative_model}")
        if self.estimated_parent_tokens:
            preamble.append(
                f"# Parent-thread cost: ${self.estimated_parent_cost_usd:.4f} "
                f"({self.estimated_parent_tokens:,} tokens)",
            )
        # Signed savings: positive renders as a savings figure, negative
        # renders as the actionable "do not offload" signal.
        if self.estimated_savings_usd >= 0:
            preamble.append(
                f"# Estimated savings: ${self.estimated_savings_usd:.4f}",
            )
        else:
            preamble.append(
                f"# Estimated cost change: +${-self.estimated_savings_usd:.4f} "
                f"(offloading would cost MORE)",
            )
        if self.cost_note:
            preamble.append(f"# Note: {self.cost_note}")
        if self.top_terms:
            preamble.append(f"# Top terms: {', '.join(self.top_terms)}")
        if self.dedup_note:
            preamble.append(f"# Dedup: {self.dedup_note}")

        return (
            "\n".join(preamble) + "\n" + self.subagent_draft.render_body()
        )


class DiagnosticsResult(BaseModel):
    """Complete diagnostics output for a session or set of sessions."""

    signals: list[DiagnosticSignal] = Field(default_factory=list)
    recommendations: list[DiagnosticRecommendation] = Field(default_factory=list)
    """Raw per-invocation recommendations. One entry per matched signal.
    Retained alongside ``aggregated_recommendations`` so ``--verbose`` and
    JSON consumers can drill into unaggregated evidence."""

    aggregated_recommendations: list[AggregatedRecommendation] = Field(
        default_factory=list,
    )
    """Recommendations aggregated by ``(agent_type, target, signal_types)``
    with occurrence counts and metric ranges. This is the default surface
    shown in the table formatter."""

    subagent_trace_count: int = 0
    """Number of subagent traces that successfully parsed and linked."""

    delegation_suggestions: list[DelegationSuggestion] = Field(default_factory=list)
    """Draft subagent definitions proposed by the clustering pipeline."""

    # v0.5: added offload_candidates (#189). Wired up in sub-issue E.
    offload_candidates: list[OffloadCandidate] = Field(default_factory=list)
    """Parent-thread offload candidates surfaced by the burst-clustering
    pipeline (#189). Empty list when sklearn isn't installed or no
    cluster met ``min_cluster_size``."""
