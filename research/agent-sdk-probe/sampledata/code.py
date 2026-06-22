"""Synthetic module so Glob/Grep have a .py target. Not imported anywhere."""


def summarize(rows: list[dict[str, str]]) -> str:
    """Return a one-line summary of sample rows. MARKER: grep target."""
    tools = sorted({r["tool"] for r in rows})
    return f"{len(rows)} rows across tools: {', '.join(tools)}"
