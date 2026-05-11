"""Signal-to-config correlation engine.

Maps detected behavior signals to specific agent configuration surfaces
and generates actionable recommendations. Uses structured rule objects
for extensibility.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Protocol

from agentfluent.agents.models import is_builtin_agent
from agentfluent.config.models import AgentConfig, Severity
from agentfluent.diagnostics.builtin_actions import (
    BuiltinConcern,
    builtin_recommendation,
)
from agentfluent.diagnostics.model_routing import SAVINGS_USD_KEY
from agentfluent.diagnostics.models import (
    DiagnosticRecommendation,
    DiagnosticSignal,
    SignalType,
)


def _relpath(path: Path) -> str:
    """Render ``path`` with ``$HOME`` replaced by ``~`` for message text.

    Why: recommendation message strings are rendered into committed
    README screenshots, so any absolute path leaks the contributor's
    home directory into a public image (#340). The ``config_file`` field
    on ``DiagnosticRecommendation`` keeps the absolute path for
    programmatic consumers.
    """
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


class CorrelationRule(Protocol):
    """Protocol for correlation rules."""

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool: ...

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation: ...


class _BuiltinBranchingRule(Protocol):
    """Extension protocol for rules that participate in built-in-agent
    branching. Rules that target agent-level config (prompt, tools,
    model) declare ``_builtin_target`` + ``_builtin_concern`` as class
    attributes; rules with no agent-level counterpart (e.g.,
    ``McpAuditRule``) do not."""

    _builtin_target: str
    _builtin_concern: BuiltinConcern


def _check_builtin(
    rule: _BuiltinBranchingRule, signal: DiagnosticSignal, reason: str,
) -> DiagnosticRecommendation | None:
    """Return a built-in recommendation when the signal's agent is a
    built-in, else ``None`` so the caller falls through to the custom
    path."""
    if signal.agent_type is None or not is_builtin_agent(signal.agent_type):
        return None
    return builtin_recommendation(
        signal,
        target=rule._builtin_target,
        concern=rule._builtin_concern,
        reason=reason,
    )


class AccessErrorRule:
    """Error pattern "blocked"/"permission denied" -> check tool access."""

    _KEYWORDS = {"blocked", "permission denied", "don't have access"}
    _builtin_target = "tools"
    _builtin_concern: BuiltinConcern = "tools"

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

        if rec := _check_builtin(self, signal, reason):
            return rec

        if config and not config.tools and not config.disallowed_tools:
            action = (
                f"Add a 'tools' list to {_relpath(config.file_path)} "
                "to explicitly grant required tool access."
            )
        elif config and config.tools:
            action = (
                f"Review the 'tools' list in {_relpath(config.file_path)} "
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
            invocation_id=signal.invocation_id,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class ErrorHandlingRule:
    """Error pattern "failed"/"error"/"retry" -> check prompt for error guidance."""

    _KEYWORDS = {"failed", "error", "retry", "unable to", "not found", "timed out"}
    _builtin_target = "prompt"
    _builtin_concern: BuiltinConcern = "recovery"

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

        if rec := _check_builtin(self, signal, reason):
            return rec

        if config and config.prompt_body:
            body_lower = config.prompt_body.lower()
            has_error_guidance = any(
                kw in body_lower for kw in ("error", "fail", "handle", "retry")
            )
            if has_error_guidance:
                action = (
                    f"The prompt in {_relpath(config.file_path)} mentions error handling, "
                    "but the agent still encounters errors. Consider more specific "
                    "recovery instructions."
                )
            else:
                action = (
                    f"Add error handling guidance to the prompt body "
                    f"in {_relpath(config.file_path)}."
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
            invocation_id=signal.invocation_id,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class TokenOutlierRule:
    """Token outlier -> recommend more focused instructions or tool restriction."""

    _builtin_target = "prompt"
    _builtin_concern: BuiltinConcern = "scope"

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.TOKEN_OUTLIER

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        reason = "High token usage suggests the agent is exploring broadly."

        if rec := _check_builtin(self, signal, reason):
            return rec

        if config and len(config.tools) > 8:
            action = (
                f"Consider restricting the tools list in {_relpath(config.file_path)} "
                "to only those needed for this agent's task."
            )
            target = "tools"
        elif config:
            action = (
                f"Add more specific instructions to the prompt in "
                f"{_relpath(config.file_path)} to reduce exploration."
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
            invocation_id=signal.invocation_id,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class DurationOutlierRule:
    """Duration outlier -> check model selection or task scoping."""

    _builtin_target = "prompt"
    _builtin_concern: BuiltinConcern = "scope"

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.DURATION_OUTLIER

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        reason = "Slow invocations may indicate an overqualified model or unclear task scope."

        if rec := _check_builtin(self, signal, reason):
            return rec

        if config and config.model and "opus" in config.model.lower():
            action = (
                f"If this agent's task is routine, consider switching "
                f"from {config.model} to a faster model in {_relpath(config.file_path)}."
            )
            target = "model"
        elif config:
            action = (
                f"Add clearer task boundaries to the prompt in "
                f"{_relpath(config.file_path)} to help the agent work more efficiently."
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
            invocation_id=signal.invocation_id,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class PermissionFailureRule:
    """PERMISSION_FAILURE -> recommend adding the denied tool to `tools`."""

    _builtin_target = "tools"
    _builtin_concern: BuiltinConcern = "tools"

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.PERMISSION_FAILURE

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        tool_name = str(signal.detail.get("tool_name", ""))
        observation = signal.message
        reason = "The subagent was denied access to a tool it attempted to call."

        if rec := _check_builtin(self, signal, reason):
            return rec

        if config and tool_name and tool_name not in config.tools:
            action = (
                f"Add '{tool_name}' to the tools list in {_relpath(config.file_path)} "
                "(or remove it from disallowed_tools)."
            )
        elif config and tool_name in config.tools:
            action = (
                f"'{tool_name}' is listed in {_relpath(config.file_path)} but was "
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
            invocation_id=signal.invocation_id,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class RetryLoopRule:
    """RETRY_LOOP -> recommend error-recovery guidance in the prompt."""

    _builtin_target = "prompt"
    _builtin_concern: BuiltinConcern = "recovery"

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

        if rec := _check_builtin(self, signal, reason):
            return rec

        if config and config.prompt_body:
            body_lower = config.prompt_body.lower()
            has_error_guidance = any(
                kw in body_lower for kw in ("error", "fail", "handle", "retry")
            )
            if has_error_guidance:
                action = (
                    f"The prompt in {_relpath(config.file_path)} mentions error handling, "
                    "but the agent still retried without progress. Consider "
                    "more specific stop conditions or alternative-tool fallbacks."
                )
            else:
                action = (
                    f"Add explicit retry / fallback guidance to the prompt "
                    f"in {_relpath(config.file_path)}."
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
            invocation_id=signal.invocation_id,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class StuckPatternRule:
    """STUCK_PATTERN -> recommend adding exit conditions to the prompt."""

    _builtin_target = "prompt"
    _builtin_concern: BuiltinConcern = "recovery"

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

        if rec := _check_builtin(self, signal, reason):
            return rec

        if config:
            action = (
                f"Add an explicit exit condition or progress check to the "
                f"prompt in {_relpath(config.file_path)} (e.g., 'after 2 failed attempts, "
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
            invocation_id=signal.invocation_id,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class ErrorSequenceRule:
    """TOOL_ERROR_SEQUENCE -> recommend fallback instructions or tool review."""

    _builtin_target = "prompt"
    _builtin_concern: BuiltinConcern = "scope"

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

        if rec := _check_builtin(self, signal, reason):
            return rec

        if config and len(config.tools) > 8:
            action = (
                f"Review the tools list in {_relpath(config.file_path)} -- the agent "
                "may be reaching for tools it does not need, or a specific "
                "tool may be misconfigured."
            )
            target = "tools"
        elif config:
            action = (
                f"Add fallback instructions to the prompt in "
                f"{_relpath(config.file_path)} so the agent knows what to do when a "
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
            invocation_id=signal.invocation_id,
            config_file=str(config.file_path) if config else "",
            signal_types=[signal.signal_type],
        )


class ModelRoutingRule:
    """MODEL_MISMATCH -> recommend switching to the right-tier model.

    Overspec: switch down + cite the cost-savings estimate when pricing
    is available. Underspec: switch up, no savings (this would cost
    more, but the tradeoff is quality).
    """

    _builtin_target = "model"
    _builtin_concern: BuiltinConcern = "model"

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
        savings = detail.get(SAVINGS_USD_KEY)

        observation = signal.message
        reason = (
            f"Observed complexity tier is '{complexity}' but the agent is "
            f"configured with {current_model}."
        )

        if rec := _check_builtin(self, signal, reason):
            return rec

        action_parts = [f"Switch to {recommended_model}"]
        if mismatch_type == "overspec" and isinstance(savings, int | float):
            action_parts.append(
                f"(estimated savings: ${savings:.2f} across "
                f"{invocation_count} invocations)",
            )
        if config:
            action_parts.append(f"— edit the `model:` field in {_relpath(config.file_path)}.")
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
            invocation_id=signal.invocation_id,
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
            invocation_id=signal.invocation_id,
            config_file=str(signal.detail.get("source_file", "")),
            signal_types=[signal.signal_type],
        )


class UnusedAgentRule:
    """``UNUSED_AGENT`` -> recommend description rewrite or accept-as-unused.

    Description is the trigger logic for delegation: when a custom
    agent is defined but never invoked, the most common cause is a
    description mismatch with how the parent thread frames the task.
    """

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.UNUSED_AGENT

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        agent_name = str(signal.detail.get("agent_name", ""))
        description = str(signal.detail.get("description", ""))
        source_file = str(signal.detail.get("source_file", ""))

        reason = (
            "An agent defined but never delegated to is either misaligned "
            "with how the parent frames tasks, or the triggering context "
            "isn't present in this analysis window."
        )
        description_clause = (
            f' Current description: "{description}".' if description else ""
        )
        action = (
            f"Compare the description against parent-thread phrasing.{description_clause} "
            f"Consider broadening the description, rewriting it, or accepting "
            f"that '{agent_name}' is unused for this workload. "
            f"File: {_relpath(Path(source_file)) if source_file else 'unknown'}."
        )

        return DiagnosticRecommendation(
            target="description",
            severity=signal.severity,
            message=f"{observation} {reason} {action}",
            observation=observation,
            reason=reason,
            action=action,
            agent_type=signal.agent_type,
            invocation_id=signal.invocation_id,
            config_file=source_file,
            signal_types=[signal.signal_type],
        )


class _QualityRule(ABC):
    """Shared base for cross-cutting and per-agent quality-axis rules.

    Three concrete rules now follow this shape (``UserCorrectionRule``,
    ``FileReworkRule``, ``ReviewerCaughtRule``); the base eliminates
    ~30 lines × 3 of identical ``DiagnosticRecommendation`` boilerplate
    and normalizes the message format (``f"[quality] {obs}. {reason}
    {action}"``) across all three.

    The ``[quality]`` prefix on ``message`` is a transitional measure
    until #273 renders axis labels from ``primary_axis`` via the
    formatter; #273's AC includes removing the hardcoded prefix from
    every concrete subclass once the formatter handles axis attribution
    uniformly.

    Subclasses must set ``SIGNAL_TYPE`` and override
    ``_observation_reason_action``. ``TARGET`` defaults to ``"subagent"``
    (the right answer for all three current rules); future quality
    rules that need a different config surface (e.g., ``"prompt"``) can
    override the class variable.
    """

    SIGNAL_TYPE: ClassVar[SignalType]
    TARGET: ClassVar[str] = "subagent"

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == self.SIGNAL_TYPE

    @abstractmethod
    def _observation_reason_action(
        self, signal: DiagnosticSignal,
    ) -> tuple[str, str, str]:
        """Return ``(observation, reason, action)`` for the recommendation."""

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation, reason, action = self._observation_reason_action(signal)
        return DiagnosticRecommendation(
            target=self.TARGET,
            severity=signal.severity,
            message=f"[quality] {observation}. {reason} {action}",
            observation=observation,
            reason=reason,
            action=action,
            agent_type=signal.agent_type,
            invocation_id=signal.invocation_id,
            config_file="",
            signal_types=[signal.signal_type],
        )


class UserCorrectionRule(_QualityRule):
    """USER_CORRECTION -> recommend a review-style subagent.

    Cross-cutting (``agent_type=None`` on the source signal). The
    recommendation copy is constant — it does not branch on detail
    keys.
    """

    SIGNAL_TYPE = SignalType.USER_CORRECTION

    def _observation_reason_action(
        self, signal: DiagnosticSignal,
    ) -> tuple[str, str, str]:
        return (
            signal.message,
            "Mid-flight corrections are evidence the parent would benefit "
            "from independent review before acting.",
            "Consider delegating to a review-style subagent (architect, "
            "code-reviewer) for design checks before implementation.",
        )


class FileReworkRule(_QualityRule):
    """FILE_REWORK -> recommend a review-style subagent.

    Cross-cutting (``agent_type=None``). Recommendation copy branches
    on ``post_completion_edits``: edits after the work was declared
    complete (stronger signal) get distinct remediation from ordinary
    iterative-development rework.
    """

    SIGNAL_TYPE = SignalType.FILE_REWORK

    def _observation_reason_action(
        self, signal: DiagnosticSignal,
    ) -> tuple[str, str, str]:
        post_completion = signal.detail.get("post_completion_edits", 0)
        if isinstance(post_completion, int) and post_completion > 0:
            reason = (
                "Repeated edits after the work was declared complete "
                "indicate a pre-implementation review would have caught "
                "issues earlier."
            )
            action = (
                "Consider an architect subagent for design review before "
                "starting implementation in this area, or a tester subagent "
                "to verify completion claims."
            )
        else:
            reason = (
                "High edit density on a single file suggests the parent "
                "would benefit from upfront design or incremental testing."
            )
            action = (
                "Consider an architect subagent for design review, or split "
                "the change into smaller verifiable steps."
            )
        return signal.message, reason, action


class ReviewerCaughtRule(_QualityRule):
    """REVIEWER_CAUGHT -> route more sessions through this review agent.

    Per-agent (``agent_type=invocation.agent_type`` on the source signal),
    so aggregation groups findings under each named review agent rather
    than lumping them into the global bucket. Recommendation copy
    branches on ``parent_acted``: when the parent followed up with
    edits to the reviewed files, the review demonstrably had impact;
    when not, the review may not be actionable or is being ignored.
    """

    SIGNAL_TYPE = SignalType.REVIEWER_CAUGHT

    def _observation_reason_action(
        self, signal: DiagnosticSignal,
    ) -> tuple[str, str, str]:
        agent_type = signal.agent_type or "review-style subagent"
        keywords = signal.detail.get("finding_keywords", [])
        finding_count = len(keywords) if isinstance(keywords, list) else 0
        parent_acted = signal.detail.get("parent_acted", False)
        observation = (
            f"`{agent_type}` produced {finding_count} substantive "
            "review finding(s) in this session"
        )
        if parent_acted:
            reason = (
                "and the parent acted on them — direct evidence the "
                "review caught real issues."
            )
            action = (
                f"Consider routing more sessions through `{agent_type}` "
                "for consistent design / quality review."
            )
        else:
            reason = (
                "but the parent's subsequent edits did not appear to "
                "address them."
            )
            action = (
                f"Investigate whether `{agent_type}`'s findings are "
                "actionable, or whether the parent prompt should require "
                "follow-through on review feedback."
            )
        return observation, reason, action


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
    UnusedAgentRule(),
    UserCorrectionRule(),
    FileReworkRule(),
    ReviewerCaughtRule(),
]


def correlate(
    signals: list[DiagnosticSignal],
    configs: dict[str, AgentConfig] | None = None,
) -> list[tuple[DiagnosticSignal, DiagnosticRecommendation]]:
    """Map signals to config surfaces and produce recommendations.

    Args:
        signals: Detected behavior signals from signal extraction.
        configs: Optional dict of agent_type (lowercase) -> AgentConfig.
            When available, recommendations reference specific config files.

    Returns:
        Paired ``(signal, recommendation)`` tuples — one per matched
        signal. The pairing is explicit (rather than positional across
        two lists) so downstream consumers like
        ``aggregate_recommendations`` can attribute evidence back to the
        source signal without relying on list-ordering invariants.
    """
    pairs: list[tuple[DiagnosticSignal, DiagnosticRecommendation]] = []

    for signal in signals:
        config = (
            configs.get(signal.agent_type.lower())
            if configs and signal.agent_type
            else None
        )

        for rule in RULES:
            if rule.matches(signal, config):
                pairs.append((signal, rule.recommend(signal, config)))
                break

    return pairs
