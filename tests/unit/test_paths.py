"""Tests for core.paths module."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentfluent.core.paths import (
    CLAUDE_CONFIG_DIR_ENV_VAR,
    DEFAULT_CLAUDE_CONFIG_DIR,
    XDG_CACHE_HOME_ENV_VAR,
    XDG_CONFIG_HOME_ENV_VAR,
    agentfluent_cache_dir,
    agentfluent_config_dir,
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


class TestAgentfluentConfigDir:
    def test_default_under_home_config(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(XDG_CONFIG_HOME_ENV_VAR, raising=False)
        assert agentfluent_config_dir() == Path.home() / ".config" / "agentfluent"

    def test_honors_xdg_config_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(XDG_CONFIG_HOME_ENV_VAR, str(tmp_path))
        assert agentfluent_config_dir() == tmp_path / "agentfluent"


class TestAgentfluentCacheDir:
    def test_default_under_home_cache(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(XDG_CACHE_HOME_ENV_VAR, raising=False)
        assert agentfluent_cache_dir() == Path.home() / ".cache" / "agentfluent"

    def test_honors_xdg_cache_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(XDG_CACHE_HOME_ENV_VAR, str(tmp_path))
        assert agentfluent_cache_dir() == tmp_path / "agentfluent"
