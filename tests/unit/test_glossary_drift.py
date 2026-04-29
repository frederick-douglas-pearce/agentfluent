"""CI drift check: docs/GLOSSARY.md must match the generator output.

If this test fails, run:

    python scripts/generate_glossary_md.py

then commit the regenerated ``docs/GLOSSARY.md``. The structured source
in ``src/agentfluent/glossary/terms.yaml`` is the only place to edit
glossary content.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentfluent.glossary import load_glossary
from agentfluent.glossary.render import generate_markdown

REPO_ROOT = Path(__file__).resolve().parents[2]
GLOSSARY_PATH = REPO_ROOT / "docs" / "GLOSSARY.md"


def test_docs_glossary_matches_generator() -> None:
    """The committed markdown must equal the generator's output byte-for-byte."""
    expected = generate_markdown(load_glossary())
    actual = GLOSSARY_PATH.read_text(encoding="utf-8")
    if expected != actual:
        pytest.fail(
            "docs/GLOSSARY.md is out of date relative to terms.yaml.\n"
            "Run: python scripts/generate_glossary_md.py\n"
            "then commit the regenerated docs/GLOSSARY.md.",
        )
