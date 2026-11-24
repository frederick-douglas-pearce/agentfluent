"""Token and cost analytics for session analysis.

Computes token usage totals, dollar costs, and cache efficiency from
parsed session messages. Handles mixed-model sessions with per-model
cost breakdown and parent-vs-subagent origin attribution.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from agentfluent.analytics.pricing import SYNTHETIC_MODELS, compute_cost, get_pricing
from agentfluent.core.session import SessionMessage, Usage

if TYPE_CHECKING:
    from agentfluent.traces.models import SubagentTrace

logger = logging.getLogger(__name__)

Origin = Literal["parent", "subagent"]

NO_PRICE = "no price"
"""Rendered in place of a dollar figure whose model had no price (#715).

Distinct from the ``-`` the agent table uses for *unknown*: an unpriced
figure is known to be missing, and ``$0.00`` would read as free. Lives
here so both ``cli`` and ``diagnostics`` render the same token."""


def row_unpriced_tokens(row: object) -> int | None:
    """A serialized ``by_model`` row's ``unpriced_tokens``, or ``None`` if unknown.

    ``None`` when the row is not a mapping, the key is missing (a pre-v0.12.0
    envelope), or the value is not a non-negative whole number -- a
    hand-edited envelope reads as unknown instead of crashing ``report`` or
    ``diff`` (#723). A whole-number float such as ``10.0`` counts as ``10``;
    a bool, a string, a negative or a fractional value does not.
    """
    if not isinstance(row, Mapping):
        return None
    value = row.get("unpriced_tokens")
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or value < 0:
        return None
    return value


def sum_unpriced_tokens(rows: Iterable[object]) -> int | None:
    """Sum ``unpriced_tokens`` over serialized ``by_model`` rows (#715, #723).

    ``None`` when any row's count is unknown (:func:`row_unpriced_tokens`):
    whether that row's ``0.0`` cost was unpriced cannot be told, so the sum is
    unknown too.
    """
    total = 0
    for row in rows:
        count = row_unpriced_tokens(row)
        if count is None:
            return None
        total += count
    return total


def side_unpriced_tokens(token_metrics: Mapping[str, Any]) -> int | None:
    """A serialized ``token_metrics`` block's total unpriced tokens (#723).

    The one rule ``report`` and ``diff`` share for a whole side, applied to
    the raw ``by_model`` rows so the two agree on both whether a side is
    legacy and its count. ``None`` (unknown) when ``by_model`` is missing or
    not a list (a v1 dict-shaped ``by_model`` predates the field), when it is
    empty although the side has tokens, or when any row's count is unknown.
    """
    rows = token_metrics.get("by_model")
    if not isinstance(rows, list):
        return None
    if not rows and serialized_total_tokens(token_metrics) > 0:
        return None
    return sum_unpriced_tokens(rows)


def serialized_count(serialized: Mapping[str, Any], key: str) -> int:
    """A serialized token count, ``0`` when missing or not a number (#742).

    A saved envelope may be hand-edited, so a null, string or other
    non-numeric value reads as ``0`` instead of crashing ``report``. An int
    or finite float is truncated to ``int``, and a numeric string such as
    ``"5"`` is parsed, as ``int(...)`` accepted before. A bool is not a count.
    """
    value = serialized.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else 0
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return 0
        return int(parsed) if math.isfinite(parsed) else 0
    return 0


def serialized_cost(serialized: Mapping[str, Any], key: str) -> float:
    """A serialized dollar figure, ``0.0`` when missing or not a number (#742).

    Same rule as :func:`serialized_count`, kept as a ``float``.
    """
    value = serialized.get(key)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else 0.0
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return 0.0
        return parsed if math.isfinite(parsed) else 0.0
    return 0.0


def serialized_total_tokens(serialized: Mapping[str, Any]) -> int:
    """Total tokens of a serialized ``token_metrics`` block or ``by_model`` row.

    ``TokenMetrics.total_tokens`` and ``ModelTokenBreakdown.total_tokens`` are
    properties that ``model_dump`` skips, so the total is rebuilt from the
    four serialized components both shapes share; a component that is
    missing, null or not a number counts as ``0`` (:func:`serialized_count`).
    The one copy ``report`` and ``diff`` use.
    """
    return sum(
        serialized_count(serialized, key)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )


@dataclass
class ModelTokenBreakdown:
    """Token counts and cost for a single (model, origin) row.

    ``origin`` distinguishes whether the usage came from the parent
    session's assistant messages or from a subagent trace. Two rows can
    share a model but differ in origin (e.g., Opus used in both the
    parent thread and a delegated subagent run).
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_5m_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    cost: float = 0.0
    origin: Origin = "parent"
    unpriced_tokens: int = 0
    """Tokens in this row whose model had no price at the session's price date,
    so they are in the token counts and not in ``cost`` (#676/#688). ``cost``
    stays ``0.0``-defaulted, so this is the only signal that a row's ``0.0`` is
    "unpriced", not "free". A per-session parent row is all-or-nothing; a subagent
    row sums its unpriced traces; and a merged run-level row (either origin) sums
    sessions priced at different dates (#546), so it can be partly unpriced."""

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


@dataclass
class TokenMetrics:
    """Aggregated token usage and cost metrics for a session.

    Top-level totals are *comprehensive*: parent-thread + subagent
    contributions summed. The ``by_model`` list decomposes them by
    ``(model, origin)``. Per #227, this matches users' intuition that
    "total cost" reflects what the session actually spent.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost: float = 0.0
    cache_efficiency: float = 0.0
    """Cache efficiency as a percentage (0-100).
    Formula: cache_read / (cache_read + input + cache_creation) * 100."""

    api_call_count: int = 0
    """Number of deduplicated assistant messages (API calls). Counts
    parent-session messages only — subagent turns are counted at the trace
    level (``SubagentTrace.turn_usages``), not here. Their spend, with turns
    counted elsewhere in the session removed, is ``trace_spend_usage`` for tokens
    and ``trace_spend_requests`` for dollars, priced per turn (#682)."""

    by_model: list[ModelTokenBreakdown] = field(default_factory=list)
    """Per-(model, origin) token breakdown. Schema v2: list, not dict.
    The ``origin`` field on each row distinguishes parent vs subagent.
    See #227."""

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    @property
    def unpriced_tokens(self) -> int:
        """Unpriced tokens summed over ``by_model`` (#715).

        A plain property, not a field: it is not serialized, so the JSON
        envelope is unchanged (the same reason ``total_tokens`` is one)."""
        return sum(row.unpriced_tokens for row in self.by_model)


def _accumulate_usage(breakdown: ModelTokenBreakdown, usage: Usage) -> None:
    """Fold one message/trace ``Usage`` into a per-model breakdown.

    The single population path for both the parent and subagent aggregation
    loops, so the cache-write TTL split (5m/1h) stays in lockstep with the
    authoritative total and any future usage field is wired in exactly one
    place rather than two copy-pasted blocks.
    """
    breakdown.input_tokens += usage.input_tokens
    breakdown.output_tokens += usage.output_tokens
    breakdown.cache_creation_input_tokens += usage.cache_creation_input_tokens
    breakdown.cache_read_input_tokens += usage.cache_read_input_tokens
    breakdown.cache_creation_5m_tokens += usage.cache_creation_5m_input_tokens
    breakdown.cache_creation_1h_tokens += usage.cache_creation_1h_input_tokens


def prompt_tokens(usage: Usage) -> int:
    """One request's prompt size: ``input + cache_creation + cache_read`` (#682).

    The token count a prompt-length tier keys on -- genai-prices' Anthropic ``input_tokens``,
    which folds cache writes and reads into the prompt. Uses the authoritative cache-write
    total, not the 5m/1h split. Meaningful only for **one** API request's ``Usage``.
    """
    return (
        usage.input_tokens
        + usage.cache_creation_input_tokens
        + usage.cache_read_input_tokens
    )


@dataclass(frozen=True)
class ApiRequest:
    """One API request to price: its usage and the prompt size that selects its tier (#682).

    Build it with :meth:`of` from a single request's ``Usage``. The explicit ``prompt_tokens``
    exists for the three callers that must not derive it from ``usage``: the parent-workload
    offload counterfactual (its alternative request tiers on the *original* prompt), and two
    aggregate fallbacks priced at the base tier (prompt ``0``) rather than tiered on a sum --
    a trace with no per-turn usage (``trace_spend_requests``) and an offload burst built
    without per-message usages (``diagnostics.parent_workload.estimate_burst_cost``).
    """

    usage: Usage
    prompt_tokens: int

    @classmethod
    def of(cls, usage: Usage) -> ApiRequest:
        return cls(usage=usage, prompt_tokens=prompt_tokens(usage))


SpendObservation = tuple[Sequence[ApiRequest], str]
"""Spend to price in a savings estimate: requests and the model they ran on (#675, #682)."""


def _cost_of(
    model: str, requests: Iterable[ApiRequest], timestamp: datetime | None = None,
) -> float | None:
    """Price API requests at ``model``'s rate, one request at a time; ``None`` iff unpriced.

    The single call site of ``get_pricing`` / ``compute_cost`` in this module (#646 AC1):
    parent rows, subagent rows, per-agent spend (``price_trace``) and every diagnostics
    dollar figure (``price_requests`` -- model-switch savings and the parent-workload offload
    estimate) all price here, so the ``Usage`` -> rate mapping (the 5m/1h cache-write split)
    lives on one path.

    **Per request, never on a sum (#682).** Each request's rates come from
    ``ModelPricing.for_prompt(request.prompt_tokens)``, so a request over its model's
    prompt-length threshold (e.g. ``claude-haiku-5-5`` above 100K) prices at the tier rate on
    every token. Pricing is therefore **not linear in summed tokens**: two 60K-prompt requests
    price at the base rate while their 120K sum would tier. Never collapse requests into one
    ``Usage`` before calling this. The current callers are guarded against doing so by
    ``TestNonLinearityGuard``, ``TestReconciliationWithTiers`` and
    ``TestDiagnosticsPerRequest`` in ``tests/unit/test_context_tier_pricing.py``; a new
    dollar figure needs its own per-request test.

    **Rates are resolved once, at ``timestamp``** -- the session's price date -- so every
    request in a session tiers against that date's table, not its own date (#738).
    """
    pricing = get_pricing(model, timestamp)
    if pricing is None:
        return None
    total = 0.0
    for request in requests:
        usage = request.usage
        total += compute_cost(
            pricing.for_prompt(request.prompt_tokens),
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_creation_5m_input_tokens,
            usage.cache_read_input_tokens,
            cache_creation_1h_tokens=usage.cache_creation_1h_input_tokens,
        )
    return total


def _populate_costs(
    by_model: dict[tuple[str, str], ModelTokenBreakdown],
    requests_by_row: dict[tuple[str, str], list[ApiRequest]],
    timestamp: datetime | None = None,
) -> float:
    """Compute per-row costs in place from each row's requests, return the summed total.

    ``timestamp`` is the session's price date (first-message timestamp, #546); it is
    forwarded to ``get_pricing`` so each row is priced at the rate in effect when the
    session ran. ``None`` → latest rate (the fallback for a missing/malformed session
    timestamp, and the default for callers that do not price by date).
    """
    total = 0.0
    for key, breakdown in by_model.items():
        cost = _cost_of(breakdown.model, requests_by_row.get(key, []), timestamp)
        if cost is None:
            breakdown.unpriced_tokens = breakdown.total_tokens
        else:
            breakdown.cost = cost
            total += cost
    return total


def trace_counts_as_spend(trace: SubagentTrace) -> bool:
    """Whether a subagent trace contributes to subagent spend (#646).

    The single inclusion predicate shared by the session fold
    (``compute_subagent_token_metrics``) and per-agent attribution
    (``analytics.agent_metrics``), so the two cannot drift on *which* traces count:
    the trace must carry a model, and not the ``<synthetic>`` sentinel (no real API
    call). It does not test depth -- which traces reach the fold is ownership
    (``traces.coverage.collect_session_traces``, #648), not this predicate's.
    """
    return trace.model is not None and trace.model not in SYNTHETIC_MODELS


def trace_spend_usage(trace: SubagentTrace) -> Usage:
    """The usage a trace contributes to spend (#701), summed -- for token counts only.

    ``trace.spend_usage`` (Σ ``spend_turns``) once ``dedupe_trace_turns`` has run on the
    session, else the per-file ``trace.usage`` -- which is also what a trace dedup skipped
    returns (no ``turn_usages``, or failing ``trace_counts_as_spend``), so callers
    must gate on ``trace_counts_as_spend`` first. Every spend *token* reader goes through it:
    the session fold (``compute_subagent_token_metrics``), per-agent ``spend_tokens``
    (``analytics.agent_metrics``), ``trace_coverage`` and the verbose Per-Invocation Detail
    Tokens column (``cli.formatters.table``). **Dollars never price this sum** -- they price
    ``trace_spend_requests`` one request at a time (#682).
    """
    spend = trace.spend_usage
    return spend if spend is not None else trace.usage


def trace_spend_requests(trace: SubagentTrace) -> list[ApiRequest]:
    """The API requests a trace contributes to spend, one per turn (#682).

    The same turns as ``trace_spend_usage`` -- deduplicated ``spend_turns`` once
    ``dedupe_trace_turns`` has run, else every ``turn_usages`` turn -- so dollars and tokens
    cover one set of turns. A trace with no per-turn usage at all (not built by the parser;
    the parser always records turns, and a turn-less parsed trace has zero usage) falls back
    to its aggregate ``usage`` as **one request at the base tier** (prompt ``0``): tiering on
    a sum would over-price it, so this under-prices instead, and says so here.
    """
    if trace.spend_turns is not None:
        return [ApiRequest.of(usage) for usage in trace.spend_turns]
    if trace.turn_usages:
        return [ApiRequest.of(turn.usage) for turn in trace.turn_usages]
    return [ApiRequest(usage=trace.usage, prompt_tokens=0)]


def trace_spend(trace: SubagentTrace | None) -> SpendObservation | None:
    """A trace's spend requests and the model it ran on, or ``None`` when it has none (#675).

    The ``trace_counts_as_spend`` / ``trace_spend_requests`` pair every spend reader
    uses (#646, #701, #682), packaged for the diagnostics savings estimates so their
    dollars and the analytics fold rest on one definition of spend. ``None`` for a
    missing trace or one that does not count as spend -- never an empty request list.
    """
    if trace is None or trace.model is None or not trace_counts_as_spend(trace):
        return None
    return trace_spend_requests(trace), trace.model


def counted_parent_message_ids(messages: Iterable[SessionMessage]) -> set[str]:
    """``message_id`` s of the parent-session turns ``compute_token_metrics``
    counts (#701).

    Uses the same predicate (``counts_as_parent_call``) so a trace turn is
    never dropped as "already counted" on the strength of a main-session turn
    the parent fold skipped (no usage, or ``<synthetic>``). Empty ids are not
    ids -- the same reading the #696 fragment merge takes.
    """
    return {
        msg.message_id
        for msg in messages
        if counts_as_parent_call(msg) and msg.message_id
    }


@dataclass(frozen=True)
class TraceDedupStats:
    """What ``dedupe_trace_turns`` dropped as already counted (#648 AC4)."""

    dropped_turns: int = 0
    dropped_tokens: int = 0


def dedupe_trace_turns(
    counted_message_ids: Iterable[str],
    depth_one_in_invocation_order: Sequence[SubagentTrace],
    rest: Iterable[SubagentTrace] = (),
) -> TraceDedupStats:
    """Set each trace's ``spend_turns`` so no API turn is counted twice (#701).

    Exclusivity -- every turn's usage lives in exactly one file -- holds for
    almost every trace, but **not** for fork agents, which replay the spawning
    conversation: sibling fork traces share ``message.id`` s with each other and
    with the main session. Summing per-file usage then counts those turns more
    than once. This is the hard guard: first occurrence wins.

    **Precedence is fixed here**, so every caller gets the same answer:
    ``counted_message_ids`` (the main session's counted turns -- see
    ``counted_parent_message_ids``) first, then ``depth_one_in_invocation_order``
    as given (the caller is the only one who knows invocation order), then
    ``rest`` sorted by ``(depth, agent_id)``.

    A turn with no ``message_id`` -- ``None`` or ``""``, matching the #696
    merge's notion of "no id" -- always counts (it cannot be matched). A trace
    with no ``turn_usages`` (not parser-built) is left alone and spends its
    ``usage``. A trace *object* appearing twice is processed once; two distinct
    objects for the same ``agent_id`` are not merged (#705).

    **Only a trace that counts as spend may claim an id** -- the trace-side twin
    of ``counted_parent_message_ids``' rule for the main session. A trace failing
    ``trace_counts_as_spend`` (no model, or a ``<synthetic>`` first assistant
    message -- the #692 shape) is skipped entirely and its ``spend_turns`` stays
    ``None``: every spend reader excludes it anyway, so letting it claim a turn
    it shares with a later fork would leave that turn counted nowhere.

    Mutates ``spend_turns`` only (``spend_usage`` is derived from it); the public
    per-file ``usage`` is unchanged.
    Returns how many turns, and how many tokens, were dropped as duplicates.
    """
    ordered = [
        *depth_one_in_invocation_order,
        *sorted(rest, key=lambda trace: (trace.depth, trace.agent_id)),
    ]
    seen = set(counted_message_ids)
    processed: set[int] = set()
    dropped_turns = 0
    dropped_tokens = 0
    for trace in ordered:
        if id(trace) in processed:
            continue
        processed.add(id(trace))
        if not trace.turn_usages or not trace_counts_as_spend(trace):
            continue
        kept: list[Usage] = []
        for turn in trace.turn_usages:
            if turn.message_id:
                if turn.message_id in seen:
                    dropped_turns += 1
                    dropped_tokens += turn.usage.total_tokens
                    continue
                seen.add(turn.message_id)
            kept.append(turn.usage)
        trace.spend_turns = kept
    return TraceDedupStats(dropped_turns=dropped_turns, dropped_tokens=dropped_tokens)


def price_requests(
    requests: Iterable[ApiRequest], model: str, *, timestamp: datetime | None = None,
) -> float | None:
    """Price API requests at ``model``'s rate; ``None`` iff ``model`` is unpriced.

    The public token -> dollar primitive (#675, #682): ``price_trace``, the
    model-switch savings figures (``diagnostics.model_routing`` and the
    duration-outlier rule) and ``diagnostics.parent_workload``'s offload
    estimate route through it -- and it routes through ``_cost_of``, so all five
    usage components -- including the 5m/1h cache-write split -- map to rates on a
    single path (#646 AC1), and each request selects its own prompt-length tier.
    It replaced ``price_usage``, which took one ``Usage`` and so invited pricing a sum.

    **Model-switch counterfactual.** A savings estimate prices the *same* requests at
    two models: the token mix actually observed, cache reads and writes included, at
    the alternative model's rates, each request tiered on its own prompt at each model.
    That deliberately ignores tokenizer differences between model generations, a
    different turn count on the other model, and per-request overlay levers (multipliers
    and surcharges #537-#539). ``diagnostics.parent_workload``'s offload estimate uses a
    *different* counterfactual on purpose -- a delegated subagent loses the parent's
    cache, so its alternative side re-prices cache reads as fresh input -- and the two
    are not meant to converge.
    """
    return _cost_of(model, requests, timestamp)


def price_trace(
    trace: SubagentTrace, *, timestamp: datetime | None = None,
) -> float | None:
    """Price one subagent trace's deduplicated turns at its model's rate, one request per turn.

    Returns ``None`` only when the trace's model is unpriced. **Precondition:**
    ``trace_counts_as_spend(trace)`` -- a trace that does not count as spend raises
    ``ValueError`` rather than being priced as $0, so "unpriced" and "excluded" never
    share a return value. ``timestamp`` is the session price date (#546).
    """
    if not trace_counts_as_spend(trace) or trace.model is None:
        raise ValueError(
            f"price_trace: trace {trace.agent_id!r} does not count as spend "
            f"(model={trace.model!r}); check trace_counts_as_spend first"
        )
    return price_requests(trace_spend_requests(trace), trace.model, timestamp=timestamp)


def _aggregate_totals(
    rows: Iterable[ModelTokenBreakdown],
) -> tuple[int, int, int, int, float, float]:
    """Sum totals across breakdown rows. Returns (input, output, cache_creation,
    cache_read, total_cost, cache_efficiency).

    Cache efficiency formula: cache_read / (cache_read + input + cache_creation) * 100.
    """
    total_input = 0
    total_output = 0
    total_cache_creation = 0
    total_cache_read = 0
    total_cost = 0.0
    for r in rows:
        total_input += r.input_tokens
        total_output += r.output_tokens
        total_cache_creation += r.cache_creation_input_tokens
        total_cache_read += r.cache_read_input_tokens
        total_cost += r.cost
    cache_denom = total_cache_read + total_input + total_cache_creation
    cache_efficiency = (
        round(total_cache_read / cache_denom * 100, 1) if cache_denom > 0 else 0.0
    )
    return (
        total_input, total_output, total_cache_creation, total_cache_read,
        total_cost, cache_efficiency,
    )


def session_price_timestamp(messages: list[SessionMessage]) -> datetime | None:
    """Return the session's price date: the first message carrying a timestamp.

    This is the single source of truth for pricing a session by the rate in effect
    when it ran (#546, AC#2). Returns ``None`` when no message has a timestamp
    (missing/malformed → latest-rate fallback, AC#3), which ``get_pricing`` treats as
    "latest". The parser sets ``SessionMessage.timestamp`` to ``None`` for absent or
    unparseable timestamps, so a malformed value never reaches pricing as a bad date.
    """
    price_date = next((m.timestamp for m in messages if m.timestamp is not None), None)
    if price_date is None:
        # AC3 (#546): no usable session timestamp (missing or parser-coerced malformed) ->
        # cost falls back to the latest rate. Log at DEBUG so the fallback is observable.
        logger.debug(
            "Session has no usable message timestamp; pricing at the latest rate."
        )
    return price_date


def counts_as_parent_call(msg: SessionMessage) -> bool:
    """Whether a parent-session message is a counted API call: an assistant
    message with usage, not the ``<synthetic>`` sentinel (no real API call, no
    pricing). Shared by ``compute_token_metrics``,
    ``counted_parent_message_ids`` and the SDK main-session savings estimate
    (``diagnostics.model_routing``, #675) so they cannot drift (#701)."""
    return (
        msg.type == "assistant"
        and msg.usage is not None
        and msg.model not in SYNTHETIC_MODELS
    )


def compute_token_metrics(
    messages: list[SessionMessage], *, timestamp: datetime | None = None
) -> TokenMetrics:
    """Compute parent-session token usage totals and dollar costs.

    Processes only assistant messages that have usage data. Messages should
    already be deduplicated (streaming snapshots collapsed). Subagent
    contributions are not included here — call
    ``compute_subagent_token_metrics`` separately and merge into the
    parent ``TokenMetrics`` (see ``analytics.pipeline.analyze_session``).

    ``timestamp`` (#546) is the session price date used for date-aware rate lookup;
    ``None`` → latest rate. The pipeline derives it once via ``session_price_timestamp``
    and passes the same value here and to ``compute_subagent_token_metrics`` so parent
    and subagent rows price at one consistent session date.

    Returns:
        TokenMetrics with parent-only totals, per-model breakdown
        (origin="parent"), cost, and cache efficiency.
    """
    by_model: dict[tuple[str, str], ModelTokenBreakdown] = {}
    requests_by_row: dict[tuple[str, str], list[ApiRequest]] = {}
    api_call_count = 0

    for msg in messages:
        usage = msg.usage
        if usage is None or not counts_as_parent_call(msg):
            continue

        api_call_count += 1
        model_name = msg.model or "unknown"
        key = (model_name, "parent")

        breakdown = by_model.get(key)
        if breakdown is None:
            breakdown = ModelTokenBreakdown(model=model_name, origin="parent")
            by_model[key] = breakdown

        _accumulate_usage(breakdown, usage)
        # Each message is one API request: priced on its own prompt-length tier (#682).
        requests_by_row.setdefault(key, []).append(ApiRequest.of(usage))

    _populate_costs(by_model, requests_by_row, timestamp)
    rows = list(by_model.values())
    (
        total_input, total_output, total_cache_creation, total_cache_read,
        total_cost, cache_efficiency,
    ) = _aggregate_totals(rows)

    return TokenMetrics(
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_input_tokens=total_cache_creation,
        cache_read_input_tokens=total_cache_read,
        total_cost=total_cost,
        cache_efficiency=cache_efficiency,
        api_call_count=api_call_count,
        by_model=rows,
    )


def compute_subagent_token_metrics(
    traces: list[SubagentTrace], *, timestamp: datetime | None = None
) -> list[ModelTokenBreakdown]:
    """Aggregate subagent-trace token usage into per-model breakdowns.

    Tokens come from ``trace_spend_usage`` -- the trace-level sum with turns already
    counted elsewhere in the session removed (#701); dollars from ``price_trace``,
    which prices the same turns one request at a time (#682). This is the
    authoritative source — per-call ``SubagentToolCall.usage`` is left
    at zero by the parser (see traces/parser.py docstring). Skips
    traces that do not count as spend (``trace_counts_as_spend``: no model, or
    the ``<synthetic>`` sentinel).

    ``timestamp`` (#546) is the parent session's price date, applied to subagent rows
    too so the whole session prices at one consistent date; ``None`` → latest rate.

    Inclusion is ``trace_counts_as_spend`` (shared with per-agent attribution, #646).
    Each row's cost is the sum of its traces' ``price_trace`` costs.

    Returns one ``ModelTokenBreakdown`` per distinct subagent model,
    each carrying ``origin="subagent"``. Costs are populated. The
    caller is responsible for merging these rows into a parent
    ``TokenMetrics``.
    """
    by_model: dict[tuple[str, str], ModelTokenBreakdown] = {}
    for trace in traces:
        if not trace_counts_as_spend(trace) or trace.model is None:
            continue
        usage = trace_spend_usage(trace)
        key = (trace.model, "subagent")
        breakdown = by_model.get(key)
        if breakdown is None:
            breakdown = ModelTokenBreakdown(model=trace.model, origin="subagent")
            by_model[key] = breakdown
        _accumulate_usage(breakdown, usage)
        # Priced per trace -- one request per turn (#682) -- and summed, never
        # aggregate-then-price, so per-agent cost reconciles with this row by
        # construction (#646 AC8; see ``_cost_of``).
        cost = price_trace(trace, timestamp=timestamp)
        if cost is None:
            breakdown.unpriced_tokens += usage.total_tokens
        else:
            breakdown.cost += cost

    return list(by_model.values())


def fold_subagent_metrics_in(
    parent: TokenMetrics, subagent_rows: list[ModelTokenBreakdown],
) -> TokenMetrics:
    """Return a new ``TokenMetrics`` with subagent rows folded into parent.

    Top-level totals (input/output/cache + cost) become comprehensive.
    The ``by_model`` list is the union of parent and subagent rows
    (rows with the same ``model`` but different ``origin`` stay
    distinct). Cache efficiency is recomputed from the comprehensive
    totals. When ``subagent_rows`` is empty, returns ``parent`` itself
    (identity preserved) so the merge is free in the common no-trace case.
    """
    if not subagent_rows:
        return parent

    combined_rows = list(parent.by_model) + list(subagent_rows)
    (
        total_input, total_output, total_cache_creation, total_cache_read,
        total_cost, cache_efficiency,
    ) = _aggregate_totals(combined_rows)

    return TokenMetrics(
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_input_tokens=total_cache_creation,
        cache_read_input_tokens=total_cache_read,
        total_cost=total_cost,
        cache_efficiency=cache_efficiency,
        api_call_count=parent.api_call_count,
        by_model=combined_rows,
    )
