"""YAML glossary loader with cross-reference validation.

Reads ``terms.yaml`` from the installed package via ``importlib.resources``
so the loader works in zip-installed and editable layouts alike. Validation
is fail-fast: invalid categories, duplicate names, broken cross-refs, or
schema-extra keys raise at load time rather than silently producing dead
links in CLI output or generated markdown.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from typing import cast

import yaml
from pydantic import TypeAdapter, ValidationError

from agentfluent.glossary.models import GlossaryEntry

_TERMS_RESOURCE = "terms.yaml"

_entry_list_adapter = TypeAdapter(list[GlossaryEntry])


class GlossaryError(ValueError):
    """Raised when ``terms.yaml`` fails schema or cross-reference validation."""


def load_glossary() -> list[GlossaryEntry]:
    """Return all glossary entries in declaration order.

    Cached after the first call so CLI subcommands and the markdown
    generator can call freely without re-parsing YAML.
    """
    return list(_load_cached())


@lru_cache(maxsize=1)
def _load_cached() -> tuple[GlossaryEntry, ...]:
    raw_text = files("agentfluent.glossary").joinpath(_TERMS_RESOURCE).read_text(
        encoding="utf-8",
    )
    raw = yaml.safe_load(raw_text)
    if not isinstance(raw, list):
        msg = f"{_TERMS_RESOURCE} must contain a YAML list at the top level"
        raise GlossaryError(msg)

    try:
        entries = _entry_list_adapter.validate_python(raw)
    except ValidationError as e:
        msg = f"{_TERMS_RESOURCE} failed schema validation:\n{e}"
        raise GlossaryError(msg) from e

    _check_unique_names(entries)
    _check_unique_aliases(entries)
    _check_cross_references(entries)
    return tuple(entries)


def reset_cache() -> None:
    """Clear the load cache. Tests use this to force a re-read."""
    _load_cached.cache_clear()


def _check_unique_names(entries: list[GlossaryEntry]) -> None:
    seen: dict[str, int] = {}
    for i, entry in enumerate(entries):
        if entry.name in seen:
            msg = (
                f"Duplicate glossary term {entry.name!r} "
                f"(positions {seen[entry.name]} and {i})"
            )
            raise GlossaryError(msg)
        seen[entry.name] = i


def _check_unique_aliases(entries: list[GlossaryEntry]) -> None:
    """Aliases must not collide with canonical names or other aliases.

    Otherwise ``agentfluent explain <alias>`` becomes ambiguous.
    """
    canonical = {e.name for e in entries}
    alias_owner: dict[str, str] = {}
    for entry in entries:
        for alias in entry.aliases:
            if alias in canonical:
                msg = (
                    f"Alias {alias!r} on term {entry.name!r} collides with "
                    f"canonical name {alias!r}"
                )
                raise GlossaryError(msg)
            if alias in alias_owner:
                msg = (
                    f"Alias {alias!r} declared on both {alias_owner[alias]!r} "
                    f"and {entry.name!r}"
                )
                raise GlossaryError(msg)
            alias_owner[alias] = entry.name


def _check_cross_references(entries: list[GlossaryEntry]) -> None:
    canonical = {e.name for e in entries}
    for entry in entries:
        for ref in entry.related:
            if ref not in canonical:
                msg = (
                    f"Term {entry.name!r} references unknown term "
                    f"{ref!r} in 'related'"
                )
                raise GlossaryError(msg)


def find_term(query: str, entries: list[GlossaryEntry]) -> GlossaryEntry | None:
    """Look up a term by exact canonical name or exact alias."""
    for entry in entries:
        if entry.name == query or query in entry.aliases:
            return entry
    return None


def fuzzy_match(query: str, entries: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """Return candidates matching ``query`` after normalization.

    Strategy (architect-approved): underscore/hyphen/case normalization
    plus substring match. Trailing ``s`` is stripped to handle plurals.
    Linear scan -- the term list is small enough that ``rapidfuzz`` is
    over-engineering. Returns canonical entries deduped by ``name``.
    """
    needle = _normalize(query)
    if not needle:
        return []
    matches: dict[str, GlossaryEntry] = {}
    for entry in entries:
        haystack = [_normalize(entry.name)] + [_normalize(a) for a in entry.aliases]
        if any(needle in candidate for candidate in haystack):
            matches.setdefault(entry.name, entry)
    return list(matches.values())


def _normalize(s: str) -> str:
    """Lowercase, collapse separators, drop a trailing ``s`` for plurals."""
    out = s.lower().replace("-", "").replace("_", "").replace(" ", "")
    if len(out) > 1 and out.endswith("s"):
        out = out[:-1]
    return out


def categories_in_use(entries: list[GlossaryEntry]) -> list[str]:
    """Return the categories actually represented in ``entries``."""
    seen: set[str] = set()
    out: list[str] = []
    for entry in entries:
        if entry.category not in seen:
            seen.add(entry.category)
            out.append(cast(str, entry.category))
    return out
