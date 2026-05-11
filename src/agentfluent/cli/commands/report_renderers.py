"""Markdown section renderers for ``agentfluent report`` (#354).

Each ``render_*`` function takes the envelope's ``data`` payload (a JSON-
deserialized dict produced by ``AnalysisResult.model_dump(mode="json")``)
and returns a Markdown string for one section, in the D030 order:
Summary -> Token Metrics -> Agent Metrics -> Diagnostics -> Offload ->
Footer. ``report.py`` composes them.

Sharp edge: ``data`` is a dict, not a hydrated ``AnalysisResult``. The
``@property`` accessors that exist on the source dataclasses
(``TokenMetrics.total_tokens``, ``ModelTokenBreakdown.total_tokens``,
``AgentTypeMetrics.avg_tokens_per_invocation``) are NOT serialized by
``model_dump``. Renderers derive those values explicitly via the
``_total_tokens`` / ``_avg`` helpers below — reaching for
``row["total_tokens"]`` would silently get ``None`` / ``KeyError``.

Empty / fallback behavior, per the #354 acceptance criteria:
- ``render_agent_metrics``: prints "No agent invocations." when none.
- ``render_diagnostics``: prints "No findings." when no recommendations.
- ``render_offload``: returns ``""`` (section absent) when no
  positive-savings candidates — matches the issue's "if present" spec.
- ``render_footer``: always emits a reproduction command so the report
  is readable as a standalone document.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

UNKNOWN_PROJECT = "(unknown project)"
GLOBAL_AGENT_LABEL = "(global)"

DEFAULT_TOP_N = 5

SEVERITY_ORDER = ("critical", "warning", "info")
SEVERITY_HEADINGS = {
    "critical": "Critical",
    "warning": "Warning",
    "info": "Info",
}


def _total_tokens(row: dict[str, Any]) -> int:
    """Sum the four token components on a token-metrics dict/row.

    ``TokenMetrics.total_tokens`` and ``ModelTokenBreakdown.total_tokens``
    are ``@property`` accessors that pydantic's ``model_dump`` does not
    serialize. Renderers derive the total here so the report's totals
    column matches what the CLI table prints.
    """
    return (
        int(row.get("input_tokens", 0))
        + int(row.get("output_tokens", 0))
        + int(row.get("cache_creation_input_tokens", 0))
        + int(row.get("cache_read_input_tokens", 0))
    )


def _avg_tokens_per_invocation(row: dict[str, Any]) -> float | None:
    """Mirror ``AgentTypeMetrics.avg_tokens_per_invocation`` (a property
    not serialized by ``model_dump``). ``None`` when there's nothing to
    average over, matching the property's contract."""
    count = int(row.get("invocation_count", 0))
    tokens = int(row.get("total_tokens", 0))
    if count > 0 and tokens > 0:
        return tokens / count
    return None


def _fmt_cost(cost: float) -> str:
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def _fmt_tokens(tokens: int) -> str:
    return f"{tokens:,}"


def _fmt_duration_seconds(duration_ms: int) -> str:
    if duration_ms <= 0:
        return "—"
    return f"{duration_ms / 1000:.1f}s"


def _md_table(
    headers: list[str],
    rows: list[list[str]],
    align: list[str],
) -> str:
    """Render a GitHub-flavored Markdown table.

    ``align`` is a parallel list of one-char codes: ``l`` (left), ``r``
    (right), ``c`` (center). Numeric columns get ``r`` so the report
    matches the CLI table's right-aligned cost/token cells.
    """
    sep_map = {"l": ":---", "r": "---:", "c": ":---:"}
    sep = [sep_map[a] for a in align]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _format_window(window: dict[str, Any] | None) -> str:
    """Human-readable window line for the summary bullet.

    Returns ``"all sessions"`` when no time filter was applied, otherwise
    ``"<since> -> <until> (N of M sessions)"`` using the resolved UTC
    timestamps already on ``WindowMetadata``.
    """
    if not window:
        return "all sessions"
    since = window.get("since") or "—"
    until = window.get("until") or "—"
    before = window.get("session_count_before_filter")
    after = window.get("session_count_after_filter")
    if before is not None and after is not None:
        return f"{since} → {until} ({after} of {before} sessions)"
    return f"{since} → {until}"


