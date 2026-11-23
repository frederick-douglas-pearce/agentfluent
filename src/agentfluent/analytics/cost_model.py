"""The cost-model stamp: which rate table and cost method priced an envelope (#543).

Dollar figures for an *unchanged* session move whenever AgentFluent changes the rate table it
prices with or the way it computes cost. ``agentfluent diff`` must not present such a move as an
agent-behavior change, so ``analyze`` stamps each envelope with a :class:`CostModelStamp` and
``diff`` compares the two stamps.

The stamp has two parts:

- ``genai_prices_version`` -- the upstream rate table. ``genai-prices`` is pinned exactly, so the
  installed version identifies the base rates. A pin bump is detected without any manual step.
- ``cost_method_version`` -- :data:`COST_METHOD_VERSION`, a hand-maintained integer for
  everything the pin does not capture.

**When to bump** :data:`COST_METHOD_VERSION` -- any change that can move the dollar figure of an
unchanged session while the genai-prices pin stays put:

- where spend comes from or how it is folded (trace sourcing, the session fold, turn dedup);
- the local overlay levers on the base ⊕ overlay seam (``pricing`` module docstring): 1h cache
  writes, fast mode, service tier, data residency, server-tool surcharges;
- date-aware rate selection, and prompt-length tier selection (#682);
- a rate-altering edit to ``pricing._KNOWN_MODELS``, ``pricing._ALIASES`` or
  ``pricing._RESIDUAL`` -- one that changes the price of an id that was already priced.

Additive curation (an id that was unpriced becomes priced) needs no bump: the run's cost
coverage already discloses unpriced tokens. A change that is provably price-neutral needs none
either. Bumping is a review-time rule: no test can force it.
``tests/unit/test_cost_model.py::test_cost_method_tripwire`` pins fixture costs together with this
constant, so it fails when costs move on the code paths its fixtures exercise and prompts the
bump decision; re-pinning the costs without bumping still passes. Its fixtures carry tiered
requests on both sides of a threshold (#682) but no 1h cache writes, fast mode (``speed``),
``service_tier``, server-tool use, ``inference_geo``, or
fork-duplicated message ids, so changes to those levers move no pinned cost.

Version history:

- ``1`` -- pre-release v0.12.0 development builds on ``main`` only (never in a tagged release;
  the dogfood runner wrote envelopes stamped ``1``): per-agent spend priced from subagent traces
  (#646), fork-replayed turns counted once (#701), session totals fold every trace the session
  owns (#648), 1h cache writes derived as 2x input (#534). Envelopes from earlier releases carry
  no stamp.
- ``2`` -- the first released stamp (v0.12.0). Adds prompt-length (context) tiers priced per
  request (#682, D067): every dollar figure
  prices one API request at a time, and a request whose prompt (``input + cache_creation +
  cache_read``) exceeds its model's threshold prices at the tier rate. Moves cost upward for
  ``claude-haiku-5-5`` requests over 100K, ``claude-sonnet-4-5-20250929`` requests over 200K,
  and ``claude-opus-4-6`` / ``claude-sonnet-4-6`` requests over 200K dated before 2026-03-13.
"""

from __future__ import annotations

from pydantic import BaseModel

from agentfluent.analytics._genai_source import installed_version

COST_METHOD_VERSION = 2
"""How AgentFluent computes cost, beyond the upstream rate table. See the module docstring for
when to bump it."""


class CostModelStamp(BaseModel):
    """Identifies the cost model that priced an envelope's dollar figures (#543)."""

    genai_prices_version: str | None = None
    """Installed genai-prices version; ``None`` when it could not be determined."""

    cost_method_version: int | None = None
    """:data:`COST_METHOD_VERSION` at analyze time; ``None`` only on a malformed stamp."""


def current_cost_model_stamp() -> CostModelStamp:
    """Build the stamp for the cost model this process prices with."""
    return CostModelStamp(
        genai_prices_version=installed_version(),
        cost_method_version=COST_METHOD_VERSION,
    )
