"""Tests for core.paths module."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentfluent.core.paths import (
    CLAUDE_CONFIG_DIR_ENV_VAR,
    DEFAULT_CLAUDE_CONFIG_DIR,
    XDG_CACHE_HOME_ENV_VAR,
    XDG_CONFIG_HOME_ENV_VAR,
    XDG_STATE_HOME_ENV_VAR,
    agentfluent_cache_dir,
    agentfluent_config_dir,
    agentfluent_state_dir,
    home_relative,
    settings_path_for,
    validate_claude_config_dir,
)


class TestDefaults:
    def test_default_is_home_dot_claude(self) -> None:
        assert DEFAULT_CLAUDE_CONFIG_DIR == Path.home() / ".claude"

    def test_env_var_name(self) -> None:
        assert CLAUDE_CONFIG_DIR_ENV_VAR == "CLAUDE_CONFIG_DIR"


class TestSettingsPathFor:
    def test_default_under_home_dot_claude(self) -> None:
        assert settings_path_for(None) == Path.home() / ".claude" / "settings.json"

    def test_override_resolves_inside_config_root(self, tmp_path: Path) -> None:
        assert settings_path_for(tmp_path) == tmp_path / "settings.json"


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


class TestAgentfluentStateDir:
    def test_default_under_home_local_state(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(XDG_STATE_HOME_ENV_VAR, raising=False)
        assert agentfluent_state_dir() == Path.home() / ".local" / "state" / "agentfluent"

    def test_honors_xdg_state_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(XDG_STATE_HOME_ENV_VAR, str(tmp_path))
        assert agentfluent_state_dir() == tmp_path / "agentfluent"


class TestHomeRelative:
    """``home_relative`` renders ``$HOME`` as ``~`` for message text (#340, #753)."""

    @pytest.fixture
    def fake_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        home = tmp_path / "al"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        return home

    def test_path_under_home_becomes_tilde_relative(self, fake_home: Path) -> None:
        path = fake_home / ".claude" / "agents" / "tester.md"
        assert home_relative(path) == "~/.claude/agents/tester.md"

    def test_path_outside_home_unchanged(self, fake_home: Path) -> None:
        assert home_relative(Path("/etc/agentfluent/global.md")) == (
            "/etc/agentfluent/global.md"
        )

    def test_sibling_sharing_home_prefix_unchanged(self, fake_home: Path) -> None:
        # ``/.../al`` must not swallow ``/.../alice`` -- a component match,
        # not a string-prefix match.
        sibling = fake_home.parent / "alice" / "agents" / "pm.md"
        assert home_relative(sibling) == str(sibling)

    def test_path_under_symlinked_home_matches_resolved_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # Agent definitions are stored resolved; $HOME may be a symlink
        # (e.g. /home -> /var/home). The resolved path must still render ~/.
        real_home = tmp_path / "var" / "home" / "al"
        real_home.mkdir(parents=True)
        link_home = tmp_path / "home-link"
        link_home.symlink_to(real_home, target_is_directory=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: link_home))
        resolved_path = real_home / ".claude" / "agents" / "tester.md"
        assert home_relative(resolved_path) == "~/.claude/agents/tester.md"
        # The unresolved spelling under the symlink still matches too.
        assert home_relative(link_home / "x.md") == "~/x.md"
