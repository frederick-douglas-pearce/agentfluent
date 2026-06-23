"""#519 corpus matrix runner for the representative Agent SDK agent (`agent.py`).

Runs `agent.py` across a small configuration matrix, copies each run's raw
session file(s) out of `~/.claude/projects/<slug>/` into the gitignored
`corpus/`, and writes `corpus/manifest.json` -- a config->file index so S3 (#520)
can correlate observed format differences to the inputs that produced them.

The matrix toggles ONE axis per run (running the cross-product is gold-plating,
per architect review on #517/#519):

  | run | variant  | main model | subagent model | isolates                        |
  |-----|----------|------------|----------------|---------------------------------|
  | a   | flat     | haiku      | --             | full tool_use / error surface   |
  | b   | subagent | sonnet     | haiku          | delegation + parent!=child model|
  | c   | flat     | sonnet     | --             | model recording (2nd model)     |

This satisfies #519's ACs: >=1 delegation run + >=1 without; >=2 distinct models;
and run (b) is a genuine model-divergence sample (parent sonnet, child haiku) --
the highest-value artifact for #112 model-routing, since the child's
`toolUseResult.resolvedModel` can be checked against a known config value.

Variant 1 (#518 hello-world) and the #522 `large` spill capture are NOT re-run
here; if their raw files are still present under `corpus/`, they are indexed as
`pre_existing` entries so the manifest is the single index of all SDK corpus.

RAW DATA ONLY. The corpus and this manifest embed absolute filesystem paths and
are gitignored -- they are never committed. Anonymization + fixture graduation is
#521's job. Each file entry carries a `contains_abs_paths` flag to hand #521 a
mechanical scrubbing worklist.

Run:
    uv run --group research python research/agent-sdk-probe/run_matrix.py

Throwaway research scaffolding. Not part of the published `agentfluent` package.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from claude_agent_sdk import project_key_for_directory

PROBE_DIR = Path(__file__).parent
AGENT = PROBE_DIR / "agent.py"
CORPUS = PROBE_DIR / "corpus"

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

MATRIX = [
    {"variant": "flat", "main_model": HAIKU, "subagent_model": HAIKU},
    {"variant": "subagent", "main_model": SONNET, "subagent_model": HAIKU},
    {"variant": "flat", "main_model": SONNET, "subagent_model": SONNET},
]

# Bytes that, if present in a captured file, mean it must be scrubbed before it
# can graduate to a committed fixture (#521).
_HOME = str(Path.home()).encode()
_SLUG = project_key_for_directory(str(PROBE_DIR)).encode()


def _run_agent(entry: dict) -> dict:
    """Invoke agent.py as a subprocess (isolation against SDK/asyncio state bleed
    and mid-matrix faults) and return its RESULT_JSON record."""
    cmd = [
        sys.executable,
        str(AGENT),
        entry["variant"],
        entry["main_model"],
        entry["subagent_model"],
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise RuntimeError(f"agent.py {entry['variant']} exited {proc.returncode}")
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT_JSON "):
            return json.loads(line[len("RESULT_JSON ") :])
    raise RuntimeError(f"no RESULT_JSON line from agent.py {entry['variant']}")


def _file_record(path: Path) -> dict:
    data = path.read_bytes()
    is_jsonl = path.suffix == ".jsonl"
    return {
        "path": str(path.relative_to(CORPUS)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "lines": data.count(b"\n") if is_jsonl else None,
        "contains_abs_paths": _HOME in data or _SLUG in data,
    }


def _cli_version(jsonl: Path) -> str | None:
    """Read the `version` field (Claude Code CLI version) off the first trace line
    that carries one."""
    with jsonl.open() as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            version = obj.get("version") or obj.get("claude_code_version")
            if version:
                return str(version)
    return None


def _capture(record: dict) -> dict:
    """Copy a run's session file(s) into corpus/, assert completeness, and return
    the manifest run entry."""
    session_id = record["session_id"]
    src_jsonl = Path(record["source_jsonl"])
    src_dir = src_jsonl.with_suffix("")  # sibling <id>/ holding subagents/, tool-results/

    dst_jsonl = CORPUS / f"{session_id}.jsonl"
    dst_dir = CORPUS / session_id
    CORPUS.mkdir(exist_ok=True)
    dst_jsonl.write_bytes(src_jsonl.read_bytes())

    files = [_file_record(dst_jsonl)]
    subagent_files: list[str] = []
    tool_results_files: list[str] = []
    if src_dir.is_dir():
        for child in sorted(src_dir.rglob("*")):
            if child.is_file():
                dst = dst_dir / child.relative_to(src_dir)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(child.read_bytes())
                rel = str(dst.relative_to(CORPUS))
                files.append(_file_record(dst))
                if "subagents/" in rel:
                    subagent_files.append(rel)
                elif "tool-results/" in rel:
                    tool_results_files.append(rel)

    # Completeness post-condition: a subagent run MUST yield a child trace, else we
    # captured a partial corpus and the manifest would lie. Fail loudly.
    if record["variant"] == "subagent" and not subagent_files:
        raise RuntimeError(
            f"subagent run {session_id} produced no subagents/*.jsonl -- partial "
            "capture; not recording a misleading manifest entry"
        )

    return {
        "variant": record["variant"],
        "main_model": record["main_model"],
        "subagent_model": record["subagent_model"],
        "session_id": session_id,
        "source_jsonl": str(src_jsonl),
        "corpus_jsonl": str(dst_jsonl.relative_to(CORPUS)),
        "sdk_version": record["sdk_version"],
        "cli_version": _cli_version(dst_jsonl),
        "prompt": record["prompt"],
        "config": record["config"],
        "subagent_files": subagent_files,
        "tool_results_files": tool_results_files,
        "files": files,
        "init": record["init"],
    }


def _pre_existing(produced_ids: set[str]) -> list[dict]:
    """Index any raw *.jsonl already under corpus/ from prior ad-hoc runs (#518
    hello-world, #522 large) so the manifest is the single index of all corpus.
    Their config provenance was not recorded at capture time."""
    out = []
    for jsonl in sorted(CORPUS.glob("*.jsonl")):
        if jsonl.stem in produced_ids:
            continue
        out.append(
            {
                **_file_record(jsonl),
                "note": "ad-hoc capture (#518 probe / #522 large); config provenance not recorded",
            }
        )
    return out


def main() -> None:
    runs = [_capture(_run_agent(entry)) for entry in MATRIX]
    produced = {r["session_id"] for r in runs}
    manifest = {
        "captured_at": datetime.now(UTC).isoformat(),
        "generator": "run_matrix.py (#519)",
        "probe_dir": str(PROBE_DIR),
        "project_slug": project_key_for_directory(str(PROBE_DIR)),
        "runs": runs,
        "pre_existing": _pre_existing(produced),
    }
    manifest_path = CORPUS / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nWrote {manifest_path} ({len(runs)} runs, "
          f"{len(manifest['pre_existing'])} pre-existing).")
    for r in runs:
        models = r["main_model"] + (
            f" -> {r['subagent_model']}" if r["variant"] == "subagent" else ""
        )
        print(f"  {r['variant']:9} {models:48} {r['session_id']}")


if __name__ == "__main__":
    main()
