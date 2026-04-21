"""Project and session discovery from ~/.claude/projects/."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agentfluent.core.paths import DEFAULT_CLAUDE_CONFIG_DIR

DEFAULT_PROJECTS_DIR = DEFAULT_CLAUDE_CONFIG_DIR / "projects"


@dataclass
class SessionInfo:
    """Metadata for a single JSONL session file."""

    filename: str
    path: Path
    size_bytes: int
    modified: datetime
    subagent_count: int = 0
    """Number of subagent trace files in <session-uuid>/subagents/."""


@dataclass
class ProjectInfo:
    """Metadata for a discovered project directory."""

    slug: str
    """Directory name as-is (e.g., '-home-fdpearce-Documents-Projects-git-codefluent')."""

    display_name: str
    """Human-friendly name derived from slug (e.g., 'codefluent')."""

    path: Path
    session_count: int = 0
    total_size_bytes: int = 0
    earliest_session: datetime | None = None
    latest_session: datetime | None = None
    sessions: list[SessionInfo] = field(default_factory=list)


def slug_to_display_name(slug: str) -> str:
    """Convert a dash-encoded project directory name to a human-friendly name.

    The directory format is: -home-user-path-to-project
    We take the last path segment as the display name.
    """
    # Remove leading dash and split
    parts = slug.lstrip("-").split("-")
    # The last segment is typically the project name
    # For paths like -home-fdpearce-Documents-Projects-git-codefluent -> codefluent
    return parts[-1] if parts else slug


def _count_subagent_files(session_path: Path) -> int:
    """Count subagent JSONL files for a session.

    Subagent traces live at: <session-uuid>/subagents/agent-<agentId>.jsonl
    where <session-uuid> is a directory named the same as the session file (minus .jsonl).
    """
    session_dir = session_path.parent / session_path.stem
    subagents_dir = session_dir / "subagents"
    if not subagents_dir.is_dir():
        return 0
    return sum(1 for f in subagents_dir.iterdir() if f.suffix == ".jsonl")


def discover_sessions(project_path: Path) -> list[SessionInfo]:
    """Discover all JSONL session files within a project directory.

    Returns session metadata sorted by modification time (newest first).
    Only top-level .jsonl files are returned; subagent files are counted but not listed.
    """
    sessions: list[SessionInfo] = []

    if not project_path.is_dir():
        return sessions

    for entry in project_path.iterdir():
        if entry.is_file() and entry.suffix == ".jsonl":
            stat = entry.stat()
            sessions.append(
                SessionInfo(
                    filename=entry.name,
                    path=entry,
                    size_bytes=stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    subagent_count=_count_subagent_files(entry),
                )
            )

    sessions.sort(key=lambda s: s.modified, reverse=True)
    return sessions


def discover_projects(base_path: Path | None = None) -> list[ProjectInfo]:
    """Discover all projects in the Claude projects directory.

    Args:
        base_path: Override for the projects directory. Defaults to ~/.claude/projects/.

    Returns:
        List of ProjectInfo sorted by latest session (newest first).

    Raises:
        FileNotFoundError: If the base path does not exist.
    """
    projects_dir = base_path or DEFAULT_PROJECTS_DIR

    if not projects_dir.exists():
        msg = f"Projects directory not found: {projects_dir}"
        raise FileNotFoundError(msg)

    projects: list[ProjectInfo] = []

    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Skip hidden directories and non-project entries
        if entry.name.startswith("."):
            continue

        sessions = discover_sessions(entry)

        total_size = sum(s.size_bytes for s in sessions)
        earliest = min((s.modified for s in sessions), default=None)
        latest = max((s.modified for s in sessions), default=None)

        projects.append(
            ProjectInfo(
                slug=entry.name,
                display_name=slug_to_display_name(entry.name),
                path=entry,
                session_count=len(sessions),
                total_size_bytes=total_size,
                earliest_session=earliest,
                latest_session=latest,
                sessions=sessions,
            )
        )

    # Sort by latest session, projects with no sessions last
    projects.sort(
        key=lambda p: p.latest_session or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return projects


def find_project(slug_or_name: str, base_path: Path | None = None) -> ProjectInfo | None:
    """Find a project by slug or display name.

    Matches against both the full slug and the derived display name (case-insensitive).
    """
    for project in discover_projects(base_path):
        if project.slug == slug_or_name or project.display_name.lower() == slug_or_name.lower():
            return project
    return None
