"""``cost_coverage``: the run-level block of what dollar figures cover (#676, #688)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentfluent.analytics.agent_metrics import AgentTypeMetrics
from agentfluent.analytics.cost_coverage import (
    PRICING_GAP_WARNING_CODE,
    CostCoverage,
    PricingCoverage,
    compute_cost_coverage,
    pricing_gap_warning,
)
from agentfluent.analytics.pipeline import (
    AnalysisResult,
    _merge_token_metrics,
    analyze_session,
    analyze_sessions,
)
from agentfluent.analytics.tokens import ModelTokenBreakdown, TokenMetrics, compute_token_metrics
from agentfluent.config.models import Severity
from agentfluent.core.session import SessionMessage, Usage
from agentfluent.diagnostics.model_routing import (
    PRICED_INVOCATION_COUNT_KEY,
    SAVINGS_USD_KEY,
    savings_is_partial,
    savings_scope_from_detail,
)
from agentfluent.diagnostics.models import (
    DiagnosticSignal,
    DiagnosticsResult,
    OffloadCandidate,
    SignalType,
)
from tests.unit.test_trace_coverage import (
    _NESTED_MAIN,
    _UNPRICED_MODEL,
    _build_session,
    _result,
    _spawn,
    _tok,
    _write,
)
from tests.unit.test_trace_coverage import (
    _assistant as _trace_assistant,
)

_PRICED = "claude-haiku-4-5-20251001"


def _row(model: str, tokens: int, *, unpriced: bool, origin: str = "parent") -> ModelTokenBreakdown:
    return ModelTokenBreakdown(
        model=model, input_tokens=tokens, cost=0.0 if unpriced else 1.0,
        origin=origin,  # type: ignore[arg-type]
        unpriced_tokens=tokens if unpriced else 0,
    )


def _agent(
    total: int, linked: int, spend: int, priced: int, name: str = "worker",
) -> AgentTypeMetrics:
    return AgentTypeMetrics(
        agent_type=name, is_builtin=False, invocation_count=total,
        linked_invocation_count=linked, spend_invocation_count=spend,
        priced_invocation_count=priced,
    )


def _mismatch(
    mismatch_type: str, savings: float | None, priced: int, total: int,
) -> DiagnosticSignal:
    return DiagnosticSignal(
        signal_type=SignalType.MODEL_MISMATCH, severity=Severity.WARNING,
        agent_type="worker", message="m",
        detail={
            "mismatch_type": mismatch_type, "invocation_count": total,
            SAVINGS_USD_KEY: savings, PRICED_INVOCATION_COUNT_KEY: priced,
        },
    )


def _offload(cost_priced: bool | None) -> OffloadCandidate:
    return OffloadCandidate(
        name="n", description="d", confidence="low", cluster_size=5,
        cohesion_score=0.5, alternative_model="claude-sonnet-4-6",
        cost_priced=cost_priced,
    )


def _assistant(model: str, tokens: int, msg_id: str) -> SessionMessage:
    return SessionMessage(
        type="assistant", model=model, message_id=msg_id,
        usage=Usage(input_tokens=tokens, output_tokens=0),
    )


class TestUnpricedTokens:
    """B1: a row's ``unpriced_tokens`` is the only signal its ``0.0`` cost is not "free"."""

    def test_parent_row_of_unpriced_model_records_its_tokens(self) -> None:
        metrics = compute_token_metrics([
            _assistant(_UNPRICED_MODEL, 500, "m1"), _assistant(_PRICED, 300, "m2"),
        ])
        rows = {r.model: r for r in metrics.by_model}
        assert rows[_UNPRICED_MODEL].unpriced_tokens == 500
        assert rows[_UNPRICED_MODEL].cost == 0.0
        assert rows[_PRICED].unpriced_tokens == 0
        assert rows[_PRICED].cost > 0

    def test_subagent_row_records_unpriced_trace_tokens(self, tmp_path: Path) -> None:
        analysis = analyze_session(_build_session(tmp_path))
        rows = {(r.model, r.origin): r for r in analysis.token_metrics.by_model}
        assert rows[(_UNPRICED_MODEL, "subagent")].unpriced_tokens == _tok("un1")
        assert rows[(_PRICED, "subagent")].unpriced_tokens == 0

    def test_merge_sums_unpriced_tokens(self) -> None:
        first = TokenMetrics(by_model=[_row(_UNPRICED_MODEL, 10, unpriced=True)])
        second = TokenMetrics(by_model=[_row(_UNPRICED_MODEL, 7, unpriced=True)])
        merged = _merge_token_metrics([first, second])
        assert merged.by_model[0].unpriced_tokens == 17

    def test_merge_carries_unpriced_tokens_of_a_single_row(self) -> None:
        single = TokenMetrics(by_model=[_row(_UNPRICED_MODEL, 9, unpriced=True)])
        merged = _merge_token_metrics([single])
        assert merged.by_model[0].unpriced_tokens == 9


