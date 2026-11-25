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
a correlator rule renders a path via ``_relpath``/``home_relative`` but has no
row here, and ``test_no_raw_config_path_interpolation`` fails when any
f-string in the scanned modules interpolates ``config.file_path`` or
``source_file`` without one of those helpers.

Out of scope: signals that render **session-observed** file paths
(FILE_REWORK, PARAMETER_RETRY) take tool-input paths from the session, not
from a config definition. Whether those render home-relative is an open
policy question (#757), so this guard neither covers nor decides it.

``EnvironmentWarning`` text follows the same policy: the cleanupPeriodDays
retention warning (``config/retention.py::check_cleanup_retention``) renders
the settings path home-relative in ``message`` and keeps it absolute in
``remediation_path`` (#759). As of #759 the only other producer,
``analytics/cost_coverage.py::pricing_gap_warning``, renders no path.
"""

from __future__ import annotations

import ast
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import AgentConfig, McpServerConfig, Scope, Severity
from agentfluent.config.retention import _load_settings, check_cleanup_retention
from agentfluent.diagnostics import agent_audit, correlator, mcp_assessment
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

# Rules whose path comes from detail["source_file"], mapped to the signal whose
# EMITTER_CASES row exercises them. test_every_path_rendering_rule_is_covered
# counts one as covered only while that row exists.
_SOURCE_FILE_RULES: dict[type, SignalType] = {
    correlator.McpAuditRule: SignalType.MCP_UNUSED_SERVER,
    correlator.UnusedAgentRule: SignalType.UNUSED_AGENT,
    correlator.ToolInventoryOversizedRule: SignalType.TOOL_INVENTORY_OVERSIZED,
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


# --- Static tripwire: no raw config-path interpolation ---------------------

_RENDER_CALL = re.compile(r"\b(?:_relpath|home_relative)\(")
_WRAPPERS = {"_relpath", "home_relative"}
# Scoped to the config-path emitters and these two names on purpose: a broader
# pattern would also match session-observed paths (FILE_REWORK's `file_path`
# in quality_signals.py) and so pre-decide #757.
_SCANNED_MODULES = [correlator, agent_audit, mcp_assessment]


def _is_config_path_ref(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "source_file"
    if isinstance(node, ast.Attribute):
        return node.attr == "source_file" or (
            node.attr == "file_path"
            and isinstance(node.value, ast.Name)
            and node.value.id == "config"
        )
    return False


def _raw_config_path_interpolations(source: str, name: str) -> list[str]:
    """f-string fields in ``source`` that reference a config path outside a
    wrapper call.

    A reference is allowed inside a ``_relpath(...)``/``home_relative(...)``
    call, or as the truthiness test of a conditional expression
    (``{_relpath(Path(s)) if s else 'unknown'}``).
    """
    tree = ast.parse(source)
    raw: list[str] = []

    def visit(node: ast.AST, *, wrapped: bool) -> None:
        if _is_config_path_ref(node) and not wrapped:
            raw.append(f"{name}:{node.lineno}: {ast.unparse(node)}")
            return
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _WRAPPERS
        ):
            wrapped = True
        if isinstance(node, ast.IfExp):
            visit(node.test, wrapped=True)
            visit(node.body, wrapped=wrapped)
            visit(node.orelse, wrapped=wrapped)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, wrapped=wrapped)

    for joined in (n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)):
        for field in joined.values:
            if isinstance(field, ast.FormattedValue):
                visit(field.value, wrapped=False)
    return raw


def test_no_raw_config_path_interpolation() -> None:
    """Fails on the leak itself: a config path interpolated into an f-string
    without ``_relpath``/``home_relative``. Keyed on the bug rather than the
    fix, so a new rule or branch is caught even before it has a guard row.
    Blind spots (aliasing, ``+``, ``.format``) are left to the rows above."""
    raw = [
        hit
        for module in _SCANNED_MODULES
        for hit in _raw_config_path_interpolations(inspect.getsource(module), module.__name__)
    ]
    assert raw == []


def test_static_tripwire_detects_raw_interpolation() -> None:
    """The tripwire is not vacuous: it flags the pre-#756 MCP message shape and
    accepts the wrapped and conditional forms used today."""
    source = (
        "def f(server, config, source_file):\n"
        "    a = f'in {server.source_file} x'\n"
        "    b = f'in {config.file_path} x'\n"
        "    c = f'in {source_file} x'\n"
        "    d = f'in {_relpath(config.file_path)} {home_relative(server.source_file)}'\n"
        "    e = f'{_relpath(Path(source_file)) if source_file else \"unknown\"}'\n"
        "    g = {'source_file': str(config.file_path)}\n"
    )
    hits = _raw_config_path_interpolations(source, "sample")
    assert [h.split(": ", 1)[1] for h in hits] == [
        "server.source_file", "config.file_path", "source_file",
    ]


def _matching_rule(signal: DiagnosticSignal, config: AgentConfig) -> object:
    return next(r for r in correlator.RULES if r.matches(signal, config))


def test_every_path_rendering_rule_is_covered() -> None:
    """A correlator rule that renders a config path must have a guard row."""
    covered = {case.rule for case in RULE_CASES.values()} | {
        rule for rule, signal_type in _SOURCE_FILE_RULES.items()
        if signal_type in EMITTER_CASES
    }
    rendering = {
        type(rule) for rule in correlator.RULES
        if _RENDER_CALL.search(inspect.getsource(type(rule)))
    }
    assert rendering, "expected at least one path-rendering rule"
    assert rendering <= covered, (
        f"add guard rows for: {sorted(r.__name__ for r in rendering - covered)}"
    )


# --- EnvironmentWarning text (#759) ---------------------------------------


RETENTION_CONFIG_MODES = ("override", "default")


def _retention_config_dir(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> tuple[Path | None, Path]:
    """Return (``claude_config_dir`` argument, expected settings path).

    ``override`` passes the config dir explicitly (``--claude-config-dir``).
    ``default`` passes ``None`` so the check falls back to
    ``core.paths.DEFAULT_CLAUDE_CONFIG_DIR``, which is computed from
    ``Path.home()`` at import time and so must be patched to the fake home.
    """
    config_dir = fake_home / ".claude"
    config_dir.mkdir(parents=True)
    _load_settings.cache_clear()
    if mode == "default":
        monkeypatch.setattr("agentfluent.core.paths.DEFAULT_CLAUDE_CONFIG_DIR", config_dir)
        return None, config_dir / "settings.json"
    return config_dir, config_dir / "settings.json"


@pytest.mark.parametrize("mode", RETENTION_CONFIG_MODES)
def test_retention_warning_renders_no_home_path(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    """The cleanupPeriodDays warning renders its settings path home-relative.

    No ``settings.json`` under the fake home's config dir, so the 30-day
    default applies and the warning fires. ``message`` carries
    ``~/.claude/settings.json``; ``remediation_path`` keeps the absolute path.
    """
    config_arg, absolute = _retention_config_dir(fake_home, monkeypatch, mode)

    warning = check_cleanup_retention(config_arg, None)

    assert warning is not None
    assert warning.remediation_path == absolute
    assert str(absolute).startswith(str(fake_home))
    assert str(fake_home) not in warning.message
    assert "~/.claude/settings.json" in warning.message


@pytest.mark.parametrize("mode", RETENTION_CONFIG_MODES)
def test_retention_warning_renders_path_through_home_relative(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    """The message path goes through ``home_relative``, not an inline rewrite.

    A spy replaces ``home_relative`` in ``config.retention``: the message must
    carry the spy's output for exactly the settings path, and
    ``remediation_path`` must not pass through it. This pins the mechanism
    (prefix-safe matching and the resolved-home fallback live in
    ``home_relative``), which an outcome check under a plain fake home cannot.
    """
    config_arg, absolute = _retention_config_dir(fake_home, monkeypatch, mode)
    seen: list[Path] = []

    def spy(path: Path) -> str:
        seen.append(path)
        return "<HOME_RELATIVE>"

    monkeypatch.setattr("agentfluent.config.retention.home_relative", spy)

    warning = check_cleanup_retention(config_arg, None)

    assert warning is not None
    assert seen == [absolute]
    assert warning.message.endswith("to <HOME_RELATIVE>.")
    assert warning.remediation_path == absolute


def test_retention_warning_outside_home_rendered_unchanged(
    fake_home: Path, tmp_path: Path,
) -> None:
    """A config dir outside ``$HOME`` keeps its absolute path in the message."""
    config_dir = tmp_path / "elsewhere" / ".claude"
    config_dir.mkdir(parents=True)
    _load_settings.cache_clear()

    warning = check_cleanup_retention(config_dir, None)

    assert warning is not None
    assert not str(config_dir).startswith(str(fake_home))
    assert warning.message.endswith(f"to {config_dir / 'settings.json'}.")
    assert warning.remediation_path == config_dir / "settings.json"
