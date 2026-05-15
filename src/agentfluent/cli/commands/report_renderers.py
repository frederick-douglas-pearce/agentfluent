"""Markdown section renderers for ``agentfluent report`` (#354).

Each ``render_*`` takes the envelope's ``data`` payload (a JSON-
deserialized dict from ``AnalysisResult.model_dump(mode="json")``) and
returns one Markdown section. ``report.py`` composes them in the D030
order: Summary -> Token Metrics -> Agent Metrics -> Diagnostics ->
Offload -> Footer.

Renderers receive a dict, not a hydrated model — so ``@property``
accessors (``TokenMetrics.total_tokens``,
``AgentTypeMetrics.avg_tokens_per_invocation``) aren't in ``data``;
the ``_total_tokens`` / ``_avg_tokens_per_invocation`` helpers derive
them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentfluent.cli.formatters.helpers import (
    GLOBAL_AGENT_LABEL,
    format_cost,
    format_tokens,
)
from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import Axis

UNKNOWN_PROJECT = "(unknown project)"
DEFAULT_TOP_N = 5

SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.CRITICAL,
    Severity.WARNING,
    Severity.INFO,
)


def _total_tokens(row: dict[str, Any]) -> int:
    """Sum the four token components; pydantic's ``model_dump`` skips the
    ``total_tokens`` ``@property`` accessor."""
    # int(...) at the helper boundary satisfies mypy-strict on the
    # ``dict[str, Any]`` source; callsites stay coercion-free.
    return int(
        row.get("input_tokens", 0)
        + row.get("output_tokens", 0)
        + row.get("cache_creation_input_tokens", 0)
        + row.get("cache_read_input_tokens", 0),
    )


def _avg_tokens_per_invocation(row: dict[str, Any]) -> float | None:
    """Mirror ``AgentTypeMetrics.avg_tokens_per_invocation`` (a property
    not serialized by ``model_dump``)."""
    count = row.get("invocation_count", 0)
    tokens = row.get("total_tokens", 0)
    if count > 0 and tokens > 0:
        return float(tokens) / float(count)
    return None


def _fmt_duration_seconds(duration_ms: int) -> str:
    if duration_ms <= 0:
        return "—"
    return f"{duration_ms / 1000:.1f}s"


def _md_table(
    headers: list[str],
    rows: list[list[str]],
    align: list[str],
) -> str:
    """Render a GitHub-flavored Markdown table; ``align`` is one
    ``l`` / ``r`` / ``c`` per column."""
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
    if not window:
        return "all sessions"
    # since / until are independently Optional — one-sided filters
    # (e.g. ``--since`` only) are valid.
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
    total_cost = tm.get("total_cost", 0.0)
    input_tokens = tm.get("input_tokens", 0)
    output_tokens = tm.get("output_tokens", 0)
    cache_creation = tm.get("cache_creation_input_tokens", 0)
    cache_read = tm.get("cache_read_input_tokens", 0)

    bullets = [
        f"- **Project:** {project}",
        f"- **Sessions analyzed:** {session_count}",
        f"- **Window:** {_format_window(data.get('window'))}",
        f"- **Total cost (API rate):** {format_cost(total_cost)}",
        (
            f"- **Total tokens:** {format_tokens(total_tokens)} "
            f"(input {format_tokens(input_tokens)}, "
            f"output {format_tokens(output_tokens)}, "
            f"cache creation {format_tokens(cache_creation)}, "
            f"cache read {format_tokens(cache_read)})"
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

    # Mirrors table.py:166 — (model, parent-first) so the report row
    # order matches the CLI table users already know.
    sorted_rows = sorted(
        by_model,
        key=lambda b: (b.get("model", ""), 0 if b.get("origin") == "parent" else 1),
    )

    headers = ["Model", "Origin", "Input", "Output", "Cache", "Cost"]
    align = ["l", "l", "r", "r", "r", "r"]
    rows: list[list[str]] = []
    for r in sorted_rows:
        cache = (
            r.get("cache_creation_input_tokens", 0)
            + r.get("cache_read_input_tokens", 0)
        )
        rows.append([
            r.get("model", ""),
            r.get("origin", ""),
            format_tokens(r.get("input_tokens", 0)),
            format_tokens(r.get("output_tokens", 0)),
            format_tokens(cache),
            format_cost(r.get("cost", 0.0)),
        ])

    total_cache = (
        tm.get("cache_creation_input_tokens", 0)
        + tm.get("cache_read_input_tokens", 0)
    )
    rows.append([
        "**Total**",
        "",
        f"**{format_tokens(tm.get('input_tokens', 0))}**",
        f"**{format_tokens(tm.get('output_tokens', 0))}**",
        f"**{format_tokens(total_cache)}**",
        f"**{format_cost(tm.get('total_cost', 0.0))}**",
    ])

    return "## Token Metrics\n\n" + _md_table(headers, rows, align)


def render_agent_metrics(data: dict[str, Any]) -> str:
    am = data.get("agent_metrics") or {}
    by_type = am.get("by_agent_type") or {}
    total_invocations = am.get("total_invocations", 0)

    if total_invocations == 0 or not by_type:
        return "## Agent Metrics\n\nNo agent invocations.\n"

    headers = ["Agent Type", "Count", "Tokens", "Avg Tokens/Call", "Duration"]
    align = ["l", "r", "r", "r", "r"]
    rows: list[list[str]] = []
    for _key, m in sorted(by_type.items()):
        agent_type = m.get("agent_type", "")
        if m.get("is_builtin"):
            agent_type = f"{agent_type} (builtin)"
        avg = _avg_tokens_per_invocation(m)
        avg_label = format_tokens(int(avg)) if avg is not None else "—"
        rows.append([
            agent_type,
            str(m.get("invocation_count", 0)),
            format_tokens(m.get("total_tokens", 0)),
            avg_label,
            _fmt_duration_seconds(m.get("total_duration_ms", 0)),
        ])

    rows.append(["**Total**", f"**{total_invocations}**", "", "", ""])

    agent_pct = am.get("agent_token_percentage", 0.0)
    table = _md_table(headers, rows, align)
    return (
        "## Agent Metrics\n\n"
        + table
        + f"\nAgent token share of session total: **{agent_pct}%**\n"
    )


def _axis_label(primary_axis: str) -> str:
    """Plain-Markdown axis label; ``[`` is backslash-escaped so GitHub
    doesn't render it as a link."""
    try:
        axis = Axis(primary_axis)
    except ValueError:
        axis = Axis.COST
    return rf"\[{axis.value}]"


def _top_n_summary(aggs: list[dict[str, Any]], top_n: int) -> str:
    if top_n <= 0 or not aggs:
        return ""
    shown = aggs[:top_n]
    lines = [f"**Top {len(shown)} priority fixes:**\n"]
    for idx, agg in enumerate(shown, start=1):
        agent = agg.get("agent_type") or GLOBAL_AGENT_LABEL
        sev = agg.get("severity", "")
        count = agg.get("count", 1)
        target = agg.get("target", "")
        axis = _axis_label(agg.get("primary_axis", Axis.COST.value))
        lines.append(
            f"{idx}. **{sev}** · agent: `{agent}` · {count}× · "
            f"target: `{target}` · axis: {axis}",
        )
    return "\n".join(lines) + "\n\n"


def render_diagnostics(data: dict[str, Any]) -> str:
    diag = data.get("diagnostics") or {}
    aggs: list[dict[str, Any]] = diag.get("aggregated_recommendations") or []

    if not aggs:
        return "## Diagnostics\n\nNo findings.\n"

    out = ["## Diagnostics\n", _top_n_summary(aggs, DEFAULT_TOP_N)]

    grouped: dict[Severity, list[dict[str, Any]]] = {s: [] for s in SEVERITY_ORDER}
    for agg in aggs:
        try:
            sev = Severity(agg.get("severity", Severity.INFO.value))
        except ValueError:
            sev = Severity.INFO
        grouped[sev].append(agg)

    for sev in SEVERITY_ORDER:
        group = grouped[sev]
        if not group:
            continue
        out.append(f"### {sev.value.title()} ({len(group)})\n")
        for agg in group:
            axis = _axis_label(agg.get("primary_axis", Axis.COST.value))
            target = agg.get("target", "")
            agent = agg.get("agent_type") or GLOBAL_AGENT_LABEL
            count = agg.get("count", 1)
            msg = agg.get("representative_message", "")
            out.append(
                f"- {axis} (target: `{target}`, agent: `{agent}`, "
                f"count: {count}×) — {msg}",
            )
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def render_offload(data: dict[str, Any]) -> str:
    """Offload candidates; negative-savings rows hidden per #344, and
    the section is omitted entirely when no positive candidates
    remain (issue spec: "if present")."""
    diag = data.get("diagnostics") or {}
    candidates = diag.get("offload_candidates") or []
    positive = [
        c for c in candidates if c.get("estimated_savings_usd", 0.0) > 0
    ]
    if not positive:
        return ""

    positive.sort(
        key=lambda c: c.get("estimated_savings_usd", 0.0),
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
            tools_display = c.get("tools_note") or "—"
        rows.append([
            c.get("name", ""),
            c.get("confidence", ""),
            str(c.get("cluster_size", 0)),
            tools_display,
            format_cost(c.get("estimated_savings_usd", 0.0)),
        ])

    return "## Offload Candidates\n\n" + _md_table(headers, rows, align)


def _reproduction_command(data: dict[str, Any]) -> str:
    """Always emitted: the null-window case degrades to
    ``--project P --json`` so the report stays readable as a
    standalone document."""
    project = data.get("project_name") or UNKNOWN_PROJECT
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


def render_footer(data: dict[str, Any], *, now: datetime | None = None) -> str:
    """Render the reproduction footer.

    ``now`` is an injection seam for snapshot tests so the rendered output
    is deterministic. Production callers omit it and accept
    ``datetime.now(UTC)``.
    """
    cmd = _reproduction_command(data)
    moment = now if now is not None else datetime.now(UTC)
    generated = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "## Reproduction\n\n"
        "```bash\n"
        f"{cmd}\n"
        "```\n\n"
        f"*Generated: {generated}*\n"
    )
