"""Public types for the Tier 3 GitHub enrichment subpackage.

Re-exported through ``agentfluent.github`` so downstream signal
extractors (``#400``, ``#401``) consume them via the package root
rather than reaching into individual modules.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class GitHubRepo(BaseModel):
    """A GitHub repository identified by owner and repo name.

    Produced by the infrastructure layer (either inferred from the
    project's git remote or parsed from an explicit ``--repo`` override)
    and consumed by per-signal Tier 3 extractors. Owning this model in
    the infrastructure layer means signal modules never re-parse a git
    remote — they receive an already-validated ``GitHubRepo`` from the
    pipeline.
    """

    owner: str
    repo: str


class GhNotInstalledError(Exception):
    """``gh`` binary is not on PATH.

    Raised by :func:`agentfluent.github.detection.detect_gh` when the
    GitHub CLI cannot be invoked. The CLI catches this and prints a
    friendly install hint; programmatic callers handle it however they
    like.
    """


class GhNotAuthenticatedError(Exception):
    """``gh`` is installed but ``gh auth status`` returned non-zero.

    Raised by :func:`agentfluent.github.detection.detect_gh` when the
    user has the binary but has not run ``gh auth login``. The CLI
    surfaces this with the appropriate ``gh auth login`` hint.
    """


class RepoInferenceError(Exception):
    """The project's git remote does not map to a GitHub repository.

    Raised by :func:`agentfluent.github.detection.infer_repo` when the
    project directory is not a git repo, has no ``origin`` remote, or
    the remote URL is not a GitHub host. The CLI prints a hint pointing
    at the ``--repo OWNER/NAME`` override.
    """


class RateLimitedError(Exception):
    """``gh api`` returned 403 or 429 with rate-limit headers.

    Carries the reset time and endpoint so per-signal extractors can
    log a useful WARNING and skip cleanly. Tier 3 sets
    ``DiagnosticsResult.tier3_degraded = True`` when at least one
    extractor hits this path; the run still exits 0.
    """

    def __init__(self, *, reset_at: datetime, endpoint: str) -> None:
        super().__init__(
            f"GitHub API rate limit hit for {endpoint!r}; resets at {reset_at.isoformat()}",
        )
        self.reset_at = reset_at
        self.endpoint = endpoint
