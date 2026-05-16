"""End-to-end ``--session`` scope test against real session data.

Smallest viable smoke test: pick a real session, run the full CLI with
``analyze --session <file> --diagnostics --json``, and assert the
envelope reports the expected scope. This proves the path filter +
diagnostics pipeline composition holds against real JSONL shapes that
the unit-test builders don't necessarily reproduce.

Deeper assertions about diagnostic correctness against real data would
be brittle (the answer depends on which session pytest happened to
pick), so the assertions stay strictly structural.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from agentfluent.cli.main import app
from agentfluent.core.discovery import DEFAULT_PROJECTS_DIR, discover_projects

has_real_data = DEFAULT_PROJECTS_DIR.exists() and any(DEFAULT_PROJECTS_DIR.iterdir())

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not has_real_data, reason="No real session data at ~/.claude/projects/",
    ),
]


class TestSessionScopeAgainstRealData:
    def test_session_flag_produces_scoped_envelope(self) -> None:
        projects = discover_projects()
        with_sessions = [p for p in projects if p.session_count > 0]
        if not with_sessions:
            pytest.skip("No projects with sessions found")
        # Pick the project with the smallest total size so the test
        # stays fast; pick the smallest session inside it for the same
        # reason. Bigger sessions exercise the same code paths.
        project = min(with_sessions, key=lambda p: p.total_size_bytes)
        session = min(project.sessions, key=lambda s: s.size_bytes)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["analyze", "--project", project.display_name,
             "--session", session.filename,
             "--diagnostics", "--json"],
        )
        assert result.exit_code == 0, result.output

        envelope = json.loads(result.stdout)
        assert envelope["command"] == "analyze"
        data = envelope["data"]
        assert data["scope_session"] == session.filename, (
            f"scope_session={data['scope_session']!r} did not match "
            f"requested session={session.filename!r}"
        )
        assert data["session_count"] == 1
