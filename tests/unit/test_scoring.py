"""Tests for config scoring rubric."""

import subprocess
import sys
from pathlib import Path

import pytest

from agentfluent.config import scoring
from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.config.scanner import parse_agent_file
from agentfluent.config.scoring import score_agent
from agentfluent.diagnostics import _complexity

AGENTS_FIXTURES = Path(__file__).parent.parent / "fixtures" / "agents"

# #680: one id per `complex` family (Opus, Fable, Mythos), undated + dated, and
# the bare alias. Flagged by default; unflagged once the tier no longer holds them.
COMPLEX_TIER_MODELS: tuple[str, ...] = (
    "claude-opus-4-7",
    "claude-opus-5-5",
    "claude-opus-4-5-20251101",
    "claude-fable-5-2",
    "claude-mythos-5-2",
    "opus",
)


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

    # #680: "expensive" is the `complex` tier, not a hand-kept id list.
    @pytest.mark.parametrize("model", COMPLEX_TIER_MODELS)
    def test_complex_tier_model_read_only_is_flagged(self, model: str) -> None:
        agent = _make_agent(model=model, tools=["Read", "Glob", "Grep"])
        score = score_agent(agent)
        assert score.dimension_scores["model_selection"] == 15
        recs = [r for r in score.recommendations if r.dimension == "model_selection"]
        assert len(recs) == 1
        assert recs[0].severity is Severity.INFO
        assert recs[0].current_value == model

    @pytest.mark.parametrize(
        "model",
        [
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            "sonnet",
            "inherit",
            "claude-experimental-9000",
            # #745 gap: bare `[1m]` aliases do not resolve yet; flips when #745 lands.
            "opus[1m]",
        ],
    )
    def test_non_complex_model_read_only_is_not_flagged(self, model: str) -> None:
        agent = _make_agent(model=model, tools=["Read", "Glob", "Grep"])
        score = score_agent(agent)
        assert score.dimension_scores["model_selection"] == 25
        assert not [r for r in score.recommendations if r.dimension == "model_selection"]

    def test_complex_model_with_mcp_tool_is_not_flagged(self) -> None:
        agent = _make_agent(
            model="claude-opus-5-5",
            tools=["Read", "Grep", "mcp__github__get_issue"],
        )
        score = score_agent(agent)
        assert score.dimension_scores["model_selection"] == 25

    def test_complex_model_with_no_tools_is_not_flagged(self) -> None:
        agent = _make_agent(model="claude-opus-5-5", tools=[])
        score = score_agent(agent)
        assert score.dimension_scores["model_selection"] == 25

    def test_gate_reads_the_tier_map(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Mechanism pin (#680 AC4): a family added to the `complex` tier is flagged
        # with no edit in config/scoring.py. Fails against a hard-coded list there;
        # `test_gate_reads_only_the_tier_map` covers a list kept beside the tier.
        agent = _make_agent(model="claude-testfam-1", tools=["Read"])
        assert score_agent(agent).dimension_scores["model_selection"] == 25

        complex_prefixes = _complexity._TIER_BY_FAMILY["complex"]
        monkeypatch.setitem(
            _complexity._TIER_BY_FAMILY, "complex", (*complex_prefixes, "claude-testfam"),
        )
        score = score_agent(agent)
        assert score.dimension_scores["model_selection"] == 15

    def test_gate_reads_only_the_tier_map(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # #680 AC1: the tier is the only source. With every real family removed from
        # `complex`, none of COMPLEX_TIER_MODELS (nor the old list's claude-opus-4-6) is
        # flagged -- fails if an id list or a family-prefix check covering any of them
        # (Opus, Fable, Mythos, bare `opus`) is kept alongside the classifier.
        assert not hasattr(scoring, "EXPENSIVE_MODELS")
        monkeypatch.setitem(_complexity._TIER_BY_FAMILY, "complex", ("claude-testfam",))
        for model in (*COMPLEX_TIER_MODELS, "claude-opus-4-6"):
            score = score_agent(_make_agent(model=model, tools=["Read"]))
            assert score.dimension_scores["model_selection"] == 25
            assert not [
                r for r in score.recommendations if r.dimension == "model_selection"
            ]

    def test_importing_scoring_does_not_load_diagnostics(self) -> None:
        # #680: the tier classifier is imported inside the function, so the config
        # package does not pull in the diagnostics pipeline (which imports config).
        code = (
            "import sys; import agentfluent.config.scoring; "
            "sys.exit(3 if 'agentfluent.diagnostics' in sys.modules else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr or "agentfluent.diagnostics was loaded"


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
