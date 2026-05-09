"""Tests for the Offload Candidates section of the analyze formatter (#189-F).

Covers compact-table rendering (column shape + sort order), signed-savings
display (positive vs the cost-MORE flip-with-warning case), verbose mode's
yaml_draft inclusion, and empty-list silence (consistency with delegation
suggestions).
"""

from __future__ import annotations

from functools import partial

from agentfluent.cli.formatters.table import (
    OFFLOAD_COST_MORE_NOTE,
    _format_offload_candidates,
)
from agentfluent.diagnostics.models import (
    DiagnosticsResult,
    OffloadCandidate,
)
from tests._builders import delegation_suggestion
from tests.unit.cli.conftest import render_section

_render = partial(render_section, _format_offload_candidates)


def _candidate(
    *,
    name: str = "pytest-runner",
    confidence: str = "medium",
    cluster_size: int = 6,
    cohesion_score: float = 0.55,
    estimated_savings_usd: float = 0.42,
    estimated_parent_cost_usd: float = 1.25,
    estimated_parent_tokens: int = 50_000,
    cost_note: str = "",
    parent_model: str = "claude-opus-4-7",
    alternative_model: str = "claude-sonnet-4-6",
    tools: list[str] | None = None,
    tools_note: str = "",
    dedup_note: str = "",
    matched_agent: str = "",
) -> OffloadCandidate:
    resolved_tools = tools if tools is not None else ["Bash", "Read"]
    return OffloadCandidate(
        name=name,
        description=f"Handles delegations related to: {name}.",
        confidence=confidence,  # type: ignore[arg-type]
        cluster_size=cluster_size,
        cohesion_score=cohesion_score,
        top_terms=["pytest", "tests", "run"],
        tool_sequence_summary=resolved_tools,
        tools=resolved_tools,
        tools_note=tools_note,
        estimated_parent_tokens=estimated_parent_tokens,
        estimated_parent_cost_usd=estimated_parent_cost_usd,
        estimated_savings_usd=estimated_savings_usd,
        parent_model=parent_model,
        alternative_model=alternative_model,
        cost_note=cost_note,
        target_kind="subagent",
        subagent_draft=delegation_suggestion(
            name=name,
            tools=resolved_tools,
            tools_observed=resolved_tools,
            confidence=confidence,
        ),
        skill_draft=None,
        dedup_note=dedup_note,
        matched_agent=matched_agent,
    )


