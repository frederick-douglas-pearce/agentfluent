"""Snapshot persistence for the window-over-window dogfood diff.

Each run writes one ``agentfluent analyze --json`` snapshot per project-slug so
the *next* run can diff the current window against the previous one (dogfooding
``agentfluent diff``). Snapshots live in a **user-global, gitignored-by-location**
state tree — NEVER inside the repo (an in-tree state dir is one ``.gitignore``
slip from leaking project data to a public repo) and NEVER inside the curated
``.claude/specs/analysis/`` dogfood dir (those are hand-authored artifacts).

Layout, honoring ``$XDG_STATE_HOME`` (falling back to ``~/.local/state``), which
mirrors the ``agentfluent_config_dir`` / ``agentfluent_cache_dir`` XDG convention
in ``agentfluent.core.paths``::

    $XDG_STATE_HOME/agentfluent/dogfood/<slug>/snapshot-<runstamp>.json
"""

from __future__ import annotations

import os
import re
from pathlib import Path

AGENTFLUENT_SUBDIR = "agentfluent"
DOGFOOD_SUBDIR = "dogfood"
XDG_STATE_HOME_ENV_VAR = "XDG_STATE_HOME"

SNAPSHOT_PREFIX = "snapshot-"
SNAPSHOT_SUFFIX = ".json"

# Keep disk bounded: retain the N most recent snapshots per slug. A daily cadence
# with a few-day rolling window only ever needs the two most recent to diff, but
# a fortnight of history is cheap and useful for eyeballing longer trends.
DEFAULT_SNAPSHOT_RETENTION = 14

# A project-slug is a Claude Code directory key like ``-home-user-project``. Guard
# against a malformed slug escaping the state tree (path traversal / absolute
# paths) before it is used as a directory name.
_SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def dogfood_state_dir() -> Path:
    """Root of the dogfood snapshot tree (``<state>/agentfluent/dogfood``)."""
    xdg = os.environ.get(XDG_STATE_HOME_ENV_VAR)
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / AGENTFLUENT_SUBDIR / DOGFOOD_SUBDIR


def slug_dir(slug: str, *, root: Path | None = None) -> Path:
    """Per-slug snapshot directory. ``root`` overrides the state root (tests)."""
    # ``.`` / ``..`` match the dot-permitting regex but are traversal — reject them.
    if slug in {".", ".."} or not _SAFE_SLUG_RE.match(slug):
        msg = f"unsafe project-slug for snapshot path: {slug!r}"
        raise ValueError(msg)
    return (root or dogfood_state_dir()) / slug


def latest_snapshot(slug: str, *, root: Path | None = None) -> Path | None:
    """Most-recent existing snapshot for ``slug`` (the diff baseline), or ``None``.

    Called BEFORE the current run writes its snapshot, so it returns the
    *previous* run's output — exactly the ``agentfluent diff`` baseline.
    """
    directory = slug_dir(slug, root=root)
    if not directory.is_dir():
        return None
    snapshots = sorted(_iter_snapshots(directory))
    return snapshots[-1] if snapshots else None


def new_snapshot_path(slug: str, runstamp: str, *, root: Path | None = None) -> Path:
    """Path for this run's snapshot. ``runstamp`` must sort lexically by recency
    (e.g. a ``YYYYMMDDTHHMMSSZ`` UTC stamp), which is what makes ``latest_snapshot``
    a simple ``sorted()[-1]``. Creates the slug directory if absent."""
    if not _SAFE_SLUG_RE.match(runstamp):
        msg = f"unsafe runstamp for snapshot path: {runstamp!r}"
        raise ValueError(msg)
    directory = slug_dir(slug, root=root)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{SNAPSHOT_PREFIX}{runstamp}{SNAPSHOT_SUFFIX}"


def prune_snapshots(
    slug: str,
    *,
    keep: int = DEFAULT_SNAPSHOT_RETENTION,
    root: Path | None = None,
) -> list[Path]:
    """Delete all but the ``keep`` most-recent snapshots for ``slug``.

    Returns the deleted paths. ``keep <= 0`` is treated as ``keep = 1`` so a
    misconfiguration can never wipe the baseline the next run needs to diff.
    """
    keep = max(keep, 1)
    directory = slug_dir(slug, root=root)
    if not directory.is_dir():
        return []
    snapshots = sorted(_iter_snapshots(directory))
    stale = snapshots[:-keep] if len(snapshots) > keep else []
    for path in stale:
        path.unlink()
    return stale


def _iter_snapshots(directory: Path) -> list[Path]:
    return [
        p
        for p in directory.iterdir()
        if p.is_file()
        and p.name.startswith(SNAPSHOT_PREFIX)
        and p.name.endswith(SNAPSHOT_SUFFIX)
    ]
