"""Signal-to-config correlation engine.

Maps detected behavior signals to specific agent configuration surfaces
and generates actionable recommendations. Uses structured rule objects
for extensibility.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Protocol

from agentfluent.agents.models import is_builtin_agent
from agentfluent.config.models import AgentConfig, HookFieldCoverage, Severity
from agentfluent.diagnostics._complexity import (
    faster_tier,
    select_target_model,
)
from agentfluent.diagnostics.builtin_actions import (
    BuiltinConcern,
    builtin_recommendation,
)
from agentfluent.diagnostics.model_routing import (
    SAVINGS_USD_KEY,
    estimate_model_savings,
)
from agentfluent.diagnostics.models import (
    DiagnosticRecommendation,
    DiagnosticSignal,
    SignalType,
)
from agentfluent.diagnostics.quality_signals import (
    PARENT_ACTED_HEALTHY_BAND_HIGH,
    PARENT_ACTED_HEALTHY_BAND_LOW,
)
from agentfluent.diagnostics.tool_orchestration import ESTIMATED_TOKEN_SAVINGS_KEY
from agentfluent.diagnostics.trace_signals import PARAMETER_RETRY_EXAMPLE_KEY
from agentfluent.glossary.loader import builtin_tool_names_cached


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


_DURATION_HOOK_EVENT = "PostToolUse"
_DURATION_HOOK_FIELD = "duration_ms"


def _duration_ms_covered(
    hook_coverage: dict[str, list[HookFieldCoverage]] | None,
    agent_type: str | None,
) -> bool | None:
    """Whether ``agent_type`` has a ``(PostToolUse, duration_ms)`` hook.

    Returns the coverage ``bool`` when an explicit result for that pair exists,
    else ``None`` meaning *unknown* — no coverage map, no agent name, the agent
    was not inspected, or the map carries no matching entry. ``None`` is treated
    as "don't emit the hook rec" (default-deny), matching the
    ``hook_coverage=None`` backward-compat contract.
    """
    if not hook_coverage or not agent_type:
        return None
    for cov in hook_coverage.get(agent_type.lower(), []):
        if cov.hook_event == _DURATION_HOOK_EVENT and cov.field_name == _DURATION_HOOK_FIELD:
            return cov.covered
    return None


class DurationOutlierRule:
    """Duration outlier -> check hook coverage, model selection, or scoping."""

    _builtin_target = "prompt"
    _builtin_concern: BuiltinConcern = "scope"

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.DURATION_OUTLIER

    def recommend(
        self,
        signal: DiagnosticSignal,
        config: AgentConfig | None,
        *,
        hook_coverage: dict[str, list[HookFieldCoverage]] | None = None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        reason = "Slow invocations may indicate an overqualified model or unclear task scope."

        if rec := _check_builtin(self, signal, reason):
            return rec

        # Hook-coverage branch (#425): the most actionable fix is "you have no
        # timing hook, so slow calls go undetected at runtime." Emit it only on
        # explicit not-covered evidence AND when we can cite the agent's config
        # file — unknown coverage falls through to the model/prompt branches.
        if config is not None and (
            _duration_ms_covered(hook_coverage, signal.agent_type) is False
        ):
            return self._hook_recommendation(signal, config)

        # Resolve the model the agent runs on: the editable declaration
        # (config.model) wins, else the model the slow invocation actually
        # used (carried on the signal from its trace). `faster_tier` +
        # `select_target_model` name a concrete one-tier-down target, or
        # return None when the model is unknown or already at the fastest
        # tier — in which case we fall back to a task-scoping suggestion
        # rather than emit an unnamed "a faster model" rec (#170).
        current_model = config.model if (config and config.model) else None
        if current_model is None:
            detail_model = signal.detail.get("current_model")
            current_model = detail_model if isinstance(detail_model, str) else None
        tier = faster_tier(current_model)
        target_model = select_target_model(current_model, tier) if tier else None

        if target_model is not None:
            action = self._model_action(signal, config, current_model, target_model)
            target = "model"
        elif config:
            action = (
                f"Add clearer task boundaries to the prompt in "
                f"{_relpath(config.file_path)} to help the agent work more efficiently."
            )
            target = "prompt"
        else:
            action = (
                "Add clearer task boundaries to the agent's prompt to help "
                "it work more efficiently."
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

    @staticmethod
    def _hook_recommendation(
        signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        """Recommend adding a ``duration_ms`` PostToolUse hook (``target=hooks``)."""
        observation = signal.message
        reason = (
            "Slow tool calls go undetected at runtime when no PostToolUse hook "
            "gates on `duration_ms`."
        )
        action = (
            "Add a PostToolUse hook that logs or gates on `duration_ms` to surface "
            "slow tool calls before they compound. Note: project-level hooks in "
            "`.claude/settings.json` are not currently inspected."
        )
        return DiagnosticRecommendation(
            target="hooks",
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

    @staticmethod
    def _model_action(
        signal: DiagnosticSignal,
        config: AgentConfig | None,
        current_model: str | None,
        target_model: str,
    ) -> str:
        """Compose the concrete current → target switch suggestion.

        Latency leads (it's the duration signal); cost is a parenthetical
        scoped to *this invocation* — a single slow outlier's tokens are
        atypically high, so framing them as per-agent savings would
        overstate the typical case (architect review, #170).
        """
        tokens = signal.detail.get("total_tokens")
        savings, _ = estimate_model_savings(
            current_model, target_model,
            tokens if isinstance(tokens, int | float) else None,
            count=1,
        )
        where = f" in {_relpath(config.file_path)}" if config else ""
        action = (
            f"If this agent's task is routine, consider switching from "
            f"{current_model} to {target_model}{where}."
        )
        if savings is not None and savings > 0:
            action += f" (estimated savings: ${savings:.2f} for this invocation)"
        return action


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
        # Main-vs-subagent origin (#112). Copied onto the recommendation as
        # the explicit discriminator JSON consumers read (AC#6); also selects
        # the config surface named in the action.
        routing_scope = str(detail.get("routing_scope", "subagent"))
        is_main_session = routing_scope == "main_session"

        observation = signal.message
        if is_main_session:
            reason = (
                f"Observed main-session complexity tier is '{complexity}' but "
                f"the SDK main session runs on {current_model}."
            )
        else:
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
        if is_main_session:
            # No ``.claude/agents/*.md`` for a main session — the surface is
            # the SDK options object the developer sets in code.
            action_parts.append(
                "— set the `model` field in your `ClaudeAgentOptions`.",
            )
            action = " ".join(action_parts)
        elif config:
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
            routing_scope=routing_scope,
        )


class ToolOrchestrationRule:
    """TOOL_ORCHESTRATION_CHAIN -> recommend Programmatic Tool Calling.

    The agent orchestrates long tool-call chains whose large intermediate
    results pass through the context window. Recommends migrating the
    chained tools to ``allowed_callers: ["code_execution_20250825"]`` so
    intermediates stay in the code-execution sandbox. Severity stays
    ``INFO`` (carried from the signal) to reflect the Tier A metadata-only
    precision limitation.
    """

    _ARTICLE_URL = "https://www.anthropic.com/engineering/advanced-tool-use"
    # "tools" is the closest fit in the coarse concern taxonomy
    # (scope/recovery/tools/model): the fix is a tool-coordination change
    # (migrate to ``allowed_callers``), not a grant/deny of tools. The
    # built-in action ("route to a custom subagent with explicit tool
    # grants") still lands the user in the right place. Revisit if a 3rd
    # orchestration-pattern signal makes a dedicated concern worthwhile.
    _builtin_target = "tools"
    _builtin_concern: BuiltinConcern = "tools"

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.TOOL_ORCHESTRATION_CHAIN

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        savings = signal.detail.get(ESTIMATED_TOKEN_SAVINGS_KEY)

        observation = signal.message
        reason = (
            "Sequential tool-call chains with large intermediate payloads "
            "inflate context-window usage. Programmatic Tool Calling lets "
            "the agent orchestrate tool calls in a sandboxed code-execution "
            "environment where intermediate results don't enter the context "
            "window. Anthropic reports a 37% token reduction on complex "
            "research tasks."
        )

        if rec := _check_builtin(self, signal, reason):
            return rec

        action_parts = [
            "Consider migrating the tools this agent calls to "
            '`allowed_callers: ["code_execution_20250825"]` so intermediate '
            "results are processed in the code-execution sandbox",
        ]
        if isinstance(savings, int) and savings > 0:
            action_parts.append(
                f"(estimated savings: ~{savings:,} tokens at the 37% benchmark)",
            )
        if config:
            action_parts.append(
                f"— edit the tool definitions referenced by "
                f"{_relpath(config.file_path)}.",
            )
        else:
            action_parts.append(f"— see {self._ARTICLE_URL}.")
        action = " ".join(action_parts)

        return DiagnosticRecommendation(
            target="tools",
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


class ParameterRetryRule:
    """PARAMETER_RETRY -> recommend `input_examples` on the tool definition.

    The agent retried the same tool with different parameter shapes after
    an initial error -- it's guessing at the input format. The fix is an
    ``input_examples`` array on the *tool definition* (MCP server, SDK
    tool, or custom tool), not the agent's own prompt/tools config. This
    rule therefore does NOT branch on built-in agents the way
    prompt/tools/model rules do: ``input_examples`` helps whoever calls
    the tool regardless of whether the caller is a built-in agent.

    Two *different* "built-in" axes apply here, and must not be conflated:
    built-in **agent** (``is_builtin_agent``) — NOT branched on, above — and
    built-in **tool** (the glossary ``builtin_tool`` category, via
    ``builtin_tool_names``) — which this rule DOES branch on. A retry on a
    built-in tool like ``Read`` is annotated *informational*: you cannot edit
    a built-in tool's definition to add ``input_examples``, so the fix is not
    user-actionable (#510). A built-in *tool* can still be called from inside a
    custom *agent*, so the two axes are orthogonal.

    When the trace captured a subsequent successful call, its ``input`` dict
    is surfaced as a paste-ready example (D002: informational, never
    auto-applied).
    """

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.PARAMETER_RETRY

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        tool_name = str(signal.detail.get("tool_name", "the tool"))
        observation = signal.message
        # Exact-match (no case-folding) is correct here: trace tool names are
        # machine-emitted canonical identifiers (``Read``, ``Edit``) that match
        # terms.yaml verbatim. This intentionally differs from
        # ``is_builtin_agent``, which lower-cases because agent types are
        # user-authored and free-cased.
        is_builtin_tool = tool_name in builtin_tool_names_cached()

        if is_builtin_tool:
            # The `input_examples` fix lives on the tool definition, which a
            # user cannot edit for a Claude Code built-in -- so annotate the
            # fire as informational rather than emit a non-actionable action
            # (#510). Severity/target are left unchanged: this is annotation,
            # not scoring deprioritization (the latter is a tracked follow-up).
            reason = (
                f"'{tool_name}' is a built-in Claude Code tool, so its "
                "definition cannot be edited to add an `input_examples` array "
                "-- this finding is informational, not directly user-tunable. "
                "The retry pattern still indicates input-format guessing; the "
                "fix applies only if it recurs on a custom SDK/MCP tool you own."
            )
            action_parts = [
                f"No action on the built-in '{tool_name}' itself. For any "
                "custom SDK/MCP tool showing the same pattern, add an "
                "`input_examples` array to its definition showing the expected "
                "parameter shape.",
            ]
        else:
            reason = (
                "Parameter-retry patterns indicate the agent is guessing at "
                "input formats. Adding concrete examples to the tool definition "
                "(an `input_examples` array) improves accuracy from 72% to 90% "
                "on complex parameter handling (Anthropic benchmark)."
            )
            action_parts = [
                f"Add an `input_examples` array to the '{tool_name}' tool "
                "definition showing the expected parameter shape.",
            ]

        example = signal.detail.get(PARAMETER_RETRY_EXAMPLE_KEY)
        if isinstance(example, dict):
            rendered = json.dumps(example, indent=2, ensure_ascii=False)
            label = (
                "Observed successful call shape (informational)"
                if is_builtin_tool
                else f"Suggested `input_examples` entry for tool '{tool_name}' "
                "based on the observed successful call"
            )
            action_parts.append(f"{label}:\n{rendered}")
        action = "\n".join(action_parts)

        return DiagnosticRecommendation(
            target="tools",
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


class ToolInventoryOversizedRule:
    """``TOOL_INVENTORY_OVERSIZED`` -> recommend Tool Search + trimming.

    Targets the ``tools`` surface: an oversized declared inventory with
    low observed utilization both wastes context-window tokens on unused
    schemas and degrades tool-selection accuracy. The fix is to defer
    schema loading (Tool Search) and prune tools that are never called.
    """

    def matches(self, signal: DiagnosticSignal, config: AgentConfig | None) -> bool:
        return signal.signal_type == SignalType.TOOL_INVENTORY_OVERSIZED

    def recommend(
        self, signal: DiagnosticSignal, config: AgentConfig | None,
    ) -> DiagnosticRecommendation:
        observation = signal.message
        source_file = str(signal.detail.get("source_file", ""))

        reason = (
            "Large tool inventories consume context-window tokens for schema "
            "definitions and degrade tool-selection accuracy. RAG-MCP "
            "(arXiv 2505.03275) shows retrieval-based tool selection outperforms "
            "full-schema dumps above ~30 tools (43% vs 14% accuracy); Anthropic's "
            "Advanced Tool Use article reports the Tool Search Tool lifts Opus 4.5 "
            "accuracy from 79.5% to 88.1% while preserving ~85% of the context "
            "window."
        )
        file_clause = (
            f" File: {_relpath(Path(source_file))}." if source_file else ""
        )
        action = (
            "Enable the Tool Search Tool so schemas load on demand. For API/SDK "
            "agents, add a `tool_search_tool_regex_20251119` tool to the `tools[]` "
            "array and set `defer_loading: true` on the individual tools that "
            'should load lazily; for MCP servers, use an `mcp_toolset` block with '
            '`default_config: {"defer_loading": true}`. (Claude Code agents can '
            "set `defer_loading: true` in agent frontmatter.) Also review the "
            "declared tool list for tools that are never invoked and consider "
            f"removing them.{file_clause}"
        )

        return DiagnosticRecommendation(
            target="tools",
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
        # #396: band derived from the per-agent parent_acted rate across
        # the session, stashed onto every signal in the group by the
        # emitter. Pre-#396 signals (no band key) fall back to the old
        # per-signal parent_acted boolean.
        band = signal.detail.get("parent_acted_band")
        rate = signal.detail.get("parent_acted_rate")
        acted_count = signal.detail.get("parent_acted_count")
        total = signal.detail.get("total_findings")
        observation = (
            f"`{agent_type}` produced {finding_count} substantive "
            "review finding(s) in this session"
        )
        if band == "within" and isinstance(rate, float):
            reason = (
                f"and the parent followed up on {acted_count} of "
                f"{total} ({rate:.0%}) — a healthy review-and-reject "
                "collaboration pattern."
            )
            action = (
                "No action needed: this rate sits in the healthy band "
                f"({PARENT_ACTED_HEALTHY_BAND_LOW:.0%}-"
                f"{PARENT_ACTED_HEALTHY_BAND_HIGH:.0%}). Investigate "
                "only if it falls below that band."
            )
        elif band == "above" and isinstance(rate, float):
            reason = (
                f"and the parent acted on {acted_count} of {total} "
                f"({rate:.0%}) — high follow-through, reviewer is "
                "well-tuned."
            )
            action = (
                f"Consider routing more sessions through `{agent_type}` "
                "for consistent design / quality review."
            )
        elif band == "below" and isinstance(rate, float):
            reason = (
                f"but the parent followed up on only {acted_count} of "
                f"{total} ({rate:.0%}) — reviewer findings may be "
                "going unread."
            )
            action = (
                f"Investigate whether `{agent_type}`'s findings are "
                "actionable, or whether the parent prompt should "
                "require follow-through on review feedback."
            )
        elif signal.detail.get("parent_acted", False):
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


class FeatFixProximityRule(_QualityRule):
    """FEAT_FIX_PROXIMITY -> recommend a review-style subagent (or
    revisit reviewer effectiveness when one was already used).

    Cross-cutting (``agent_type=None``). Recommendation copy branches
    on ``session_used_reviewer``: a fix-storm right after a feature
    shipped *without* a reviewer is a strong "add review" signal;
    the same pattern *with* a reviewer in the loop instead asks whether
    the reviewer's coverage matched the feature's risk surface.
    """

    SIGNAL_TYPE = SignalType.FEAT_FIX_PROXIMITY

    def _observation_reason_action(
        self, signal: DiagnosticSignal,
    ) -> tuple[str, str, str]:
        used_reviewer = signal.detail.get("session_used_reviewer")
        observation = signal.message
        if used_reviewer is True:
            reason = (
                "A review-style subagent ran on the originating session, "
                "yet a fix landed quickly. The reviewer's coverage may not "
                "have matched the feature's risk surface."
            )
            action = (
                "Audit the reviewer's prompt and tool access against the "
                "kinds of issues the fix commits addressed."
            )
        else:
            reason = (
                "Fixes landing within days of a feature on the same files "
                "indicate a quality miss an independent reviewer could "
                "have caught before merge."
            )
            action = (
                "Consider routing similar feature work through an "
                "architect or code-reviewer subagent before commit."
            )
        return observation, reason, action


class CIFailureFirstPushRule(_QualityRule):
    """CI_FAILURE_FIRST_PUSH -> recommend pre-commit validation.

    Tier 3 signal (#400). Cross-cutting (``agent_type=None``) because
    the miss is about a PR's first push, not a specific subagent.
    The recommendation copy nudges the user toward putting validation
    in the agent's prompt or hooks rather than relying on CI to
    catch issues post-push.
    """

    SIGNAL_TYPE = SignalType.CI_FAILURE_FIRST_PUSH

    def _observation_reason_action(
        self, signal: DiagnosticSignal,
    ) -> tuple[str, str, str]:
        d = signal.detail
        # All four detail keys are populated by the extractor, but the
        # correlator runs against any DiagnosticSignal carrying the
        # CI_FAILURE_FIRST_PUSH type — including manually-constructed
        # ones in tests and future emitters. Guard each key so a
        # malformed signal still renders a coherent message instead of
        # "PR #None ('(no title)') failed CI on first push (ci: failure)."
        pr_number = d.get("pr_number")
        pr_number_disp = f"#{pr_number}" if pr_number is not None else "(unknown)"
        pr_title = d.get("pr_title") or "(no title)"
        context = d.get("primary_context") or "ci"
        state = d.get("primary_state") or "failure"
        observation = (
            f"PR {pr_number_disp} ({pr_title!r}) failed CI on first push "
            f"({context}: {state})."
        )
        reason = (
            "CI failures on first push indicate the agent did not "
            "validate its changes against the project's test/lint "
            "suite before committing."
        )
        action = (
            "Consider adding pre-commit validation to the agent's "
            "prompt or hooks. Review whether the agent has access "
            "to the project's test runner."
        )
        return observation, reason, action


class PRReviewCommentDensityRule(_QualityRule):
    """PR_REVIEW_COMMENT_DENSITY -> recommend an architect / code-review
    subagent before opening similar PRs.

    Tier 3 signal (#401). Cross-cutting (``agent_type=None``) because
    the signal is about a PR's reviewer-effort cost, not a specific
    subagent's behavior. Recommendation copy nudges the user toward
    invoking an architect or code-review subagent before opening PRs
    when this kind of work tends to attract heavy review.
    """

    SIGNAL_TYPE = SignalType.PR_REVIEW_COMMENT_DENSITY

    def _observation_reason_action(
        self, signal: DiagnosticSignal,
    ) -> tuple[str, str, str]:
        d = signal.detail
        # All detail keys are populated by the extractor, but the
        # correlator runs against any DiagnosticSignal carrying this
        # type — including manually-constructed ones in tests and
        # future emitters. Guard each access so a malformed signal
        # still renders a coherent message instead of "PR #None ..."
        # (parity with CIFailureFirstPushRule's defensive guard).
        pr_number = d.get("pr_number")
        pr_number_disp = (
            f"#{pr_number}" if pr_number is not None else "(unknown)"
        )
        pr_title = d.get("pr_title") or "(no title)"
        comments = d.get("external_comment_count")
        comments_disp = (
            f"{comments}" if isinstance(comments, int) else "(unknown)"
        )
        lines = d.get("lines_changed")
        lines_disp = (
            f"{lines}" if isinstance(lines, int) else "(unknown)"
        )
        density = d.get("density")
        density_disp = (
            f"{density:.2f}" if isinstance(density, (int, float)) else "?"
        )
        observation = (
            f"PR {pr_number_disp} ({pr_title!r}) received {comments_disp} "
            f"review comment(s) across {lines_disp} line(s) changed "
            f"(density: {density_disp})."
        )
        reason = (
            "High review comment density suggests the code needed "
            "substantial human review. An architect or code-review "
            "agent could catch common issues before the PR is opened."
        )
        action = (
            "Consider invoking an architect or code-review agent "
            "before opening PRs for this type of work."
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
    ParameterRetryRule(),
    ModelRoutingRule(),
    ToolOrchestrationRule(),
    McpAuditRule(),
    UnusedAgentRule(),
    ToolInventoryOversizedRule(),
    UserCorrectionRule(),
    FileReworkRule(),
    ReviewerCaughtRule(),
    FeatFixProximityRule(),
    CIFailureFirstPushRule(),
    PRReviewCommentDensityRule(),
]


def correlate(
    signals: list[DiagnosticSignal],
    configs: dict[str, AgentConfig] | None = None,
    hook_coverage: dict[str, list[HookFieldCoverage]] | None = None,
) -> list[tuple[DiagnosticSignal, DiagnosticRecommendation]]:
    """Map signals to config surfaces and produce recommendations.

    Args:
        signals: Detected behavior signals from signal extraction.
        configs: Optional dict of agent_type (lowercase) -> AgentConfig.
            When available, recommendations reference specific config files.
        hook_coverage: Optional dict of agent_type (lowercase) ->
            HookFieldCoverage results, parallel to ``configs`` (#425). Only
            ``DurationOutlierRule`` consumes it; when a ``DURATION_OUTLIER``
            agent has an explicit not-covered ``(PostToolUse, duration_ms)``
            entry the rule recommends adding a timing hook. **Contract with the
            caller (#426):** to make the load-bearing "zero-hook agent" case
            fire, emit a ``covered=False`` entry for *every* inspected custom
            agent, including those declaring no hooks. Absent agents are treated
            as "not inspected" (default-deny) — no hook rec.

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
                if isinstance(rule, DurationOutlierRule):
                    rec = rule.recommend(signal, config, hook_coverage=hook_coverage)
                else:
                    rec = rule.recommend(signal, config)
                pairs.append((signal, rec))
                break

    return pairs
