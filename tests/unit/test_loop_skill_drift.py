"""CI guards for the split release-loop harness (engine / config / thin skill).

As of #612 the loop harness is three files instead of a spec-with-embedded-mirror:

  * ``.claude/skills/release-loop/SKILL.md`` -- the thin ``/release-loop`` entry point;
  * ``.claude/skills/release-loop/loop-engine.md`` -- the generic engine (the operating
    procedure + all semantics), the single source of truth for the loop's logic; and
  * ``.claude/loop.config.md`` -- the per-project bindings (parameters, architect triggers,
    source layout, security routing).

The old byte-identity guard (SKILL.md == a spec mirror) is retired: the procedure now lives
once, in the engine, so there is nothing to mirror. In its place these guards pin the invariants
the split is supposed to hold:

  * the engine still SAYS the load-bearing PARKED / curated-subset semantics (#584 / D048);
  * the engine stays GENERIC -- no project-binding literal leaks across the seam;
  * every parameter the engine names is DEFINED in the config (completeness); and
  * the thin skill still POINTS at both sibling files (pointer-rot).

See issue #612 and decision D051 for the split rationale.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "release-loop" / "SKILL.md"
ENGINE_PATH = REPO_ROOT / ".claude" / "skills" / "release-loop" / "loop-engine.md"
CONFIG_PATH = REPO_ROOT / ".claude" / "loop.config.md"


# ---------------------------------------------------------------------------
# 1. Load-bearing loop semantics must survive in the engine.
# ---------------------------------------------------------------------------
# The RUN PARKED resting state + bidirectional curated-subset invariant (#584 / D048). These
# tokens/phrases were formerly pinned in SKILL.md; the procedure moved to the engine, so they
# are pinned there now. A future edit must not silently delete them from the operating
# procedure.
_REQUIRED_LOOP_SEMANTICS = (
    "RUN PARKED",  # the resting-state sentinel
    "RUN RESUMED",  # the explicit-release (un-park) sentinel
    "`parked`",  # the first-class non-terminal Status token
    "awaiting:",  # the Notes marker carrying the external condition
    "Roster reconciliation",  # the surface-once curation step (BACKLOG_SOURCE roster)
    "- surfaced-join:",  # greppable join-dedup record
    "- surfaced-leave:",  # greppable leave-dedup record
)


@pytest.mark.parametrize("needle", _REQUIRED_LOOP_SEMANTICS)
def test_release_loop_semantics_present(needle: str) -> None:
    """The PARKED / curated-subset semantics must survive in the generic engine."""
    engine_text = ENGINE_PATH.read_text(encoding="utf-8")
    assert needle in engine_text, (
        f"The release-loop procedure lost a load-bearing #584 token/phrase: "
        f"{needle!r} is no longer in {ENGINE_PATH.relative_to(REPO_ROOT)}. If this "
        "removal is intentional, update tests/unit/test_loop_skill_drift.py "
        "(_REQUIRED_LOOP_SEMANTICS) and record the decision in decisions.md."
    )


# ---------------------------------------------------------------------------
# 2. The engine must stay generic (no project-binding literals leak the seam).
# ---------------------------------------------------------------------------
# Scoped to *bindings* that belong in loop.config.md -- NOT to git/GitHub verbs or issue refs,
# which the engine legitimately describes. Each of these is a value the porting seam is supposed
# to isolate; its presence in the engine means a project binding leaked through.
_FORBIDDEN_IN_ENGINE = (
    "agentfluent",  # the project name (also catches src/agentfluent, mypy target)
    "uv run",  # the LINT_CMD/TYPE_CMD/TEST_CMD runner
    "mypy",  # the TYPE_CMD tool
    "ruff",  # the LINT_CMD tool
    "pytest",  # the TEST_CMD tool
    "anthropic-feature-watch",  # the project's research-scout feed
)


@pytest.mark.parametrize("needle", _FORBIDDEN_IN_ENGINE)
def test_engine_stays_generic(needle: str) -> None:
    """No AgentFluent-specific binding literal may appear in the generic engine."""
    engine_text = ENGINE_PATH.read_text(encoding="utf-8").lower()
    assert needle not in engine_text, (
        f"A project-binding literal leaked into the generic engine: {needle!r} appears in "
        f"{ENGINE_PATH.relative_to(REPO_ROOT)}. Bindings belong in "
        f"{CONFIG_PATH.relative_to(REPO_ROOT)} and must be referenced from the engine by "
        "parameter NAME only. Move the value to the config (or express it as a parameter)."
    )


# ---------------------------------------------------------------------------
# 3. Config completeness: every parameter the engine names must be defined in the config.
# ---------------------------------------------------------------------------
# The engine references project values only by CAPS parameter name; the config must bind every
# one. This is the DIP contract of the seam -- the engine depends on the config's vocabulary, so
# a name the engine uses with no config definition is a broken binding.
_ENGINE_PARAMETERS = (
    "ARCHITECT_TRIGGERS",
    "BACKLOG_SOURCE",
    "BRANCH_FMT",
    "CODE_REVIEW",
    "COMMIT_CONV",
    "DESIGN_AGENT",
    "LEDGER_ROOT",
    "LINT_CMD",
    "MERGE_METHOD",
    "PRIORITY_LABELS",
    "PR_TEMPLATE",
    "RELEASE_SCHEME",
    "SCOPE_AGENT",
    "SECURITY_REVIEW",
    "SOURCE_LAYOUT",
    "TEST_CMD",
    "TYPE_CMD",
    "VERIFY",
)


@pytest.mark.parametrize("parameter", _ENGINE_PARAMETERS)
def test_config_defines_engine_parameters(parameter: str) -> None:
    """Each parameter the engine names by CAPS must be bound in loop.config.md."""
    engine_text = ENGINE_PATH.read_text(encoding="utf-8")
    config_text = CONFIG_PATH.read_text(encoding="utf-8")
    # The engine must actually reference it (keeps this list honest as the engine evolves)...
    assert parameter in engine_text, (
        f"{parameter!r} is listed in _ENGINE_PARAMETERS but no longer referenced in "
        f"{ENGINE_PATH.relative_to(REPO_ROOT)}. Remove it from the list if the engine dropped it."
    )
    # ...and the config must define it.
    assert parameter in config_text, (
        f"The engine names {parameter!r} but {CONFIG_PATH.relative_to(REPO_ROOT)} does not "
        "define it. Every engine parameter needs a per-project binding, or the loop has a "
        "dangling reference at runtime."
    )


# ---------------------------------------------------------------------------
# 4. Pointer-rot: the thin skill must still route to both sibling files.
# ---------------------------------------------------------------------------
def test_skill_points_to_engine_and_config() -> None:
    """SKILL.md must name both the engine and the config it delegates to."""
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    # Frontmatter is load-bearing for skill triggering (and a downstream fixture pins it).
    assert skill_text.startswith("---\nname: release-loop"), (
        f"{SKILL_PATH.relative_to(REPO_ROOT)} lost its `name: release-loop` frontmatter -- the "
        "skill would not register (or trigger) correctly."
    )
    for sibling in ("loop-engine.md", "loop.config.md"):
        assert sibling in skill_text, (
            f"The thin skill no longer points at {sibling!r}. A partial load must read both "
            "siblings; without the reference the procedure/bindings never load. Restore the "
            f"pointer in {SKILL_PATH.relative_to(REPO_ROOT)}."
        )
