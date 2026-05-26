"""File-backed TTL cache for Tier 3 GitHub responses.

One JSON file per cache entry, named by SHA-256 over the request
inputs. The key deliberately includes the ``--jq`` filter so two
callers hitting the same endpoint with different projections do not
collide on disk — a cached response filtered to ``mergeable_state``
must never be served to a caller that needs ``additions + deletions``.

TTLs are caller-chosen (passed in per :func:`get` call) rather than
encoded inside the cache, so the three constants in this module
(:data:`TTL_CLOSED_PR`, :data:`TTL_OPEN_PR_OR_CI`,
:data:`TTL_REPO_METADATA`) are just shared defaults that signal
extractors import; the cache itself is tier-agnostic.

Cache location: ``agentfluent_cache_dir() / "github" / "<sha256>.json"``.
The directory is created on first write; tests pass a ``cache_dir``
override to keep their state isolated.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentfluent.core.paths import agentfluent_cache_dir

logger = logging.getLogger(__name__)

# Cache TTL tiers (seconds). Three tiers per architect note 3 in #399
# review. Closed PRs are immutable; open PRs / CI status are mutable;
# repo metadata changes slowly. Callers pick the right one per
# endpoint — the cache itself just respects whatever TTL it is handed.
TTL_CLOSED_PR = 7 * 86400  # 7 days
TTL_OPEN_PR_OR_CI = 15 * 60  # 15 minutes
TTL_REPO_METADATA = 24 * 3600  # 24 hours

_CACHE_SUBDIR = "github"
_TIMESTAMP_KEY = "_cached_at"


def cache_key(
    *,
    endpoint: str,
    query_params: dict[str, str] | None,
    jq_filter: str | None,
    auth_user_login: str,
) -> str:
    """Build a SHA-256 cache key from request inputs.

    The key includes ``jq_filter`` so that two callers hitting the same
    endpoint with different field projections get different cache
    entries (architect note 1 in #399 review). ``auth_user_login`` is
    included so a shared machine doesn't leak cache state between
    accounts. ``query_params`` is serialized with ``sort_keys=True`` so
    equivalent dicts hash to the same key regardless of insertion
    order.
    """
    parts = [
        endpoint,
        json.dumps(query_params or {}, sort_keys=True, separators=(",", ":")),
        jq_filter or "",
        auth_user_login,
    ]
    payload = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cache_root(cache_dir: Path | None) -> Path:
    return (cache_dir if cache_dir is not None else agentfluent_cache_dir()) / _CACHE_SUBDIR


def _entry_path(key: str, *, cache_dir: Path | None) -> Path:
    return _cache_root(cache_dir) / f"{key}.json"


def get(
    key: str,
    *,
    ttl: int,
    cache_dir: Path | None = None,
    now: datetime | None = None,
) -> Any:
    """Read a cache entry. Returns the cached payload or ``None``.

    Returns ``None`` on cache miss, expiry, or any read/parse error.
    A corrupted entry is treated as a miss (logged at DEBUG) rather
    than raising — Tier 3 should never crash because of a stale cache
    file on disk.

    ``now`` defaults to ``datetime.now(UTC)``; tests pass an explicit
    value to exercise the expiry boundary deterministically.
    """
    path = _entry_path(key, cache_dir=cache_dir)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.debug("cache entry unreadable: %s", path, exc_info=True)
        return None

    cached_at_raw = payload.get(_TIMESTAMP_KEY)
    if not isinstance(cached_at_raw, str):
        return None
    try:
        cached_at = datetime.fromisoformat(cached_at_raw)
    except ValueError:
        return None

    current = now if now is not None else datetime.now(UTC)
    age_sec = (current - cached_at).total_seconds()
    if age_sec >= ttl:
        return None

    return payload.get("data")


def set(  # noqa: A001 — name parallels `get`
    key: str,
    value: Any,
    *,
    endpoint: str,
    jq_filter: str | None,
    cache_dir: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Write a cache entry. Silently swallows write errors.

    A failed cache write must never block the actual API response from
    being returned to the caller, so OS errors are logged at DEBUG and
    swallowed. ``endpoint`` and ``jq_filter`` are persisted alongside
    the payload for human debuggability — a user inspecting the cache
    dir can see which endpoint a hash corresponds to.
    """
    root = _cache_root(cache_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.debug("could not create cache dir %s", root, exc_info=True)
        return

    current = now if now is not None else datetime.now(UTC)
    payload = {
        _TIMESTAMP_KEY: current.isoformat(),
        "endpoint": endpoint,
        "jq_filter": jq_filter,
        "data": value,
    }
    path = _entry_path(key, cache_dir=cache_dir)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError:
        logger.debug("could not write cache entry %s", path, exc_info=True)


def clear_all(*, cache_dir: Path | None = None) -> None:
    """Remove every entry under the github cache root.

    Intended for tests and an eventual ``agentfluent github wipe-cache``
    CLI command. Silently no-ops when the directory does not exist.
    """
    root = _cache_root(cache_dir)
    if not root.exists():
        return
    for entry in root.glob("*.json"):
        try:
            entry.unlink()
        except OSError:
            logger.debug("could not remove cache entry %s", entry, exc_info=True)
