"""Recommendation text for built-in agents (#166).

Built-in agents (``Explore``, ``general-purpose``, ``Plan``, ``code-reviewer``,
``statusline-setup``, ``claude-code-guide`` — see
``agentfluent.agents.models.BUILTIN_AGENT_TYPES``) have no user-editable
prompt file, no tool-list config, and no model selector. The generic
rule templates that tell the user to "edit the agent's prompt in
~/.claude/agents/<name>.md" are therefore un-actionable for these
agents.

This module owns the built-in-specific action text and the helper that
each correlation rule calls when ``is_builtin_agent(signal.agent_type)``
is true. Kept separate from ``correlator.py`` to keep that file under
the per-module size convention as the rule count grows.
"""

from __future__ import annotations

from typing import Literal

from agentfluent.diagnostics.models import (
    DiagnosticRecommendation,
    DiagnosticSignal,
)

BuiltinConcern = Literal["scope", "recovery", "tools", "model"]

_BUILTIN_ACTIONS: dict[BuiltinConcern, str] = {
    "scope": (
        "Built-in agent — prompt is not user-editable. "
        "Consider: (a) a custom wrapper subagent that narrows the task scope, "
        "(b) tightening the delegation prompt passed to this agent, "
        "or (c) rerouting this task to a different agent."
    ),
    "recovery": (
        "Built-in agent — prompt is not user-editable. "
        "Add retry bounds or exit conditions to the *delegating* agent's "
        "prompt, since the built-in agent cannot enforce them itself. "
        "Alternatively, wrap this call in a custom subagent that owns the "
        "recovery logic."
    ),
    "tools": (
        "Built-in agent — tool list is not user-editable. "
        "Route this task to a custom subagent with explicit tool grants, "
        "or confirm the built-in agent's fixed tool set is actually required."
    ),
    "model": (
        "Built-in agent — model is not user-configurable. "
        "Create a custom subagent with the recommended model, or reroute "
        "this task to an existing agent that already uses it."
    ),
}


def builtin_recommendation(
    signal: DiagnosticSignal,
    *,
    target: str,
    concern: BuiltinConcern,
    observation: str,
    reason: str,
) -> DiagnosticRecommendation:
    """Build a recommendation for a signal whose agent is a built-in.

    Callers (correlation rules) supply the rule-specific ``observation``
    and ``reason`` text so each recommendation still reads like it came
    from the source rule; only the ``action`` differs from the custom-
    agent path. ``is_builtin=True`` is stamped so downstream consumers
    (JSON output, priority scoring in #172) can distinguish these rows
    without re-deriving.
    """
    action = _BUILTIN_ACTIONS[concern]
    return DiagnosticRecommendation(
        target=target,
        severity=signal.severity,
        message=f"{observation} {reason} {action}".strip(),
        observation=observation,
        reason=reason,
        action=action,
        agent_type=signal.agent_type,
        config_file="",
        signal_types=[signal.signal_type],
        is_builtin=True,
    )
