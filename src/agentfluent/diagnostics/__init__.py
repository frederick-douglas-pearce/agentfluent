"""Diagnostics package -- behavior-to-improvement analysis."""

from __future__ import annotations

import logging

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import AgentConfig
from agentfluent.config.scanner import scan_agents
from agentfluent.diagnostics.correlator import correlate
from agentfluent.diagnostics.models import DiagnosticsResult
from agentfluent.diagnostics.signals import extract_signals
from agentfluent.diagnostics.trace_signals import extract_trace_signals
from agentfluent.traces.models import UNKNOWN_AGENT_TYPE

logger = logging.getLogger(__name__)


def run_diagnostics(
    invocations: list[AgentInvocation],
    *,
    subagent_trace_count: int = 0,
) -> DiagnosticsResult:
    """Run the full diagnostics pipeline on agent invocations.

    Extracts behavior signals, scans for agent config files, correlates
    signals with config, and produces recommendations.

    Args:
        invocations: Extracted agent invocations from session analysis.
        subagent_trace_count: Number of subagent trace files detected.

    Returns:
        DiagnosticsResult with signals and recommendations.
    """
    signals = extract_signals(invocations)

    # Fold in trace-level signals for any invocation with an attached
    # subagent trace. The linker normally copies agent_type from parent
    # to trace, but defend against UNKNOWN_AGENT_TYPE for unlinked or
    # programmatically-built traces.
    for inv in invocations:
        if inv.trace is None:
            continue
        for signal in extract_trace_signals(inv.trace):
            if not signal.agent_type or signal.agent_type == UNKNOWN_AGENT_TYPE:
                signal.agent_type = inv.agent_type
            signals.append(signal)

    # Build config lookup for correlation
    configs: dict[str, AgentConfig] | None = None
    try:
        agent_configs = scan_agents("all")
        if agent_configs:
            configs = {c.name.lower(): c for c in agent_configs}
    except OSError:
        logger.debug("Could not scan agent config files", exc_info=True)

    recommendations = correlate(signals, configs)

    return DiagnosticsResult(
        signals=signals,
        recommendations=recommendations,
        subagent_trace_count=subagent_trace_count,
    )
