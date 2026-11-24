"""Agent definition scanner and parser.

Discovers and parses agent `.md` files from user and project scopes.
Agent definitions use YAML frontmatter followed by a markdown prompt body.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from agentfluent.config.models import AgentConfig, Scope
from agentfluent.core.paths import AGENTS_SUBDIR, DEFAULT_CLAUDE_CONFIG_DIR

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENTS_DIR = DEFAULT_CLAUDE_CONFIG_DIR / AGENTS_SUBDIR


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into YAML frontmatter and body.

    Expects the format:
        ---
        yaml content
        ---
        markdown body

    Returns (frontmatter_dict, body_string). If no valid frontmatter
    is found, returns ({}, full_content).
    """
    content = content.strip()
    if not content.startswith("---"):
        return {}, content

    # Find the closing ---
    end_idx = content.find("---", 3)
    if end_idx == -1:
        return {}, content

    yaml_str = content[3:end_idx].strip()
    body = content[end_idx + 3:].strip()

    try:
        frontmatter = yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        return {}, content

    if not isinstance(frontmatter, dict):
        return {}, content

    return frontmatter, body


def _to_string_list(value: Any) -> list[str]:
    """Coerce a frontmatter value to a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _split_top_level_commas(text: str) -> tuple[list[str], bool]:
    """Split on commas outside parentheses; report whether a `(` was left open.

    A stray `)` never takes the depth below zero, so the commas after it
    still split.
    """
    pieces: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            pieces.append("".join(current))
            current = []
            continue
        current.append(char)
    pieces.append("".join(current))
    return pieces, depth > 0


def _to_tool_list(value: Any, path: Path, field: str) -> list[str]:
    """Coerce a `tools` / `disallowedTools` frontmatter value to tool entries.

    Claude Code accepts these fields as a comma-separated string
    (`Read, Grep, Bash`) or a YAML list. A string is split on commas at
    parenthesis depth 0, so a specifier such as `Agent(worker, researcher)`
    stays one entry. An unclosed `(` keeps the rest of the string as one
    entry and logs a warning. YAML list items are never comma-split. In
    both forms, entries are stripped, and empty or null ones are dropped.
    """
    if isinstance(value, str):
        pieces, unclosed = _split_top_level_commas(value)
        if unclosed:
            logger.warning(
                "Unbalanced '(' in %s frontmatter of %s; kept the rest as one entry",
                field, path.name,
            )
    elif isinstance(value, list):
        pieces = [str(item) for item in value if item is not None]
    else:
        return []
    return [piece.strip() for piece in pieces if piece.strip()]


def parse_agent_file(path: Path, scope: Scope) -> AgentConfig | None:
    """Parse a single agent definition `.md` file.

    Returns None if the file cannot be read. Files without valid
    frontmatter are still returned with a warning.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Cannot read agent file: %s", path)
        return None

    frontmatter, body = _parse_frontmatter(content)

    if not frontmatter:
        logger.warning("No valid YAML frontmatter in: %s", path.name)

    name = frontmatter.get("name") or path.stem

    return AgentConfig(
        name=name,
        file_path=path.resolve(),
        scope=scope,
        description=str(frontmatter.get("description", "")),
        model=frontmatter.get("model"),
        prompt_body=body,
        tools=_to_tool_list(frontmatter.get("tools"), path, "tools"),
        disallowed_tools=_to_tool_list(
            frontmatter.get("disallowedTools"), path, "disallowedTools",
        ),
        mcp_servers=_to_string_list(frontmatter.get("mcpServers")),
        hooks=frontmatter.get("hooks") or {},
        skills=_to_string_list(frontmatter.get("skills")),
        memory=frontmatter.get("memory"),
        isolation=frontmatter.get("isolation"),
        color=frontmatter.get("color"),
        raw_frontmatter=frontmatter,
    )


def _scan_directory(agents_dir: Path, scope: Scope) -> list[AgentConfig]:
    """Scan a directory for agent `.md` files."""
    if not agents_dir.is_dir():
        return []

    agents: list[AgentConfig] = []
    for entry in sorted(agents_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".md":
            agent = parse_agent_file(entry, scope)
            if agent is not None:
                agents.append(agent)
    return agents


def scan_agents(
    scope: str = "all",
    *,
    user_path: Path | None = None,
    project_path: Path | None = None,
) -> list[AgentConfig]:
    """Discover and parse agent definition files.

    Args:
        scope: Which locations to scan -- "user", "project", or "all".
        user_path: Override for user agents directory. Defaults to ~/.claude/agents/.
        project_path: Override for project agents directory. Defaults to .claude/agents/.

    Returns:
        List of parsed AgentConfig objects, sorted by scope then name.
    """
    agents: list[AgentConfig] = []

    if scope in ("user", "all"):
        user_dir = user_path or DEFAULT_USER_AGENTS_DIR
        agents.extend(_scan_directory(user_dir, Scope.USER))

    if scope in ("project", "all"):
        project_dir = project_path or (Path.cwd() / ".claude" / "agents")
        agents.extend(_scan_directory(project_dir, Scope.PROJECT))

    return agents
