"""Fold by ownership, cohort labels and ``trace_coverage`` (#648).

One synthetic session carries every cohort at once:

* ``w1`` (worker, linked) spawns ``leaf1`` (depth 2), which spawns ``deep1``
  (depth 3);
* ``fA`` (``forkx``) and ``fB`` (``forky``) are linked fork agents that both
  replay ``msg_shared``, so the dedup decides which file keeps it;
* ``cr1`` is a ``/code-review`` background agent: its sidecar has no
  ``toolUseId``, so it is session-spawned;
* ``r0`` was spawned by a main-session ``Agent`` call whose result never
  linked (#468 class), so it is session-spawned too, and its child ``c0`` is
  nested under a root that owns no invocation row.

Every turn has a distinct usage, so any double count or any dropped trace
moves a total by an amount no other mistake produces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import agentfluent.analytics.pipeline as pipeline_mod
import agentfluent.traces.coverage as coverage_mod
from agentfluent.analytics.pipeline import AnalysisResult, analyze_session, analyze_sessions
from agentfluent.analytics.trace_coverage import TraceCoverage
from agentfluent.traces.discovery import discover_session_subagents
from agentfluent.traces.lineage import resolve_lineage

_MODEL = "claude-haiku-4-5-20251001"
_NESTED_MAIN = (
    Path(__file__).parent.parent / "fixtures" / "nested_session" / "nested-session-1.jsonl"
)


def _usage(inp: int, out: int) -> dict[str, int]:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


# Per-turn usage. Totals are pairwise distinct.
_U: dict[str, dict[str, int]] = {
    "main1": _usage(1, 2),
    "w1": _usage(10, 20),
    "leaf1": _usage(30, 40),
    "deep1": _usage(50, 60),
    "shared": _usage(100, 200),
    "fA": _usage(300, 400),
    "fB": _usage(500, 600),
    "cr1": _usage(700, 800),
    "r0": _usage(900, 1000),
    "c0": _usage(1100, 1200),
}


def _tok(key: str) -> int:
    return sum(_U[key].values())


def _assistant(
    msg_id: str, usage_key: str, content: list[dict[str, Any]] | None = None,
    *, sidechain: bool = True,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "type": "assistant",
        "message": {
            "id": msg_id, "model": _MODEL, "role": "assistant",
            "content": content or [{"type": "text", "text": "ok"}],
            "usage": _U[usage_key],
        },
        "timestamp": "2026-11-01T00:00:01Z",
    }
    if sidechain:
        line["isSidechain"] = True
    return line


def _spawn(tool_use_id: str, agent_type: str) -> list[dict[str, Any]]:
    return [{
        "type": "tool_use", "id": tool_use_id, "name": "Agent",
        "input": {"subagent_type": agent_type, "description": "d", "prompt": "p"},
    }]


def _result(tool_use_id: str, agent_id: str, agent_type: str) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"},
        ]},
        "toolUseResult": {
            "status": "completed", "agentId": agent_id, "agentType": agent_type,
            "totalTokens": 1, "totalToolUseCount": 0, "totalDurationMs": 1,
        },
        "timestamp": "2026-11-01T00:00:02Z",
    }


def _write(path: Path, lines: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))


def _build_session(root: Path) -> Path:
    main = root / "cov-session.jsonl"
    _write(main, [
        {"type": "user", "message": {"role": "user", "content": "go"},
         "timestamp": "2026-11-01T00:00:00Z"},
        _assistant("msg_main1", "main1", [
            *_spawn("toolu_w", "worker"), *_spawn("toolu_fa", "forkx"),
            *_spawn("toolu_fb", "forky"), *_spawn("toolu_r0", "runner"),
        ], sidechain=False),
        _result("toolu_w", "w1", "worker"),
        _result("toolu_fa", "fA", "forkx"),
        _result("toolu_fb", "fB", "forky"),
        # toolu_r0 never returns a result: its invocation has no agent_id.
    ])
    subagents = root / "cov-session" / "subagents"
    subagents.mkdir(parents=True)
    traces: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {
        "w1": ([_assistant("msg_w1", "w1", _spawn("toolu_w_leaf", "leaf"))],
               {"agentType": "worker", "toolUseId": "toolu_w"}),
        "leaf1": ([_assistant("msg_leaf1", "leaf1", _spawn("toolu_leaf_deep", "deep"))],
                  {"agentType": "leaf", "toolUseId": "toolu_w_leaf"}),
        "deep1": ([_assistant("msg_deep1", "deep1")],
                  {"agentType": "deep", "toolUseId": "toolu_leaf_deep"}),
        "fA": ([_assistant("msg_shared", "shared"), _assistant("msg_fA", "fA")],
               {"agentType": "forkx", "toolUseId": "toolu_fa", "isFork": True}),
        "fB": ([_assistant("msg_shared", "shared"), _assistant("msg_fB", "fB")],
               {"agentType": "forky", "toolUseId": "toolu_fb", "isFork": True}),
        "cr1": ([_assistant("msg_cr1", "cr1")],
                {"agentType": "general-purpose", "name": "code-review", "spawnDepth": 1}),
        "r0": ([_assistant("msg_r0", "r0", _spawn("toolu_r0_child", "child"))],
               {"agentType": "runner", "toolUseId": "toolu_r0"}),
        "c0": ([_assistant("msg_c0", "c0")],
               {"agentType": "child", "toolUseId": "toolu_r0_child"}),
    }
    for agent_id, (lines, sidecar) in traces.items():
        _write(subagents / f"agent-{agent_id}.jsonl", lines)
        (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps(sidecar))
    return main


_ALL_SUBAGENT_TOKENS = sum(
    _tok(k) for k in ("w1", "leaf1", "deep1", "shared", "fA", "fB", "cr1", "r0", "c0")
)


def _subagent_tokens(analysis: pipeline_mod.SessionAnalysis) -> int:
    return sum(r.total_tokens for r in analysis.token_metrics.by_model if r.origin == "subagent")


def _subagent_cost(analysis: pipeline_mod.SessionAnalysis | AnalysisResult) -> float:
    return sum(r.cost for r in analysis.token_metrics.by_model if r.origin == "subagent")


def _per_agent_cost(analysis: pipeline_mod.SessionAnalysis | AnalysisResult) -> float:
    return sum(
        m.estimated_total_cost_usd for m in analysis.agent_metrics.by_agent_type.values()
    )


def _cohorts(path: Path, agent_filter: str | None = None) -> dict[str, str]:
    from agentfluent.agents.extractor import extract_agent_invocations
    from agentfluent.core.parser import parse_session

    invocations = extract_agent_invocations(parse_session(path))
    traces = coverage_mod.collect_session_traces(invocations, path, agent_filter=agent_filter)
    return {owned.agent_id: owned.cohort for owned in traces.owned}


class TestCohorts:
    """AC1: every trace file gets exactly one cohort, from lineage + sidecar."""

    def test_every_file_is_labeled_once(self, tmp_path: Path) -> None:
        assert _cohorts(_build_session(tmp_path)) == {
            "w1": "linked", "fA": "linked", "fB": "linked",
            "leaf1": "nested", "deep1": "nested", "c0": "nested",
            "cr1": "session_spawned", "r0": "session_spawned",
        }

    def test_parse_failure_is_unparseable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real = coverage_mod.parse_subagent_trace

        def flaky(path: Path):  # type: ignore[no-untyped-def]
            if path.name == "agent-cr1.jsonl":
                raise ValueError("bad")
            return real(path)

        monkeypatch.setattr(coverage_mod, "parse_subagent_trace", flaky)
        main = _build_session(tmp_path)
        assert _cohorts(main)["cr1"] == "unparseable"
        coverage = analyze_session(main).trace_coverage
        assert coverage.unparseable.files == 1
        assert coverage.unparseable.folded is False
        assert coverage.session_spawned.files == 1  # r0 only


class TestFoldByOwnership:
    """AC2: the session fold is every parseable trace the session owns.

    Asserts the MECHANISM -- which traces reach the folding function -- as well
    as the total, so dropping any one cohort from the fold fails here.
    """

    def test_every_owned_trace_reaches_the_fold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: list[list[Any]] = []
        real = pipeline_mod.compute_subagent_token_metrics

        def spy(traces, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(list(traces))
            return real(traces, **kwargs)

        monkeypatch.setattr(pipeline_mod, "compute_subagent_token_metrics", spy)
        analyze_session(_build_session(tmp_path))
        folded = sorted(trace.agent_id for trace in captured[0])
        assert folded == sorted(["w1", "fA", "fB", "leaf1", "deep1", "cr1", "r0", "c0"])

    def test_session_total_counts_each_turn_once(self, tmp_path: Path) -> None:
        analysis = analyze_session(_build_session(tmp_path))
        assert _subagent_tokens(analysis) == _ALL_SUBAGENT_TOKENS

    def test_depth_three_folds_but_is_not_attached(self, tmp_path: Path) -> None:
        """The child tree stays capped at depth 2 (#659); the fold does not read it."""
        analysis = analyze_session(_build_session(tmp_path))
        worker = next(inv.trace for inv in analysis.invocations if inv.agent_id == "w1")
        assert worker is not None
        assert [c.agent_id for c in worker.children] == ["leaf1"]
        assert worker.children[0].children == []
        assert analysis.trace_coverage.nested.tokens == (
            _tok("leaf1") + _tok("deep1") + _tok("c0")
        )

    def test_by_agent_type_stays_invocation_scoped(self, tmp_path: Path) -> None:
        analysis = analyze_session(_build_session(tmp_path))
        assert set(analysis.agent_metrics.by_agent_type) == {
            "worker", "forkx", "forky", "runner",
        }


class TestReconciliation:
    """AC3: Σ per-agent + folded non-linked cost == subagent-origin by_model cost."""

    def test_per_session(self, tmp_path: Path) -> None:
        analysis = analyze_session(_build_session(tmp_path))
        coverage = analysis.trace_coverage
        extra = coverage.nested.cost + coverage.session_spawned.cost
        assert extra > 0
        assert _per_agent_cost(analysis) + extra == pytest.approx(_subagent_cost(analysis))
        linked_and_extra = (
            coverage.linked.tokens + coverage.nested.tokens + coverage.session_spawned.tokens
        )
        assert linked_and_extra == _subagent_tokens(analysis)

    def test_aggregate(self, tmp_path: Path) -> None:
        result = analyze_sessions([_build_session(tmp_path), _NESTED_MAIN])
        coverage = result.trace_coverage
        extra = coverage.nested.cost + coverage.session_spawned.cost
        assert _per_agent_cost(result) + extra == pytest.approx(_subagent_cost(result))
        assert coverage.nested.files == 4  # leaf1, deep1, c0 + the fixture's leaf0001


class TestAgentFilter:
    """C4 ``--agent`` rule, and filter-invariant dedup precedence (#701 review A)."""

    def test_nested_follow_their_root(self, tmp_path: Path) -> None:
        cohorts = _cohorts(_build_session(tmp_path), agent_filter="worker")
        assert cohorts["leaf1"] == "nested"
        assert cohorts["deep1"] == "nested"
        # c0's root r0 owns no invocation row: unresolvable root -> filtered.
        assert cohorts["c0"] == "filtered"
        assert cohorts["cr1"] == "filtered"
        assert cohorts["fA"] == "filtered"

    def test_session_spawned_match_on_name(self, tmp_path: Path) -> None:
        cohorts = _cohorts(_build_session(tmp_path), agent_filter="code-review")
        assert cohorts["cr1"] == "session_spawned"
        assert cohorts["w1"] == "filtered"
        assert cohorts["leaf1"] == "filtered"

    def test_session_spawned_match_on_agent_type(self, tmp_path: Path) -> None:
        cohorts = _cohorts(_build_session(tmp_path), agent_filter="General-Purpose")
        assert cohorts["cr1"] == "session_spawned"

    def test_filtered_fold_excludes_filtered_cohort(self, tmp_path: Path) -> None:
        analysis = analyze_session(_build_session(tmp_path), agent_filter="worker")
        assert _subagent_tokens(analysis) == _tok("w1") + _tok("leaf1") + _tok("deep1")
        assert analysis.trace_coverage.filtered.folded is False
        assert analysis.trace_coverage.filtered.files == 5  # fA, fB, cr1, r0, c0

    def test_row_is_the_same_with_and_without_agent_filter(self, tmp_path: Path) -> None:
        """fA (earlier) and fB share msg_shared. Without the filter fA claims it;
        with ``--agent forky`` fA is filtered out, and fB must still not claim it."""
        main = _build_session(tmp_path)
        unfiltered = analyze_session(main).agent_metrics.by_agent_type["forky"]
        filtered = analyze_session(main, agent_filter="forky").agent_metrics.by_agent_type[
            "forky"
        ]
        assert unfiltered.spend_tokens == _tok("fB")
        assert filtered.spend_tokens == unfiltered.spend_tokens
        assert filtered.estimated_total_cost_usd == pytest.approx(
            unfiltered.estimated_total_cost_usd,
        )


class TestCoverageObject:
    """AC4: the coverage object's contents and its JSON round trip."""

    def test_cohort_counts_and_flags(self, tmp_path: Path) -> None:
        coverage = analyze_session(_build_session(tmp_path)).trace_coverage
        assert (coverage.linked.files, coverage.nested.files) == (3, 3)
        assert coverage.session_spawned.files == 2
        assert coverage.linked.tokens == _tok("w1") + _tok("shared") + _tok("fA") + _tok("fB")
        assert coverage.session_spawned.tokens == _tok("cr1") + _tok("r0")
        assert [c.folded for c in (coverage.linked, coverage.nested, coverage.session_spawned)] == [
            True, True, True,
        ]
        assert coverage.beyond_linked is True

    def test_session_spawned_breakdown_by_spawner(self, tmp_path: Path) -> None:
        coverage = analyze_session(_build_session(tmp_path)).trace_coverage
        breakdown = {k: (v.files, v.tokens) for k, v in coverage.session_spawned_by_type.items()}
        assert breakdown == {"code-review": (1, _tok("cr1")), "runner": (1, _tok("r0"))}

    def test_deduped_turns_and_tokens(self, tmp_path: Path) -> None:
        coverage = analyze_session(_build_session(tmp_path)).trace_coverage
        assert coverage.deduped_turns == 1
        assert coverage.deduped_tokens == _tok("shared")

    def test_full_coverage_has_nothing_beyond_linked(self) -> None:
        sdk_main = Path(__file__).parent.parent / "fixtures" / "sdk_session" / "sdk-main-1.jsonl"
        coverage = analyze_session(sdk_main).trace_coverage
        assert coverage.linked.files >= 1
        assert coverage.beyond_linked is False

    def test_aggregate_survives_json_rehydration(self, tmp_path: Path) -> None:
        result = analyze_sessions([_build_session(tmp_path), _NESTED_MAIN])
        payload = json.loads(result.model_dump_json())
        assert payload["trace_coverage"]["session_spawned"]["files"] == 2
        assert payload["sessions"][0]["trace_coverage"]["nested"]["files"] == 3
        rehydrated = AnalysisResult.model_validate(payload)
        assert rehydrated.trace_coverage == result.trace_coverage
        assert isinstance(rehydrated.sessions[0].trace_coverage, TraceCoverage)


class TestLineageSkipsIdlessSidecars:
    """#701 review H4: a sidecar with no ``toolUseId`` contributes no edge, even
    when the emitter index IS built for another trace in the session."""

    def test_idless_sidecar_gets_no_parent_and_keeps_type_and_name(
        self, tmp_path: Path,
    ) -> None:
        _build_session(tmp_path)
        lineages = resolve_lineage(
            discover_session_subagents(tmp_path / "cov-session"),
            {"toolu_w", "toolu_fa", "toolu_fb", "toolu_r0"},
        )
        # leaf1 is unresolved against the main session, so the index was built.
        assert lineages["leaf1"].depth == 2
        assert lineages["cr1"].parent_invocation_id is None
        assert lineages["cr1"].depth == 1
        assert lineages["cr1"].agent_type == "general-purpose"
        assert lineages["cr1"].name == "code-review"
