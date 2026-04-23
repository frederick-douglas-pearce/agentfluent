"""Tests for MCP server discovery (config/mcp_discovery.py).

Covers reading ``~/.claude.json`` (user + project_local scopes) and
``.mcp.json`` (project_shared scope), precedence resolution when the
same server name appears in multiple scopes, gating via
``enabledMcpjsonServers`` / ``disabledMcpjsonServers``, and the
``--claude-config-dir`` override surface.

Fixtures at ``tests/fixtures/mcp/`` cover the simple single-scope
cases; multi-scope and path-sensitive scenarios are built
programmatically so that the project_dir path doesn't need template
substitution at test time.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agentfluent.config.mcp_discovery import (
    MCP_PROJECT_FILENAME,
    _load_json,
    discover_mcp_servers,
    resolve_project_disk_path,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "mcp"


def _write_claude_json(
    path: Path,
    *,
    user_servers: dict[str, dict] | None = None,
    project_dir: Path | None = None,
    project_local_servers: dict[str, dict] | None = None,
    enabled_whitelist: list[str] | None = None,
    disabled_list: list[str] | None = None,
) -> None:
    """Write a ``~/.claude.json``-style file with the given contents.

    Project path keys are stored as the resolved absolute string of
    ``project_dir``, matching how Claude Code keys its ``projects``
    map.
    """
    data: dict = {}
    if user_servers is not None:
        data["mcpServers"] = user_servers
    if project_dir is not None and (
        project_local_servers is not None
        or enabled_whitelist is not None
        or disabled_list is not None
    ):
        entry: dict = {}
        if project_local_servers is not None:
            entry["mcpServers"] = project_local_servers
        if enabled_whitelist is not None:
            entry["enabledMcpjsonServers"] = enabled_whitelist
        if disabled_list is not None:
            entry["disabledMcpjsonServers"] = disabled_list
        data["projects"] = {str(project_dir.resolve()): entry}
    path.write_text(json.dumps(data))


def _write_mcp_project(path: Path, servers: dict[str, dict]) -> None:
    path.write_text(json.dumps({"mcpServers": servers}))


def _override_claude_json_location(
    monkeypatch: pytest.MonkeyPatch, claude_json: Path,
) -> None:
    """Redirect ``claude_json_for(None)`` at the given path.

    Monkeypatches ``Path.home`` in both ``core.paths`` and
    ``config.mcp_discovery`` because ``claude_json_for`` uses
    ``Path.home() / ".claude.json"`` when no override is passed. For
    tests that exercise the override branch directly, construct a
    ``claude_config_dir`` whose parent contains ``claude_json``.
    """
    monkeypatch.setattr(Path, "home", lambda: claude_json.parent)


class TestReadUserScope:
    def test_fixture_with_mcpservers_returns_entries(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text((FIXTURES / "claude_user_only.json").read_text())
        _override_claude_json_location(monkeypatch, claude_json)

        servers = discover_mcp_servers(claude_config_dir=None, project_dir=None)

        assert {s.server_name for s in servers} == {"github", "unused-server"}
        for s in servers:
            assert s.scope == "user"
            assert s.enabled is True
            assert s.configured_tools is None
            assert s.source_file == claude_json

    def test_missing_claude_json_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # No ~/.claude.json at the mocked home.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert discover_mcp_servers(claude_config_dir=None, project_dir=None) == []

    def test_malformed_json_logs_warning_and_returns_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{this is not valid json")
        _override_claude_json_location(monkeypatch, claude_json)

        with caplog.at_level(logging.WARNING, logger="agentfluent.config.mcp_discovery"):
            servers = discover_mcp_servers(claude_config_dir=None, project_dir=None)

        assert servers == []
        assert any("Malformed JSON" in rec.message for rec in caplog.records)


class TestReadProjectLocal:
    def test_project_local_servers_discovered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        project_dir = tmp_path / "myproj"
        project_dir.mkdir()
        claude_json = tmp_path / ".claude.json"
        _write_claude_json(
            claude_json,
            project_dir=project_dir,
            project_local_servers={
                "local-only": {"command": "mcp-local"},
            },
        )
        _override_claude_json_location(monkeypatch, claude_json)

        servers = discover_mcp_servers(
            claude_config_dir=None, project_dir=project_dir,
        )

        assert len(servers) == 1
        assert servers[0].server_name == "local-only"
        assert servers[0].scope == "project_local"
        assert servers[0].source_file == claude_json

    def test_no_project_entry_returns_empty_for_project_scopes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        project_dir = tmp_path / "myproj"
        project_dir.mkdir()
        claude_json = tmp_path / ".claude.json"
        # Claude.json has user servers but no matching project entry.
        _write_claude_json(claude_json, user_servers={"u": {"command": "c"}})
        _override_claude_json_location(monkeypatch, claude_json)

        servers = discover_mcp_servers(
            claude_config_dir=None, project_dir=project_dir,
        )

        # Only the user server survives; project scopes are empty.
        assert [s.server_name for s in servers] == ["u"]
        assert servers[0].scope == "user"


class TestReadProjectShared:
    def test_mcp_json_read_and_all_enabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        claude_json = tmp_path / ".claude.json"
        _write_claude_json(claude_json)  # no user, no project_local
        _write_mcp_project(
            project_dir / MCP_PROJECT_FILENAME,
            {
                "shared-a": {"command": "a"},
                "shared-b": {"command": "b"},
            },
        )
        _override_claude_json_location(monkeypatch, claude_json)

        servers = discover_mcp_servers(
            claude_config_dir=None, project_dir=project_dir,
        )

        assert {s.server_name for s in servers} == {"shared-a", "shared-b"}
        for s in servers:
            assert s.scope == "project_shared"
            assert s.enabled is True
            assert s.source_file == project_dir / MCP_PROJECT_FILENAME

    def test_missing_mcp_json_returns_empty_contribution(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        claude_json = tmp_path / ".claude.json"
        _write_claude_json(claude_json)
        _override_claude_json_location(monkeypatch, claude_json)

        servers = discover_mcp_servers(
            claude_config_dir=None, project_dir=project_dir,
        )

        assert servers == []

    def test_disabled_list_gates_shared_server_off_not_filtered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        claude_json = tmp_path / ".claude.json"
        _write_claude_json(
            claude_json,
            project_dir=project_dir,
            disabled_list=["shared-disabled"],
        )
        _write_mcp_project(
            project_dir / MCP_PROJECT_FILENAME,
            {
                "shared-enabled": {"command": "e"},
                "shared-disabled": {"command": "d"},
            },
        )
        _override_claude_json_location(monkeypatch, claude_json)

        servers = discover_mcp_servers(
            claude_config_dir=None, project_dir=project_dir,
        )

        by_name = {s.server_name: s for s in servers}
        # Both kept; one is gated off.
        assert set(by_name) == {"shared-enabled", "shared-disabled"}
        assert by_name["shared-enabled"].enabled is True
        assert by_name["shared-disabled"].enabled is False

    def test_enabled_whitelist_disables_unlisted_servers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        claude_json = tmp_path / ".claude.json"
        _write_claude_json(
            claude_json,
            project_dir=project_dir,
            enabled_whitelist=["shared-allowed"],
        )
        _write_mcp_project(
            project_dir / MCP_PROJECT_FILENAME,
            {
                "shared-allowed": {"command": "a"},
                "shared-not-allowed": {"command": "n"},
            },
        )
        _override_claude_json_location(monkeypatch, claude_json)

        servers = discover_mcp_servers(
            claude_config_dir=None, project_dir=project_dir,
        )
        by_name = {s.server_name: s for s in servers}
        assert by_name["shared-allowed"].enabled is True
        assert by_name["shared-not-allowed"].enabled is False


class TestPerServerFields:
    def test_disabled_flag_propagates_and_tools_list_propagates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(
            (FIXTURES / "claude_user_with_disabled.json").read_text(),
        )
        _override_claude_json_location(monkeypatch, claude_json)

        servers = discover_mcp_servers(claude_config_dir=None, project_dir=None)
        by_name = {s.server_name: s for s in servers}

        assert by_name["alpha"].enabled is False
        assert by_name["alpha"].configured_tools is None
        assert by_name["beta"].enabled is True
        assert by_name["beta"].configured_tools == ["read", "write"]


class TestPrecedence:
    def test_project_local_overrides_shared_overrides_user(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        claude_json = tmp_path / ".claude.json"

        # Same server name "target" in all three scopes.
        _write_claude_json(
            claude_json,
            user_servers={
                "target": {"command": "user-target"},
                "user-only": {"command": "u"},
            },
            project_dir=project_dir,
            project_local_servers={
                "target": {"command": "local-target"},
                "local-only": {"command": "l"},
            },
        )
        _write_mcp_project(
            project_dir / MCP_PROJECT_FILENAME,
            {
                "target": {"command": "shared-target"},
                "shared-only": {"command": "s"},
            },
        )
        _override_claude_json_location(monkeypatch, claude_json)

        servers = discover_mcp_servers(
            claude_config_dir=None, project_dir=project_dir,
        )
        by_name = {s.server_name: s for s in servers}

        # target wins from project_local (highest precedence).
        assert by_name["target"].scope == "project_local"
        assert by_name["target"].source_file == claude_json
        # Each scope's unique entries survive.
        assert by_name["user-only"].scope == "user"
        assert by_name["shared-only"].scope == "project_shared"
        assert by_name["local-only"].scope == "project_local"

    def test_winning_source_file_points_at_correct_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        claude_json = tmp_path / ".claude.json"
        mcp_project = project_dir / MCP_PROJECT_FILENAME

        _write_claude_json(claude_json, user_servers={"s": {"command": "u"}})
        _write_mcp_project(mcp_project, {"s": {"command": "p"}})
        _override_claude_json_location(monkeypatch, claude_json)

        servers = discover_mcp_servers(
            claude_config_dir=None, project_dir=project_dir,
        )
        assert len(servers) == 1
        # project_shared beats user; source_file is the .mcp.json path.
        assert servers[0].scope == "project_shared"
        assert servers[0].source_file == mcp_project


class TestConfigDirOverride:
    def test_claude_config_dir_override_redirects_claude_json(
        self, tmp_path: Path,
    ) -> None:
        # Layout: /override/.claude/ (the override dir) and
        # /override/.claude.json (the sibling file). This matches the
        # pattern `claude_json_for(config_root)` implements.
        override_root = tmp_path / "override"
        override_root.mkdir()
        claude_config_dir = override_root / ".claude"
        claude_config_dir.mkdir()
        claude_json = override_root / ".claude.json"
        _write_claude_json(claude_json, user_servers={"overridden": {"command": "c"}})

        # No monkeypatching of Path.home — the override should kick in.
        servers = discover_mcp_servers(
            claude_config_dir=claude_config_dir, project_dir=None,
        )

        assert len(servers) == 1
        assert servers[0].server_name == "overridden"
        assert servers[0].source_file == claude_json

    def test_mcp_project_path_unaffected_by_claude_config_dir_override(
        self, tmp_path: Path,
    ) -> None:
        override_root = tmp_path / "override"
        override_root.mkdir()
        claude_config_dir = override_root / ".claude"
        claude_config_dir.mkdir()
        claude_json = override_root / ".claude.json"
        _write_claude_json(claude_json)  # no user mcpServers

        # project_dir is entirely outside the override hierarchy.
        project_dir = tmp_path / "unrelated-project"
        project_dir.mkdir()
        _write_mcp_project(
            project_dir / MCP_PROJECT_FILENAME,
            {"project-scope": {"command": "c"}},
        )

        servers = discover_mcp_servers(
            claude_config_dir=claude_config_dir, project_dir=project_dir,
        )

        assert len(servers) == 1
        assert servers[0].server_name == "project-scope"
        assert servers[0].scope == "project_shared"
        assert servers[0].source_file == project_dir / MCP_PROJECT_FILENAME


class TestResolveProjectDiskPath:
    """Slug → original project-path lookup via ~/.claude.json's
    ``projects`` dict keys. Uses unambiguous forward-encoding
    (abs_path → slug) rather than lossy reverse parsing.
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        _load_json.cache_clear()
        yield
        _load_json.cache_clear()

    def _write_projects_json(
        self, path: Path, project_abs_paths: list[str],
    ) -> None:
        data = {"projects": {p: {"mcpServers": {}} for p in project_abs_paths}}
        path.write_text(json.dumps(data))

    def test_match_returns_original_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        claude_json = tmp_path / ".claude.json"
        self._write_projects_json(
            claude_json,
            ["/home/user/my-project", "/home/user/other"],
        )
        _override_claude_json_location(monkeypatch, claude_json)
        assert resolve_project_disk_path(
            "-home-user-my-project", claude_config_dir=None,
        ) == Path("/home/user/my-project")

    def test_no_match_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        claude_json = tmp_path / ".claude.json"
        self._write_projects_json(claude_json, ["/home/user/project-a"])
        _override_claude_json_location(monkeypatch, claude_json)
        assert resolve_project_disk_path(
            "-home-user-unknown", claude_config_dir=None,
        ) is None

    def test_missing_claude_json_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # No ~/.claude.json at the mocked home.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert resolve_project_disk_path(
            "-home-user-any", claude_config_dir=None,
        ) is None

    def test_non_dict_projects_key_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # ~/.claude.json with projects key as a list (malformed).
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({"projects": ["not", "a", "dict"]}))
        _override_claude_json_location(monkeypatch, claude_json)
        assert resolve_project_disk_path(
            "-home-user-x", claude_config_dir=None,
        ) is None


