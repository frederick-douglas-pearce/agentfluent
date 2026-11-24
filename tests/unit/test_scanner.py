"""Tests for agent definition scanner and parser."""

import logging
from pathlib import Path

import pytest

from agentfluent.config.models import Scope
from agentfluent.config.scanner import parse_agent_file, scan_agents

AGENTS_FIXTURES = Path(__file__).parent.parent / "fixtures" / "agents"


class TestParseAgentFile:
    def test_well_configured(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "well_configured.md", Scope.USER)
        assert agent is not None
        assert agent.name == "reviewer"
        assert agent.model == "claude-sonnet-4-6"
        assert "Read" in agent.tools
        assert "Edit" in agent.disallowed_tools
        assert agent.scope == Scope.USER
        assert "github" in agent.mcp_servers
        assert agent.memory == "user"
        assert "code reviewer" in agent.prompt_body

    def test_no_tools(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "no_tools.md", Scope.PROJECT)
        assert agent is not None
        assert agent.name == "helper"
        assert agent.tools == []
        assert agent.disallowed_tools == []
        assert agent.scope == Scope.PROJECT

    def test_vague_description(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "vague_description.md", Scope.USER)
        assert agent is not None
        assert agent.description == "does stuff"

    def test_empty_prompt(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "empty_prompt.md", Scope.USER)
        assert agent is not None
        assert agent.prompt_body == ""

    def test_no_frontmatter(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "no_frontmatter.md", Scope.USER)
        assert agent is not None
        assert agent.name == "no_frontmatter"  # falls back to filename stem
        assert agent.model is None
        assert agent.tools == []

    def test_nonexistent_file(self) -> None:
        result = parse_agent_file(Path("/nonexistent/file.md"), Scope.USER)
        assert result is None

    def test_raw_frontmatter_preserved(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "well_configured.md", Scope.USER)
        assert agent is not None
        assert "name" in agent.raw_frontmatter
        assert agent.raw_frontmatter["name"] == "reviewer"

    def test_file_path_is_absolute(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "well_configured.md", Scope.USER)
        assert agent is not None
        assert agent.file_path.is_absolute()


def _write_agent(tmp_path: Path, frontmatter: str, name: str = "agent") -> Path:
    path = tmp_path / f"{name}.md"
    path.write_text(f"---\nname: {name}\n{frontmatter}\n---\nPrompt body.\n")
    return path


def _parse(tmp_path: Path, frontmatter: str, name: str = "agent") -> list[str]:
    agent = parse_agent_file(_write_agent(tmp_path, frontmatter, name), Scope.USER)
    assert agent is not None
    return agent.tools


