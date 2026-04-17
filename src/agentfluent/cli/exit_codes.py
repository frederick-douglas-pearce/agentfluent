"""Exit code invariant for AgentFluent commands.

0 = success.
1 = user named something specific and it's wrong (bad project slug, unknown
    session, unknown agent name, invalid scope value).
2 = system searched and found nothing (no projects dir, project has no
    sessions, no agent definitions found).

`typer.BadParameter` exits 2 by Click convention for argument-level usage
errors (e.g., `--verbose --quiet` together). That's a framework-handled
separate category and does not fold into the invariant above.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_NO_DATA = 2