class TestDefensiveParsing:
    """Covers the parser's guards against malformed input shapes so we
    don't silently accept garbage that will mangle downstream data."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        _load_json.cache_clear()
        yield
        _load_json.cache_clear()

    def test_non_object_json_root_logs_warning_and_returns_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps(["not", "an", "object"]))
        _override_claude_json_location(monkeypatch, claude_json)
        with caplog.at_level(
            logging.WARNING, logger="agentfluent.config.mcp_discovery",
        ):
            servers = discover_mcp_servers(
                claude_config_dir=None, project_dir=None,
            )
        assert servers == []
        assert any("not an object" in rec.message for rec in caplog.records)

    def test_non_dict_server_entry_is_skipped_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Server entry as a string rather than dict — pathological but
        # defensible to skip rather than crash.
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(
            json.dumps({
                "mcpServers": {
                    "valid": {"command": "c"},
                    "broken": "not-a-dict",
                },
            }),
        )
        _override_claude_json_location(monkeypatch, claude_json)
        with caplog.at_level(
            logging.WARNING, logger="agentfluent.config.mcp_discovery",
        ):
            servers = discover_mcp_servers(
                claude_config_dir=None, project_dir=None,
            )
        # Only the valid entry survives; the broken one was skipped.
        assert [s.server_name for s in servers] == ["valid"]
        assert any("broken" in rec.message for rec in caplog.records)

    def test_mcp_project_file_with_non_dict_mcpservers_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # `.mcp.json` present but mcpServers value isn't a dict.
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        claude_json = tmp_path / ".claude.json"
        _write_claude_json(claude_json)
        (project_dir / MCP_PROJECT_FILENAME).write_text(
            json.dumps({"mcpServers": "oops"}),
        )
        _override_claude_json_location(monkeypatch, claude_json)
        servers = discover_mcp_servers(
            claude_config_dir=None, project_dir=project_dir,
        )
        assert servers == []

    def test_mcp_project_file_with_non_dict_server_entry_skipped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        claude_json = tmp_path / ".claude.json"
        _write_claude_json(claude_json)
        (project_dir / MCP_PROJECT_FILENAME).write_text(
            json.dumps({
                "mcpServers": {"ok": {"command": "c"}, "bad": 42},
            }),
        )
        _override_claude_json_location(monkeypatch, claude_json)
        with caplog.at_level(
            logging.WARNING, logger="agentfluent.config.mcp_discovery",
        ):
            servers = discover_mcp_servers(
                claude_config_dir=None, project_dir=project_dir,
            )
        assert [s.server_name for s in servers] == ["ok"]
        assert any("bad" in rec.message for rec in caplog.records)
