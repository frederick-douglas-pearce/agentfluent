"""Markdown render output structure tests."""

from __future__ import annotations

from agentfluent.glossary import load_glossary
from agentfluent.glossary.models import GlossaryEntry
from agentfluent.glossary.render import (
    FOOTER,
    GENERATED_HEADER,
    PREAMBLE,
    generate_markdown,
)


def _entry(name: str, **kw: object) -> GlossaryEntry:
    base: dict[str, object] = {
        "name": name,
        "category": "signal_type",
        "short": "test entry",
    }
    base.update(kw)
    return GlossaryEntry.model_validate(base)


class TestGenerateMarkdown:
    def test_includes_generated_header(self) -> None:
        out = generate_markdown([_entry("a")])
        assert out.startswith(GENERATED_HEADER)

    def test_includes_preamble(self) -> None:
        out = generate_markdown([_entry("a")])
        assert PREAMBLE in out

    def test_includes_footer(self) -> None:
        out = generate_markdown([_entry("a")])
        assert FOOTER in out

    def test_renders_term_heading(self) -> None:
        out = generate_markdown([_entry("token_outlier")])
        assert "### `token_outlier`" in out

    def test_renders_short_field(self) -> None:
        out = generate_markdown([_entry("a", short="hello world")])
        assert "**Short:** hello world" in out

    def test_omits_optional_fields_when_empty(self) -> None:
        out = generate_markdown([_entry("a")])
        assert "**Detail:**" not in out
        assert "**Example:**" not in out
        assert "**Severity:**" not in out
        assert "**Threshold:**" not in out
        assert "**Recommendation target:**" not in out
        assert "**Aliases:**" not in out
        assert "**Related:**" not in out

    def test_renders_severity_when_set(self) -> None:
        out = generate_markdown([_entry("a", severity_range="warning")])
        assert "**Severity:** warning" in out

    def test_renders_threshold_when_set(self) -> None:
        out = generate_markdown([_entry("a", threshold="2.0x")])
        assert "**Threshold:** 2.0x" in out

    def test_renders_recommendation_target(self) -> None:
        out = generate_markdown([_entry("a", recommendation_target="prompt")])
        assert "**Recommendation target:** `prompt`" in out

    def test_renders_aliases(self) -> None:
        out = generate_markdown([_entry("a", aliases=["x", "y"])])
        assert "**Aliases:** `x`, `y`" in out

    def test_renders_related_as_anchor_links(self) -> None:
        out = generate_markdown(
            [_entry("a", related=["b"]), _entry("b")],
        )
        assert "[`b`](#b)" in out

    def test_groups_by_category_in_declaration_order(self) -> None:
        """Token types section must appear before Signal types section."""
        out = generate_markdown(
            [
                _entry("sig", category="signal_type"),
                _entry("tok", category="token_type"),
            ],
        )
        assert out.index("## Token types") < out.index("## Signal types")

    def test_skips_categories_with_no_entries(self) -> None:
        out = generate_markdown([_entry("a", category="signal_type")])
        assert "## Signal types" in out
        assert "## Token types" not in out
        assert "## Severity" not in out


class TestRenderRealGlossary:
    """Smoke-test that the full packaged glossary renders without exception."""

    def test_packaged_glossary_renders(self) -> None:
        out = generate_markdown(load_glossary())
        assert out
        assert "## Token types" in out
        assert "## Signal types" in out
        assert "### `token_outlier`" in out
