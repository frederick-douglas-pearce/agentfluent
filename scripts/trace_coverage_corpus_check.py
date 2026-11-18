"""Corpus check for the widened subagent-trace fold and ``trace_coverage`` (#648).

Runs the production pipeline (``analyze_sessions``) per corpus and reports:

* **Headline movement** -- session ``total_cost``, total tokens, subagent-origin
  tokens and cost, ``cache_efficiency``, ``agent_token_percentage``, and the
  wall time of the ``analyze_sessions`` call. These exist on code before and
  after #648, so a ``before``/``after`` pair shows what the fold moved.
* **Decomposition (AC1)** -- the ``trace_coverage`` cohorts: files, tokens, cost
  per cohort, the session-spawned split by spawner, and deduplicated turns.
  Only code with #648 has it; a ``before`` run reports ``n/a``.

Run it once on the change and once on ``main``, then compare:

    uv run python scripts/trace_coverage_corpus_check.py --label after
    PYTHONPATH=/path/to/main-worktree/src \\
        uv run python scripts/trace_coverage_corpus_check.py --label before

The first output line names the ``agentfluent`` package actually imported, so a
before/after pair can be checked for which code produced it.

Corpora default to the four standard ones; pass ``--corpus NAME=DIR`` (DIR
relative to ``~/.claude/projects``, or absolute) to choose others, and ``--all``
to add one combined entry for every project under ``~/.claude/projects`` (the
full install). ``--json PATH`` writes the results for diffing. A corpus that
cannot be read is reported on stderr and skipped.

Totals here count one parse of the projects as they are on disk now; the
parent-absent cohort (``<uuid>/subagents/`` with no ``<uuid>.jsonl``) is not in
any session and so not in these numbers (#702).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import agentfluent
from agentfluent.analytics.pipeline import AnalysisResult, analyze_sessions
from agentfluent.core.discovery import discover_sessions

PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_CORPORA = {
    "agentfluent": "-home-fdpearce-Documents-Projects-git-agentfluent",
    "claude-code-loop": "-home-fdpearce-Documents-Projects-git-claude-code-loop",
    "claude-code-sessions": "-home-fdpearce-Documents-Projects-git-claude-code-sessions",
    "us-presidential-vote-analysis": (
        "-home-fdpearce-Documents-Projects-git-us-presidential-vote-analysis"
    ),
}
ALL_PROJECTS = "full-install"
COHORTS = ("linked", "nested", "session_spawned", "filtered", "unparseable")


def _headline(result: AnalysisResult, seconds: float) -> dict[str, Any]:
    tm = result.token_metrics
    subagent = [r for r in tm.by_model if r.origin == "subagent"]
    return {
        "sessions": result.session_count,
        "total_cost": round(tm.total_cost, 2),
        "total_tokens": tm.total_tokens,
        "subagent_tokens": sum(r.total_tokens for r in subagent),
        "subagent_cost": round(sum(r.cost for r in subagent), 2),
        "cache_efficiency": tm.cache_efficiency,
        "agent_token_percentage": result.agent_metrics.agent_token_percentage,
        "analyze_seconds": round(seconds, 1),
    }


def _decomposition(result: AnalysisResult) -> dict[str, Any] | str:
    coverage = getattr(result, "trace_coverage", None)
    if coverage is None:
        return "n/a (code predates #648)"
    cohorts = {name: getattr(coverage, name) for name in COHORTS}
    all_tokens = sum(c.tokens for c in cohorts.values())
    out: dict[str, Any] = {
        name: {
            "files": c.files,
            "tokens": c.tokens,
            "share": round(c.tokens / all_tokens * 100, 1) if all_tokens else 0.0,
            "cost": round(c.cost, 2),
            "unpriced_files": c.unpriced_files,
            "folded": c.folded,
        }
        for name, c in cohorts.items()
    }
    out["session_spawned_by_type"] = {
        spawner: {"files": c.files, "tokens": c.tokens}
        for spawner, c in sorted(
            coverage.session_spawned_by_type.items(), key=lambda kv: -kv[1].tokens,
        )
    }
    out["deduped_turns"] = coverage.deduped_turns
    out["deduped_tokens"] = coverage.deduped_tokens
    return out


def check(paths: list[Path]) -> dict[str, Any]:
    started = time.perf_counter()
    result = analyze_sessions(paths)
    seconds = time.perf_counter() - started
    return {"headline": _headline(result, seconds), "decomposition": _decomposition(result)}


def _session_paths(corpus_dir: Path) -> list[Path]:
    return [info.path for info in discover_sessions(corpus_dir)]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--label", default="run", help="name for this run")
    parser.add_argument(
        "--corpus", action="append", default=[], metavar="NAME=DIR",
        help="corpus to check (repeatable); defaults to the four standard corpora",
    )
    parser.add_argument(
        "--all", action="store_true", help="also check every project combined",
    )
    parser.add_argument("--json", type=Path, help="write results to this file")
    args = parser.parse_args(argv)
    for corpus in args.corpus:
        name, sep, rel_dir = corpus.partition("=")
        if not (sep and name and rel_dir):
            parser.error(f"--corpus expects NAME=DIR, got {corpus!r}")
    return args


def _print(name: str, value: dict[str, Any]) -> None:
    print(f"== {name}")
    for section, body in value.items():
        print(f"   {section}: {json.dumps(body, default=str)}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    corpora = dict(c.split("=", 1) for c in args.corpus) or DEFAULT_CORPORA
    print(f"[{args.label}] agentfluent imported from {Path(agentfluent.__file__).parent}")

    results: dict[str, Any] = {}
    for name, rel_dir in corpora.items():
        corpus_dir = PROJECTS_DIR / rel_dir
        if not corpus_dir.is_dir():
            print(f"skip {name}: {corpus_dir} not found", file=sys.stderr)
            continue
        try:
            results[name] = check(_session_paths(corpus_dir))
        except (OSError, UnicodeDecodeError) as exc:
            print(f"error: skipping corpus {name}: {exc}", file=sys.stderr)
            continue
        _print(name, results[name])

    if args.all:
        try:
            paths = [
                path
                for project in sorted(PROJECTS_DIR.iterdir())
                if project.is_dir()
                for path in _session_paths(project)
            ]
            results[ALL_PROJECTS] = check(paths)
            _print(ALL_PROJECTS, results[ALL_PROJECTS])
        except (OSError, UnicodeDecodeError) as exc:
            print(f"error: skipping {ALL_PROJECTS}: {exc}", file=sys.stderr)

    if args.json:
        try:
            args.json.write_text(
                json.dumps({"label": args.label, "corpora": results}, indent=2, default=str),
            )
        except OSError as exc:
            print(f"error: cannot write {args.json}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
