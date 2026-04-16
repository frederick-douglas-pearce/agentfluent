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
) -> DiagnosticSignal:
    return DiagnosticSignal(
        signal_type=signal_type,
        severity=severity,
        agent_type=agent_type,
        message=f"Agent '{agent_type}' output contains '{keyword}'.",
        detail={"keyword": keyword},
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