def render_summary(data: dict[str, Any]) -> str:
    project = data.get("project_name") or UNKNOWN_PROJECT
    session_count = data.get("session_count", 0)
    tm = data.get("token_metrics") or {}
    diag_version = data.get("diagnostics_version")

    total_tokens = _total_tokens(tm)
    total_cost = float(tm.get("total_cost", 0.0))
    input_tokens = int(tm.get("input_tokens", 0))
    output_tokens = int(tm.get("output_tokens", 0))
    cache_creation = int(tm.get("cache_creation_input_tokens", 0))
    cache_read = int(tm.get("cache_read_input_tokens", 0))

    bullets = [
        f"- **Project:** {project}",
        f"- **Sessions analyzed:** {session_count}",
        f"- **Window:** {_format_window(data.get('window'))}",
        f"- **Total cost (API rate):** {_fmt_cost(total_cost)}",
        (
            f"- **Total tokens:** {_fmt_tokens(total_tokens)} "
            f"(input {_fmt_tokens(input_tokens)}, "
            f"output {_fmt_tokens(output_tokens)}, "
            f"cache creation {_fmt_tokens(cache_creation)}, "
            f"cache read {_fmt_tokens(cache_read)})"
        ),
    ]
    if diag_version:
        bullets.append(f"- **AgentFluent version:** {diag_version}")

    return "## Summary\n\n" + "\n".join(bullets) + "\n"


def render_token_metrics(data: dict[str, Any]) -> str:
    tm = data.get("token_metrics") or {}
    by_model = tm.get("by_model") or []

    if not by_model:
        return "## Token Metrics\n\nNo token usage recorded.\n"

    # Match the CLI table sort: (model, parent-first). origin is a
    # Literal["parent", "subagent"] so the secondary key is a simple
    # 0/1 — same logic as ``format_analysis_table``.
    sorted_rows = sorted(
        by_model,
        key=lambda b: (b.get("model", ""), 0 if b.get("origin") == "parent" else 1),
    )

    headers = ["Model", "Origin", "Input", "Output", "Cache", "Cost"]
    align = ["l", "l", "r", "r", "r", "r"]
    rows: list[list[str]] = []
    for r in sorted_rows:
        cache = (
            int(r.get("cache_creation_input_tokens", 0))
            + int(r.get("cache_read_input_tokens", 0))
        )
        rows.append([
            str(r.get("model", "")),
            str(r.get("origin", "")),
            _fmt_tokens(int(r.get("input_tokens", 0))),
            _fmt_tokens(int(r.get("output_tokens", 0))),
            _fmt_tokens(cache),
            _fmt_cost(float(r.get("cost", 0.0))),
        ])

    total_cache = (
        int(tm.get("cache_creation_input_tokens", 0))
        + int(tm.get("cache_read_input_tokens", 0))
    )
    rows.append([
        "**Total**",
        "",
        f"**{_fmt_tokens(int(tm.get('input_tokens', 0)))}**",
        f"**{_fmt_tokens(int(tm.get('output_tokens', 0)))}**",
        f"**{_fmt_tokens(total_cache)}**",
        f"**{_fmt_cost(float(tm.get('total_cost', 0.0)))}**",
    ])

    return "## Token Metrics\n\n" + _md_table(headers, rows, align)


def render_agent_metrics(data: dict[str, Any]) -> str:
    am = data.get("agent_metrics") or {}
    by_type = am.get("by_agent_type") or {}
    total_invocations = int(am.get("total_invocations", 0))

    if total_invocations == 0 or not by_type:
        return "## Agent Metrics\n\nNo agent invocations.\n"

    headers = [
        "Agent Type", "Count", "Tokens", "Avg Tokens/Call", "Duration",
    ]
    align = ["l", "r", "r", "r", "r"]
    rows: list[list[str]] = []
    for _key in sorted(by_type.keys()):
        m = by_type[_key]
        agent_type = str(m.get("agent_type", ""))
        if m.get("is_builtin"):
            agent_type = f"{agent_type} (builtin)"
        avg = _avg_tokens_per_invocation(m)
        avg_label = _fmt_tokens(int(avg)) if avg is not None else "—"
        rows.append([
            agent_type,
            str(int(m.get("invocation_count", 0))),
            _fmt_tokens(int(m.get("total_tokens", 0))),
            avg_label,
            _fmt_duration_seconds(int(m.get("total_duration_ms", 0))),
        ])

    rows.append([
        "**Total**",
        f"**{total_invocations}**",
        "",
        "",
        "",
    ])

    agent_pct = am.get("agent_token_percentage", 0.0)
    table = _md_table(headers, rows, align)
    return (
        "## Agent Metrics\n\n"
        + table
        + f"\nAgent token share of session total: **{agent_pct}%**\n"
    )


def _axis_label(primary_axis: str) -> str:
    """Plain-Markdown axis label. Mirrors the CLI's ``[cost]`` / ``[speed]`` /
    ``[quality]`` prefix but without Rich color escapes — escaping the
    leading ``[`` so GitHub Markdown doesn't interpret it as a link."""
    safe = primary_axis if primary_axis in {"cost", "speed", "quality"} else "cost"
    return rf"\[{safe}]"


