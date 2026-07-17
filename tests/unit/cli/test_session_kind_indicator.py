"""Tests for #592 S2: the SDK-vs-Claude-Code indicator in `analyze` output.

Covers the three render surfaces of the S1 classification and the JSON
contract, for all three states (sdk / cli / unknown) per AC#4:

- the always-printed footer composition line (the **default**, non-verbose
  surface -- what AC#1 actually claims about);
- the ``--session``-scoped badge;
- the verbose Per-Session Breakdown ``Kind`` column;
- the JSON envelope emitting BOTH ``session_kind`` and raw ``entrypoint``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from agentfluent.analytics.agent_metrics import AgentMetrics
from agentfluent.analytics.pipeline import AnalysisResult, SessionAnalysis
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.cli.formatters.table import format_analysis_table
from agentfluent.core.session import SessionClass


def _session(kind: SessionClass, name: str = "s.jsonl") -> SessionAnalysis:
    return SessionAnalysis(
        session_path=Path(name),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
        session_kind=kind,
    )


def _result(*kinds: SessionClass, scope_session: str | None = None) -> AnalysisResult:
    sessions = [_session(k, f"s{i}.jsonl") for i, k in enumerate(kinds)]
    return AnalysisResult(
        sessions=sessions,
        session_count=len(sessions),
        scope_session=scope_session,
    )


def _render(result: AnalysisResult, *, verbose: bool = False) -> str:
    console = Console(record=True, width=120, force_terminal=False)
    format_analysis_table(console, result, verbose=verbose)
    return console.export_text()


class TestFooterComposition:
    """AC#1 + AC#3 on the default (non-verbose) surface."""

    def test_sdk_and_cli_are_visibly_distinct(self) -> None:
        # AC#1: the SDK label is distinct from the Claude Code label.
        out = _render(_result("sdk", "cli", "cli"))
        assert "Sessions analyzed: 3 (2 Claude Code, 1 SDK)" in out

    def test_all_sdk_project(self) -> None:
        out = _render(_result("sdk", "sdk"))
        assert "Sessions analyzed: 2 (2 SDK)" in out

    def test_all_cli_project(self) -> None:
        out = _render(_result("cli"))
        assert "Sessions analyzed: 1 (1 Claude Code)" in out

    def test_unknown_is_counted_but_never_called_claude_code(self) -> None:
        # AC#3: neutral bucket, no misleading "Claude Code" claim.
        out = _render(_result("unknown", "unknown"))
        assert "Sessions analyzed: 2 (2 unclassified)" in out
        assert "Claude Code" not in out

    def test_unknown_is_not_folded_into_claude_code(self) -> None:
        # The AC#3 regression this guards: deriving a bucket by subtraction
        # (cli = total - sdk) would report "2 Claude Code" here.
        out = _render(_result("sdk", "cli", "unknown"))
        assert "Sessions analyzed: 3 (1 Claude Code, 1 SDK, 1 unclassified)" in out

    def test_zero_count_kinds_are_omitted(self) -> None:
        out = _render(_result("sdk"))
        assert "Claude Code" not in out
        assert "unclassified" not in out

    @pytest.mark.parametrize(
        "kinds",
        [
            ("sdk", "cli", "unknown"),
            ("cli", "cli", "sdk"),
            ("unknown",),
            ("sdk", "sdk", "sdk", "cli"),
        ],
    )
    def test_counts_always_sum_to_session_count(
        self, kinds: tuple[SessionClass, ...]
    ) -> None:
        # The honesty invariant: every analyzed session is accounted for in
        # exactly one bucket.
        out = _render(_result(*kinds))
        line = next(ln for ln in out.splitlines() if "Sessions analyzed:" in ln)
        counted = sum(
            int(tok) for tok in line.split("(")[1].rstrip(")").replace(",", " ").split()
            if tok.isdigit()
        )
        assert counted == len(kinds)

    def test_no_sessions_renders_no_parenthetical(self) -> None:
        out = _render(_result())
        assert "Sessions analyzed: 0" in out
        assert "Sessions analyzed: 0 (" not in out


class TestScopeSessionBadge:
    """AC#1 + AC#3 on the ``--session``-scoped footer."""

    def test_sdk_session_carries_badge(self) -> None:
        out = _render(_result("sdk", scope_session="abc.jsonl"))
        assert "Session: abc.jsonl [SDK]" in out

    def test_cli_session_carries_distinct_badge(self) -> None:
        out = _render(_result("cli", scope_session="abc.jsonl"))
        assert "Session: abc.jsonl [Claude Code]" in out

    def test_unknown_session_is_unlabelled(self) -> None:
        # AC#3: neutral/unlabeled -- no badge, no crash, no "Claude Code".
        out = _render(_result("unknown", scope_session="abc.jsonl"))
        assert "Session: abc.jsonl" in out
        assert "[" not in out.split("Session: abc.jsonl")[1]
        assert "Claude Code" not in out


class TestVerboseKindColumn:
    """The per-session breakdown table (verbose, >1 session)."""

    def test_kind_column_labels_each_session(self) -> None:
        out = _render(_result("sdk", "cli"), verbose=True)
        assert "Kind" in out
        assert "SDK" in out
        assert "Claude Code" in out

    def test_unknown_renders_em_dash_not_a_runtime_claim(self) -> None:
        out = _render(_result("unknown", "unknown"), verbose=True)
        assert "Kind" in out
        assert "—" in out
        assert "Claude Code" not in out


class TestJsonContract:
    """AC#2: the envelope emits BOTH fields, for all three states."""

    @pytest.mark.parametrize(
        ("fixture", "expected_kind", "expected_entrypoint"),
        [
            ("sdk_session/sdk-main-1.jsonl", "sdk", "sdk-py"),
            ("session_cli_entrypoint.jsonl", "cli", "cli"),
            ("session_basic.jsonl", "unknown", None),
        ],
    )
    def test_emits_session_kind_and_raw_entrypoint(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        isolated_home: Path,
        fixture: str,
        expected_kind: str,
        expected_entrypoint: str | None,
    ) -> None:
        src = Path(__file__).parent.parent.parent / "fixtures" / fixture
        project = isolated_home / "projects" / "-home-user-test-proj"
        project.mkdir(parents=True, exist_ok=True)
        (project / "s.jsonl").write_text(src.read_text())

        result = runner.invoke(
            cli_app, ["analyze", "--project", "proj", "--format", "json"]
        )
        assert result.exit_code == 0
        session = json.loads(result.stdout)["data"]["sessions"][0]

        assert session["session_kind"] == expected_kind
        assert session["entrypoint"] == expected_entrypoint
        # The pre-#592 name must not linger in the public envelope.
        assert "session_class" not in session
