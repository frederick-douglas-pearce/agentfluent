"""Structured glossary source for AgentFluent vocabulary.

The glossary feeds three consumers:

1. ``agentfluent explain <term>`` -- terminal-native term lookup.
2. ``scripts/generate_glossary_md.py`` -- regenerates ``docs/GLOSSARY.md``
   from the structured source so the markdown can't drift.
3. Future hosted documentation (issue #97) and inline tooltips (Phase 3).

The single source of truth is :data:`agentfluent.glossary.terms.yaml`;
all other artifacts are derived from it.
"""

from agentfluent.glossary.loader import load_glossary
from agentfluent.glossary.models import (
    GLOSSARY_CATEGORIES,
    GlossaryCategory,
    GlossaryEntry,
)

__all__ = [
    "GLOSSARY_CATEGORIES",
    "GlossaryCategory",
    "GlossaryEntry",
    "load_glossary",
]
