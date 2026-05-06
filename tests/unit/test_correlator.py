"""Tests for signal-to-config correlation engine."""

from pathlib import Path

import pytest

from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.diagnostics.correlator import correlate as _correlate_pairs
from agentfluent.diagnostics.models import (
    DiagnosticRecommendation,
    DiagnosticSignal,
    SignalType,
)


def correlate(
    signals: list[DiagnosticSignal],
    configs: dict[str, AgentConfig] | None = None,
) -> list[DiagnosticRecommendation]:
    """Test wrapper: drops the signal side of ``correlate``'s paired return
    so per-rule assertions can stay focused on recommendation content.
    Pairing semantics are exercised by the aggregation tests."""
    return [rec for _, rec in _correlate_pairs(signals, configs)]


def _signal(
    signal_type: SignalType = SignalType.ERROR_PATTERN,
    severity: Severity = Severity.WARNING,
    agent_type: str = "pm",
    keyword: str = "error",
    detail: dict[str, object] | None = None,
    message: str | None = None,
    invocation_id: str | None = None,
) -> DiagnosticSignal:
    return DiagnosticSignal(
        signal_type=signal_type,
        severity=severity,
        agent_type=agent_type,
        invocation_id=invocation_id,
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


def _model_mismatch_signal(
    mismatch_type: str = "overspec",
    current_model: str = "claude-opus-4-7",
    recommended_model: str = "claude-haiku-4-5",
    savings: float | None = 12.50,
    agent_type: str = "pm",
) -> DiagnosticSignal:
    return _signal(
        signal_type=SignalType.MODEL_MISMATCH,
        severity=Severity.WARNING,
        agent_type=agent_type,
        detail={
            "mismatch_type": mismatch_type,
            "current_model": current_model,
            "recommended_model": recommended_model,
            "complexity_tier": "simple" if mismatch_type == "overspec" else "complex",
            "invocation_count": 8,
            "mean_tool_calls": 2.5,
            "mean_tokens": 500.0,
            "error_rate": 0.0,
            "estimated_savings_usd": savings,
            "current_cost_usd": 20.0 if savings is not None else None,
        },
        message=f"{mismatch_type.capitalize()}'d model: {agent_type} on {current_model}",
    )


class TestModelRoutingCorrelation:
    def test_overspec_with_config_includes_savings(self) -> None:
        signals = [_model_mismatch_signal(savings=12.5)]
        configs = {"pm": _config()}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        assert recs[0].target == "model"
        assert "12.50" in recs[0].action
        assert "pm.md" in recs[0].config_file

    def test_overspec_without_pricing_omits_savings_phrase(self) -> None:
        signals = [_model_mismatch_signal(savings=None)]
        configs = {"pm": _config()}
        recs = correlate(signals, configs)
        assert len(recs) == 1
        # "savings:" phrase only emitted when dollars are known.
        assert "savings:" not in recs[0].action.lower()

    def test_without_config(self) -> None:
        signals = [_model_mismatch_signal(savings=12.5)]
        recs = correlate(signals, None)
        assert len(recs) == 1
        assert recs[0].config_file == ""


class TestMcpAuditCorrelation:
    def _unused_signal(
        self, server_name: str = "slack", source_file: str = "/home/u/.claude.json",
    ) -> DiagnosticSignal:
        return _signal(
            signal_type=SignalType.MCP_UNUSED_SERVER,
            severity=Severity.INFO,
            agent_type="",
            message=(
                f"MCP server '{server_name}' is configured in {source_file} "
                "but has 0 tool calls across 12 analyzed sessions. "
                "Consider removing from mcpServers or marking as disabled."
            ),
            detail={
                "server_name": server_name,
                "source_file": source_file,
                "configured_tools": None,
                "sessions_analyzed": 12,
            },
        )

    def _missing_signal(
        self, server_name: str = "slack",
    ) -> DiagnosticSignal:
        return _signal(
            signal_type=SignalType.MCP_MISSING_SERVER,
            severity=Severity.WARNING,
            agent_type="",
            message=(
                f"3 failed calls to mcp__{server_name}__* across 5 sessions, "
                f"but no '{server_name}' server configured. "
                "Add to .mcp.json or ~/.claude.json."
            ),
            detail={
                "server_name": server_name,
                "total_calls": 5,
                "error_count": 3,
                "unique_tools": ["send_message"],
                "sessions_analyzed": 5,
            },
        )

    def test_unused_signal_produces_mcp_target_recommendation(self) -> None:
        recs = correlate([self._unused_signal()])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.target == "mcp"
        assert rec.severity == Severity.INFO
        # Action names the server and points at the source file.
        assert "slack" in rec.action
        assert "/home/u/.claude.json" in rec.action
        assert rec.signal_types == [SignalType.MCP_UNUSED_SERVER]

    def test_missing_signal_produces_warning_recommendation(self) -> None:
        recs = correlate([self._missing_signal()])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.target == "mcp"
        assert rec.severity == Severity.WARNING
        assert "slack" in rec.action
        # Action suggests adding to either user or project config.
        assert "~/.claude.json" in rec.action or ".mcp.json" in rec.action
        assert rec.signal_types == [SignalType.MCP_MISSING_SERVER]

    def test_non_mcp_signal_is_not_claimed_by_mcp_rule(self) -> None:
        # A plain ERROR_PATTERN signal should route through other rules,
        # not McpAuditRule. If McpAuditRule.matches were too loose this
        # test would fail.
        recs = correlate([_signal(keyword="error")])
        # At least one recommendation; target is NOT "mcp".
        assert all(r.target != "mcp" for r in recs)


def _builtin_signal(signal_type: SignalType, agent_type: str = "explore") -> DiagnosticSignal:
    detail: dict[str, object] = {}
    if signal_type == SignalType.ERROR_PATTERN:
        detail = {"keyword": "failed"}
    elif signal_type == SignalType.PERMISSION_FAILURE:
        detail = {"tool_name": "Write", "tool_calls": []}
    elif signal_type == SignalType.RETRY_LOOP:
        detail = {"tool_name": "Read", "retry_count": 3, "tool_calls": []}
    elif signal_type == SignalType.STUCK_PATTERN:
        detail = {"tool_name": "Read", "stuck_count": 4, "tool_calls": []}
    elif signal_type == SignalType.TOOL_ERROR_SEQUENCE:
        detail = {"error_count": 2, "start_index": 0, "end_index": 1, "tool_calls": []}
    elif signal_type == SignalType.MODEL_MISMATCH:
        detail = {
            "mismatch_type": "overspec",
            "current_model": "claude-opus-4-7",
            "recommended_model": "claude-haiku-4-5",
            "complexity_tier": "simple",
            "invocation_count": 5,
        }
    return DiagnosticSignal(
        signal_type=signal_type,
        severity=Severity.WARNING,
        agent_type=agent_type,
        message=f"Agent '{agent_type}' triggered {signal_type.value}.",
        detail=detail,
    )


class TestBuiltinAgentBranching:
    """Rules must emit built-in-specific action text (not "edit the prompt
    in ~/.claude/agents/<name>.md") when ``signal.agent_type`` names a
    built-in like Explore, general-purpose, or Plan — those agents have
    no user-editable config files. Issue #166."""

    @pytest.mark.parametrize(
        ("signal_type", "expected_phrase", "expected_target"),
        [
            (SignalType.TOKEN_OUTLIER, "prompt is not user-editable", "prompt"),
            (SignalType.DURATION_OUTLIER, "prompt is not user-editable", "prompt"),
            (SignalType.TOOL_ERROR_SEQUENCE, "prompt is not user-editable", "prompt"),
            (SignalType.RETRY_LOOP, "prompt is not user-editable", "prompt"),
            (SignalType.STUCK_PATTERN, "prompt is not user-editable", "prompt"),
            (SignalType.PERMISSION_FAILURE, "tool list is not user-editable", "tools"),
            (SignalType.MODEL_MISMATCH, "model is not user-configurable", "model"),
        ],
    )
    def test_builtin_agent_gets_non_editable_action_text(
        self,
        signal_type: SignalType,
        expected_phrase: str,
        expected_target: str,
    ) -> None:
        recs = correlate([_builtin_signal(signal_type)])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.is_builtin is True
        assert rec.config_file == ""
        assert rec.target == expected_target
        assert expected_phrase in rec.action

    def test_error_pattern_blocked_keyword_uses_tools_concern(self) -> None:
        signals = [_signal(keyword="blocked", agent_type="explore")]
        recs = correlate(signals)
        assert recs[0].is_builtin is True
        assert recs[0].target == "tools"
        assert "tool list is not user-editable" in recs[0].action

    def test_error_pattern_failed_keyword_uses_recovery_concern(self) -> None:
        signals = [_signal(keyword="failed", agent_type="explore")]
        recs = correlate(signals)
        assert recs[0].is_builtin is True
        assert recs[0].target == "prompt"
        assert "prompt is not user-editable" in recs[0].action

    def test_custom_agent_unchanged_by_builtin_branching(self) -> None:
        signals = [_builtin_signal(SignalType.TOKEN_OUTLIER, agent_type="pm")]
        recs = correlate(signals, {"pm": _config()})
        assert len(recs) == 1
        assert recs[0].is_builtin is False
        assert "pm.md" in recs[0].config_file
        assert "not user-editable" not in recs[0].action

    def test_builtin_detection_is_case_insensitive(self) -> None:
        signals = [_builtin_signal(SignalType.TOKEN_OUTLIER, agent_type="Explore")]
        recs = correlate(signals)
        assert recs[0].is_builtin is True

    def test_all_four_concern_templates_distinct(self) -> None:
        token_rec = correlate([_builtin_signal(SignalType.TOKEN_OUTLIER)])[0]
        retry_rec = correlate([_builtin_signal(SignalType.RETRY_LOOP)])[0]
        perm_rec = correlate([_builtin_signal(SignalType.PERMISSION_FAILURE)])[0]
        model_rec = correlate([_builtin_signal(SignalType.MODEL_MISMATCH)])[0]
        actions = {token_rec.action, retry_rec.action, perm_rec.action, model_rec.action}
        assert len(actions) == 4


class TestInvocationIdPropagation:
    """#197: invocation_id flows from signal to recommendation across rules."""

    def test_access_error_rule_propagates_invocation_id(self) -> None:
        sig = _signal(keyword="blocked", severity=Severity.CRITICAL,
                      invocation_id="ag-1")
        recs = correlate([sig])
        assert recs and recs[0].invocation_id == "ag-1"

    def test_token_outlier_rule_propagates_invocation_id(self) -> None:
        sig = _signal(
            signal_type=SignalType.TOKEN_OUTLIER,
            detail={
                "actual_value": 5000,
                "median_value": 1000,
                "q3_value": 1500,
                "iqr_value": 500,
                "p95_value": 5000,
                "threshold_value": 2250,
                "excess_iqrs": 7.0,
            },
            invocation_id="ag-outlier",
        )
        recs = correlate([sig])
        assert recs and recs[0].invocation_id == "ag-outlier"

    def test_builtin_recommendation_propagates_invocation_id(self) -> None:
        sig = _signal(
            signal_type=SignalType.RETRY_LOOP,
            severity=Severity.WARNING,
            agent_type="Explore",
            detail={"tool_name": "Bash", "retry_count": 3},
            invocation_id="ag-builtin",
        )
        recs = correlate([sig])
        assert recs and recs[0].invocation_id == "ag-builtin"
        # Built-in agents get the concern-keyed action template; field
        # still flows through.
        assert recs[0].is_builtin

    def test_cross_cutting_signal_carries_none(self) -> None:
        # MCP audit signals leave invocation_id as None; the propagation
        # should preserve None on the recommendation.
        sig = DiagnosticSignal(
            signal_type=SignalType.MCP_UNUSED_SERVER,
            severity=Severity.INFO,
            agent_type="",
            invocation_id=None,
            message="MCP server 'slack' is configured but unused.",
            detail={"server_name": "slack", "source_file": "/etc/cfg.json"},
        )
        recs = correlate([sig])
        assert recs and recs[0].invocation_id is None


class TestUserCorrectionRule:
    """USER_CORRECTION (cross-cutting quality signal) -> ``target='subagent'``
    recommendation suggesting a review-style subagent."""

    @staticmethod
    def _user_correction_signal() -> DiagnosticSignal:
        return DiagnosticSignal(
            signal_type=SignalType.USER_CORRECTION,
            severity=Severity.WARNING,
            agent_type=None,
            invocation_id=None,
            message="User correction in parent thread: no, that's wrong",
            detail={
                "correction_text": "no, that's wrong",
                "matched_pattern": "that's wrong",
                "matched_category": "strong",
                "preceding_assistant_action": "write_tool",
                "session_correction_rate": 0.25,
                "total_user_messages": 4,
                "total_corrections": 1,
            },
        )

    def test_emits_subagent_target(self) -> None:
        recs = correlate([self._user_correction_signal()])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.target == "subagent"
        assert rec.severity == Severity.WARNING
        assert rec.agent_type is None
        assert rec.invocation_id is None
        assert rec.signal_types == [SignalType.USER_CORRECTION]

    def test_message_carries_quality_prefix(self) -> None:
        recs = correlate([self._user_correction_signal()])
        # Transitional [quality] prefix until #273 renders axis labels
        # via the formatter (architect review on #269, suggestion #5).
        assert "[quality]" in recs[0].message

    def test_recommends_review_style_subagent(self) -> None:
        recs = correlate([self._user_correction_signal()])
        action = recs[0].action.lower()
        assert "architect" in action or "code-reviewer" in action

    def test_does_not_match_other_signal_types(self) -> None:
        # Ensure the rule's matches() doesn't catch unrelated signals.
        unrelated = DiagnosticSignal(
            signal_type=SignalType.TOKEN_OUTLIER,
            severity=Severity.WARNING,
            agent_type="pm",
            message="High token usage.",
            detail={"excess_iqrs": 3.0},
        )
        recs = correlate([unrelated])
        assert all(rec.target != "subagent" for rec in recs)


class TestFileReworkRule:
    """FILE_REWORK -> ``target='subagent'`` recommendation, copy
    branches on ``post_completion_edits``."""

    @staticmethod
    def _signal(post_completion: int = 0) -> DiagnosticSignal:
        return DiagnosticSignal(
            signal_type=SignalType.FILE_REWORK,
            severity=Severity.WARNING,
            agent_type=None,
            invocation_id=None,
            message="File '/src/foo.py' edited 5 times in this session",
            detail={
                "file_path": "/src/foo.py",
                "edit_count": 5,
                "post_completion_edits": post_completion,
                "edit_tools": ["Edit"],
                "completion_scope": "session",
            },
        )

    def test_emits_subagent_target(self) -> None:
        recs = correlate([self._signal()])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.target == "subagent"
        assert rec.severity == Severity.WARNING
        assert rec.agent_type is None
        assert rec.signal_types == [SignalType.FILE_REWORK]

    def test_message_carries_quality_prefix(self) -> None:
        recs = correlate([self._signal()])
        assert "[quality]" in recs[0].message

    def test_recommends_review_subagent_iterative(self) -> None:
        recs = correlate([self._signal(post_completion=0)])
        action = recs[0].action.lower()
        assert "architect" in action
        assert "smaller" in action

    def test_recommends_tester_for_post_completion_rework(self) -> None:
        """When post_completion_edits > 0 the recommendation copy
        mentions tester (verifying completion claims) in addition to
        architect (design review)."""
        recs = correlate([self._signal(post_completion=2)])
        action = recs[0].action.lower()
        assert "architect" in action
        assert "tester" in action


class TestReviewerCaughtRule:
    """REVIEWER_CAUGHT -> per-agent ``target='subagent'`` recommendation."""

    @staticmethod
    def _signal(
        agent_type: str = "architect",
        finding_keywords: list[str] | None = None,
        parent_acted: bool = True,
    ) -> DiagnosticSignal:
        kw = finding_keywords if finding_keywords is not None else [
            "blocker", "issue", "must",
        ]
        return DiagnosticSignal(
            signal_type=SignalType.REVIEWER_CAUGHT,
            severity=Severity.INFO,
            agent_type=agent_type,
            invocation_id="toolu_review",
            message=(
                f"`{agent_type}` review surfaced {len(kw)} finding-keyword(s)"
            ),
            detail={
                "finding_keywords": kw,
                "parent_acted": parent_acted,
                "response_length": 1000,
                "files_mentioned": ["src/foo.py"],
                "files_acted_on": ["src/foo.py"] if parent_acted else [],
            },
        )

    def test_emits_subagent_target_per_agent(self) -> None:
        recs = correlate([self._signal()])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.target == "subagent"
        assert rec.agent_type == "architect"
        assert rec.invocation_id == "toolu_review"
        assert rec.signal_types == [SignalType.REVIEWER_CAUGHT]

    def test_message_carries_quality_prefix(self) -> None:
        recs = correlate([self._signal()])
        assert "[quality]" in recs[0].message

    def test_parent_acted_branch_routes_more_sessions(self) -> None:
        recs = correlate([self._signal(parent_acted=True)])
        action = recs[0].action.lower()
        assert "more sessions" in action or "routing" in action

    def test_parent_did_not_act_branch_suggests_investigation(self) -> None:
        recs = correlate([self._signal(parent_acted=False)])
        action = recs[0].action.lower()
        assert "investigate" in action or "follow-through" in action

    def test_agent_name_appears_in_action(self) -> None:
        recs = correlate([self._signal(agent_type="code-reviewer")])
        assert "code-reviewer" in recs[0].action
