"""Issue #206: parse-time warnings must reach stderr (with `WARNING:`
prefix and truncated line context) and never interleave with the table
on stdout.
"""

from __future__ import annotations

from pathlib import Path

import typer
from typer.testing import CliRunner


def test_list_sessions_routes_parse_warning_to_stderr(
    runner: CliRunner,
    cli_app: typer.Typer,
    populated_home: Path,
) -> None:
    """Append a malformed line to the seeded session and confirm:

    - the warning lands on stderr with the `WARNING:` prefix and includes
      the session filename, line number, and a snippet of the bad line
    - stdout still renders the Sessions table (and the warning text does
      not appear inside it)
    """
    project_dir = populated_home / "projects" / "-home-user-test-project"
    session_path = project_dir / "session-1.jsonl"
    bad_line = "this line is not valid json at all\n"
    with session_path.open("a") as f:
        f.write(bad_line)

    result = runner.invoke(cli_app, ["list", "--project", "project"])

    assert result.exit_code == 0
    assert "WARNING: Malformed JSON" in result.stderr
    assert "session-1.jsonl" in result.stderr
    assert "this line is not valid json at all" in result.stderr
    assert "Sessions" in result.stdout
    assert "WARNING:" not in result.stdout
