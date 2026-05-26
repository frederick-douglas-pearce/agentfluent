"""Detection and identity for the Tier 3 GitHub auth path.

Three concerns:

1. **Binary + auth detection.** Confirm ``gh`` is installed and the
   user has run ``gh auth login`` before any API call. Failures here
   surface as friendly, typed exceptions the CLI converts to install
   / login hints. Memoized via :func:`functools.lru_cache` for the
   process lifetime so :func:`gh_api` can call :func:`detect_gh` as a
   precondition without paying repeated subprocess cost; tests call
   ``detect_gh.cache_clear()`` between cases.
2. **Auth user identity.** A cached ``gh api user --jq .login`` lookup
   feeds the cache key (so a shared machine can't leak entries across
   accounts). Also lru-cached for testability.
3. **Repo inference.** Map a project's on-disk directory to a
   :class:`GitHubRepo` by parsing the ``origin`` remote URL. The
   ``--repo OWNER/NAME`` CLI override goes through
   :func:`parse_repo_override` instead.

All subprocess invocations mirror ``diagnostics/git_signals.py``:
stdlib :mod:`subprocess`, bounded timeout, ``shell=False``, graceful
handling of ``FileNotFoundError`` / ``TimeoutExpired``.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from agentfluent.github.models import (
    GhNotAuthenticatedError,
    GhNotInstalledError,
    GitHubRepo,
    RepoInferenceError,
)

logger = logging.getLogger(__name__)

_GH_TIMEOUT_SEC = 30
_GIT_TIMEOUT_SEC = 10

# Owner: GitHub username / organization. Per GitHub's rules, alphanumeric
# plus hyphens, no leading/trailing hyphen, no consecutive hyphens. The
# regex is slightly looser (single-hyphen runs disallowed only at the
# boundaries) — strict-enough to reject ``.``, ``..``, ``-evil``, and
# similar non-name segments that could leak into endpoint construction.
#
# Repo: looser than owner. May contain underscores and dots, but must
# start with an alphanumeric (rejects ``.``, ``..``, leading-dot names).
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_REPO = r"[A-Za-z0-9][\w.-]*?"

# Match the two URL forms `git remote get-url origin` can produce:
#   git@github.com:owner/repo(.git)?
#   https://github.com/owner/repo(.git)?
# The trailing `.git` and any trailing slash are optional and consumed
# at the suffix; the ``_REPO`` non-greedy match lets the suffix bind.
_SSH_REMOTE = re.compile(
    rf"^git@github\.com:(?P<owner>{_OWNER})/(?P<repo>{_REPO})(?:\.git)?/?$",
)
_HTTPS_REMOTE = re.compile(
    rf"^https?://github\.com/(?P<owner>{_OWNER})/(?P<repo>{_REPO})(?:\.git)?/?$",
)
# The override accepts a trailing ``.git`` for parity with what users
# copy-paste from a clone URL, but strips it so the resulting
# ``GitHubRepo.repo`` matches what :func:`infer_repo` would produce
# for the same logical repository.
_OWNER_REPO = re.compile(
    rf"^(?P<owner>{_OWNER})/(?P<repo>{_REPO})(?:\.git)?/?$",
)


@lru_cache(maxsize=1)
def detect_gh() -> None:
    """Raise if ``gh`` is unavailable or unauthenticated.

    Two checks, fail-fast: binary present, then ``gh auth status``
    exits 0. The CLI catches ``GhNotInstalledError`` /
    ``GhNotAuthenticatedError`` and prints the install / login hint;
    programmatic callers do whatever they like.

    Memoized so :func:`gh_api` can call ``detect_gh()`` as a
    precondition on every request without paying subprocess cost; the
    successful-path return value (``None``) is cached. Raised
    exceptions are *not* cached, so a failed detection on the first
    call does not poison subsequent retries after the user installs
    or authenticates ``gh``.
    """
    if shutil.which("gh") is None:
        raise GhNotInstalledError(
            "Tier 3 signals require the GitHub CLI (`gh`). "
            "Install: https://cli.github.com/",
        )
    try:
        result = subprocess.run(  # noqa: S603 — args are constants
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError as e:
        # gh disappeared between shutil.which() and subprocess.run() —
        # rare but possible (PATH change, uninstall). Classify as
        # not-installed so the user sees the install hint, not the
        # login hint.
        raise GhNotInstalledError(
            "`gh` binary disappeared between PATH check and invocation. "
            "Reinstall: https://cli.github.com/",
        ) from e
    except subprocess.TimeoutExpired as e:
        raise GhNotAuthenticatedError(
            "`gh auth status` timed out; check `gh` configuration.",
        ) from e
    if result.returncode != 0:
        raise GhNotAuthenticatedError(
            "GitHub CLI is not authenticated. Run `gh auth login`.",
        )


@lru_cache(maxsize=1)
def gh_auth_login() -> str:
    """Return the authenticated user's GitHub login.

    Memoized for the process lifetime so we make a single
    ``gh api user`` subprocess call per CLI run (one cache write
    amortized over every Tier 3 API call). Tests call
    :func:`gh_auth_login.cache_clear` to reset between runs.

    Note: long-running embeddings that span a ``gh auth switch`` need
    to call ``cache_clear()`` manually after the switch — the memo
    intentionally pins identity for the process lifetime to keep cache
    keys stable across an analyze run.

    Raises :class:`GhNotAuthenticatedError` on any failure — by the
    time this is called, :func:`detect_gh` has already validated
    auth, so a non-zero exit here means transient gh / network state
    rather than configuration. Treating it as an auth failure is the
    right user-facing classification.
    """
    try:
        result = subprocess.run(  # noqa: S603 — args are constants
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError as e:
        # As in detect_gh: a missing-binary race is install state,
        # not auth state. detect_gh() should have caught this first,
        # but raise the correct typed error if it didn't.
        raise GhNotInstalledError(
            "`gh` binary not found. Install: https://cli.github.com/",
        ) from e
    except subprocess.TimeoutExpired as e:
        raise GhNotAuthenticatedError(
            "Could not resolve `gh` auth user (timeout). Run `gh auth status`.",
        ) from e
    if result.returncode != 0:
        raise GhNotAuthenticatedError(
            f"`gh api user` failed: {result.stderr.strip()}",
        )
    login = result.stdout.strip()
    if not login:
        raise GhNotAuthenticatedError("`gh api user` returned an empty login.")
    return login


def infer_repo(project_disk_path: Path | None) -> GitHubRepo:
    """Map a project directory to a :class:`GitHubRepo`.

    Reads ``git -C <path> remote get-url origin`` and parses the SSH
    or HTTPS form. Raises :class:`RepoInferenceError` when:

    - ``project_disk_path`` is ``None`` (slug-to-path resolution
      failed upstream),
    - the directory is not a git repo,
    - ``origin`` is absent,
    - the remote URL is not on github.com.

    The CLI catches the exception and points users at the
    ``--repo OWNER/NAME`` override.
    """
    if project_disk_path is None:
        raise RepoInferenceError(
            "could not resolve the project's on-disk path "
            "(missing or stale ~/.claude.json entry)",
        )
    if not project_disk_path.exists():
        raise RepoInferenceError(
            f"project directory does not exist: {project_disk_path}",
        )
    try:
        result = subprocess.run(  # noqa: S603 — args are constants, project_disk_path is path-typed
            ["git", "-C", str(project_disk_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError as e:
        raise RepoInferenceError("`git` binary not found on PATH.") from e
    except subprocess.TimeoutExpired as e:
        raise RepoInferenceError("`git remote get-url origin` timed out.") from e

    if result.returncode != 0:
        stderr_lower = result.stderr.lower()
        if "not a git repository" in stderr_lower:
            raise RepoInferenceError(
                f"{project_disk_path} is not a git working tree",
            )
        if "no such remote" in stderr_lower:
            raise RepoInferenceError(
                f"no `origin` remote in {project_disk_path}",
            )
        raise RepoInferenceError(
            f"`git remote get-url origin` failed in {project_disk_path}: "
            f"{result.stderr.strip()}",
        )

    url = result.stdout.strip()
    match = _SSH_REMOTE.match(url) or _HTTPS_REMOTE.match(url)
    if match is None:
        raise RepoInferenceError(
            f"`origin` remote is not on github.com: {url}",
        )
    return GitHubRepo(owner=match["owner"], repo=match["repo"])


def parse_repo_override(value: str) -> GitHubRepo:
    """Parse an explicit ``OWNER/NAME`` override into a :class:`GitHubRepo`.

    Trailing ``.git`` and trailing slashes are stripped so the result
    matches what :func:`infer_repo` would produce for the same
    logical repository — preventing cache-key fragmentation and 404s
    against ``repos/<owner>/<repo>.git/...`` endpoints.

    Raises :class:`ValueError` on malformed input — the CLI converts
    this into a ``typer.BadParameter`` so the user sees a flag-name
    error rather than a stack trace. Inputs containing ``..``,
    leading dots, or leading dashes are rejected by the underlying
    regex.
    """
    match = _OWNER_REPO.match(value.strip())
    if match is None:
        raise ValueError(
            f"--repo must be of the form OWNER/NAME (alphanumeric, "
            f"hyphen, underscore, dot — no leading dots or `..`), "
            f"got: {value!r}",
        )
    return GitHubRepo(owner=match["owner"], repo=match["repo"])
