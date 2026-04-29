"""Pydantic models for glossary entries.

Categories are a closed enum (``Literal``) so a typo in ``terms.yaml``
fails at load time rather than silently misclassifying. The display
labels live alongside the type so renderers don't reinvent them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GlossaryCategory = Literal[
    "token_type",
    "signal_type",
    "severity",
    "confidence_tier",
    "recommendation_target",
    "agent_concern",
    "cluster_metric",
    "model_routing",
    "builtin_agent_type",
    "builtin_tool",
]

# Display order + label for each category. Drives section ordering in the
# generated markdown and the ``--list`` table.
GLOSSARY_CATEGORIES: tuple[tuple[GlossaryCategory, str], ...] = (
    ("token_type", "Token types"),
    ("signal_type", "Signal types"),
    ("severity", "Severity"),
    ("confidence_tier", "Confidence tier"),
    ("recommendation_target", "Recommendation target"),
    ("agent_concern", "Built-in agent concern"),
    ("cluster_metric", "Cluster metrics"),
    ("model_routing", "Model routing"),
    ("builtin_agent_type", "Built-in agent types"),
    ("builtin_tool", "Built-in tools"),
)


class GlossaryEntry(BaseModel):
    """A single glossary term.

    Optional structured fields (``severity_range``, ``recommendation_target``,
    ``threshold``) populate Phase 3 tooltips and JSON consumers without
    re-parsing the long-form prose. ``aliases`` index into the same lookup
    dict as canonical names so fuzzy match treats them as equivalent.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    """Canonical term name as it appears in CLI output (e.g. ``token_outlier``)."""

    category: GlossaryCategory

    short: str
    """One-line definition. Doubles as the Phase 3 inline tooltip."""

    long: str = ""
    """Multi-paragraph detail. Plain prose; markdown is permitted but kept light."""

    example: str = ""
    """A representative snippet of CLI output or YAML for the term."""

    severity_range: str | None = None
    """Severities this signal can produce, e.g. ``warning`` or
    ``warning -> critical``. Only populated for ``signal_type`` entries."""

    recommendation_target: str | None = None
    """Config surface this term maps to, e.g. ``prompt`` or ``tools``.
    Only populated where the mapping is unambiguous."""

    threshold: str | None = None
    """Detection heuristic in human-readable form (e.g. ``2.0x mean``).
    Free-form because thresholds aren't uniformly numeric."""

    aliases: list[str] = Field(default_factory=list)
    """Alternate spellings or abbreviations users might type."""

    related: list[str] = Field(default_factory=list)
    """Other ``name`` values worth cross-referencing. Validated against the
    full term list at load time."""
