"""Agent-config audit: detect custom agents defined but never delegated to.

Mirrors the ``mcp_assessment.audit_mcp_servers`` pattern — compares a
configured surface (agent definitions from
``config.scanner.scan_agents``) against an observed surface (agent
types in the session's ``AgentInvocation`` list) and emits a signal
for each configured-but-unused custom agent.

Built-in agents (``general-purpose``, ``Explore``, ``Plan``, etc.) are
silently excluded per D033 — their absence in a given window may be
entirely normal and a recommendation surface would just produce
noise.

Empty-invocations early return (per architect review for #346): if
nothing fired in the window, flagging every custom agent as unused is
meaninglessly true and undermines the diagnostics surface's signal-
to-noise ratio. Callers who want config-only checks have
``agentfluent config-check``.
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
        # Empty-window suppression: flagging every custom agent as
        # unused when nothing ran is meaninglessly true.
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
