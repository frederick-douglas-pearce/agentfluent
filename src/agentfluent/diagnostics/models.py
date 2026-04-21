"""Data models for diagnostics: signals and recommendations.

These models are the output of the diagnostics pipeline. DiagnosticSignal
represents an observed behavior pattern; DiagnosticRecommendation maps
that signal to an actionable config change.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from agentfluent.config.models import Severity


class SignalType(StrEnum):
    """Types of behavior signals detected in agent invocations.

    Metadata-level signals (extracted from `AgentInvocation` fields):
    - `ERROR_PATTERN`, `TOKEN_OUTLIER`, `DURATION_OUTLIER`

    Trace-level signals (extracted from `SubagentTrace` evidence):
    - `TOOL_ERROR_SEQUENCE`, `RETRY_LOOP`, `PERMISSION_FAILURE`,
      `STUCK_PATTERN`
    """

    ERROR_PATTERN = "error_pattern"
    TOKEN_OUTLIER = "token_outlier"
    DURATION_OUTLIER = "duration_outlier"
    TOOL_ERROR_SEQUENCE = "tool_error_sequence"
    RETRY_LOOP = "retry_loop"
    PERMISSION_FAILURE = "permission_failure"
    STUCK_PATTERN = "stuck_pattern"


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


class DiagnosticsResult(BaseModel):
    """Complete diagnostics output for a session or set of sessions."""

    signals: list[DiagnosticSignal] = Field(default_factory=list)
    recommendations: list[DiagnosticRecommendation] = Field(default_factory=list)
    subagent_trace_count: int = 0
    """Number of subagent trace files detected (teaser for v1.1)."""
