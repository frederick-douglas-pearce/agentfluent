"""Configured-vs-observed agent-definition audit (#346).

Emits ``UNUSED_AGENT`` for each custom agent defined in
``~/.claude/agents/`` (or the project-scoped equivalent) with zero
invocations in the analyzed window. Built-ins excluded per D033;
empty-window suppression so the surface stays useful — callers who
want config-only checks have ``agentfluent config-check``.
"""

from __future__ import annotations

from agentfluent.agents.models import AgentInvocation, is_builtin_agent
from agentfluent.config.models import AgentConfig, Severity
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType


def audit_unused_agents(
    invocations: list[AgentInvocation],
    agent_configs: list[AgentConfig],
    *,
    sessions_analyzed: int,
) -> list[DiagnosticSignal]:
    """Emit ``UNUSED_AGENT`` signals for custom agents with zero invocations.

    ``sessions_analyzed`` is surfaced in signal detail so the user can
    judge confidence ("0 invocations across 50 sessions" is stronger
    evidence than "0 invocations across 2 sessions"). Threaded from
    ``AnalysisResult.session_count`` by the CLI; programmatic callers
    default to ``len(invocations) or 1`` via ``run_diagnostics``.
    """
    if not invocations:
        return []

    observed = {inv.agent_type.lower() for inv in invocations}

    signals: list[DiagnosticSignal] = []
    for config in agent_configs:
        if is_builtin_agent(config.name):
            continue
        if config.name.lower() in observed:
            continue
        signals.append(
            DiagnosticSignal(
                signal_type=SignalType.UNUSED_AGENT,
                severity=Severity.INFO,
                agent_type=config.name,
                message=(
                    f"Agent '{config.name}' is defined in {config.file_path} "
                    f"but has 0 invocations across {sessions_analyzed} "
                    "analyzed sessions."
                ),
                detail={
                    "agent_name": config.name,
                    "source_file": str(config.file_path),
                    "description": config.description,
                    "sessions_analyzed": sessions_analyzed,
                },
            ),
        )
    return signals