def _top_n_summary(
    aggs: list[dict[str, Any]], top_n: int,
) -> str:
    """Top-N priority pointer block above the severity groups.

    Mirrors ``_format_top_recommendations`` in the CLI table formatter
    but emits Markdown bullets. The aggregated list is already sorted
    desc by ``priority_score`` (the analyze pipeline guarantees this);
    the top N rows are the top priorities by definition.
    """
    if top_n <= 0 or not aggs:
        return ""
    shown = aggs[:top_n]
    lines = [f"**Top {len(shown)} priority fixes:**\n"]
    for idx, agg in enumerate(shown, start=1):
        agent = agg.get("agent_type") or GLOBAL_AGENT_LABEL
        sev = str(agg.get("severity", ""))
        count = int(agg.get("count", 1))
        target = str(agg.get("target", ""))
        axis = _axis_label(str(agg.get("primary_axis", "cost")))
        lines.append(
            f"{idx}. **{sev}** · agent: `{agent}` · {count}× · "
            f"target: `{target}` · axis: {axis}",
        )
    return "\n".join(lines) + "\n\n"


def render_diagnostics(data: dict[str, Any]) -> str:
    diag = data.get("diagnostics") or {}
    aggs: list[dict[str, Any]] = list(diag.get("aggregated_recommendations") or [])

    if not aggs:
        return "## Diagnostics\n\nNo findings.\n"

    out = ["## Diagnostics\n", _top_n_summary(aggs, DEFAULT_TOP_N)]

    grouped: dict[str, list[dict[str, Any]]] = {s: [] for s in SEVERITY_ORDER}
    for agg in aggs:
        sev = str(agg.get("severity", "info"))
        grouped.setdefault(sev, []).append(agg)

    for sev in SEVERITY_ORDER:
        group = grouped.get(sev) or []
        if not group:
            continue
        out.append(f"### {SEVERITY_HEADINGS[sev]} ({len(group)})\n")
        for agg in group:
            axis = _axis_label(str(agg.get("primary_axis", "cost")))
            target = str(agg.get("target", ""))
            agent = agg.get("agent_type") or GLOBAL_AGENT_LABEL
            count = int(agg.get("count", 1))
            msg = str(agg.get("representative_message", ""))
            out.append(
                f"- {axis} (target: `{target}`, agent: `{agent}`, "
                f"count: {count}×) — {msg}",
            )
        out.append("")  # blank line between groups

    return "\n".join(out).rstrip() + "\n"


def render_offload(data: dict[str, Any]) -> str:
    """Offload candidates section.

    Per #344, hide negative-savings rows (offloading would cost MORE).
    If no positive-savings candidates remain, return ``""`` so the
    section is omitted entirely — issue spec says "Offload Candidates
    (if present)".
    """
    diag = data.get("diagnostics") or {}
    candidates = diag.get("offload_candidates") or []
    positive = [
        c for c in candidates
        if float(c.get("estimated_savings_usd", 0.0)) > 0
    ]
    if not positive:
        return ""

    positive.sort(
        key=lambda c: float(c.get("estimated_savings_usd", 0.0)),
        reverse=True,
    )

    headers = ["Name", "Confidence", "Cluster size", "Tools", "Est. savings"]
    align = ["l", "l", "r", "l", "r"]
    rows: list[list[str]] = []
    for c in positive:
        tools_list = c.get("tools") or []
        if tools_list:
            tools_display = ", ".join(str(t) for t in tools_list)
        else:
            note = str(c.get("tools_note") or "")
            tools_display = note or "—"
        rows.append([
            str(c.get("name", "")),
            str(c.get("confidence", "")),
            str(int(c.get("cluster_size", 0))),
            tools_display,
            _fmt_cost(float(c.get("estimated_savings_usd", 0.0))),
        ])

    return "## Offload Candidates\n\n" + _md_table(headers, rows, align)


def _reproduction_command(data: dict[str, Any]) -> str:
    """Build the ``agentfluent analyze ...`` command that produced this snapshot.

    Always emitted (architect review): for the null-window case it
    degrades to ``agentfluent analyze --project P --json`` so the
    standalone-readability acceptance criterion is preserved.
    """
    project = data.get("project_name") or UNKNOWN_PROJECT
    # Shell-quote the project name only when it contains whitespace or
    # other characters that would split it across argv. Project display
    # names typically don't, but slugs with embedded slashes (older
    # ``~/.claude/projects/`` slugs) would, and the reader will
    # copy-paste this verbatim.
    needs_quote = any(ch in project for ch in " \t'\"\\")
    project_arg = f'"{project}"' if needs_quote else project

    parts = ["agentfluent analyze", f"--project {project_arg}"]
    window = data.get("window")
    if window:
        since = window.get("since")
        until = window.get("until")
        if since:
            parts.append(f"--since {since}")
        if until:
            parts.append(f"--until {until}")
    parts.append("--json")
    return " ".join(parts)


def render_footer(data: dict[str, Any]) -> str:
    cmd = _reproduction_command(data)
    generated = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "## Reproduction\n\n"
        "```bash\n"
        f"{cmd}\n"
        "```\n\n"
        f"*Generated: {generated}*\n"
    )
