"""Tests for SDK main-session model-routing diagnostics (#112).

Covers ``extract_sdk_main_session_signals`` / ``_build_main_session_stats``:
detection gated to ``session_kind == "sdk"`` (AC#1/#7), complexity applied
to the main session's own per-turn stats (AC#2), overspec/underspec emission
with a ``routing_scope="main_session"`` discriminator (AC#3/#6), the per-turn
token formula (input + cache_creation + output, excluding cache_read), and the
per-configured-model aggregation keying (architect C2).
"""

from __future__ import annotations

from pathlib import Path

from agentfluent.analytics.agent_metrics import AgentMetrics
from agentfluent.analytics.pipeline import SessionAnalysis
from agentfluent.analytics.pricing import MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.core.session import (
    ContentBlock,
    SessionClass,
    SessionMessage,
    Usage,
)
from agentfluent.diagnostics.model_routing import (
    _build_main_session_stats,
    _turn_work_tokens,
    extract_sdk_main_session_signals,
)


def _assistant_turn(
    model: str,
    *,
    input_tokens: int = 10,
    output_tokens: int = 10,
    cache_creation: int = 0,
    cache_read: int = 0,
    tool_calls: int = 0,
) -> SessionMessage:
    blocks = [
        ContentBlock(type="tool_use", id=f"t{i}", name="Read", input={})
        for i in range(tool_calls)
    ]
    return SessionMessage(
        type="assistant",
        model=model,
        entrypoint="sdk-py",
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        ),
        content_blocks=blocks,
    )


def _tool_result(is_error: bool) -> SessionMessage:
    return SessionMessage(
        type="user",
        entrypoint="sdk-py",
        content_blocks=[
            ContentBlock(type="tool_result", tool_use_id="t0", is_error=is_error),
        ],
    )


def _session(
    messages: list[SessionMessage],
    session_kind: SessionClass = "sdk",
) -> SessionAnalysis:
    return SessionAnalysis(
        session_path=Path("main.jsonl"),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
        session_kind=session_kind,
        messages=messages,
    )


def _simple_sdk_main(model: str, turns: int = 4) -> SessionAnalysis:
    """A light SDK main session: many small turns → classifies 'simple'."""
    return _session(
        [
            _assistant_turn(model, input_tokens=20, output_tokens=30, tool_calls=1)
            for _ in range(turns)
        ],
    )


class TestTurnWorkTokens:
    def test_excludes_cache_read_includes_cache_creation(self) -> None:
        # input(100) + cache_creation(500) + output(50) = 650; the huge
        # cache_read(100000) is carryover context and must NOT count.
        usage = Usage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=500,
            cache_read_input_tokens=100_000,
        )
        assert _turn_work_tokens(usage) == 650


class TestBuildMainSessionStats:
    def test_per_turn_mean_not_whole_session_sum(self) -> None:
        # 4 turns of 50 work-tokens each. A whole-session SUM (200) is fine
        # here, but the point is the mean is per-turn (50), keeping the
        # session under the complex threshold (architect Q2).
        stats = _build_main_session_stats(_simple_sdk_main(MODEL_SONNET))
        assert stats is not None
        assert stats.invocation_count == 4
        assert stats.mean_tokens == 50.0
        assert stats.mean_tool_calls == 1.0
        assert stats.current_model == MODEL_SONNET
        assert stats.agent_type == f"SDK main [{MODEL_SONNET}]"

    def test_synthetic_turns_excluded(self) -> None:
        msgs = [
            _assistant_turn(MODEL_SONNET, input_tokens=20, output_tokens=30),
            _assistant_turn("<synthetic>", input_tokens=0, output_tokens=0),
            _assistant_turn(MODEL_SONNET, input_tokens=20, output_tokens=30),
        ]
        stats = _build_main_session_stats(_session(msgs))
        assert stats is not None
        assert stats.invocation_count == 2  # synthetic dropped

    def test_no_assistant_turns_returns_none(self) -> None:
        assert _build_main_session_stats(_session([_tool_result(False)])) is None

    def test_error_rate_from_tool_results(self) -> None:
        msgs = [
            _assistant_turn(MODEL_HAIKU, tool_calls=1),
            _tool_result(is_error=True),
            _assistant_turn(MODEL_HAIKU, tool_calls=1),
            _tool_result(is_error=False),
        ]
        stats = _build_main_session_stats(_session(msgs))
        assert stats is not None
        # 1 error / 2 tool calls
        assert stats.error_rate == 0.5


