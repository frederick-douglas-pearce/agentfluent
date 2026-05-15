"""Markdown generator for the glossary.

The generator is the single producer of ``docs/GLOSSARY.md``. The drift
test calls :func:`generate_markdown` and compares it byte-for-byte against
the committed file -- if they diverge, CI fails with instructions to
re-run ``scripts/generate_glossary_md.py``.

The preamble and footer are constants here, not hand-edited in the
markdown, so the YAML stays the only place anyone has to touch.
"""

from __future__ import annotations

from agentfluent.glossary.models import (
    GLOSSARY_CATEGORIES,
    GlossaryCategory,
    GlossaryEntry,
)

GENERATED_HEADER = (
    "<!-- Generated from src/agentfluent/glossary/terms.yaml. "
    "Do not edit by hand -- run `python scripts/generate_glossary_md.py`. -->\n"
)

PREAMBLE = """\
# AgentFluent Glossary

A reference for the vocabulary that appears in `agentfluent` CLI output and
JSON. Every term used in `analyze --diagnostics` and `config-check` output
is defined here.

For terminal-native lookup, use `agentfluent explain <term>` instead of
opening this file.

## Reading guide

`agentfluent` reports on three layers of agent behavior, with command
reference material alongside them:

1. **Execution analytics** -- what happened: token counts, tool calls, costs.
   Mostly familiar Anthropic API vocabulary, plus a few AgentFluent-specific
   rollups (e.g., `cache_efficiency`). Skim the **Token types** section if
   you've worked with the Anthropic API before; otherwise read it through.

2. **Behavior diagnostics** -- why something looks off: signal types, severity,
   confidence. This is AgentFluent-invented vocabulary with no analogue in
   external Claude Code documentation. **Start here if you're a first-time
   user** -- the **Signal types**, **Severity**, and **Confidence tier**
   sections together give you the mental model needed to act on
   `--diagnostics` output.

3. **Recommendations** -- what to change: targets, agent concerns,
   model-routing tiers. These map signals to specific configuration
   surfaces. The **Recommendation target** and **Built-in agent concern**
   sections explain why two findings on the same agent might suggest
   different fixes.

4. **CLI commands** -- what each `agentfluent` subcommand does and when
   to use it. Start here if you want a quick overview of available
   commands before diving into the vocabulary they produce.
"""

FOOTER = """\
## See also

- README: [How It Works](../README.md#how-it-works) -- pipeline architecture
- README: [Privacy and Security](../README.md#privacy-and-security)
- [`CLAUDE.md`](../CLAUDE.md) -- project conventions and JSONL data format reference
- Anthropic API docs: [Pricing](https://platform.claude.com/docs/en/about-claude/pricing), [Prompt caching](https://docs.claude.com/en/docs/build-with-claude/prompt-caching)
"""


def generate_markdown(entries: list[GlossaryEntry]) -> str:
    """Render the full glossary markdown for ``entries``.

    Order: header comment, hand-written preamble, one section per category
    (in the order declared in ``GLOSSARY_CATEGORIES``), footer. Categories
    with no entries are skipped silently.
    """
    by_category = _group_by_category(entries)
    parts: list[str] = [GENERATED_HEADER, PREAMBLE]
    for category, label in GLOSSARY_CATEGORIES:
        section_entries = by_category.get(category, [])
        if not section_entries:
            continue
        # Blank line before `---` keeps it a horizontal rule rather than a
        # setext H2 underline for the preceding paragraph.
        parts.append(f"\n---\n\n## {label}\n\n")
        for entry in section_entries:
            parts.append(_render_entry(entry))
    parts.append("---\n\n")
    parts.append(FOOTER)
    return "".join(parts)


def _group_by_category(
    entries: list[GlossaryEntry],
) -> dict[GlossaryCategory, list[GlossaryEntry]]:
    grouped: dict[GlossaryCategory, list[GlossaryEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.category, []).append(entry)
    return grouped


def _render_entry(entry: GlossaryEntry) -> str:
    """Render a single term as markdown.

    Required fields (``name``, ``short``) always emit. Optional fields
    only emit when populated -- empty fields produce no labeled stub.
    Output ends with a single trailing blank line so consecutive entries
    don't run together.
    """
    blocks: list[str] = [f"### `{entry.name}`", f"**Short:** {entry.short.rstrip()}"]
    if entry.long:
        blocks.append(f"**Detail:** {entry.long.rstrip()}")
    if entry.example:
        blocks.append("**Example:**\n\n```\n" + entry.example.rstrip() + "\n```")
    if entry.severity_range:
        blocks.append(f"**Severity:** {entry.severity_range}")
    if entry.threshold:
        blocks.append(f"**Threshold:** {entry.threshold}")
    if entry.recommendation_target:
        blocks.append(
            f"**Recommendation target:** `{entry.recommendation_target}`",
        )
    if entry.aliases:
        alias_str = ", ".join(f"`{a}`" for a in entry.aliases)
        blocks.append(f"**Aliases:** {alias_str}")
    if entry.related:
        related_str = ", ".join(f"[`{r}`](#{_anchor(r)})" for r in entry.related)
        blocks.append(f"**Related:** {related_str}")
    return "\n\n".join(blocks) + "\n\n"


def _anchor(name: str) -> str:
    """GitHub-flavored anchor slug for a backtick-wrapped term heading.

    GitHub strips backticks but keeps the inner text; we mirror that.
    """
    return name.lower()
