"""Tests for the Tier 3 file-backed TTL cache.

Verifies the cache invariants:

1. **Key uniqueness** — endpoint, query params, jq filter, and auth
   user login each contribute to the SHA-256, so distinct projections
   of the same endpoint do not collide.
2. **TTL respected** — entries within the window read, entries past
   the window miss.
3. **Schema** — files store ``_cached_at`` + ``endpoint`` + ``jq_filter``
   + ``data`` so the cache directory is human-debuggable.
4. **Soft failure** — corrupted, unreadable, or naive-timestamp
   entries return the :data:`MISS` sentinel instead of raising.
5. **Sentinel return** — :func:`get` returns :data:`MISS` on miss
   and the cached value (which may be ``None``) on hit, so a
   legitimately-cached ``None`` payload doesn't trigger a refetch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentfluent.github import cache


def _key() -> str:
    return cache.cache_key(
        endpoint="repos/owner/r/pulls/1",
        query_params=None,
        jq_filter=None,
        auth_user_login="alice",
    )


class TestCacheKey:
    def test_endpoint_changes_key(self) -> None:
        k1 = cache.cache_key(
            endpoint="a", query_params=None, jq_filter=None, auth_user_login="u",
        )
        k2 = cache.cache_key(
            endpoint="b", query_params=None, jq_filter=None, auth_user_login="u",
        )
        assert k1 != k2

    def test_jq_filter_changes_key(self) -> None:
        k1 = cache.cache_key(
            endpoint="a", query_params=None, jq_filter=".x", auth_user_login="u",
        )
        k2 = cache.cache_key(
            endpoint="a", query_params=None, jq_filter=".y", auth_user_login="u",
        )
        assert k1 != k2

    def test_auth_user_changes_key(self) -> None:
        k1 = cache.cache_key(
            endpoint="a", query_params=None, jq_filter=None, auth_user_login="alice",
        )
        k2 = cache.cache_key(
            endpoint="a", query_params=None, jq_filter=None, auth_user_login="bob",
        )
        assert k1 != k2

    def test_query_params_order_invariant(self) -> None:
        k1 = cache.cache_key(
            endpoint="a", query_params={"x": "1", "y": "2"},
            jq_filter=None, auth_user_login="u",
        )
        k2 = cache.cache_key(
            endpoint="a", query_params={"y": "2", "x": "1"},
            jq_filter=None, auth_user_login="u",
        )
        assert k1 == k2

    def test_none_query_params_equals_empty(self) -> None:
        k1 = cache.cache_key(
            endpoint="a", query_params=None,
            jq_filter=None, auth_user_login="u",
        )
        k2 = cache.cache_key(
            endpoint="a", query_params={},
            jq_filter=None, auth_user_login="u",
        )
        assert k1 == k2


class TestGetSet:
    def test_set_then_get_round_trip(self, tmp_path: Path) -> None:
        cache.set(  # noqa: SLF001 — function is the public API
            _key(), {"hello": "world"},
            endpoint="repos/owner/r/pulls/1", jq_filter=None,
            cache_dir=tmp_path,
        )
        result = cache.get(_key(), ttl=60, cache_dir=tmp_path)
        assert result == {"hello": "world"}

    def test_missing_entry_returns_miss(self, tmp_path: Path) -> None:
        assert cache.get(_key(), ttl=60, cache_dir=tmp_path) is cache.MISS

    def test_expired_entry_returns_miss(self, tmp_path: Path) -> None:
        past = datetime.now(UTC) - timedelta(seconds=120)
        cache.set(
            _key(), {"v": 1},
            endpoint="x", jq_filter=None,
            cache_dir=tmp_path, now=past,
        )
        assert cache.get(_key(), ttl=60, cache_dir=tmp_path) is cache.MISS

    def test_cached_null_is_hit_not_miss(self, tmp_path: Path) -> None:
        # The whole point of the MISS sentinel: a legitimately cached
        # ``None`` (empty stdout from gh, or a --jq filter that yields
        # null) must serve from the cache, not trigger re-fetch every
        # call. cache.get must distinguish "cached None" from "no entry".
        cache.set(
            _key(), None,
            endpoint="x", jq_filter=".missing_field",
            cache_dir=tmp_path,
        )
        result = cache.get(_key(), ttl=60, cache_dir=tmp_path)
        assert result is None
        assert result is not cache.MISS

    def test_unexpired_entry_returns_value(self, tmp_path: Path) -> None:
        recent = datetime.now(UTC) - timedelta(seconds=10)
        cache.set(
            _key(), {"v": 1},
            endpoint="x", jq_filter=None,
            cache_dir=tmp_path, now=recent,
        )
        assert cache.get(_key(), ttl=60, cache_dir=tmp_path) == {"v": 1}

    def test_distinct_jq_filters_isolated(self, tmp_path: Path) -> None:
        k_a = cache.cache_key(
            endpoint="repos/o/r/pulls/1", query_params=None,
            jq_filter=".additions", auth_user_login="alice",
        )
        k_b = cache.cache_key(
            endpoint="repos/o/r/pulls/1", query_params=None,
            jq_filter=".mergeable_state", auth_user_login="alice",
        )
        cache.set(
            k_a, 100,
            endpoint="repos/o/r/pulls/1", jq_filter=".additions",
            cache_dir=tmp_path,
        )
        cache.set(
            k_b, "clean",
            endpoint="repos/o/r/pulls/1", jq_filter=".mergeable_state",
            cache_dir=tmp_path,
        )
        assert cache.get(k_a, ttl=60, cache_dir=tmp_path) == 100
        assert cache.get(k_b, ttl=60, cache_dir=tmp_path) == "clean"


class TestSchema:
    def test_file_contains_required_keys(self, tmp_path: Path) -> None:
        cache.set(
            _key(), {"foo": 1},
            endpoint="repos/o/r", jq_filter=".x",
            cache_dir=tmp_path,
        )
        files = list((tmp_path / "github").glob("*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text())
        assert "_cached_at" in payload
        assert payload["endpoint"] == "repos/o/r"
        assert payload["jq_filter"] == ".x"
        assert payload["data"] == {"foo": 1}


class TestSoftFailure:
    def test_corrupted_entry_returns_miss(self, tmp_path: Path) -> None:
        github_dir = tmp_path / "github"
        github_dir.mkdir()
        (github_dir / f"{_key()}.json").write_text("not-json")
        assert cache.get(_key(), ttl=60, cache_dir=tmp_path) is cache.MISS

    def test_naive_timestamp_returns_miss(self, tmp_path: Path) -> None:
        # A hand-edited or downgrade-corrupted file with a naive ISO
        # timestamp would crash the cached-at - now subtraction
        # ("can't subtract offset-naive and offset-aware datetimes").
        # The wrapper must treat it as MISS, not raise.
        github_dir = tmp_path / "github"
        github_dir.mkdir()
        (github_dir / f"{_key()}.json").write_text(
            json.dumps({
                "_cached_at": "2026-01-01T00:00:00",  # naive — no tz
                "endpoint": "x",
                "jq_filter": None,
                "data": {"v": 1},
            }),
        )
        assert cache.get(_key(), ttl=60, cache_dir=tmp_path) is cache.MISS

    def test_missing_data_key_returns_miss(self, tmp_path: Path) -> None:
        # A file with everything but the "data" key (malformed write)
        # should fall through to MISS rather than return None implicitly.
        github_dir = tmp_path / "github"
        github_dir.mkdir()
        (github_dir / f"{_key()}.json").write_text(
            json.dumps({
                "_cached_at": datetime.now(UTC).isoformat(),
                "endpoint": "x",
                "jq_filter": None,
                # no "data" key
            }),
        )
        assert cache.get(_key(), ttl=60, cache_dir=tmp_path) is cache.MISS

    def test_clear_all_removes_entries(self, tmp_path: Path) -> None:
        cache.set(
            _key(), {"a": 1},
            endpoint="x", jq_filter=None,
            cache_dir=tmp_path,
        )
        assert list((tmp_path / "github").glob("*.json"))
        cache.clear_all(cache_dir=tmp_path)
        assert not list((tmp_path / "github").glob("*.json"))

    def test_clear_all_on_missing_dir_is_noop(self, tmp_path: Path) -> None:
        cache.clear_all(cache_dir=tmp_path)  # no raise
