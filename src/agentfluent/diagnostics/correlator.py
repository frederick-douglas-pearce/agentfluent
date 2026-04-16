"""Signal-to-config correlation engine.

Maps detected behavior signals to specific agent configuration surfaces
and generates actionable recommendations. Uses structured rule objects
for extensibility.
"""

from __future__ import annotations

from typing import Protocol

from agentfluent.config.models import AgentConfig, Severity
from agentfluent.diagnostics.models import (
    DiagnosticRecommendation,
    DiagnosticSignal,
    SignalType,
)


class CorrelationRule(Protocol):
    """Protocol for correlation rules."""

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool: ...

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation: ...


class AccessErrorRule:
    """Error pattern "blocked"/"permission denied" -> check tool access."""

    _KEYWORDS = {"blocked", "permission denied", "don't have access"}

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        if signal.signal_type != SignalType.ERROR_PATTERN:
            return False
        keyword = str(signal.detail.get("keyword", "")).lower()
        return keyword in self._KEYWORDS

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        reason = "This indicates the agent lacks access to required tools."

        if config and not config.tools and not config.disallowed_tools:
            action = (
                f"Add a 'tools' list to {config.file_path} "
                "to explicitly grant required tool access."
            )
        elif config and config.tools:
            action = (
                f"Review the 'tools' list in {config.file_path} "
                "and ensure all required tools are included."
            )
        else:
            action = (
                "Check your agent's tool configuration to ensure "
                "required tools are accessible."
            )

        return DiagnosticRecommendation(
            target="tools",
            severity=Severity.CRITICAL,
            message=f"{observation} {reason} {action}",
            observation=observation,
            reason=reason,
            action=action,
            agent_type=signal.agent_type,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class ErrorHandlingRule:
    """Error pattern "failed"/"error"/"retry" -> check prompt for error guidance."""

    _KEYWORDS = {"failed", "error", "retry", "unable to", "not found", "timed out"}

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        if signal.signal_type != SignalType.ERROR_PATTERN:
            return False
        keyword = str(signal.detail.get("keyword", "")).lower()
        return keyword in self._KEYWORDS

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        reason = "Repeated errors suggest the agent lacks error handling guidance."

        if config and config.prompt_body:
            body_lower = config.prompt_body.lower()
            has_error_guidance = any(
                kw in body_lower for kw in ("error", "fail", "handle", "retry")
            )
            if has_error_guidance:
                action = (
                    f"The prompt in {config.file_path} mentions error handling, "
                    "but the agent still encounters errors. Consider more specific "
                    "recovery instructions."
                )
            else:
                action = (
                    f"Add error handling guidance to the prompt body "
                    f"in {config.file_path}."
                )
        else:
            action = (
                "Add error handling instructions to your agent's prompt body."
            )

        return DiagnosticRecommendation(
            target="prompt",
            severity=Severity.WARNING,
            message=f"{observation} {reason} {action}",
            observation=observation,
            reason=reason,
            action=action,
            agent_type=signal.agent_type,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class TokenOutlierRule:
    """Token outlier -> recommend more focused instructions or tool restriction."""

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.TOKEN_OUTLIER

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        reason = "High token usage suggests the agent is exploring broadly."

        if config and len(config.tools) > 8:
            action = (
                f"Consider restricting the tools list in {config.file_path} "
                "to only those needed for this agent's task."
            )
            target = "tools"
        elif config:
            action = (
                f"Add more specific instructions to the prompt in "
                f"{config.file_path} to reduce exploration."
            )
            target = "prompt"
        else:
            action = (
                "Consider adding more specific instructions to the agent's "
                "prompt or restricting its tool list."
            )
            target = "prompt"

        return DiagnosticRecommendation(
            target=target,
            severity=Severity.WARNING,
            message=f"{observation} {reason} {action}",
            observation=observation,
            reason=reason,
            action=action,
            agent_type=signal.agent_type,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class DurationOutlierRule:
    """Duration outlier -> check model selection or task scoping."""

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.DURATION_OUTLIER

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        reason = "Slow invocations may indicate an overqualified model or unclear task scope."

        if config and config.model and "opus" in config.model.lower():
            action = (
                f"If this agent's task is routine, consider switching "
                f"from {config.model} to a faster model in {config.file_path}."
            )
            target = "model"
        elif config:
            action = (
                f"Add clearer task boundaries to the prompt in "
                f"{config.file_path} to help the agent work more efficiently."
            )
            target = "prompt"
        else:
            action = (
                "Consider using a faster model or adding clearer task "
                "boundaries to the agent's prompt."
            )
            target = "model"

        return DiagnosticRecommendation(
            target=target,
            severity=Severity.WARNING,
            message=f"{observation} {reason} {action}",
            observation=observation,
            reason=reason,
            action=action,
            agent_type=signal.agent_type,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


# Module-level rule registry. Add new rules here.
RULES: list[CorrelationRule] = [
    AccessErrorRule(),
    ErrorHandlingRule(),
    TokenOutlierRule(),
    DurationOutlierRule(),
]


def correlate(
    signals: list[DiagnosticSignal],
    configs: dict[str, AgentConfig] | None = None,
) -> list[DiagnosticRecommendation]:
    """Map signals to config surfaces and produce recommendations.

    Args:
        signals: Detected behavior signals from signal extraction.
        configs: Optional dict of agent_type (lowercase) -> AgentConfig.
            When available, recommendations reference specific config files.

    Returns:
        List of actionable recommendations.
    """
    recommendations: list[DiagnosticRecommendation] = []

    for signal in signals:
        config = configs.get(signal.agent_type.lower()) if configs else None

        for rule in RULES:
            if rule.matches(signal, config):
                recommendations.append(rule.recommend(signal, config))
                break

    return recommendations
