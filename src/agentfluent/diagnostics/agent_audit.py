"""Configured-vs-observed agent-definition audits.

- ``audit_unused_agents`` (#346): emits ``UNUSED_AGENT`` for each custom
  agent defined in ``~/.claude/agents/`` (or the project-scoped
  equivalent) with zero invocations in the analyzed window. Built-ins
  excluded per D033; empty-window suppression so the surface stays
  useful — callers who want config-only checks have
  ``agentfluent config-check``.
- ``audit_tool_inventory`` (#372): emits ``TOOL_INVENTORY_OVERSIZED``
  for agents that declare a large tool list but exercise few of those
  tools (low utilization), the configured-vs-observed analog applied to
  the ``tools:`` surface rather than delegation.
"""

from __future__ import annotations

from agentfluent.agents.models import AgentInvocation, is_builtin_agent
from agentfluent.config.models import AgentConfig, Severity
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType

# RAG-MCP (arXiv 2505.03275) heatmap inflection point: tool-selection
# accuracy degrades once an agent declares more than ~30 tools. Below
# this, full-schema loading is fine. Anthropic's "Advanced Tool Use"
# article corroborates with a first-party benchmark (#404).
TOOL_INVENTORY_THRESHOLD = 30

# Utilization gate: declared count alone isn't enough — an agent that
# declares 40 tools and uses 38 is fine. Fire only when fewer than half
# the declared tools are ever observed in the window.
TOOL_UTILIZATION_THRESHOLD = 0.5


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


def audit_tool_inventory(
    invocations: list[AgentInvocation],
    agent_configs: list[AgentConfig],
    *,
    sessions_analyzed: int,
    declared_threshold: int = TOOL_INVENTORY_THRESHOLD,
    utilization_threshold: float = TOOL_UTILIZATION_THRESHOLD,
) -> list[DiagnosticSignal]:
    """Emit ``TOOL_INVENTORY_OVERSIZED`` for oversized, under-used tool lists.

    A signal fires for an agent config when ALL of the following hold:

    - it declares more than ``declared_threshold`` tools (the RAG-MCP
      inflection point), and the declared list is not a wildcard (``*``),
      which leaves the denominator undefined;
    - the agent was invoked at least once in the window (zero-invocation
      agents are :func:`audit_unused_agents`'s domain, not this one);
    - at least one of those invocations reported ``toolStats`` so observed
      tool diversity is *known* (an agent whose invocations all lack
      ``toolStats`` is suppressed rather than scored as zero-utilization —
      missing data must not masquerade as a finding);
    - the utilization ratio (unique observed tools / declared tools) is
      below ``utilization_threshold``.

    Scope is config-declared agents only (custom ``.md`` agents and SDK
    ``allowed_tools``). Built-in agents are excluded because there is no
    declared-tool-count data source for them; see #372 for the deferred
    built-in-coverage follow-up.
    """
    if not invocations:
        return []

    observed_by_agent: dict[str, set[str]] = {}
    for inv in invocations:
        if inv.tool_stats:
            observed_by_agent.setdefault(inv.agent_type.lower(), set()).update(
                inv.tool_stats,
            )

    signals: list[DiagnosticSignal] = []
    for config in agent_configs:
        declared = config.tools
        if "*" in declared:
            continue
        declared_count = len(declared)
        if declared_count <= declared_threshold:
            continue

        # An agent only appears in observed_by_agent when at least one of
        # its invocations reported toolStats, so an empty set covers both
        # suppressed cases: never invoked in the window (that's the
        # unused-agent audit's domain), and invoked but with no toolStats
        # (observed diversity unknown — suppress rather than report 0%).
        observed = observed_by_agent.get(config.name.lower(), set())
        if not observed:
            continue

        observed_count = len(observed)
        ratio = observed_count / declared_count
        if ratio >= utilization_threshold:
            continue

        signals.append(
            DiagnosticSignal(
                signal_type=SignalType.TOOL_INVENTORY_OVERSIZED,
                severity=Severity.INFO,
                agent_type=config.name,
                message=(
                    f"Agent '{config.name}' declares {declared_count} tools "
                    f"but used only {observed_count} unique tools "
                    f"({ratio:.0%} utilization) across {sessions_analyzed} "
                    "analyzed sessions."
                ),
                detail={
                    "agent_name": config.name,
                    "source_file": str(config.file_path),
                    "declared_count": declared_count,
                    "observed_count": observed_count,
                    "utilization_ratio": ratio,
                    "observed_tools": sorted(observed),
                    "sessions_analyzed": sessions_analyzed,
                },
            ),
        )
    return signals
