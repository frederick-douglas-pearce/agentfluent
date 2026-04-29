"""Schema and cross-reference validation for the glossary loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentfluent.glossary import load_glossary
from agentfluent.glossary.loader import (
    GlossaryError,
    _check_cross_references,
    _check_unique_aliases,
    _check_unique_names,
    find_term,
    fuzzy_match,
    reset_cache,
)
from agentfluent.glossary.models import GlossaryEntry


def _entry(name: str, **kw: object) -> GlossaryEntry:
    """Build a GlossaryEntry with sensible defaults for tests."""
    base: dict[str, object] = {
        "name": name,
        "category": "signal_type",
        "short": "test entry",
    }
    base.update(kw)
    return GlossaryEntry.model_validate(base)


class TestPackagedGlossaryLoads:
    """The packaged terms.yaml must satisfy every validation rule."""

    def setup_method(self) -> None:
        reset_cache()

    def test_load_returns_entries(self) -> None:
        entries = load_glossary()
        assert len(entries) > 0

    def test_every_entry_is_pydantic_model(self) -> None:
        for entry in load_glossary():
            assert isinstance(entry, GlossaryEntry)

    def test_no_unknown_categories(self) -> None:
        from agentfluent.glossary.models import GLOSSARY_CATEGORIES

        valid = {cat for cat, _ in GLOSSARY_CATEGORIES}
        for entry in load_glossary():
            assert entry.category in valid

    def test_every_entry_has_short(self) -> None:
        for entry in load_glossary():
            assert entry.short.strip(), f"{entry.name} missing short"


class TestSchemaValidation:
    """Pydantic-level validation runs at load time."""

    def test_unknown_category_raises(self) -> None:
        with pytest.raises(ValidationError):
            GlossaryEntry.model_validate(
                {"name": "x", "category": "not_a_category", "short": "..."},
            )

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GlossaryEntry.model_validate(
                {
                    "name": "x",
                    "category": "signal_type",
                    "short": "...",
                    "bogus_field": "value",
                },
            )

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            GlossaryEntry.model_validate({"name": "x", "category": "signal_type"})


class TestUniqueNames:
    def test_duplicate_name_raises(self) -> None:
        entries = [_entry("dup"), _entry("dup")]
        with pytest.raises(GlossaryError, match="Duplicate"):
            _check_unique_names(entries)

    def test_unique_names_pass(self) -> None:
        entries = [_entry("a"), _entry("b")]
        _check_unique_names(entries)


class TestUniqueAliases:
    def test_alias_collides_with_canonical(self) -> None:
        entries = [_entry("a"), _entry("b", aliases=["a"])]
        with pytest.raises(GlossaryError, match="collides with canonical"):
            _check_unique_aliases(entries)

    def test_alias_declared_twice(self) -> None:
        entries = [_entry("a", aliases=["x"]), _entry("b", aliases=["x"])]
        with pytest.raises(GlossaryError, match="declared on both"):
            _check_unique_aliases(entries)

    def test_unique_aliases_pass(self) -> None:
        entries = [_entry("a", aliases=["x"]), _entry("b", aliases=["y"])]
        _check_unique_aliases(entries)


class TestCrossReferences:
    def test_unknown_related_term_raises(self) -> None:
        entries = [_entry("a", related=["nonexistent"])]
        with pytest.raises(GlossaryError, match="unknown term"):
            _check_cross_references(entries)

    def test_valid_cross_ref_passes(self) -> None:
        entries = [_entry("a", related=["b"]), _entry("b")]
        _check_cross_references(entries)


def test_loader_rejects_top_level_non_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A YAML scalar at top level must raise GlossaryError."""
    bogus = tmp_path / "bad.yaml"
    bogus.write_text("just_a_string\n")
    _patch_resource(monkeypatch, bogus)
    reset_cache()
    with pytest.raises(GlossaryError, match="must contain a YAML list"):
        load_glossary()


def test_loader_surfaces_pydantic_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A schema-violating entry must raise GlossaryError, not ValidationError."""
    bogus = tmp_path / "bad.yaml"
    bogus.write_text(
        textwrap.dedent(
            """\
            - name: x
              category: not_a_real_category
              short: "..."
            """,
        ),
    )
    _patch_resource(monkeypatch, bogus)
    reset_cache()
    with pytest.raises(GlossaryError, match="failed schema validation"):
        load_glossary()


def _patch_resource(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Make ``importlib.resources.files`` for the glossary point at ``path``.

    The loader reads via
    ``files('agentfluent.glossary').joinpath('terms.yaml').read_text()``;
    we shim the joined ``Traversable`` with a real Path so the read
    returns ``path``'s contents.
    """
    text = path.read_text(encoding="utf-8")
    yaml.safe_load(text)  # surface yaml syntax errors before patching

    class _FakeTraversable:
        def __init__(self, content: str) -> None:
            self.content = content

        def read_text(self, encoding: str = "utf-8") -> str:
            return self.content

    class _FakeAnchor:
        def __init__(self, content: str) -> None:
            self.content = content

        def joinpath(self, _name: str) -> _FakeTraversable:
            return _FakeTraversable(self.content)

    def fake_files(_anchor: str) -> _FakeAnchor:
        return _FakeAnchor(text)

    monkeypatch.setattr("agentfluent.glossary.loader.files", fake_files)


class TestFindTerm:
    def test_exact_canonical(self) -> None:
        entries = [_entry("alpha"), _entry("beta")]
        assert find_term("alpha", entries) is entries[0]

    def test_exact_alias(self) -> None:
        entries = [_entry("alpha", aliases=["a", "first"])]
        assert find_term("first", entries) is entries[0]

    def test_no_match(self) -> None:
        entries = [_entry("alpha")]
        assert find_term("zzz", entries) is None


class TestFuzzyMatch:
    def test_substring(self) -> None:
        entries = [_entry("token_outlier"), _entry("duration_outlier")]
        result = fuzzy_match("outlier", entries)
        assert {e.name for e in result} == {"token_outlier", "duration_outlier"}

    def test_normalization_underscore_to_hyphen(self) -> None:
        entries = [_entry("token_outlier")]
        assert fuzzy_match("token-outlier", entries)[0].name == "token_outlier"

    def test_normalization_case_insensitive(self) -> None:
        entries = [_entry("token_outlier")]
        assert fuzzy_match("TOKEN_OUTLIER", entries)[0].name == "token_outlier"

    def test_plural_strip(self) -> None:
        entries = [_entry("token_outlier")]
        assert fuzzy_match("outliers", entries)[0].name == "token_outlier"

    def test_alias_indexed(self) -> None:
        entries = [_entry("target_prompt", aliases=["prompt"])]
        assert fuzzy_match("prompt", entries)[0].name == "target_prompt"

    def test_empty_query(self) -> None:
        entries = [_entry("alpha")]
        assert fuzzy_match("", entries) == []

    def test_no_match(self) -> None:
        entries = [_entry("alpha")]
        assert fuzzy_match("zzz", entries) == []
