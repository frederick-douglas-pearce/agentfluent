"""Config-definition paths never leak the home directory into rendered text (#756).

Every diagnostic emitter that renders a **config-definition path** (an agent
``.md`` file, ``~/.claude.json``, a project ``.mcp.json``) into a signal
``message`` or into recommendation text must render it home-relative
(``core/paths.py::home_relative``). Structured fields
(``detail["source_file"]``, ``DiagnosticRecommendation.config_file``) keep the
absolute path (#340, #753).

**Adding a config-path emitter?** Add a row to ``EMITTER_CASES`` (a signal
emitter) or ``RULE_CASES`` (a correlator rule that interpolates
``config.file_path``). ``test_every_path_rendering_rule_is_covered`` fails when
a correlator rule calls ``_relpath`` but has no row here.

Out of scope: signals that render **session-observed** file paths
(FILE_REWORK, PARAMETER_RETRY) take tool-input paths from the session, not
from a config definition. Whether those render home-relative is an open
policy question (#757), so this guard neither covers nor decides it.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import AgentConfig, McpServerConfig, Scope, Severity
from agentfluent.diagnostics import correlator
from agentfluent.diagnostics.agent_audit import (
    audit_tool_inventory,
    audit_unused_agents,
)
from agentfluent.diagnostics.correlator import correlate
from agentfluent.diagnostics.mcp_assessment import audit_mcp_servers
from agentfluent.diagnostics.models import (
    DiagnosticRecommendation,
    DiagnosticSignal,
    SignalType,
)

AGENT = "worker"


@pytest.fixture
def fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home" / "u"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _agent_config(home: Path, **overrides: object) -> AgentConfig:
    fields: dict[str, object] = {
        "name": AGENT,
        "file_path": home / ".claude" / "agents" / f"{AGENT}.md",
        "scope": Scope.USER,
    }
    fields.update(overrides)
    return AgentConfig.model_validate(fields)


def _invocation(agent_type: str, tool_stats: dict[str, int] | None = None) -> AgentInvocation:
    return AgentInvocation(
        tool_use_id=f"toolu_{agent_type}",
        agent_type=agent_type,
        description="...",
        prompt="...",
        tool_stats=tool_stats,
    )


def _texts(
    signals: list[DiagnosticSignal],
    pairs: list[tuple[DiagnosticSignal, DiagnosticRecommendation]],
) -> list[str]:
    texts = [s.message for s in signals]
    for _, rec in pairs:
        texts += [rec.message, rec.observation, rec.reason, rec.action]
    return texts


# --- Signal emitters (real detectors) ------------------------------------

Detected = tuple[list[DiagnosticSignal], list[AgentConfig], str]
"""(signals, agent configs to correlate with, absolute config path)."""


def _unused_agent(home: Path) -> Detected:
    config = _agent_config(home)
    signals = audit_unused_agents([_invocation("pm")], [config], sessions_analyzed=3)
    return signals, [config], str(config.file_path)


def _tool_inventory_oversized(home: Path) -> Detected:
    config = _agent_config(home, tools=[f"tool_{i}" for i in range(40)])
    signals = audit_tool_inventory(
        [_invocation(AGENT, {"tool_0": 1})], [config], sessions_analyzed=3,
    )
    return signals, [config], str(config.file_path)


def _mcp_unused_server(home: Path) -> Detected:
    source = home / ".claude.json"
    server = McpServerConfig(server_name="slack", source_file=source, scope="user")
    signals = audit_mcp_servers(usage_by_server={}, configured=[server], sessions_analyzed=3)
    return signals, [], str(source)


EMITTER_CASES: dict[SignalType, Callable[[Path], Detected]] = {
    SignalType.UNUSED_AGENT: _unused_agent,
    SignalType.TOOL_INVENTORY_OVERSIZED: _tool_inventory_oversized,
    SignalType.MCP_UNUSED_SERVER: _mcp_unused_server,
}


@pytest.mark.parametrize("signal_type", list(EMITTER_CASES), ids=str)
def test_signal_emitter_renders_no_home_path(
    fake_home: Path, signal_type: SignalType,
) -> None:
    signals, configs, absolute = EMITTER_CASES[signal_type](fake_home)
    # Non-vacuity: the detector fired and its structured field carries the
    # absolute path under the fake home.
    assert [s.signal_type for s in signals] == [signal_type]
    assert signals[0].detail["source_file"] == absolute
    assert absolute.startswith(str(fake_home))

    pairs = correlate(signals, {c.name.lower(): c for c in configs})
    assert len(pairs) == 1
    rec = pairs[0][1]
    assert rec.config_file == absolute
    assert "~/" in rec.action  # the path is rendered, home-relative

    for text in _texts(signals, pairs):
        assert str(fake_home) not in text


# --- Correlator rules that interpolate config.file_path -------------------


@dataclass(frozen=True)
class RuleCase:
    rule: type
    signal_type: SignalType
    detail: dict[str, object]
    config: dict[str, object]


_MANY_TOOLS = [f"tool_{i}" for i in range(10)]
_ERROR_PROMPT = "Handle errors and retry once."
_PLAIN_PROMPT = "Summarize the input."

# One row per branch that renders the path. Rules without a config-path
# branch (e.g. ParameterRetryRule, the hook-coverage branch of
# DurationOutlierRule) are not listed.
RULE_CASES: dict[str, RuleCase] = {
    "access-error-no-tools": RuleCase(
        correlator.AccessErrorRule, SignalType.ERROR_PATTERN, {"keyword": "blocked"}, {},
    ),
    "access-error-with-tools": RuleCase(
        correlator.AccessErrorRule, SignalType.ERROR_PATTERN, {"keyword": "blocked"},
        {"tools": ["Read"]},
    ),
    "error-handling-guided": RuleCase(
        correlator.ErrorHandlingRule, SignalType.ERROR_PATTERN, {"keyword": "error"},
        {"prompt_body": _ERROR_PROMPT},
    ),
    "error-handling-unguided": RuleCase(
        correlator.ErrorHandlingRule, SignalType.ERROR_PATTERN, {"keyword": "error"},
        {"prompt_body": _PLAIN_PROMPT},
    ),
    "token-outlier-many-tools": RuleCase(
        correlator.TokenOutlierRule, SignalType.TOKEN_OUTLIER, {}, {"tools": _MANY_TOOLS},
    ),
    "token-outlier-few-tools": RuleCase(
        correlator.TokenOutlierRule, SignalType.TOKEN_OUTLIER, {}, {},
    ),
    "duration-outlier-model": RuleCase(
        correlator.DurationOutlierRule, SignalType.DURATION_OUTLIER, {},
        {"model": "claude-opus-4-6"},
    ),
    "duration-outlier-prompt": RuleCase(
        correlator.DurationOutlierRule, SignalType.DURATION_OUTLIER, {},
        {"model": "claude-haiku-4-5"},
    ),
    "permission-failure-missing-tool": RuleCase(
        correlator.PermissionFailureRule, SignalType.PERMISSION_FAILURE,
        {"tool_name": "Bash"}, {"tools": ["Read"]},
    ),
    "permission-failure-listed-tool": RuleCase(
        correlator.PermissionFailureRule, SignalType.PERMISSION_FAILURE,
        {"tool_name": "Bash"}, {"tools": ["Bash"]},
    ),
    "retry-loop-guided": RuleCase(
        correlator.RetryLoopRule, SignalType.RETRY_LOOP, {}, {"prompt_body": _ERROR_PROMPT},
    ),
    "retry-loop-unguided": RuleCase(
        correlator.RetryLoopRule, SignalType.RETRY_LOOP, {}, {"prompt_body": _PLAIN_PROMPT},
    ),
    "stuck-pattern": RuleCase(
        correlator.StuckPatternRule, SignalType.STUCK_PATTERN, {"stuck_count": 3}, {},
    ),
    "error-sequence-many-tools": RuleCase(
        correlator.ErrorSequenceRule, SignalType.TOOL_ERROR_SEQUENCE, {},
        {"tools": _MANY_TOOLS},
    ),
    "error-sequence-few-tools": RuleCase(
        correlator.ErrorSequenceRule, SignalType.TOOL_ERROR_SEQUENCE, {}, {},
    ),
    "model-mismatch-subagent": RuleCase(
        correlator.ModelRoutingRule, SignalType.MODEL_MISMATCH,
        {
            "mismatch_type": "overspec",
            "current_model": "claude-opus-4-6",
            "recommended_model": "claude-haiku-4-5",
            "complexity_tier": "simple",
        },
        {},
    ),
    "tool-orchestration": RuleCase(
        correlator.ToolOrchestrationRule, SignalType.TOOL_ORCHESTRATION_CHAIN, {}, {},
    ),
}

# Rules whose path comes from detail["source_file"]; their rows live in
# EMITTER_CASES above.
_SOURCE_FILE_RULES = {
    correlator.McpAuditRule,
    correlator.UnusedAgentRule,
    correlator.ToolInventoryOversizedRule,
}


@pytest.mark.parametrize("case_id", list(RULE_CASES))
def test_rule_renders_no_home_path(fake_home: Path, case_id: str) -> None:
    case = RULE_CASES[case_id]
    config = _agent_config(fake_home, **case.config)
    signal = DiagnosticSignal(
        signal_type=case.signal_type,
        severity=Severity.WARNING,
        agent_type=AGENT,
        message=f"Agent '{AGENT}' showed {case.signal_type}.",
        detail=dict(case.detail),
    )

    pairs = correlate([signal], {AGENT: config})

    assert len(pairs) == 1
    rec = pairs[0][1]
    # Non-vacuity: the intended rule handled it, took a branch that renders
    # the config path, and the structured field is absolute.
    assert rec.signal_types == [case.signal_type]
    assert type(_matching_rule(signal, config)) is case.rule
    assert "~/.claude/agents/worker.md" in rec.action
    assert rec.config_file == str(config.file_path)

    for text in _texts([signal], pairs):
        assert str(fake_home) not in text


def _matching_rule(signal: DiagnosticSignal, config: AgentConfig) -> object:
    return next(r for r in correlator.RULES if r.matches(signal, config))


def test_every_path_rendering_rule_is_covered() -> None:
    """A correlator rule that calls ``_relpath`` must have a guard row."""
    covered = {case.rule for case in RULE_CASES.values()} | _SOURCE_FILE_RULES
    rendering = {
        type(rule) for rule in correlator.RULES
        if "_relpath(" in inspect.getsource(type(rule))
    }
    assert rendering, "expected at least one path-rendering rule"
    assert rendering <= covered, (
        f"add guard rows for: {sorted(r.__name__ for r in rendering - covered)}"
    )