class TestOffloadCandidatesSection:
    def test_absent_when_no_candidates(self) -> None:
        diag = DiagnosticsResult(offload_candidates=[])
        assert _render(diag) == ""

    def test_section_header_and_columns_present(self) -> None:
        diag = DiagnosticsResult(offload_candidates=[_candidate()])
        text = _render(diag)
        assert "Offload Candidates" in text
        for column in (
            "Name", "Confidence", "Cluster size", "Tools", "Est. savings", "Note",
        ):
            assert column in text

    def test_compact_row_renders_expected_values(self) -> None:
        diag = DiagnosticsResult(
            offload_candidates=[
                _candidate(
                    name="pytest-runner",
                    estimated_savings_usd=1.25,
                    cluster_size=8,
                    tools=["Bash", "Read"],
                ),
            ],
        )
        text = _render(diag)
        assert "pytest-runner" in text
        assert "Bash" in text and "Read" in text
        assert "$1.25" in text
        assert "medium" in text  # confidence
        assert "8" in text  # cluster size

    def test_negative_savings_flips_sign_and_carries_warning(self) -> None:
        # Architect Q1 verdict: render `+$X.XX` in red savings cell with
        # the short `offload would cost MORE` note, matching the model's
        # yaml_draft preamble phrasing. The verbose cost_note ("cache
        # load-bearing...") lives in the --verbose YAML preamble, not
        # the compact table — duplicating would just bloat the row.
        # Negative-savings rows are hidden by default since #344;
        # opt in with ``show_negative_savings=True`` to exercise the
        # legacy rendering path that still has to render correctly when
        # the user explicitly asked for it.
        diag = DiagnosticsResult(
            offload_candidates=[
                _candidate(
                    name="cache-heavy",
                    estimated_savings_usd=-2.50,
                    cost_note=(
                        "Offloading would increase cost — parent-thread "
                        "cache appears load-bearing for this pattern."
                    ),
                ),
            ],
        )
        text = _render(diag, show_negative_savings=True)
        # Sign flip in the savings cell — magnitude with a `+` prefix.
        assert "+$2.50" in text
        # Short warning in the Note column.
        assert OFFLOAD_COST_MORE_NOTE in text
        # The verbose cost_note appears ONLY in --verbose mode.
        assert "load-bearing" not in text
        verbose_text = _render(diag, verbose=True, show_negative_savings=True)
        assert "load-bearing" in verbose_text

    def test_sorts_by_savings_descending_negatives_at_bottom(self) -> None:
        # Architect Q2 verdict: biggest dollar wins first; negative-savings
        # rows naturally sink to the bottom — verified with
        # ``show_negative_savings=True`` so all rows render.
        diag = DiagnosticsResult(
            offload_candidates=[
                _candidate(name="middle-row", estimated_savings_usd=0.50),
                _candidate(name="top-row", estimated_savings_usd=2.00),
                _candidate(name="bottom-row", estimated_savings_usd=-1.00),
            ],
        )
        text = _render(diag, show_negative_savings=True)
        top_idx = text.index("top-row")
        mid_idx = text.index("middle-row")
        bot_idx = text.index("bottom-row")
        assert top_idx < mid_idx < bot_idx

    def test_verbose_renders_yaml_draft_below_table(self) -> None:
        diag = DiagnosticsResult(offload_candidates=[_candidate()])
        text = _render(diag, verbose=True)
        # Compact table is still rendered first.
        assert "Offload Candidates" in text
        # The YAML draft's offload-flavored preamble appears in verbose
        # output. It's built by OffloadCandidate.yaml_draft from the
        # model — the formatter just escapes + prints it.
        assert "parent-thread offload candidate" in text
        assert "claude-opus-4-7" in text  # parent model
        assert "claude-sonnet-4-6" in text  # alt model

    def test_negative_savings_hidden_by_default(self) -> None:
        """#344: a section named "Offload Candidates" full of "do not
        offload" rows misleads at a glance, so negative-savings rows are
        suppressed by default. Mixing positive + negative renders only
        the positives."""
        diag = DiagnosticsResult(
            offload_candidates=[
                _candidate(name="positive-row", estimated_savings_usd=2.00),
                _candidate(name="negative-row", estimated_savings_usd=-1.50),
            ],
        )
        text = _render(diag)
        assert "positive-row" in text
        assert "negative-row" not in text
        assert "+$1.50" not in text  # the cost-MORE flip never renders
        # No footnote when at least one positive row remains — the
        # actionable content carries on its own.
        assert "negative-savings rows hidden" not in text

    def test_all_negative_renders_footnote(self) -> None:
        """#344: when every candidate is anti-actionable, render a one-line
        footnote pointing at the opt-in flag rather than silently empty
        — keeps the diagnostic discoverable."""
        diag = DiagnosticsResult(
            offload_candidates=[
                _candidate(name="anti-1", estimated_savings_usd=-3.40),
                _candidate(name="anti-2", estimated_savings_usd=-1.10),
                _candidate(name="anti-3", estimated_savings_usd=-0.50),
            ],
        )
        text = _render(diag)
        assert "Offload Candidates" in text
        assert "3 negative-savings rows hidden" in text
        assert "--show-negative-savings" in text
        # Bodies of the rows should not appear.
        for name in ("anti-1", "anti-2", "anti-3"):
            assert name not in text

    def test_show_negative_savings_passthrough_renders_all(self) -> None:
        """#344: ``--show-negative-savings`` must include the negative rows
        AND keep them sorted by savings descending (no new ordering)."""
        diag = DiagnosticsResult(
            offload_candidates=[
                _candidate(name="positive-row", estimated_savings_usd=2.00),
                _candidate(name="negative-row", estimated_savings_usd=-1.50),
            ],
        )
        text = _render(diag, show_negative_savings=True)
        pos_idx = text.index("positive-row")
        neg_idx = text.index("negative-row")
        assert pos_idx < neg_idx  # positives still sort above negatives

    def test_dedup_note_surfaces_in_compact_view(self) -> None:
        diag = DiagnosticsResult(
            offload_candidates=[
                _candidate(
                    matched_agent="existing-pytest-runner",
                    dedup_note=(
                        "suppressed — already covered by 'existing-pytest-"
                        "runner' (similarity 0.78)"
                    ),
                ),
            ],
        )
        text = _render(diag)
        assert "existing-pytest-runner" in text
