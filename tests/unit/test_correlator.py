"""Tests for signal-to-config correlation engine."""

from pathlib import Path

from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.diagnostics.correlator import correlate
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType


def _signal(
    signal_type: SignalType = SignalType.ERROR_PATTERN,
    severity: Severity = Severity.WARNING,
    agent_type: str = "pm",
    keyword: str = "error",
    detail: dict[str, object] | None = None,
    message: str | None = None,
) -> DiagnosticSignal:
    return DiagnosticSignal(
        signal_type=signal_type,
        severity=severity,
        agent_type=agent_type,
        message=message or f"Agent '{agent_type}' output contains '{keyword}'.",
        detail=detail if detail is not None else {"keyword": keyword},
    )


def _config(
    name: str = "pm",
    tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
    prompt_body: str = "You are a helpful agent.",
    model: str = "claude-sonnet-4-6",
) -> AgentConfig:
    return AgentConfig(
        name=name,
        file_path=Path(f"/home/user/.claude/agents/{name}.md"),
        scope=Scope.USER,
        tools=tools or [],
        disallowed_tools=disallowed_tools or [],
        prompt_body=prompt_body,
        model=model,
    )


class TestAccessErrorCorrelation:
    def test_blocked_with_no_tools(self) -> None:
        signals = [_signal(severity=Severity.CRITICAL, keyword="blocked")]
        configs = {"pm": _config(tools=[], disallowed_tools=[])}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "tools"
        assert recs[0].severity == Severity.CRITICAL
        assert "pm.md" in recs[0].config_file

    def test_permission_denied_with_tools(self) -> None:
        signals = [_signal(severity=Severity.CRITICAL, keyword="permission denied")]
        configs = {"pm": _config(tools=["Read", "Grep"])}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert "review" in recs[0].action.lower()

    def test_blocked_without_config(self) -> None:
        signals = [_signal(severity=Severity.CRITICAL, keyword="blocked")]
        recs = correlate(signals, None)
        assert len(recs) == 1
        assert recs[0].config_file == ""


class TestErrorHandlingCorrelation:
    def test_error_with_no_handling_in_prompt(self) -> None:
        signals = [_signal(keyword="failed")]
        configs = {"pm": _config(prompt_body="You are a PM agent.")}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "prompt"
        assert "error handling" in recs[0].action.lower()

    def test_error_with_handling_in_prompt(self) -> None:
        signals = [_signal(keyword="error")]
        configs = {"pm": _config(prompt_body="Handle errors gracefully.")}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert "more specific" in recs[0].action.lower()

    def test_error_without_config(self) -> None:
        signals = [_signal(keyword="failed")]
        recs = correlate(signals, None)
        assert len(recs) == 1
        assert recs[0].target == "prompt"
        assert recs[0].config_file == ""


class TestTokenOutlierCorrelation:
    def test_with_large_tools_list(self) -> None:
        signals = [_signal(
            signal_type=SignalType.TOKEN_OUTLIER,
            keyword="",
        )]
        configs = {"pm": _config(
            tools=["Read", "Edit", "Bash", "Grep", "Glob", "Write",
                   "Agent", "WebFetch", "WebSearch"],
        )}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "tools"

    def test_with_small_tools_list(self) -> None:
        signals = [_signal(signal_type=SignalType.TOKEN_OUTLIER, keyword="")]
        configs = {"pm": _config(tools=["Read"])}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "prompt"

    def test_without_config(self) -> None:
        signals = [_signal(signal_type=SignalType.TOKEN_OUTLIER, keyword="")]
        recs = correlate(signals, None)
        assert len(recs) == 1


class TestDurationOutlierCorrelation:
    def test_with_opus_model(self) -> None:
        signals = [_signal(signal_type=SignalType.DURATION_OUTLIER, keyword="")]
        configs = {"pm": _config(model="claude-opus-4-6")}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "model"

    def test_with_sonnet_model(self) -> None:
        signals = [_signal(signal_type=SignalType.DURATION_OUTLIER, keyword="")]
        configs = {"pm": _config(model="claude-sonnet-4-6")}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "prompt"

    def test_without_config(self) -> None:
        signals = [_signal(signal_type=SignalType.DURATION_OUTLIER, keyword="")]
        recs = correlate(signals, None)
        assert len(recs) == 1
        assert recs[0].target == "model"
        assert recs[0].config_file == ""


class TestCorrelateGeneral:
    def test_empty_signals(self) -> None:
        assert correlate([], None) == []

    def test_multiple_signals(self) -> None:
        signals = [
            _signal(keyword="blocked"),
            _signal(signal_type=SignalType.TOKEN_OUTLIER, keyword=""),
        ]
        configs = {"pm": _config()}
        recs = correlate(signals, configs)
        assert len(recs) == 2

    def test_recommendation_has_observation_reason_action(self) -> None:
        signals = [_signal(keyword="error")]
        configs = {"pm": _config()}
        recs = correlate(signals, configs)
        assert recs[0].observation
        assert recs[0].reason
        assert recs[0].action


