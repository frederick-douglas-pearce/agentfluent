"""Tier 3 GitHub API wrapper.

Single public entry point — :func:`gh_api` — that signal extractors
call to fetch a GitHub resource. Responsibilities:

- Subprocess invocation of ``gh api`` (with optional ``--jq`` filter
  and ``-f`` query params).
- Cache lookup / write keyed on the request inputs (see
  :mod:`agentfluent.github.cache`).
- Rate-limit detection (HTTP 403 / 429) raising
  :class:`RateLimitedError`, which downstream extractors catch to set
  ``DiagnosticsResult.tier3_degraded = True`` and skip.

The wrapper is intentionally small. Per-endpoint logic (which jq
filter, which TTL, how to interpret the response) lives in the
signal modules — this file knows nothing about CI status,
PR reviews, or any specific endpoint.

# TODO(#400, #401): when concrete signal extractors land, define
# shared jq projection constants here for endpoints that both
# signals fetch (e.g. pulls/{n}) so cache entries can be reused.
# Deferred per architect review of #399 — no extractors exist yet
# to share them with.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentfluent.github import cache
from agentfluent.github.detection import detect_gh, gh_auth_login
from agentfluent.github.models import RateLimitedError

logger = logging.getLogger(__name__)

_GH_API_TIMEOUT_SEC = 60

# `gh api` surfaces rate-limit responses as "HTTP 403:" or "HTTP 429:" on
# stderr. Case-insensitive because ``gh``'s formatting isn't a
# contractual API — a future release that writes lowercase "http 429"
# must still trigger the soft-failure path. Without the IGNORECASE
# the keyword check below (which lower-cases) would accept the body
# while the code check would reject the prefix, and the rate-limit
# response would crash the analyze run.
_RATE_LIMIT_RE = re.compile(r"HTTP\s+(?P<code>403|429)\b", re.IGNORECASE)
_RATE_LIMIT_KEYWORDS = ("rate limit", "secondary rate limit", "abuse detection")

# ``gh api`` without ``-i`` does not surface response headers, so the
# precise reset time isn't available to us. The fallback used by
# :func:`_rate_limit_reset_fallback` is a 60-second skew — enough for
# extractors to log a meaningful WARNING; the value is informational
# rather than load-bearing for any current control-flow decision.
_RATE_LIMIT_FALLBACK_SEC = 60


def gh_api(
    endpoint: str,
    *,
    jq_filter: str | None = None,
    cache_ttl: int,
    query_params: dict[str, str] | None = None,
    no_cache: bool = False,
    cache_dir: Path | None = None,
) -> Any:
    """Fetch a GitHub API resource via the ``gh`` CLI, with caching.

    Args:
        endpoint: ``gh api`` endpoint path, e.g. ``repos/{owner}/{repo}/pulls/{n}``.
        jq_filter: Optional ``--jq`` expression passed to ``gh`` for
            server-side field selection. Part of the cache key — two
            callers with different projections do not share entries.
        cache_ttl: Per-call TTL (seconds). Pick from the three tier
            constants in :mod:`agentfluent.github.cache`.
        query_params: Mapping of query parameters; each becomes
            ``-f key=value`` on the ``gh`` command line.
        no_cache: If True, skip the cache *read* but still *write*
            the fresh response (next run sees cached data).
        cache_dir: Cache root override (tests only; production uses
            :func:`agentfluent_cache_dir`).

    Returns:
        Parsed JSON response (dict, list, or ``None`` for empty / null
        projections — cached the same way, served from cache on hit).

    Raises:
        GhNotInstalledError: ``gh`` binary not on PATH.
        GhNotAuthenticatedError: ``gh auth status`` non-zero.
        RateLimitedError: ``gh`` returned 403/429 with a rate-limit
            signature. Signal extractors catch this to flag Tier 3 as
            degraded and skip cleanly.
        RuntimeError: ``gh api`` returned a non-rate-limit error.
            Bubbles up so misconfiguration is loud, not silent.
        ValueError: Response was not valid JSON.
    """
    # Precondition: gh installed and authenticated. detect_gh is
    # lru_cached, so this is a no-op subprocess-cost after the first
    # successful call in a process — but it ensures programmatic
    # callers that skipped CLI-side detection get the right typed
    # exception (GhNotInstalledError) rather than a FileNotFoundError
    # mis-classified as auth failure inside gh_auth_login.
    detect_gh()

    user_login = gh_auth_login()
    key = cache.cache_key(
        endpoint=endpoint,
        query_params=query_params,
        jq_filter=jq_filter,
        auth_user_login=user_login,
    )
    if not no_cache:
        cached = cache.get(key, ttl=cache_ttl, cache_dir=cache_dir)
        # Sentinel comparison — NOT ``is not None`` — so a legitimately
        # cached ``None`` payload (empty stdout from gh, null --jq
        # projection) serves from cache instead of triggering a refetch
        # on every call.
        if cached is not cache.MISS:
            return cached

    cmd = ["gh", "api", endpoint]
    if jq_filter is not None:
        cmd.extend(["--jq", jq_filter])
    for k, v in (query_params or {}).items():
        cmd.extend(["-f", f"{k}={v}"])

    try:
        result = subprocess.run(  # noqa: S603 — args constructed from typed inputs
            cmd,
            capture_output=True,
            text=True,
            timeout=_GH_API_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "`gh` binary disappeared mid-run; was it removed from PATH?",
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"`gh api {endpoint}` timed out after {_GH_API_TIMEOUT_SEC}s.",
        ) from e

    if result.returncode != 0:
        _maybe_raise_rate_limit(endpoint, result.stderr)
        raise RuntimeError(
            f"`gh api {endpoint}` failed (exit {result.returncode}): "
            f"{result.stderr.strip()}",
        )

    stdout = result.stdout.strip()
    if not stdout:
        parsed: Any = None
    else:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"`gh api {endpoint}` returned non-JSON output: {stdout[:200]!r}",
            ) from e

    cache.set(
        key,
        parsed,
        endpoint=endpoint,
        jq_filter=jq_filter,
        cache_dir=cache_dir,
    )
    return parsed


def _maybe_raise_rate_limit(endpoint: str, stderr: str) -> None:
    """Inspect stderr for a rate-limit signature; raise if found.

    `gh api` does not surface response headers by default, so the
    signature detection is heuristic: an HTTP 403/429 line plus any
    of the rate-limit keywords GitHub uses in error bodies. Other
    403s (e.g. permission denied on a private repo) fall through to
    the generic RuntimeError path so misconfiguration stays loud.
    """
    if _RATE_LIMIT_RE.search(stderr) is None:
        return
    lowered = stderr.lower()
    if not any(kw in lowered for kw in _RATE_LIMIT_KEYWORDS):
        # 403 without rate-limit keywords is likely a permissions error
        # (private repo, missing scope) — propagate as generic failure.
        return
    reset_at = datetime.now(UTC) + timedelta(seconds=_RATE_LIMIT_FALLBACK_SEC)
    raise RateLimitedError(reset_at=reset_at, endpoint=endpoint)