class TestExtractSdkMainSessionSignals:
    def test_overspec_emitted_for_simple_work_on_sonnet(self) -> None:
        signals = extract_sdk_main_session_signals([_simple_sdk_main(MODEL_SONNET)])
        assert len(signals) == 1
        detail = signals[0].detail
        assert detail["mismatch_type"] == "overspec"
        assert detail["current_model"] == MODEL_SONNET
        assert detail["recommended_model"] == MODEL_HAIKU
        assert detail["routing_scope"] == "main_session"
        assert "SDK main session" in signals[0].message

    def test_below_min_turns_suppressed(self) -> None:
        # 2 turns < _MIN_INVOCATIONS_FOR_ANALYSIS (3): fail-safe to
        # under-detection (architect Q5).
        assert extract_sdk_main_session_signals(
            [_simple_sdk_main(MODEL_SONNET, turns=2)],
        ) == []

    def test_cli_session_suppressed_d013(self) -> None:
        # AC#7: never emit main-session recs for Claude Code interactive.
        cli = _session(
            [
                _assistant_turn(MODEL_SONNET, input_tokens=20, output_tokens=30)
                for _ in range(4)
            ],
            session_kind="cli",
        )
        assert extract_sdk_main_session_signals([cli]) == []

    def test_unknown_session_suppressed(self) -> None:
        unknown = _session(
            [
                _assistant_turn(MODEL_SONNET, input_tokens=20, output_tokens=30)
                for _ in range(4)
            ],
            session_kind="unknown",
        )
        assert extract_sdk_main_session_signals([unknown]) == []

    def test_haiku_main_not_flagged(self) -> None:
        # Simple work on the cheapest tier is already right — no overspec.
        assert extract_sdk_main_session_signals(
            [_simple_sdk_main(MODEL_HAIKU)],
        ) == []

    def test_distinct_models_stay_distinct_rows(self) -> None:
        # C2: two SDK main sessions with different configured models must
        # not blend — the per-model agent_type keeps them separate.
        signals = extract_sdk_main_session_signals(
            [_simple_sdk_main(MODEL_SONNET), _simple_sdk_main(MODEL_OPUS)],
        )
        assert len(signals) == 2
        agent_types = {s.agent_type for s in signals}
        assert agent_types == {
            f"SDK main [{MODEL_SONNET}]",
            f"SDK main [{MODEL_OPUS}]",
        }

    def test_same_model_sessions_share_agg_key(self) -> None:
        # Two same-model sessions → same agent_type so the aggregator
        # merges them (savings summed) rather than emitting duplicate rows.
        signals = extract_sdk_main_session_signals(
            [_simple_sdk_main(MODEL_SONNET), _simple_sdk_main(MODEL_SONNET)],
        )
        assert len(signals) == 2
        assert {s.agent_type for s in signals} == {f"SDK main [{MODEL_SONNET}]"}

    def test_fixture_wires_session_kind_and_gates_short_session(self) -> None:
        # End-to-end on the purpose-built fixture: analyze_session populates
        # session_kind="sdk", and the sonnet main classifies 'simple'
        # (moderate tier) — but the fixture has only 2 turns, so the ≥3 gate
        # suppresses the signal (documents the fail-safe on the real file).
        from agentfluent.analytics.pipeline import analyze_session
        from agentfluent.diagnostics._complexity import (
            classify_complexity,
            classify_model_tier,
        )

        fixture = (
            Path(__file__).parent.parent
            / "fixtures" / "sdk_session" / "sdk-main-1.jsonl"
        )
        sa = analyze_session(fixture)
        assert sa.session_kind == "sdk"
        stats = _build_main_session_stats(sa)
        assert stats is not None
        assert stats.current_model == MODEL_SONNET
        assert classify_complexity(stats) == "simple"
        assert classify_model_tier(stats.current_model) == "moderate"
        assert extract_sdk_main_session_signals([sa]) == []  # 2 turns < 3

    def test_cache_read_heavy_turns_still_simple(self) -> None:
        # Regression guard for the token formula: massive cache_read must
        # not push a light session to 'complex' (which would mask overspec).
        heavy_read = _session(
            [
                _assistant_turn(
                    MODEL_SONNET,
                    input_tokens=20,
                    output_tokens=30,
                    cache_read=200_000,
                    tool_calls=1,
                )
                for _ in range(4)
            ],
        )
        signals = extract_sdk_main_session_signals([heavy_read])
        assert len(signals) == 1
        assert signals[0].detail["mismatch_type"] == "overspec"


class TestPipelineIntegration:
    """Pins the C1 seam: ``run_diagnostics`` surfaces a main-session rec from
    the ``sessions`` param, and the now-unconditional ``sessions`` pass does
    NOT switch on git / Tier-3 signal paths (those keep their own
    ``git_repo`` / ``github_repo`` gates). Guards against a future gate
    refactor silently regressing either half.
    """

    def test_main_session_rec_produced_and_git_tier3_stay_off(self) -> None:
        from agentfluent.diagnostics import run_diagnostics

        sa = _simple_sdk_main(MODEL_SONNET, turns=5)
        # No subagent invocations, no git_repo, no github_repo — the pure
        # SDK main-session path.
        result = run_diagnostics([], sessions=[sa])

        model_recs = [
            r
            for r in result.recommendations
            if r.target == "model" and r.routing_scope == "main_session"
        ]
        assert len(model_recs) == 1
        assert model_recs[0].agent_type == f"SDK main [{MODEL_SONNET}]"
        assert "ClaudeAgentOptions" in model_recs[0].action
        # Tier-3 GitHub path never ran (would flip this true on a crash).
        assert result.tier3_degraded is False