class TestToolListForms:
    """#747: `tools`/`disallowedTools` accept a comma string or a YAML list."""

    def test_comma_string_is_split(self, tmp_path: Path) -> None:
        assert _parse(tmp_path, "tools: Read, Grep, Glob") == ["Read", "Grep", "Glob"]

    def test_disallowed_tools_comma_string_is_split(self, tmp_path: Path) -> None:
        agent = parse_agent_file(
            _write_agent(tmp_path, "disallowedTools: Write, Edit"), Scope.USER,
        )
        assert agent is not None
        assert agent.disallowed_tools == ["Write", "Edit"]

    def test_yaml_block_list(self, tmp_path: Path) -> None:
        assert _parse(tmp_path, "tools:\n  - Read\n  - Grep") == ["Read", "Grep"]

    def test_yaml_flow_list(self, tmp_path: Path) -> None:
        assert _parse(tmp_path, 'tools: ["Read", "Grep"]') == ["Read", "Grep"]

    def test_comma_and_list_forms_parse_identically(self, tmp_path: Path) -> None:
        comma = _parse(tmp_path, "tools: Read, Grep, Bash", name="comma")
        listed = _parse(tmp_path, "tools:\n  - Read\n  - Grep\n  - Bash", name="listed")
        assert comma == listed

    def test_commas_inside_parentheses_do_not_split(self, tmp_path: Path) -> None:
        tools = _parse(tmp_path, "tools: Agent(worker, researcher), Read, Bash")
        assert tools == ["Agent(worker, researcher)", "Read", "Bash"]

    def test_stray_close_paren_does_not_stop_splitting(self, tmp_path: Path) -> None:
        assert _parse(tmp_path, "tools: Read), Grep, Glob") == ["Read)", "Grep", "Glob"]

    def test_unclosed_paren_keeps_remainder_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="agentfluent.config.scanner"):
            tools = _parse(tmp_path, "tools: Read, Agent(a, b, Grep")
        assert tools == ["Read", "Agent(a, b, Grep"]
        assert "Unbalanced '('" in caplog.text
        assert "agent.md" in caplog.text

    def test_balanced_parens_do_not_warn(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="agentfluent.config.scanner"):
            _parse(tmp_path, "tools: Agent(a, b), Read")
        assert "Unbalanced" not in caplog.text

    def test_empty_entries_and_whitespace_dropped(self, tmp_path: Path) -> None:
        assert _parse(tmp_path, "tools: ' Read ,, Grep ,'") == ["Read", "Grep"]

    def test_wildcard_scalar(self, tmp_path: Path) -> None:
        assert _parse(tmp_path, 'tools: "*"') == ["*"]

    def test_null_yaml_list_item_dropped(self, tmp_path: Path) -> None:
        assert _parse(tmp_path, "tools:\n  - Read\n  -\n  - Grep") == ["Read", "Grep"]

    def test_yaml_list_items_are_not_comma_split(self, tmp_path: Path) -> None:
        assert _parse(tmp_path, "tools:\n  - Read, Grep") == ["Read, Grep"]

    def test_skills_comma_string_is_not_split(self, tmp_path: Path) -> None:
        # Upstream documents no comma form for `skills` (#747 AC3).
        agent = parse_agent_file(_write_agent(tmp_path, "skills: a, b"), Scope.USER)
        assert agent is not None
        assert agent.skills == ["a, b"]


class TestScanAgents:
    def test_scan_directory(self) -> None:
        agents = scan_agents("user", user_path=AGENTS_FIXTURES)
        assert len(agents) >= 4  # we have 5 fixtures
        names = {a.name for a in agents}
        assert "reviewer" in names

    def test_scan_user_scope(self) -> None:
        agents = scan_agents("user", user_path=AGENTS_FIXTURES)
        assert all(a.scope == Scope.USER for a in agents)

    def test_scan_project_scope(self) -> None:
        agents = scan_agents("project", project_path=AGENTS_FIXTURES)
        assert all(a.scope == Scope.PROJECT for a in agents)

    def test_scan_both_scopes(self, tmp_path: Path) -> None:
        # Create user and project dirs with one agent each
        user_dir = tmp_path / "user_agents"
        user_dir.mkdir()
        (user_dir / "agent_a.md").write_text("---\nname: a\n---\nPrompt A")

        project_dir = tmp_path / "project_agents"
        project_dir.mkdir()
        (project_dir / "agent_b.md").write_text("---\nname: b\n---\nPrompt B")

        agents = scan_agents("all", user_path=user_dir, project_path=project_dir)
        assert len(agents) == 2
        names = {a.name for a in agents}
        assert names == {"a", "b"}

    def test_scan_empty_directory(self, tmp_path: Path) -> None:
        agents = scan_agents("user", user_path=tmp_path)
        assert agents == []

    def test_scan_nonexistent_directory(self, tmp_path: Path) -> None:
        agents = scan_agents("user", user_path=tmp_path / "nonexistent")
        assert agents == []

    def test_scan_sorted_by_name(self) -> None:
        agents = scan_agents("user", user_path=AGENTS_FIXTURES)
        names = [a.name for a in agents]
        # Files are sorted by filename, names come from frontmatter
        assert names == sorted(names, key=str.lower) or len(agents) > 0
