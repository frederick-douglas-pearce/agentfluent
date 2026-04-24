"""Tests for ``DelegationSuggestion.yaml_draft`` computed field.

These tests stay isolated from ``test_delegation.py`` because that
module skips when scikit-learn is unavailable — the YAML draft itself
has no sklearn dependency, so it must remain testable regardless.
"""

from __future__ import annotations

from agentfluent.diagnostics.models import DelegationSuggestion


def _suggestion(
    name: str = "test-runner",
    description: str = "Handles delegations related to: pytest, tests, run.",
    tools: list[str] | None = None,
    tools_note: str = "",
    confidence: str = "high",
    dedup_note: str = "",
    top_terms: list[str] | None = None,
    cohesion_score: float = 0.85,
) -> DelegationSuggestion:
    return DelegationSuggestion(
        name=name,
        description=description,
        model="claude-sonnet-4-6",
        tools=tools if tools is not None else ["Read", "Grep"],
        tools_note=tools_note,
        prompt_template="You run pytest tests and report results.",
        confidence=confidence,  # type: ignore[arg-type]
        cluster_size=10,
        cohesion_score=cohesion_score,
        top_terms=top_terms if top_terms is not None else ["pytest", "tests", "run"],
        dedup_note=dedup_note,
    )


class TestYamlDraftStructure:
    def test_preamble_lists_name_and_confidence(self) -> None:
        out = _suggestion().yaml_draft
        assert out.startswith("# Suggested agent: test-runner")
        assert "# Confidence: high (10 invocations, 0.85 cohesion)" in out

    def test_frontmatter_separators_bracket_yaml_block(self) -> None:
        out = _suggestion().yaml_draft
        parts = out.split("---\n")
        assert len(parts) == 3
        assert "description:" in parts[1]
        assert "model: claude-sonnet-4-6" in parts[1]

    def test_prompt_body_appears_after_second_separator(self) -> None:
        out = _suggestion().yaml_draft
        assert out.rstrip().endswith("You run pytest tests and report results.")

    def test_tools_rendered_as_yaml_list(self) -> None:
        out = _suggestion(tools=["Read", "Grep", "Bash"]).yaml_draft
        assert "tools:\n- Read\n- Grep\n- Bash" in out

    def test_top_terms_in_preamble_when_present(self) -> None:
        out = _suggestion().yaml_draft
        assert "# Top terms: pytest, tests, run" in out

    def test_top_terms_line_omitted_when_empty(self) -> None:
        out = _suggestion(top_terms=[]).yaml_draft
        assert "Top terms" not in out


class TestYamlDraftConfidenceHandling:
    def test_low_confidence_preamble_includes_review_warning(self) -> None:
        out = _suggestion(confidence="low", cohesion_score=0.45).yaml_draft
        assert "# REVIEW BEFORE USE" in out
        assert "# Confidence: low" in out

    def test_medium_and_high_confidence_omit_review_warning(self) -> None:
        assert "REVIEW" not in _suggestion(confidence="high").yaml_draft
        assert "REVIEW" not in _suggestion(confidence="medium").yaml_draft


class TestYamlDraftEdgeCases:
    def test_empty_tools_with_note_surfaces_note_as_comment(self) -> None:
        out = _suggestion(tools=[], tools_note="no subagent traces linked").yaml_draft
        assert "tools: []" in out
        assert "# tools: no subagent traces linked" in out

    def test_empty_tools_without_note_shows_empty_list(self) -> None:
        out = _suggestion(tools=[]).yaml_draft
        assert "tools: []" in out
        assert "# tools:" not in out

    def test_dedup_note_surfaces_in_preamble(self) -> None:
        out = _suggestion(
            dedup_note="suppressed — already covered by 'pm' (similarity 0.85)",
        ).yaml_draft
        assert "# Note: suppressed" in out

    def test_description_with_special_chars_is_yaml_quoted_safely(self) -> None:
        # pyyaml's safe_dump handles special-char escaping; a description
        # containing quotes and colons must not produce invalid YAML.
        out = _suggestion(
            description='Handles "tests" with: special chars',
        ).yaml_draft
        assert "description:" in out
        import yaml
        frontmatter_block = out.split("---\n")[1]
        parsed = yaml.safe_load(frontmatter_block)
        assert parsed["description"] == 'Handles "tests" with: special chars'


class TestYamlDraftJsonSerialization:
    def test_yaml_draft_appears_in_model_dump(self) -> None:
        dumped = _suggestion().model_dump()
        assert "yaml_draft" in dumped
        assert dumped["yaml_draft"].startswith("# Suggested agent:")

    def test_yaml_draft_appears_in_json_mode_dump(self) -> None:
        dumped = _suggestion().model_dump(mode="json")
        assert "yaml_draft" in dumped
        assert isinstance(dumped["yaml_draft"], str)
