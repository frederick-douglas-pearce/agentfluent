"""Tests for the ``gh_api`` wrapper.

The wrapper layers cache + subprocess + rate-limit detection over
``gh api``. Each test patches ``subprocess.run`` and the auth-user
memo to keep behavior deterministic without `gh` on PATH.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentfluent.github import client, detection
from agentfluent.github.models import RateLimitedError


@pytest.fixture(autouse=True)
def _stub_auth_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid the real ``gh api user`` lookup for every test in this file."""
    detection.gh_auth_login.cache_clear()
    monkeypatch.setattr(
        "agentfluent.github.client.gh_auth_login", lambda: "alice",
    )


def _fake_run(
    *, stdout: str = "", stderr: str = "", returncode: int = 0,
) -> Any:
    def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=_args, returncode=returncode, stdout=stdout, stderr=stderr,
        )
    return runner


class TestSuccess:
    def test_parses_json_response(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.client.subprocess.run",
            _fake_run(stdout='{"number": 42}'),
        )
        out = client.gh_api(
            "repos/owner/r/pulls/42",
            cache_ttl=60,
            cache_dir=tmp_path,
        )
        assert out == {"number": 42}

    def test_writes_cache_entry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.client.subprocess.run",
            _fake_run(stdout='{"v": 1}'),
        )
        client.gh_api("x", cache_ttl=60, cache_dir=tmp_path)
        cached_files = list((tmp_path / "github").glob("*.json"))
        assert len(cached_files) == 1

    def test_cache_hit_skips_subprocess(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        calls = {"count": 0}

        def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls["count"] += 1
            return subprocess.CompletedProcess(
                args=_args, returncode=0, stdout='{"v": 1}', stderr="",
            )

        monkeypatch.setattr("agentfluent.github.client.subprocess.run", runner)
        client.gh_api("x", cache_ttl=60, cache_dir=tmp_path)
        client.gh_api("x", cache_ttl=60, cache_dir=tmp_path)
        assert calls["count"] == 1

    def test_no_cache_bypasses_read_but_writes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # Seed the cache with stale data, then run with no_cache=True.
        # The fresh response must come back, AND the stale entry must
        # be overwritten so the next no_cache=False run sees the new value.
        from agentfluent.github import cache as cache_mod
        key = cache_mod.cache_key(
            endpoint="x", query_params=None, jq_filter=None,
            auth_user_login="alice",
        )
        cache_mod.set(
            key, {"stale": True},
            endpoint="x", jq_filter=None, cache_dir=tmp_path,
        )

        monkeypatch.setattr(
            "agentfluent.github.client.subprocess.run",
            _fake_run(stdout='{"fresh": true}'),
        )
        out = client.gh_api(
            "x", cache_ttl=60, cache_dir=tmp_path, no_cache=True,
        )
        assert out == {"fresh": True}
        # Subsequent cached read sees the freshly written entry.
        assert cache_mod.get(key, ttl=60, cache_dir=tmp_path) == {"fresh": True}


class TestErrors:
    def test_rate_limit_403_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.client.subprocess.run",
            _fake_run(
                returncode=1,
                stderr=(
                    "gh: HTTP 403: API rate limit exceeded for user alice "
                    "(see https://docs.github.com/...)"
                ),
            ),
        )
        with pytest.raises(RateLimitedError):
            client.gh_api("x", cache_ttl=60, cache_dir=tmp_path)

    def test_rate_limit_429_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.client.subprocess.run",
            _fake_run(
                returncode=1,
                stderr=(
                    "gh: HTTP 429: You have exceeded a secondary rate limit "
                    "and have been temporarily blocked"
                ),
            ),
        )
        with pytest.raises(RateLimitedError):
            client.gh_api("x", cache_ttl=60, cache_dir=tmp_path)

    def test_permission_403_propagates_as_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.client.subprocess.run",
            _fake_run(
                returncode=1,
                stderr="gh: HTTP 403: Resource not accessible by integration",
            ),
        )
        with pytest.raises(RuntimeError, match="failed"):
            client.gh_api("x", cache_ttl=60, cache_dir=tmp_path)

    def test_other_failure_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.client.subprocess.run",
            _fake_run(returncode=1, stderr="gh: HTTP 500"),
        )
        with pytest.raises(RuntimeError, match="failed"):
            client.gh_api("x", cache_ttl=60, cache_dir=tmp_path)

    def test_invalid_json_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.client.subprocess.run",
            _fake_run(stdout="not-json"),
        )
        with pytest.raises(ValueError, match="non-JSON"):
            client.gh_api("x", cache_ttl=60, cache_dir=tmp_path)