class TestSubagentUnpricedSum:
    """D7: a subagent row SUMS its unpriced traces (``+=``, not ``=``)."""

    def _metrics(
        self, monkeypatch: pytest.MonkeyPatch, traces: list[tuple[int, float | None]],
    ) -> ModelTokenBreakdown:
        import agentfluent.analytics.tokens as tokens_mod

        fakes = [
            SimpleNamespace(model="claude-x", tokens=t, cost=c) for t, c in traces
        ]
        monkeypatch.setattr(tokens_mod, "trace_counts_as_spend", lambda trace: True)
        monkeypatch.setattr(
            tokens_mod, "trace_spend_usage",
            lambda trace: Usage(input_tokens=trace.tokens, output_tokens=0),
        )
        monkeypatch.setattr(tokens_mod, "price_trace", lambda trace, timestamp=None: trace.cost)
        rows = tokens_mod.compute_subagent_token_metrics(fakes)  # type: ignore[arg-type]
        assert len(rows) == 1
        return rows[0]

    def test_two_unpriced_traces_sum(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = self._metrics(monkeypatch, [(300, None), (200, None)])
        assert row.unpriced_tokens == 500
        assert row.cost == 0.0

    def test_partly_priced_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = self._metrics(monkeypatch, [(300, None), (200, 1.25), (50, None)])
        assert row.unpriced_tokens == 350
        assert 0 < row.unpriced_tokens < row.total_tokens == 550
        assert row.cost == pytest.approx(1.25)


class TestPricingCoverage:
    def test_full_pricing(self) -> None:
        coverage = compute_cost_coverage([_row(_PRICED, 100, unpriced=False)], {}, None)
        assert coverage.pricing == PricingCoverage(
            tokens=100, unpriced_tokens=0, unpriced_models={},
        )

    def test_partial_pricing_names_ids(self) -> None:
        rows = [
            _row(_PRICED, 100, unpriced=False),
            _row("claude-zeta-1", 30, unpriced=True),
            _row("claude-alpha-1", 20, unpriced=True, origin="subagent"),
            _row("claude-alpha-1", 5, unpriced=True),
        ]
        pricing = compute_cost_coverage(rows, {}, None).pricing
        assert pricing.tokens == 155
        assert pricing.unpriced_tokens == 55
        assert pricing.unpriced_models == {"claude-alpha-1": 25, "claude-zeta-1": 30}
        assert list(pricing.unpriced_models) == ["claude-alpha-1", "claude-zeta-1"]

    def test_zero_pricing_coverage(self) -> None:
        rows = [
            _row("claude-opus-5-5", 400, unpriced=True),
            _row("claude-fable-5-2", 100, unpriced=True),
        ]
        pricing = compute_cost_coverage(rows, {}, None).pricing
        assert pricing.unpriced_tokens == pricing.tokens == 500


class TestInvocationCoverage:
    def test_counts_sum_across_agent_types(self) -> None:
        by_type = {"a": _agent(4, 3, 3, 2, "a"), "b": _agent(2, 2, 1, 1, "b")}
        inv = compute_cost_coverage([], by_type, None).invocations
        assert (inv.total, inv.linked, inv.spend, inv.priced) == (6, 5, 4, 3)

    def test_linked_invocation_count_from_pipeline(self, tmp_path: Path) -> None:
        """B2: the ``runner`` invocation never returned, so it has no trace."""
        analysis = analyze_session(_build_session(tmp_path))
        by_type = analysis.agent_metrics.by_agent_type
        assert by_type["runner"].invocation_count == 1
        assert by_type["runner"].linked_invocation_count == 0
        assert by_type["worker"].linked_invocation_count == 1
        inv = compute_cost_coverage([], by_type, None).invocations
        assert (inv.total, inv.linked, inv.spend, inv.priced) == (4, 3, 3, 3)
        assert inv.priced <= inv.spend <= inv.linked <= inv.total

    def test_linked_counts_a_trace_that_is_not_spend(self) -> None:
        """A linked trace with no model is linked but not spend."""
        from agentfluent.agents.models import AgentInvocation
        from agentfluent.analytics.agent_metrics import compute_agent_metrics
        from agentfluent.traces.models import SubagentTrace

        trace = SubagentTrace(agent_id="a1", agent_type="worker", delegation_prompt="p")
        invocation = AgentInvocation(
            agent_type="worker", description="d", prompt="p", tool_use_id="t1",
        )
        invocation.attach_trace(trace)
        metrics = compute_agent_metrics([invocation]).by_agent_type["worker"]
        assert (metrics.invocation_count, metrics.linked_invocation_count) == (1, 1)
        assert metrics.spend_invocation_count == 0

    def test_invariant_holds_on_every_row(self, tmp_path: Path) -> None:
        """``priced <= spend <= linked <= invocation_count`` per agent type."""
        result = analyze_sessions([_build_session(tmp_path), _NESTED_MAIN])
        rows = result.agent_metrics.by_agent_type.values()
        assert rows
        for m in rows:
            assert (
                m.priced_invocation_count <= m.spend_invocation_count
                <= m.linked_invocation_count <= m.invocation_count
            ), m.agent_type

    def test_strict_chain_on_a_row_with_every_gap(self, tmp_path: Path) -> None:
        """r2.recheck.3: each link of ``priced < spend < linked < total`` is strict
        on one row, so a counter that ignores price, ignores the model response,
        or ignores the link fails here."""
        main = tmp_path / "chain.jsonl"
        _write(main, [
            {"type": "user", "message": {"role": "user", "content": "go"},
             "timestamp": "2026-11-01T00:00:00Z"},
            _trace_assistant("msg_main", "main1", [
                *_spawn("toolu_p", "worker"), *_spawn("toolu_u", "worker"),
                *_spawn("toolu_n", "worker"), *_spawn("toolu_m", "worker"),
            ], sidechain=False),
            _result("toolu_p", "p1", "worker"),
            _result("toolu_u", "u1", "worker"),
            _result("toolu_n", "n1", "worker"),
            # toolu_m never returns: that invocation has no linked trace.
        ])
        subagents = tmp_path / "chain" / "subagents"
        subagents.mkdir(parents=True)
        traces = {
            "p1": [_trace_assistant("msg_p1", "w1")],
            "u1": [_trace_assistant("msg_u1", "fA", model=_UNPRICED_MODEL)],
            # No assistant turn: linked, but no model response, so no spend.
            "n1": [{"type": "user", "isSidechain": True,
                    "message": {"role": "user", "content": "task"},
                    "timestamp": "2026-11-01T00:00:01Z"}],
        }
        for agent_id, lines in traces.items():
            _write(subagents / f"agent-{agent_id}.jsonl", lines)
            (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps({
                "agentType": "worker", "toolUseId": f"toolu_{agent_id[0]}",
            }))
        row = analyze_sessions([main]).agent_metrics.by_agent_type["worker"]
        assert (
            row.priced_invocation_count, row.spend_invocation_count,
            row.linked_invocation_count, row.invocation_count,
        ) == (1, 2, 3, 4)

    def test_agent_filter_scopes_coverage_like_the_totals(self, tmp_path: Path) -> None:
        """D8: coverage describes the same scope as the totals it qualifies.

        ``--agent worker`` filters the unpriced session-spawned trace out of
        ``token_metrics``, so it leaves ``pricing`` too; the unfiltered run
        reports it.
        """
        main = _build_session(tmp_path)
        unfiltered = analyze_sessions([main]).cost_coverage
        filtered = analyze_sessions([main], agent_filter="worker").cost_coverage
        assert unfiltered is not None and filtered is not None
        assert _UNPRICED_MODEL in unfiltered.pricing.unpriced_models
        assert filtered.pricing.unpriced_models == {}
        assert (filtered.invocations.total, filtered.invocations.linked) == (1, 1)

    def test_merge_sums_linked_counts(self, tmp_path: Path) -> None:
        main = _build_session(tmp_path)
        result = analyze_sessions([main, main])
        assert result.agent_metrics.by_agent_type["worker"].linked_invocation_count == 2


class TestDiagnosticsCoverage:
    def test_savings_partial_and_unpriced_figures(self) -> None:
        diagnostics = DiagnosticsResult(
            signals=[
                _mismatch("overspec", 1.5, priced=3, total=3),
                _mismatch("overspec", 0.5, priced=1, total=4),
                _mismatch("overspec", None, priced=0, total=4),
                _mismatch("underspec", None, priced=0, total=4),
            ],
            offload_candidates=[_offload(True), _offload(False), _offload(None)],
        )
        coverage = compute_cost_coverage([], {}, diagnostics).diagnostics
        assert coverage is not None
        assert coverage.savings_figures == 3
        assert coverage.partial_figures == 1
        assert coverage.unpriced_figures == 2

    def test_non_mismatch_signals_are_ignored(self) -> None:
        signal = DiagnosticSignal(
            signal_type=next(s for s in SignalType if s != SignalType.MODEL_MISMATCH),
            severity=Severity.WARNING, agent_type="worker", message="m",
            detail={SAVINGS_USD_KEY: 9.0, "mismatch_type": "overspec"},
        )
        coverage = compute_cost_coverage([], {}, DiagnosticsResult(signals=[signal])).diagnostics
        assert coverage is not None
        assert (coverage.savings_figures, coverage.unpriced_figures) == (0, 0)

    def test_no_diagnostics_is_none(self) -> None:
        assert compute_cost_coverage([], {}, None).diagnostics is None

    @pytest.mark.parametrize(
        ("detail", "partial"),
        [
            ({"invocation_count": 4, PRICED_INVOCATION_COUNT_KEY: 4}, False),
            ({"invocation_count": 4, PRICED_INVOCATION_COUNT_KEY: 3}, True),
            ({"invocation_count": 4}, False),
        ],
    )
    def test_savings_is_partial_matches_scope_phrase(
        self, detail: dict[str, Any], partial: bool,
    ) -> None:
        assert savings_is_partial(detail) is partial
        assert (" of " in savings_scope_from_detail(detail)) is partial


class TestAnalysisResultField:
    def test_end_to_end_block(self, tmp_path: Path) -> None:
        result = analyze_sessions([_build_session(tmp_path)])
        coverage = result.cost_coverage
        assert coverage is not None
        assert coverage.pricing.unpriced_models == {_UNPRICED_MODEL: _tok("un1")}
        assert coverage.pricing.tokens == result.token_metrics.total_tokens
        assert coverage.invocations.total == 4
        assert coverage.invocations.linked == 3

    def test_survives_json_rehydration(self, tmp_path: Path) -> None:
        result = analyze_sessions([_build_session(tmp_path)])
        payload = json.loads(result.model_dump_json())
        assert payload["cost_coverage"]["invocations"]["linked"] == 3
        rehydrated = AnalysisResult.model_validate(payload)
        assert rehydrated.cost_coverage == result.cost_coverage

    def test_legacy_envelope_is_unknown_not_full(self, tmp_path: Path) -> None:
        """B3: a session without ``trace_coverage`` predates v0.12.0 -- unknown."""
        payload = json.loads(analyze_sessions([_build_session(tmp_path)]).model_dump_json())
        for session in payload["sessions"]:
            del session["trace_coverage"]
        del payload["trace_coverage"], payload["cost_coverage"]
        legacy = AnalysisResult.model_validate(payload)
        assert legacy.sessions[0].trace_coverage is None
        assert legacy.trace_coverage is None
        assert legacy.cost_coverage is None
        assert json.loads(legacy.model_dump_json())["cost_coverage"] is None

    def test_one_legacy_session_makes_the_run_unknown(self, tmp_path: Path) -> None:
        payload = json.loads(
            analyze_sessions([_build_session(tmp_path)]).model_dump_json(),
        )
        modern = payload["sessions"][0]
        legacy = {k: v for k, v in modern.items() if k != "trace_coverage"}
        payload["sessions"] = [modern, legacy]
        result = AnalysisResult.model_validate(payload)
        assert result.trace_coverage is None
        assert result.cost_coverage is None

    def test_no_sessions_is_measured_empty(self) -> None:
        coverage = AnalysisResult().cost_coverage
        assert coverage == CostCoverage()


class TestPricingGapWarning:
    def test_warns_and_names_ids(self) -> None:
        coverage = compute_cost_coverage(
            [_row(_PRICED, 100, unpriced=False), _row("claude-zeta-1", 1_500, unpriced=True)],
            {}, None,
        )
        warning = pricing_gap_warning(coverage)
        assert warning is not None
        assert warning.code == PRICING_GAP_WARNING_CODE
        assert warning.severity == Severity.WARNING
        assert "1,500 of 1,600 tokens" in warning.message
        assert "claude-zeta-1" in warning.message

    def test_no_warning_when_fully_priced_or_unknown(self) -> None:
        priced = compute_cost_coverage([_row(_PRICED, 5, unpriced=False)], {}, None)
        assert pricing_gap_warning(priced) is None
        assert pricing_gap_warning(None) is None
