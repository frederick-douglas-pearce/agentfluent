"""Data models for agent configuration assessment.

These models represent parsed agent definition files and their scoring results.
They cross module boundaries (scanner -> scorer -> CLI -> diagnostics), so
Pydantic is used for validation and serialization.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Scope(StrEnum):
    """Where an agent definition was discovered."""

    USER = "user"
    PROJECT = "project"


class Severity(StrEnum):
    """Recommendation severity level."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AgentConfig(BaseModel):
    """Parsed agent definition from a `.md` file.

    Captures both explicitly-modeled fields and raw frontmatter for
    forward compatibility with new agent config fields.
    """

    name: str
    """Agent name from frontmatter or filename."""

    file_path: Path
    """Absolute path to the `.md` file."""

    scope: Scope
    """Whether this came from user or project agents directory."""

    # Core fields
    description: str = ""
    """Agent description from frontmatter."""

    model: str | None = None
    """Model name (e.g., 'claude-opus-4-6')."""

    prompt_body: str = ""
    """Everything after the YAML frontmatter closing `---`."""

    # Tool access
    tools: list[str] = Field(default_factory=list)
    """Allowed tools list."""

    disallowed_tools: list[str] = Field(default_factory=list)
    """Disallowed tools list."""

    # Additional config fields
    mcp_servers: list[str] = Field(default_factory=list)
    """MCP server names."""

    hooks: dict[str, Any] = Field(default_factory=dict)
    """Hook configuration (complex nested structure)."""

    skills: list[str] = Field(default_factory=list)
    """Skill names."""

    memory: str | None = None
    """Memory scope (e.g., 'user')."""

    isolation: str | None = None
    """Isolation mode (e.g., 'worktree')."""

    color: str | None = None
    """Agent color for display."""

    raw_frontmatter: dict[str, Any] = Field(default_factory=dict)
    """Complete raw frontmatter dict for fields not explicitly modeled."""


class ConfigRecommendation(BaseModel):
    """A specific, actionable recommendation from config scoring."""

    dimension: str
    """Which scoring dimension produced this recommendation."""

    severity: Severity
    """How important this recommendation is."""

    message: str
    """Human-readable recommendation text."""

    current_value: str = ""
    """What was found in the config (for context)."""

    suggested_action: str = ""
    """What the user should change."""


class ConfigScore(BaseModel):
    """Scoring results for a single agent configuration."""

    agent_name: str
    overall_score: int = 0
    """Overall score (0-100), sum of dimension scores."""

    dimension_scores: dict[str, int] = Field(default_factory=dict)
    """Per-dimension scores, keyed by dimension name."""

    recommendations: list[ConfigRecommendation] = Field(default_factory=list)
    """Actionable recommendations for improving the config."""
