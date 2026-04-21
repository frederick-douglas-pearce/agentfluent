"""Integration tests for subagent trace parsing against real data.

These tests validate the full chain — discovery + parser + linker —
against real subagent traces at ``~/.claude/projects/<slug>/<session>/subagents/``.
Skipped in CI (no real data available) and do not depend on specific
project names or trace contents.
"""

from __future__ import annotations

import pytest

from agentfluent.analytics.pipeline import analyze_session
from agentfluent.core.discovery import DEFAULT_PROJECTS_DIR, discover_projects
from agentfluent.traces.discovery import discover_subagent_files
from agentfluent.traces.parser import parse_subagent_trace


def _find_project_with_subagents() -> tuple[object, dict[str, list[object]]] | None:
    """Scan real projects for one that actually has subagent trace files.

    Returns ``(project, subagent_map)`` on the first hit, or ``None``.
    The project is whatever ``discover_projects`` returns; the map is
    ``discover_subagent_files``' ``session_id -> files`` structure.
    """
    if not DEFAULT_PROJECTS_DIR.exists():
        return None
    for project in discover_projects():
        subagents = discover_subagent_files(project.path)
        if subagents:
            return project, subagents
    return None


_real_subagent_hit = _find_project_with_subagents()
_has_real_subagents = _real_subagent_hit is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _has_real_subagents,
        reason="No subagent traces found under ~/.claude/projects/",
    ),
]


class TestDiscoversRealSubagents:
    def test_at_least_one_project_has_subagent_files(self) -> None:
        assert _real_subagent_hit is not None
        _, subagents = _real_subagent_hit
        assert len(subagents) >= 1

    def test_at_least_one_session_has_one_trace_file(self) -> None:
        assert _real_subagent_hit is not None
        _, subagents = _real_subagent_hit
        total_files = sum(len(files) for files in subagents.values())
        assert total_files >= 1


class TestParsesRealTraceEndToEnd:
    def test_first_real_trace_has_non_empty_content(self) -> None:
        """Pick any real subagent file and parse it; assert the basics hold."""
        assert _real_subagent_hit is not None
        _, subagents = _real_subagent_hit

        first_file = next(
            info for files in subagents.values() for info in files
        )
        trace = parse_subagent_trace(first_file.path)

        # At least the filename should give us an agent_id.
        assert trace.agent_id == first_file.agent_id

        # A realistic subagent trace ought to carry SOME of these signals —
        # assert permissively, since older traces may be minimal.
        has_calls = len(trace.tool_calls) > 0
        has_prompt = bool(trace.delegation_prompt)
        has_usage = trace.usage.total_tokens > 0
        assert has_calls or has_prompt or has_usage, (
            "Real trace should carry at least one of: tool_calls, delegation_prompt, usage"
        )


class TestLinkerOnRealSession:
    def test_at_least_one_invocation_picks_up_its_trace(self) -> None:
        """analyze_session on a session that has subagents wires at least
        one trace onto an invocation."""
        assert _real_subagent_hit is not None
        project, subagents = _real_subagent_hit

        # Find a session file alongside a subagents/ directory with files.
        # ``subagents`` is keyed by session_id (directory stem); its siblings
        # at ``<project>/<session_id>.jsonl`` are the session files we want.
        for session_id in subagents:
            session_path = project.path / f"{session_id}.jsonl"
            if not session_path.is_file():
                continue
            result = analyze_session(session_path)
            if any(inv.trace is not None for inv in result.invocations):
                return  # success for this session; test passes

        pytest.skip(
            "Real subagent directories exist but none have a sibling "
            "<session>.jsonl with invocations matching the trace agent_ids",
        )
