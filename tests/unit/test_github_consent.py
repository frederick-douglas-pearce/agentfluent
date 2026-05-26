"""Tests for the Tier 3 consent flow.

Covers the three invariants the consent surface promises:

1. **TTY path** prompts once, records on accept, returns False on
   decline, does not re-prompt on subsequent calls.
2. **Non-TTY path** records silently and returns True — the
   ``--github`` CLI flag itself is per-invocation consent.
3. **Schema extensibility** — writing a second consent key under
   ``consents`` does not destroy an earlier entry, satisfying the
   architect-mandated forward-compat invariant for v0.8.x and beyond.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentfluent.github import consent


class TestPrompt:
    def test_tty_accept_records_and_returns_true(self, tmp_path: Path) -> None:
        result = consent.prompt_and_record_if_needed(
            is_tty=True,
            config_dir=tmp_path,
            cache_dir_display=tmp_path / "fake-cache",
            input_fn=lambda _prompt: "y",
            output_fn=lambda _msg: None,
        )
        assert result is True
        assert consent.has_consent(config_dir=tmp_path)

    def test_tty_decline_returns_false_and_no_record(self, tmp_path: Path) -> None:
        result = consent.prompt_and_record_if_needed(
            is_tty=True,
            config_dir=tmp_path,
            cache_dir_display=tmp_path / "fake-cache",
            input_fn=lambda _prompt: "n",
            output_fn=lambda _msg: None,
        )
        assert result is False
        assert not consent.has_consent(config_dir=tmp_path)

    def test_tty_empty_answer_treated_as_decline(self, tmp_path: Path) -> None:
        result = consent.prompt_and_record_if_needed(
            is_tty=True,
            config_dir=tmp_path,
            cache_dir_display=tmp_path / "fake-cache",
            input_fn=lambda _prompt: "",
            output_fn=lambda _msg: None,
        )
        assert result is False

    def test_tty_eof_treated_as_decline(self, tmp_path: Path) -> None:
        def raise_eof(_prompt: str) -> str:
            raise EOFError

        result = consent.prompt_and_record_if_needed(
            is_tty=True,
            config_dir=tmp_path,
            cache_dir_display=tmp_path / "fake-cache",
            input_fn=raise_eof,
            output_fn=lambda _msg: None,
        )
        assert result is False

    def test_repeat_call_skips_prompt(self, tmp_path: Path) -> None:
        # A user with prior consent must NOT see the disclosure or
        # the prompt again — check both input_fn and output_fn are
        # untouched, not just one. (Prior version of this test only
        # asserted input_fn was untouched, so a regression that
        # re-printed the disclosure on every run would have passed.)
        consent.record_consent(config_dir=tmp_path)
        input_calls = {"count": 0}
        output_calls = {"count": 0}

        def fake_input(_prompt: str) -> str:
            input_calls["count"] += 1
            return "y"

        def fake_output(_msg: str) -> None:
            output_calls["count"] += 1

        result = consent.prompt_and_record_if_needed(
            is_tty=True,
            config_dir=tmp_path,
            cache_dir_display=tmp_path / "fake-cache",
            input_fn=fake_input,
            output_fn=fake_output,
        )
        assert result is True
        assert input_calls["count"] == 0
        assert output_calls["count"] == 0


class TestNonTty:
    def test_non_tty_auto_consents(self, tmp_path: Path) -> None:
        result = consent.prompt_and_record_if_needed(
            is_tty=False,
            config_dir=tmp_path,
            cache_dir_display=tmp_path / "fake-cache",
        )
        assert result is True
        assert consent.has_consent(config_dir=tmp_path)


class TestDisclosureOutput:
    def test_default_disclosure_writes_to_stderr_not_stdout(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # The default output target is stderr so JSON piped from
        # stdout (e.g. `agentfluent analyze --github --json | jq`)
        # is not corrupted by the disclosure text.
        consent.prompt_and_record_if_needed(
            is_tty=True,
            config_dir=tmp_path,
            cache_dir_display=tmp_path / "fake-cache",
            input_fn=lambda _prompt: "y",
            # output_fn not supplied → default = stderr writer
        )
        captured = capsys.readouterr()
        assert "AgentFluent's --github" in captured.err
        assert "AgentFluent's --github" not in captured.out


class TestSchema:
    def test_record_then_load_round_trip(self, tmp_path: Path) -> None:
        consent.record_consent(config_dir=tmp_path)
        path = consent.consent_path(config_dir=tmp_path)
        data = json.loads(path.read_text())
        assert data["version"] == 1
        assert "github_api" in data["consents"]
        assert "granted_at" in data["consents"]["github_api"]
        assert data["consents"]["github_api"]["version"] == 1

    def test_second_consent_key_preserves_first(self, tmp_path: Path) -> None:
        # Pretend a future AgentFluent release records a second consent
        # surface (e.g., telemetry). The earlier github_api entry must
        # survive — that is the schema's whole reason to exist.
        consent.record_consent(config_dir=tmp_path)
        original = json.loads(consent.consent_path(config_dir=tmp_path).read_text())
        original_granted_at = original["consents"]["github_api"]["granted_at"]

        consent.record_consent(surface="telemetry", config_dir=tmp_path)
        merged = json.loads(consent.consent_path(config_dir=tmp_path).read_text())

        assert "github_api" in merged["consents"]
        assert "telemetry" in merged["consents"]
        assert (
            merged["consents"]["github_api"]["granted_at"]
            == original_granted_at
        )

    def test_has_consent_false_for_unrelated_surface(self, tmp_path: Path) -> None:
        consent.record_consent(config_dir=tmp_path)
        assert consent.has_consent(surface="telemetry", config_dir=tmp_path) is False
