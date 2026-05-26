"""First-run consent flow for Tier 3 GitHub enrichment.

Stores a small JSON record at
``agentfluent_config_dir() / "github-consent.json"`` so the
interactive prompt only fires the first time a user passes
``--github``. Non-TTY contexts (CI, pipes) treat the explicit flag
itself as consent and silently record it.

The schema is intentionally extensible from day one (architect note
3 in #399 review). Future consent surfaces (PAT auth, telemetry,
anything else that talks to a third party) drop into the
``consents`` object as new keys without breaking existing entries::

    {
      "version": 1,
      "consents": {
        "github_api": {"granted_at": "...", "version": 1},
        "telemetry":  {"granted_at": "...", "version": 1}   # future
      }
    }
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentfluent.core.paths import agentfluent_config_dir

logger = logging.getLogger(__name__)

CONSENT_FILENAME = "github-consent.json"
GITHUB_API_SURFACE = "github_api"
_SCHEMA_VERSION = 1

_DISCLOSURE = """\
AgentFluent's --github flag enables Tier 3 quality signals, which call
GitHub's API via the `gh` CLI.

  Sent to api.github.com (via `gh`):
    • Repository owner / name (inferred from your git remote)
    • PR numbers and commit SHAs touched in the analyzed time window
    • Your GitHub username (carried in the `gh` auth headers)

  Cached locally (response bodies, hashed filenames):
    {cache_dir}

  Not sent:
    • JSONL session contents, prompts, or tool outputs
    • File contents or agent definitions

You can wipe the cache at any time with: rm -rf {cache_dir}
"""


def consent_path(*, config_dir: Path | None = None) -> Path:
    """Filesystem location of the consent file. ``config_dir`` overrides
    for tests; production always uses :func:`agentfluent_config_dir`."""
    base = config_dir if config_dir is not None else agentfluent_config_dir()
    return base / CONSENT_FILENAME


def _load(config_dir: Path | None) -> dict[str, Any]:
    path = consent_path(config_dir=config_dir)
    if not path.exists():
        return {"version": _SCHEMA_VERSION, "consents": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.debug("consent file unreadable: %s", path, exc_info=True)
        return {"version": _SCHEMA_VERSION, "consents": {}}
    if not isinstance(data, dict):
        return {"version": _SCHEMA_VERSION, "consents": {}}
    # Defensive normalization: a hand-edited file might miss the consents
    # object; ensure it exists so callers can index without a guard.
    if not isinstance(data.get("consents"), dict):
        data["consents"] = {}
    return data


def has_consent(
    surface: str = GITHUB_API_SURFACE,
    *,
    config_dir: Path | None = None,
) -> bool:
    """``True`` when ``surface`` already has a granted consent record."""
    data = _load(config_dir)
    entry = data["consents"].get(surface)
    return isinstance(entry, dict) and "granted_at" in entry


def record_consent(
    surface: str = GITHUB_API_SURFACE,
    *,
    version: int = 1,
    config_dir: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Persist consent for ``surface`` without altering other entries.

    The on-disk file is read first, the new entry is merged in, and
    the whole document is rewritten — so writing a second consent key
    never destroys an earlier one (the extensibility invariant
    asserted by ``test_github_consent.py``).
    """
    data = _load(config_dir)
    current = now if now is not None else datetime.now(UTC)
    data["consents"][surface] = {
        "granted_at": current.isoformat(),
        "version": version,
    }
    path = consent_path(config_dir=config_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        logger.warning("could not persist consent record to %s", path, exc_info=True)


def _default_disclosure_output(message: str) -> None:
    """Write the disclosure to stderr, not stdout.

    Default ``output_fn`` for :func:`prompt_and_record_if_needed`.
    Writing to stderr keeps the disclosure out of any JSON or
    machine-readable payload the user is piping from stdout (e.g.
    ``agentfluent analyze --github --json | jq .``) — a TTY prompt
    can still fire because ``sys.stdin.isatty()`` is unaffected by
    stdout redirection.
    """
    sys.stderr.write(message)
    if not message.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()


def prompt_and_record_if_needed(
    *,
    is_tty: bool,
    config_dir: Path | None = None,
    cache_dir_display: Path | None = None,
    input_fn: Any = input,
    output_fn: Any = None,
) -> bool:
    """Resolve consent for the GitHub API surface for this run.

    Returns ``True`` when consent is in place after this call (already
    granted, freshly accepted, or non-TTY auto-consented), ``False``
    when the user declined the interactive prompt.

    - **TTY, no prior consent:** write the disclosure to stderr, ask
      ``[y/N]`` on stdin; record consent on Y, return False on N or
      empty.
    - **TTY, prior consent:** return ``True`` immediately — disclosure
      is *not* re-shown, prompt is *not* re-issued.
    - **Non-TTY:** treat ``--github`` itself as consent; record
      silently and return ``True``. This matches the spike's privacy
      model — the CLI flag is explicit per-invocation opt-in.

    ``cache_dir_display`` is the path shown in the disclosure (defaults
    to the github subdir under :func:`agentfluent_cache_dir`); tests
    inject this to keep output stable.

    ``input_fn`` and ``output_fn`` are injected for test isolation;
    production uses ``input`` for the prompt and a stderr writer for
    the disclosure (so JSON piped from stdout stays clean).
    """
    if has_consent(config_dir=config_dir):
        return True

    if not is_tty:
        record_consent(config_dir=config_dir)
        return True

    out = output_fn if output_fn is not None else _default_disclosure_output
    if cache_dir_display is None:
        from agentfluent.core.paths import agentfluent_cache_dir
        cache_dir_display = agentfluent_cache_dir() / "github"
    out(_DISCLOSURE.format(cache_dir=cache_dir_display))
    try:
        answer = input_fn("Enable Tier 3 GitHub enrichment for this and future runs? [y/N]: ")
    except EOFError:
        return False
    if answer.strip().lower() in {"y", "yes"}:
        record_consent(config_dir=config_dir)
        return True
    return False


def is_stdin_tty() -> bool:
    """Helper for callers that don't already have a stream handle."""
    return sys.stdin.isatty()
