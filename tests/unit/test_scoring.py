"""Tests for config scoring rubric."""

from pathlib import Path

from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.config.scanner import parse_agent_file
from agentfluent.config.scoring import score_agent

AGENTS_FIXTURES = Path(__file__).parent.parent / "fixtures" / "agents"


def _make_agent(**kwargs: object) -> AgentConfig:
    """Helper to create an AgentConfig with defaults."""
    defaults: dict[str, object] = {
        "name": "test-agent",
        "file_path": Path("/tmp/test.md"),
        "scope": Scope.USER,
    }
    defaults.update(kwargs)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


class TestScoreWellConfigured:
    def test_high_overall_score(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "well_configured.md", Scope.USER)
        assert agent is not None
        score = score_agent(agent)
        assert score.overall_score >= 90

    def test_all_dimensions_scored(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "well_configured.md", Scope.USER)
        assert agent is not None
        score = score_agent(agent)
        assert "description" in score.dimension_scores
        assert "tool_restrictions" in score.dimension_scores
        assert "model_selection" in score.dimension_scores
        assert "prompt_body" in score.dimension_scores

    def test_few_recommendations(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "well_configured.md", Scope.USER)
        assert agent is not None
        score = score_agent(agent)
        assert len(score.recommendations) <= 1


class TestDescriptionScoring:
    def test_no_description(self) -> None:
        agent = _make_agent(description="")
        score = score_agent(agent)
        assert score.dimension_scores["description"] == 0
        critical = [r for r in score.recommendations if r.severity == Severity.CRITICAL]
        assert any("no description" in r.message.lower() for r in critical)

    def test_short_description(self) -> None:
        agent = _make_agent(description="A helper")
        score = score_agent(agent)
        assert score.dimension_scores["description"] < 25

    def test_good_description(self) -> None:
        agent = _make_agent(
            description=(
                "Invoke when reviewing pull requests for code quality. "
                "Do NOT use for implementation tasks."
            ),
        )
        score = score_agent(agent)
        assert score.dimension_scores["description"] == 25

    def test_action_verbs_detected(self) -> None:
        agent = _make_agent(
            description="This agent helps analyze and review code changes in detail.",
        )
        score = score_agent(agent)
        # Should get action verbs points
        assert score.dimension_scores["description"] >= 15


class TestToolRestrictionScoring:
    def test_no_restrictions(self) -> None:
        agent = _make_agent()
        score = score_agent(agent)
        assert score.dimension_scores["tool_restrictions"] == 0
        critical = [r for r in score.recommendations if r.severity == Severity.CRITICAL]
        assert any("no tool restrictions" in r.message.lower() for r in critical)

    def test_allowlist_only(self) -> None:
        agent = _make_agent(tools=["Read", "Grep"])
        score = score_agent(agent)
        assert score.dimension_scores["tool_restrictions"] >= 15

    def test_denylist_only(self) -> None:
        agent = _make_agent(disallowed_tools=["Bash", "Edit"])
        score = score_agent(agent)
        assert score.dimension_scores["tool_restrictions"] >= 10

    def test_both_lists(self) -> None:
        agent = _make_agent(tools=["Read"], disallowed_tools=["Bash"])
        score = score_agent(agent)
        assert score.dimension_scores["tool_restrictions"] >= 20

    def test_mcp_servers_bonus(self) -> None:
        agent = _make_agent(
            tools=["Read"], disallowed_tools=["Bash"], mcp_servers=["github"],
        )
        score = score_agent(agent)
        assert score.dimension_scores["tool_restrictions"] == 25


class TestModelSelectionScoring:
    def test_no_model(self) -> None:
        agent = _make_agent()
        score = score_agent(agent)
        assert score.dimension_scores["model_selection"] == 0

    def test_model_specified(self) -> None:
        agent = _make_agent(model="claude-sonnet-4-6")
        score = score_agent(agent)
        assert score.dimension_scores["model_selection"] == 25

    def test_expensive_model_read_only_tools(self) -> None:
        agent = _make_agent(
            model="claude-opus-4-6",
            tools=["Read", "Glob", "Grep"],
        )
        score = score_agent(agent)
        # Should still score but get a recommendation
        assert score.dimension_scores["model_selection"] < 25
        recs = [r for r in score.recommendations if r.dimension == "model_selection"]
        assert len(recs) == 1

    def test_expensive_model_with_write_tools(self) -> None:
        # Opus with write tools is fine -- complex task
        agent = _make_agent(
            model="claude-opus-4-6",
            tools=["Read", "Edit", "Bash"],
        )
        score = score_agent(agent)
        assert score.dimension_scores["model_selection"] == 25


class TestPromptBodyScoring:
    def test_no_prompt(self) -> None:
        agent = _make_agent(prompt_body="")
        score = score_agent(agent)
        assert score.dimension_scores["prompt_body"] == 0
        critical = [r for r in score.recommendations if r.severity == Severity.CRITICAL]
        assert any("no prompt body" in r.message.lower() for r in critical)

    def test_short_prompt(self) -> None:
        agent = _make_agent(prompt_body="Be helpful.")
        score = score_agent(agent)
        assert score.dimension_scores["prompt_body"] < 15

    def test_full_prompt(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "well_configured.md", Scope.USER)
        assert agent is not None
        score = score_agent(agent)
        assert score.dimension_scores["prompt_body"] == 25

    def test_prompt_without_sections(self) -> None:
        long_text = "This is a prompt body. " * 20
        agent = _make_agent(prompt_body=long_text)
        score = score_agent(agent)
        # Gets length points but not section points
        info = [r for r in score.recommendations if r.dimension == "prompt_body"]
        assert any("sections" in r.message.lower() for r in info)


class TestOverallScore:
    def test_sum_of_dimensions(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "well_configured.md", Scope.USER)
        assert agent is not None
        score = score_agent(agent)
        expected = sum(score.dimension_scores.values())
        assert score.overall_score == expected

    def test_max_score_100(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "well_configured.md", Scope.USER)
        assert agent is not None
        score = score_agent(agent)
        assert score.overall_score <= 100

    def test_min_score_0(self) -> None:
        agent = _make_agent()
        score = score_agent(agent)
        assert score.overall_score >= 0


class TestFixtureScoring:
    def test_no_tools_agent(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "no_tools.md", Scope.USER)
        assert agent is not None
        score = score_agent(agent)
        assert score.dimension_scores["tool_restrictions"] == 0

    def test_vague_description_agent(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "vague_description.md", Scope.USER)
        assert agent is not None
        score = score_agent(agent)
        assert score.dimension_scores["description"] < 15

    def test_empty_prompt_agent(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "empty_prompt.md", Scope.USER)
        assert agent is not None
        score = score_agent(agent)
        assert score.dimension_scores["prompt_body"] == 0

    def test_no_frontmatter_agent(self) -> None:
        agent = parse_agent_file(AGENTS_FIXTURES / "no_frontmatter.md", Scope.USER)
        assert agent is not None
        score = score_agent(agent)
        assert score.dimension_scores["model_selection"] == 0
