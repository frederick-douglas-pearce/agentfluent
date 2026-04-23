"""Data models for diagnostics: signals, recommendations, and delegation drafts.

DiagnosticSignal represents an observed behavior pattern;
DiagnosticRecommendation maps that signal to an actionable config
change; DelegationSuggestion is the draft for a brand-new subagent
proposed by clustering recurring general-purpose delegations.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

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
    agent_type: str
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

    agent_type: str = ""
    """Which agent this recommendation applies to."""

    config_file: str = ""
    """Path to the agent config file, if known."""

    signal_types: list[SignalType] = Field(default_factory=list)
    """Which signal types contributed to this recommendation."""


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
    """Union of tools observed in the cluster's subagent traces. Empty
    when no traces were linked to the member invocations."""

    tools_note: str = ""
    """Diagnostic note when ``tools`` cannot be derived (e.g., older
    sessions lacking trace capture)."""

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


class DiagnosticsResult(BaseModel):
    """Complete diagnostics output for a session or set of sessions."""

    signals: list[DiagnosticSignal] = Field(default_factory=list)
    recommendations: list[DiagnosticRecommendation] = Field(default_factory=list)
    subagent_trace_count: int = 0
    """Number of subagent traces that successfully parsed and linked."""

    delegation_suggestions: list[DelegationSuggestion] = Field(default_factory=list)
    """Draft subagent definitions proposed by the clustering pipeline."""
