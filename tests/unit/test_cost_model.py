"""Tests for the cost-model stamp (#543): ``analytics.cost_model``."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from agentfluent.analytics import _genai_source
from agentfluent.analytics.cost_model import (
    COST_METHOD_VERSION,
    CostModelStamp,
    current_cost_model_stamp,
)
from agentfluent.analytics.pipeline import AnalysisResult, analyze_sessions

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Sessions chosen to exercise parent pricing across several models, a linked subagent
# trace (per-agent spend, #646) and a nested trace folded into the session total (#648).
_TRIPWIRE_SESSIONS = [
    FIXTURES / "session_basic.jsonl",
    FIXTURES / "session_with_agent.jsonl",
    FIXTURES / "nested_session" / "nested-session-1.jsonl",
    FIXTURES / "parent_workload_session.jsonl",
]

# (COST_METHOD_VERSION, total cost, {(model, origin): cost}, {agent_type: cost}).
_PINNED = (
    1,
    0.21237365,
    {
        ("claude-sonnet-4-20250514", "parent"): 0.022125,
        ("claude-opus-4-6", "parent"): 0.13575,
        ("claude-haiku-4-5-20251001", "parent"): 0.000138,
        ("claude-haiku-4-5-20251001", "subagent"): 0.02221065,
        ("claude-opus-4-7", "parent"): 0.03215,
    },
    {"pm": 0.0, "explore": 0.0, "worker": 0.01429065},
)


def test_cost_method_tripwire() -> None:
    """Fixture dollars are pinned together with ``COST_METHOD_VERSION``.

    If this fails because the costs moved: did *how* cost is computed change (spend
    source, fold, dedup, overlay levers, a rate-altering curation edit)? Then bump
    ``COST_METHOD_VERSION`` in ``analytics/cost_model.py`` (add a version-history line)
    and re-pin. If only the genai-prices pin moved, re-pin without bumping -- the stamp's
    ``genai_prices_version`` already records that. This guards only the code paths these
    fixtures exercise.
    """
    result = analyze_sessions(_TRIPWIRE_SESSIONS)
    observed = (
        COST_METHOD_VERSION,
        round(result.token_metrics.total_cost, 8),
        {
            (m.model, m.origin): round(m.cost, 8)
            for m in result.token_metrics.by_model
        },
        {
            agent_type: round(metrics.estimated_total_cost_usd, 8)
            for agent_type, metrics in result.agent_metrics.by_agent_type.items()
        },
    )
    assert observed == _PINNED, test_cost_method_tripwire.__doc__


class TestCurrentStamp:
    def test_reads_pin_from_installed_metadata(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The pin comes from the installed distribution, never a literal, so a pin bump
        is detected without a manual step."""
        looked_up: list[str] = []

        def _sentinel(name: str) -> str:
            looked_up.append(name)
            return "9.9.9-sentinel"

        monkeypatch.setattr(_genai_source, "_dist_version", _sentinel)
        stamp = current_cost_model_stamp()
        assert stamp.genai_prices_version == "9.9.9-sentinel"
        assert looked_up == ["genai-prices"]
        assert stamp.cost_method_version == COST_METHOD_VERSION

    def test_unknown_pin_when_distribution_metadata_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _missing(_name: str) -> str:
            raise PackageNotFoundError("genai-prices")

        monkeypatch.setattr(_genai_source, "_dist_version", _missing)
        stamp = current_cost_model_stamp()
        assert stamp.genai_prices_version is None
        assert stamp.cost_method_version == COST_METHOD_VERSION


class TestLegacyRehydration:
    def test_legacy_envelope_rehydrates_without_a_stamp(self) -> None:
        """A pre-v0.12.0 envelope must never pick up the reader's own cost model."""
        result = AnalysisResult.model_validate({"session_count": 1})
        assert result.cost_model is None

    def test_stamped_envelope_round_trips(self) -> None:
        stamp = CostModelStamp(genai_prices_version="0.2.3", cost_method_version=1)
        dumped = AnalysisResult(cost_model=stamp).model_dump(mode="json")
        assert AnalysisResult.model_validate(dumped).cost_model == stamp
