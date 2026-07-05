"""CI drift guard: the release-loop skill body and its spec mirror must match.

The orchestrator procedure lives in two hand-maintained copies:

  * ``.claude/skills/release-loop/SKILL.md`` -- the live skill (what actually
    runs when ``/release-loop`` is invoked); and
  * ``.claude/specs/prd-loop-engineering.md`` section 7 -- a byte-identical
    mirror embedded in a 4-backtick fenced block, kept so the spec stays
    self-contained and "ready-to-drop-in".

Every edit to the loop's operating steps must land in BOTH copies or the spec
silently drifts from the live skill (see issue #575). This test fails if they
diverge. On a conflict, ``SKILL.md`` is the operative copy and section 7 mirrors
it -- but sync direction is the editor's call; reconcile both to match.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "release-loop" / "SKILL.md"
SPEC_PATH = REPO_ROOT / ".claude" / "specs" / "prd-loop-engineering.md"

# The mirror is embedded in a 4-backtick fence (SKILL.md's own body uses inner
# 3-backtick fences, so a 4-backtick anchor is unambiguous). Capture the content
# between the opening ````<info-string> line and the closing ```` line, without
# the fence lines themselves, preserving bytes exactly (raw slice -- no
# line-split/rejoin, which would risk mangling the trailing newline).
_FENCE_BLOCK = re.compile(
    r"^````[a-zA-Z0-9]*\n(?P<body>.*?)^````[ \t]*$\n?",
    re.MULTILINE | re.DOTALL,
)


def _extract_mirror(spec_text: str) -> str:
    """Return the single 4-backtick fenced block embedding the skill mirror."""
    matches = list(_FENCE_BLOCK.finditer(spec_text))
    # Anchor-integrity: exactly one 4-backtick block, or the guard is unreliable
    # (a future spec restructure must not silently make this test vacuous).
    assert len(matches) == 1, (
        f"Expected exactly one 4-backtick fenced block in {SPEC_PATH.name} "
        f"(the release-loop skill mirror in section 7); found {len(matches)}. "
        "The drift guard's extraction anchor is ambiguous -- update "
        "tests/unit/test_loop_skill_drift.py to re-anchor on the mirror block."
    )
    body = matches[0].group("body")
    # Vacuity guard: the block must actually be the skill (its frontmatter),
    # so a restructure can't leave a passing empty/placeholder stub.
    assert body.startswith("---\nname: release-loop"), (
        f"The 4-backtick block in {SPEC_PATH.name} does not start with the "
        "release-loop skill frontmatter (`---\\nname: release-loop`). The drift "
        "guard is anchored on the wrong block -- update "
        "tests/unit/test_loop_skill_drift.py."
    )
    return body


def test_release_loop_skill_matches_spec_mirror() -> None:
    """The live SKILL.md must equal the section-7 mirror byte-for-byte."""
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    mirror_text = _extract_mirror(SPEC_PATH.read_text(encoding="utf-8"))
    if skill_text != mirror_text:
        pytest.fail(
            "The release-loop orchestrator procedure has drifted between its two "
            "hand-maintained copies:\n"
            f"  * {SKILL_PATH.relative_to(REPO_ROOT)} (live skill)\n"
            f"  * {SPEC_PATH.relative_to(REPO_ROOT)} section 7 (spec mirror, "
            "the 4-backtick fenced block)\n"
            "Edit BOTH copies so they stay byte-for-byte identical. If they "
            "already say what you intend, copy one over the other -- SKILL.md is "
            "the operative copy that runs at loop time, so it is the authority "
            "when resolving a genuine conflict.",
        )


# The RUN PARKED resting state + bidirectional curated-subset invariant (#584 /
# D048). Byte-identity alone cannot catch BOTH copies dropping these semantics
# together, so this content-presence guard pins the load-bearing tokens/phrases
# that a future edit must not silently delete from the operating procedure.
_REQUIRED_LOOP_SEMANTICS = (
    "RUN PARKED",  # the resting-state sentinel
    "RUN RESUMED",  # the explicit-release (un-park) sentinel
    "`parked`",  # the first-class non-terminal Status token
    "awaiting:",  # the Notes marker carrying the external condition
    "Milestone-roster reconciliation",  # the surface-once curation step
    "- surfaced-join:",  # greppable join-dedup record
    "- surfaced-leave:",  # greppable leave-dedup record
)


@pytest.mark.parametrize("needle", _REQUIRED_LOOP_SEMANTICS)
def test_release_loop_semantics_present(needle: str) -> None:
    """The PARKED / curated-subset semantics must survive in the live skill.

    Complements the byte-identity guard above: that test only proves the two
    copies AGREE; this proves they still SAY the #584 semantics (a joint
    deletion would pass byte-identity but silently regress the procedure).
    """
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    assert needle in skill_text, (
        f"The release-loop procedure lost a load-bearing #584 token/phrase: "
        f"{needle!r} is no longer in {SKILL_PATH.relative_to(REPO_ROOT)}. If this "
        "removal is intentional, update tests/unit/test_loop_skill_drift.py "
        "(_REQUIRED_LOOP_SEMANTICS) and record the decision in decisions.md."
    )
