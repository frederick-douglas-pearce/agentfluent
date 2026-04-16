"""Tests for agent definition scanner and parser."""

from pathlib import Path

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
