"""Unit tests for ``config.hook_inspector``.

Fixtures use the real nested Claude Code matcher-group hook schema
(``event -> [{matcher, hooks: [{type, command}]}]``) so green tests reflect
production shape (see #424 architect review).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agentfluent.config.hook_inspector import (
    KNOWN_HOOK_FIELDS,
    inspect_hook_field,
)
from agentfluent.config.models import AgentConfig, Scope

FIXTURES = Path(__file__).parent.parent / "fixtures" / "hooks"


def _agent(hooks: dict[str, Any]) -> AgentConfig:
    """Build a minimal AgentConfig carrying the given hooks dict."""
    return AgentConfig(
        name="test-agent",
        file_path=Path("/tmp/test-agent.md"),
        scope=Scope.PROJECT,
        hooks=hooks,
    )


def _external_hook(script_name: str) -> dict[str, Any]:
    """A PostToolUse hook group invoking an external script by name."""
    return {
        "PostToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": f"python3 {script_name}"}],
            }
        ]
    }


def _load_fixture_hooks(filename: str) -> dict[str, Any]:
    data = yaml.safe_load((FIXTURES / filename).read_text())
    hooks: dict[str, Any] = data["hooks"]
    return hooks


def test_external_script_with_duration_ms_is_covered() -> None:
    config = _agent(_external_hook("timing_hook.py"))
    result = inspect_hook_field(
        config, "PostToolUse", "duration_ms", project_root=FIXTURES
    )
    assert result.covered is True
    assert result.source == str(FIXTURES / "timing_hook.py")
    assert result.hook_event == "PostToolUse"
    assert result.field_name == "duration_ms"


def test_external_script_without_duration_ms_is_uncovered() -> None:
    config = _agent(_external_hook("secrets_hook.py"))
    result = inspect_hook_field(
        config, "PostToolUse", "duration_ms", project_root=FIXTURES
    )
    assert result.covered is False


def test_inline_command_with_duration_ms_is_covered() -> None:
    config = _agent(_load_fixture_hooks("inline_bash_timing.yaml"))
    result = inspect_hook_field(
        config, "PostToolUse", "duration_ms", project_root=FIXTURES
    )
    assert result.covered is True
    assert result.source == "(inline)"


def test_no_post_tool_use_hooks_is_uncovered() -> None:
    config = _agent(_load_fixture_hooks("no_post_hook.yaml"))
    result = inspect_hook_field(
        config, "PostToolUse", "duration_ms", project_root=FIXTURES
    )
    assert result.covered is False
    assert result.source == ""


def test_missing_external_script_does_not_crash() -> None:
    config = _agent(_external_hook("does_not_exist.py"))
    result = inspect_hook_field(
        config, "PostToolUse", "duration_ms", project_root=FIXTURES
    )
    assert result.covered is False
    assert result.source == ""


def test_non_utf8_external_script_does_not_crash(tmp_path: Path) -> None:
    """A binary/non-UTF-8 hook script degrades to not-covered, no crash."""
    bad = tmp_path / "binary_hook.py"
    bad.write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
    config = _agent(_external_hook("binary_hook.py"))
    result = inspect_hook_field(
        config, "PostToolUse", "duration_ms", project_root=tmp_path
    )
    assert result.covered is False


def test_claude_project_dir_expansion() -> None:
    """A quoted ``$CLAUDE_PROJECT_DIR`` path resolves under project_root."""
    hooks = {
        "PostToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": 'python3 "$CLAUDE_PROJECT_DIR/timing_hook.py"',
                    }
                ],
            }
        ]
    }
    config = _agent(hooks)
    result = inspect_hook_field(
        config, "PostToolUse", "duration_ms", project_root=FIXTURES
    )
    assert result.covered is True
    assert result.source == str(FIXTURES / "timing_hook.py")


def test_known_hook_fields_registry() -> None:
    assert "duration_ms" in KNOWN_HOOK_FIELDS["PostToolUse"]
    assert {"tool_input", "tool_response", "tool_name"} <= KNOWN_HOOK_FIELDS[
        "PostToolUse"
    ]
    assert KNOWN_HOOK_FIELDS["Stop"] == {"background_tasks", "session_crons"}
