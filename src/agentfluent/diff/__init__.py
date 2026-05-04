"""Diff comparison between two `agentfluent analyze --json` envelopes.

Public surface:

- :func:`compute_diff` — pure function over two envelope dicts.
- :func:`load_envelope` — file → validated envelope dict.
- :class:`DiffResult` — typed output, JSON-serializable via ``model_dump``.

The CLI wrapper (`agentfluent.cli.commands.diff_cmd`) layers I/O and
formatting on top of these primitives.
"""

from __future__ import annotations

from agentfluent.diff.compute import compute_diff, has_regression
from agentfluent.diff.loader import EnvelopeLoadError, load_envelope
from agentfluent.diff.models import (
    AgentTypeDelta,
    DiffResult,
    ModelTokenDelta,
    RecommendationDelta,
    TokenMetricsDelta,
)

__all__ = [
    "AgentTypeDelta",
    "DiffResult",
    "EnvelopeLoadError",
    "ModelTokenDelta",
    "RecommendationDelta",
    "TokenMetricsDelta",
    "compute_diff",
    "has_regression",
    "load_envelope",
]
