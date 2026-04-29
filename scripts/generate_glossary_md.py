#!/usr/bin/env python3
"""Regenerate docs/GLOSSARY.md from src/agentfluent/glossary/terms.yaml.

The committed markdown is the rendered output of this script. The drift
test in tests/unit/glossary/test_drift.py compares the file on disk
against ``generate_markdown(load_glossary())`` and fails CI if they
diverge -- so re-run this any time you edit ``terms.yaml``.
"""

from __future__ import annotations

from pathlib import Path

from agentfluent.glossary import load_glossary
from agentfluent.glossary.render import generate_markdown

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "docs" / "GLOSSARY.md"


def main() -> None:
    rendered = generate_markdown(load_glossary())
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(REPO_ROOT)} ({len(rendered)} bytes)")


if __name__ == "__main__":
    main()
