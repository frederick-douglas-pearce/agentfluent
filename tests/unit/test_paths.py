"""Tests for core.paths module."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentfluent.core.paths import (
    CLAUDE_CONFIG_DIR_ENV_VAR,
    DEFAULT_CLAUDE_CONFIG_DIR,
    validate_claude_config_dir,
)


class TestDefaults:
    def test_default_is_home_dot_claude(self) -> None:
        assert DEFAULT_CLAUDE_CONFIG_DIR == Path.home() / ".claude"

    def test_env_var_name(self) -> None:
        assert CLAUDE_CONFIG_DIR_ENV_VAR == "CLAUDE_CONFIG_DIR"


class TestValidateClaudeConfigDir:
    def test_none_returns_none(self) -> None:
        assert validate_claude_config_dir(None) is None

    def test_valid_directory_returns_resolved_path(self, tmp_path: Path) -> None:
        result = validate_claude_config_dir(tmp_path)
        assert result == tmp_path.resolve()

    def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        with pytest.raises(FileNotFoundError, match="not found"):
            validate_claude_config_dir(missing)

    def test_file_not_directory_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "notadir.txt"
        f.write_text("")
        with pytest.raises(NotADirectoryError, match="not a directory"):
            validate_claude_config_dir(f)
