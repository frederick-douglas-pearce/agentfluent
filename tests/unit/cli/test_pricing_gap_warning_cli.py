"""``analyze`` appends the ``unpriced_tokens`` warning on a pricing gap (#688).

End to end through the CLI, so the append itself is what is tested, not only
``pricing_gap_warning``: full and quiet JSON both carry it on a run with an
unpriced model, and a fully priced run carries none.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer
from typer.testing import CliRunner

from agentfluent.analytics.cost_coverage import PRICING_GAP_WARNING_CODE
from agentfluent.cli.exit_codes import EXIT_OK
from agentfluent.cli.formatters.json_output import parse_json_output
from agentfluent.config.retention import _load_settings
from tests.unit.test_trace_coverage import _UNPRICED_MODEL, _build_session


def _config(root: Path) -> tuple[Path, Path]:
    cfg = root / "claude"
    project_dir = cfg / "projects" / "-home-user-test-project"
    project_dir.mkdir(parents=True)
    (cfg / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 3650}))
    return cfg, project_dir


def _warnings(runner: CliRunner, cli_app: typer.Typer, cfg: Path, *extra: str) -> list[dict]:
    _load_settings.cache_clear()
    result = runner.invoke(
        cli_app,
        ["--claude-config-dir", str(cfg), "analyze", "--project", "project",
         "--format", "json", "--no-diagnostics", *extra],
    )
    assert result.exit_code == EXIT_OK, result.stdout
    data = parse_json_output(result.stdout, expected_command="analyze")
    warnings: list[dict] = data["warnings"]
    return warnings


def test_unpriced_model_adds_the_warning_in_full_and_quiet_json(
    runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
) -> None:
    cfg, project_dir = _config(tmp_path)
    _build_session(project_dir)
    for extra in ((), ("--quiet",)):
        codes = {w["code"]: w for w in _warnings(runner, cli_app, cfg, *extra)}
        assert PRICING_GAP_WARNING_CODE in codes, extra
        assert _UNPRICED_MODEL in codes[PRICING_GAP_WARNING_CODE]["message"]


def test_fully_priced_run_has_no_pricing_warning(
    runner: CliRunner, cli_app: typer.Typer, tmp_path: Path, fixtures_dir: Path,
) -> None:
    cfg, project_dir = _config(tmp_path)
    shutil.copy(fixtures_dir / "session_basic.jsonl", project_dir / "s1.jsonl")
    codes = [w["code"] for w in _warnings(runner, cli_app, cfg)]
    assert PRICING_GAP_WARNING_CODE not in codes
