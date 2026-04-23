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


class PermissionFailureRule:
    """PERMISSION_FAILURE -> recommend adding the denied tool to `tools`."""

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.PERMISSION_FAILURE

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        tool_name = str(signal.detail.get("tool_name", ""))
        observation = signal.message
        reason = "The subagent was denied access to a tool it attempted to call."

        if config and tool_name and tool_name not in config.tools:
            action = (
                f"Add '{tool_name}' to the tools list in {config.file_path} "
                "(or remove it from disallowed_tools)."
            )
        elif config and tool_name in config.tools:
            action = (
                f"'{tool_name}' is listed in {config.file_path} but was "
                "still denied -- check disallowed_tools and any hooks that "
                "might block this tool."
            )
        else:
            action = (
                f"Grant the subagent access to '{tool_name}' in its agent "
                "configuration, or remove calls to that tool from the prompt."
                if tool_name
                else "Grant the subagent access to the denied tool in its config."
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


class RetryLoopRule:
    """RETRY_LOOP -> recommend error-recovery guidance in the prompt."""

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.RETRY_LOOP

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        reason = (
            "Repeated retries on the same tool indicate the agent lacks "
            "recovery guidance for failures."
        )

        if config and config.prompt_body:
            body_lower = config.prompt_body.lower()
            has_error_guidance = any(
                kw in body_lower for kw in ("error", "fail", "handle", "retry")
            )
            if has_error_guidance:
                action = (
                    f"The prompt in {config.file_path} mentions error handling, "
                    "but the agent still retried without progress. Consider "
                    "more specific stop conditions or alternative-tool fallbacks."
                )
            else:
                action = (
                    f"Add explicit retry / fallback guidance to the prompt "
                    f"in {config.file_path}."
                )
        else:
            action = (
                "Add explicit retry / fallback guidance to the agent's "
                "prompt body."
            )

        return DiagnosticRecommendation(
            target="prompt",
            severity=signal.severity,
            message=f"{observation} {reason} {action}",
            observation=observation,
            reason=reason,
            action=action,
            agent_type=signal.agent_type,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class StuckPatternRule:
    """STUCK_PATTERN -> recommend adding exit conditions to the prompt."""

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.STUCK_PATTERN

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        count = signal.detail.get("stuck_count", "multiple")
        reason = (
            f"The agent repeated an identical call {count} times without "
            "progress, indicating no exit condition."
        )

        if config:
            action = (
                f"Add an explicit exit condition or progress check to the "
                f"prompt in {config.file_path} (e.g., 'after 2 failed attempts, "
                "return the error instead of retrying')."
            )
        else:
            action = (
                "Add an explicit exit condition or progress check to the "
                "agent's prompt."
            )

        return DiagnosticRecommendation(
            target="prompt",
            severity=Severity.CRITICAL,
            message=f"{observation} {reason} {action}",
            observation=observation,
            reason=reason,
            action=action,
            agent_type=signal.agent_type,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class ErrorSequenceRule:
    """TOOL_ERROR_SEQUENCE -> recommend fallback instructions or tool review."""

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.TOOL_ERROR_SEQUENCE

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        reason = (
            "Multiple consecutive tool errors suggest the agent lacks "
            "fallback instructions for when a tool call fails."
        )

        if config and len(config.tools) > 8:
            action = (
                f"Review the tools list in {config.file_path} -- the agent "
                "may be reaching for tools it does not need, or a specific "
                "tool may be misconfigured."
            )
            target = "tools"
        elif config:
            action = (
                f"Add fallback instructions to the prompt in "
                f"{config.file_path} so the agent knows what to do when a "
                "tool call fails repeatedly."
            )
            target = "prompt"
        else:
            action = (
                "Add fallback instructions to the agent's prompt so it "
                "recovers from repeated tool failures."
            )
            target = "prompt"

        return DiagnosticRecommendation(
            target=target,
            severity=signal.severity,
            message=f"{observation} {reason} {action}",
            observation=observation,
            reason=reason,
            action=action,
            agent_type=signal.agent_type,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class ModelRoutingRule:
    """MODEL_MISMATCH -> recommend switching to the right-tier model.

    Overspec: switch down + cite the cost-savings estimate when pricing
    is available. Underspec: switch up, no savings (this would cost
    more, but the tradeoff is quality).
    """

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.MODEL_MISMATCH

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        detail = signal.detail
        mismatch_type = str(detail.get("mismatch_type", ""))
        current_model = str(detail.get("current_model", ""))
        recommended_model = str(detail.get("recommended_model", ""))
        complexity = str(detail.get("complexity_tier", "moderate"))
        invocation_count = detail.get("invocation_count", 0)
        savings = detail.get("estimated_savings_usd")

        observation = signal.message
        reason = (
            f"Observed complexity tier is '{complexity}' but the agent is "
            f"configured with {current_model}."
        )

        action_parts = [f"Switch to {recommended_model}"]
        if mismatch_type == "overspec" and isinstance(savings, int | float):
            action_parts.append(
                f"(estimated savings: ${savings:.2f} across "
                f"{invocation_count} invocations)",
            )
        if config:
            action_parts.append(f"— edit the `model:` field in {config.file_path}.")
            action = " ".join(action_parts)
        else:
            action = " ".join(action_parts) + "."

        return DiagnosticRecommendation(
            target="model",
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
class McpAuditRule:
    """MCP server audit signals -> recommend mcpServers config edit.

    Both ``MCP_UNUSED_SERVER`` and ``MCP_MISSING_SERVER`` land here;
    the recommendation branches on signal_type to produce the right
    observation/reason/action. Severity mirrors the signal
    (INFO for unused, WARNING for missing) so downstream sort by
    severity keeps advisory notes below actionable warnings.
    """

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type in (
            SignalType.MCP_UNUSED_SERVER,
            SignalType.MCP_MISSING_SERVER,
        )

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        server_name = str(signal.detail.get("server_name", ""))

        if signal.signal_type == SignalType.MCP_UNUSED_SERVER:
            source_file = str(signal.detail.get("source_file", ""))
            reason = (
                "A configured server with no observed usage is either "
                "unused or inactive — removing it reduces config drift."
            )
            action = (
                f"Remove '{server_name}' from mcpServers in {source_file}, "
                "or set disabled: true if you expect to use it later."
            )
        else:  # MCP_MISSING_SERVER
            reason = (
                "Failed calls to an unconfigured MCP server indicate the "
                "server is expected but not installed in any config "
                "scope visible to the agent."
            )
            action = (
                f"Add '{server_name}' to ~/.claude.json (user scope) or "
                ".mcp.json (project scope) so the agent can reach it."
            )

        return DiagnosticRecommendation(
            target="mcp",
            severity=signal.severity,
            message=f"{observation} {reason} {action}",
            observation=observation,
            reason=reason,
            action=action,
            agent_type=signal.agent_type,
            config_file=str(signal.detail.get("source_file", "")),
            signal_types=[signal.signal_type],
        )


RULES: list[CorrelationRule] = [
    AccessErrorRule(),
    ErrorHandlingRule(),
    TokenOutlierRule(),
    DurationOutlierRule(),
    PermissionFailureRule(),
    RetryLoopRule(),
    StuckPatternRule(),
    ErrorSequenceRule(),
    ModelRoutingRule(),
    McpAuditRule(),
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
