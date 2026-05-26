"""Tier 3 GitHub enrichment infrastructure (#399).

Public surface for downstream signal extractors (#400, #401). All
imports go through this module so signal code does not need to know
the internal file layout — it consumes a curated set of names.
"""

from __future__ import annotations

from agentfluent.github.cache import (
    TTL_CLOSED_PR,
    TTL_OPEN_PR_OR_CI,
    TTL_REPO_METADATA,
)
from agentfluent.github.client import gh_api
from agentfluent.github.consent import (
    has_consent,
    is_stdin_tty,
    prompt_and_record_if_needed,
    record_consent,
)
from agentfluent.github.detection import (
    detect_gh,
    gh_auth_login,
    infer_repo,
    parse_repo_override,
)
from agentfluent.github.models import (
    GhNotAuthenticatedError,
    GhNotInstalledError,
    GitHubRepo,
    RateLimitedError,
    RepoInferenceError,
)

__all__ = [
    "TTL_CLOSED_PR",
    "TTL_OPEN_PR_OR_CI",
    "TTL_REPO_METADATA",
    "GhNotAuthenticatedError",
    "GhNotInstalledError",
    "GitHubRepo",
    "RateLimitedError",
    "RepoInferenceError",
    "detect_gh",
    "gh_api",
    "gh_auth_login",
    "has_consent",
    "infer_repo",
    "is_stdin_tty",
    "parse_repo_override",
    "prompt_and_record_if_needed",
    "record_consent",
]
