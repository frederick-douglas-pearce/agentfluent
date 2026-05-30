"""#333 ERROR_PATTERN residual FP sampling pass.

Walks every project under ~/.claude/projects/, replays the current
`_extract_error_signals` logic (bounded window, post-#281), and emits
one TSV row per ERROR_PATTERN match — annotated with the data the
architect review needs:

  - has_trace             : did this invocation have a linked subagent trace?
                            (hypothesis 5: suppress metadata fallback here)
  - trace_signal_types    : if traced, which trace signals fired?
                            (clean trace = no signals → current _dedup_error_patterns
                            would NOT drop this metadata signal; hypothesis 5 would)
  - matches_in_window     : how many ERROR_REGEX hits in the 200-char window?
                            (sizes the min-match gate for untraced invocations)

`your_label` (TP / FP / borderline) is left blank for the human to fill.

Output: sample.tsv next to this script. Re-run anytime; deterministic order.

Not part of the shipped CLI — one-off calibration tool. Lives under
.claude/specs/analysis/ alongside the writeup it feeds (findings.md).
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

from agentfluent.analytics.pipeline import analyze_session
from agentfluent.core.discovery import discover_projects
from agentfluent.diagnostics.signals import iter_error_matches
from agentfluent.diagnostics.trace_signals import extract_trace_signals

SNIPPET_RADIUS = 250
SNIPPET_MAX_CHARS = 600

OUTPUT_PATH = Path(__file__).parent / "sample.tsv"

COLUMNS = [
    "signal_id",
    "project",
    "session",
    "agent_type",
    "keyword",
    "matches_in_window",
    "has_trace",
    "trace_signal_types",
    "output_text_len",
    "match_offset",
    "snippet",
    "your_label",
]


def _snippet(text: str, start: int, end: int) -> str:
    """Replay the snippet shape `_extract_error_signals` emits, single-lined."""
    lo = max(0, start - SNIPPET_RADIUS)
    hi = min(len(text), end + SNIPPET_RADIUS)
    raw = text[lo:hi].replace("\n", " ").replace("\t", " ").strip()
    raw = " ".join(raw.split())  # collapse runs of whitespace
    if len(raw) > SNIPPET_MAX_CHARS:
        raw = raw[: SNIPPET_MAX_CHARS - 1] + "…"
    return raw


def main() -> int:
    projects = discover_projects()
    if not projects:
        print("no projects found under ~/.claude/projects/", file=sys.stderr)
        return 1

    rows: list[dict[str, object]] = []
    project_match_counts: Counter[str] = Counter()
    project_session_skips: Counter[str] = Counter()
    signal_counter = 0

    for project in projects:
        if not project.sessions:
            continue
        for session in project.sessions:
            try:
                analysis = analyze_session(session.path)
            except Exception as exc:  # noqa: BLE001 — one-off survey, log + continue
                project_session_skips[project.slug] += 1
                print(
                    f"[skip] {project.slug}/{session.path.name}: {exc!r}",
                    file=sys.stderr,
                )
                continue

            for inv in analysis.invocations:
                if not inv.output_text:
                    continue
                matches = list(iter_error_matches(inv.output_text))
                if not matches:
                    continue

                if inv.trace is not None:
                    trace_sigs = extract_trace_signals(
                        inv.trace,
                        agent_type=inv.agent_type,
                        invocation_id=inv.invocation_id,
                    )
                    trace_types = sorted({s.signal_type.value for s in trace_sigs})
                else:
                    trace_types = []

                for match in matches:
                    signal_counter += 1
                    project_match_counts[project.slug] += 1
                    rows.append(
                        {
                            "signal_id": f"S{signal_counter:04d}",
                            "project": project.slug,
                            "session": session.path.name,
                            "agent_type": inv.agent_type,
                            "keyword": match.group(0).lower(),
                            "matches_in_window": len(matches),
                            "has_trace": "true" if inv.trace is not None else "false",
                            "trace_signal_types": ",".join(trace_types),
                            "output_text_len": len(inv.output_text),
                            "match_offset": match.start(),
                            "snippet": _snippet(
                                inv.output_text, match.start(), match.end()
                            ),
                            "your_label": "",
                        }
                    )

    rows.sort(key=lambda r: (r["project"], r["session"], r["signal_id"]))
    OUTPUT_PATH.write_text("")  # truncate
    with OUTPUT_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nwrote {len(rows)} rows to {OUTPUT_PATH}", file=sys.stderr)
    print("\nper-project ERROR_PATTERN match counts:", file=sys.stderr)
    for slug, count in project_match_counts.most_common():
        skips = project_session_skips.get(slug, 0)
        skip_note = f" (skipped {skips} sessions)" if skips else ""
        print(f"  {slug:60} {count:5d}{skip_note}", file=sys.stderr)

    traced = sum(1 for r in rows if r["has_trace"] == "true")
    untraced = len(rows) - traced
    clean_trace = sum(
        1
        for r in rows
        if r["has_trace"] == "true" and not r["trace_signal_types"]
    )
    multi_match = sum(1 for r in rows if int(r["matches_in_window"]) >= 2)
    print(
        f"\nbreakdown: traced={traced} (clean-trace={clean_trace}) "
        f"untraced={untraced} ≥2-matches-in-window={multi_match}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