def _perm_signal(
    tool_name: str = "Write",
    agent_type: str = "pm",
) -> DiagnosticSignal:
    return _signal(
        signal_type=SignalType.PERMISSION_FAILURE,
        severity=Severity.CRITICAL,
        agent_type=agent_type,
        detail={
            "tool_name": tool_name,
            "matched_keyword": "permission denied",
            "tool_calls": [],
        },
        message=f"Subagent '{agent_type}' was denied access to tool '{tool_name}'.",
    )


class TestPermissionFailureCorrelation:
    def test_with_tool_missing_from_config(self) -> None:
        signals = [_perm_signal(tool_name="Write")]
        configs = {"pm": _config(tools=["Read", "Grep"])}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "tools"
        assert recs[0].severity == Severity.CRITICAL
        assert "'Write'" in recs[0].action
        assert "pm.md" in recs[0].config_file

    def test_with_tool_in_config(self) -> None:
        signals = [_perm_signal(tool_name="Write")]
        configs = {"pm": _config(tools=["Write", "Read"])}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert "disallowed_tools" in recs[0].action.lower()

    def test_without_config(self) -> None:
        signals = [_perm_signal(tool_name="Bash")]
        recs = correlate(signals, None)
        assert len(recs) == 1
        assert recs[0].target == "tools"
        assert recs[0].config_file == ""


def _retry_signal(agent_type: str = "pm") -> DiagnosticSignal:
    return _signal(
        signal_type=SignalType.RETRY_LOOP,
        severity=Severity.WARNING,
        agent_type=agent_type,
        detail={
            "tool_name": "Bash",
            "retry_count": 3,
            "first_error_message": "Permission denied",
            "eventual_success": False,
            "tool_calls": [],
        },
        message=f"Subagent '{agent_type}' retried tool 'Bash' 3 times.",
    )


class TestRetryLoopCorrelation:
    def test_without_error_guidance_in_prompt(self) -> None:
        signals = [_retry_signal()]
        configs = {"pm": _config(prompt_body="You are a PM agent.")}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "prompt"
        assert "fallback" in recs[0].action.lower()

    def test_with_error_guidance_in_prompt(self) -> None:
        signals = [_retry_signal()]
        configs = {"pm": _config(prompt_body="Handle errors and retry gracefully.")}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert "more specific" in recs[0].action.lower()

    def test_without_config(self) -> None:
        signals = [_retry_signal()]
        recs = correlate(signals, None)
        assert len(recs) == 1
        assert recs[0].config_file == ""


def _stuck_signal(agent_type: str = "pm", count: int = 5) -> DiagnosticSignal:
    return _signal(
        signal_type=SignalType.STUCK_PATTERN,
        severity=Severity.CRITICAL,
        agent_type=agent_type,
        detail={
            "tool_name": "Read",
            "stuck_count": count,
            "input_summary": "ls /missing",
            "tool_calls": [],
        },
        message=f"Subagent '{agent_type}' repeated tool 'Read' {count} times.",
    )


class TestStuckPatternCorrelation:
    def test_with_config(self) -> None:
        signals = [_stuck_signal()]
        configs = {"pm": _config()}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "prompt"
        assert recs[0].severity == Severity.CRITICAL
        assert "exit condition" in recs[0].action.lower()

    def test_stuck_count_in_reason(self) -> None:
        signals = [_stuck_signal(count=7)]
        recs = correlate(signals, {"pm": _config()})
        assert "7" in recs[0].reason

    def test_without_config(self) -> None:
        signals = [_stuck_signal()]
        recs = correlate(signals, None)
        assert len(recs) == 1
        assert recs[0].config_file == ""


def _err_seq_signal(
    agent_type: str = "pm",
    count: int = 2,
    severity: Severity = Severity.WARNING,
) -> DiagnosticSignal:
    return _signal(
        signal_type=SignalType.TOOL_ERROR_SEQUENCE,
        severity=severity,
        agent_type=agent_type,
        detail={
            "error_count": count,
            "start_index": 0,
            "end_index": count - 1,
            "tool_calls": [],
        },
        message=f"Subagent '{agent_type}' had {count} consecutive tool errors.",
    )


class TestErrorSequenceCorrelation:
    def test_with_small_tools_list(self) -> None:
        signals = [_err_seq_signal()]
        configs = {"pm": _config(tools=["Read"])}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "prompt"
        assert "fallback" in recs[0].action.lower()

    def test_with_large_tools_list(self) -> None:
        signals = [_err_seq_signal()]
        configs = {"pm": _config(
            tools=["Read", "Edit", "Bash", "Grep", "Glob", "Write",
                   "Agent", "WebFetch", "WebSearch"],
        )}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "tools"

    def test_severity_mirrors_signal(self) -> None:
        signals = [_err_seq_signal(count=4, severity=Severity.CRITICAL)]
        recs = correlate(signals, None)
        assert recs[0].severity == Severity.CRITICAL
