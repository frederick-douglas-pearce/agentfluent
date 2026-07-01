"""Agent configuration scoring rubric.

Scores agent definitions against best practices across four dimensions.
Rule-based scoring (no LLM). Weights and thresholds are configurable.
"""

from __future__ import annotations

import re

from agentfluent.config.models import (
    AgentConfig,
    ConfigRecommendation,
    ConfigScore,
    Severity,
)

# Read-only tools suggest simpler tasks where cheaper models suffice
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "Read", "Glob", "Grep", "WebFetch", "WebSearch",
})

# Action verbs that indicate a well-described agent purpose
ACTION_VERBS: frozenset[str] = frozenset({
    "analyze", "build", "check", "create", "debug", "deploy", "design",
    "evaluate", "extract", "fix", "generate", "implement", "investigate",
    "manage", "monitor", "optimize", "plan", "refactor", "review",
    "scan", "search", "test", "translate", "validate", "verify", "write",
})

# Expensive models that may be overkill for simple tasks
EXPENSIVE_MODELS: frozenset[str] = frozenset({
    "claude-opus-4-6", "claude-opus-4-5-20251101",
})

# Patterns suggesting structured prompt sections
SECTION_PATTERN = re.compile(r"^#{1,3}\s+", re.MULTILINE)

# Keywords suggesting error handling guidance
ERROR_KEYWORDS: frozenset[str] = frozenset({
    "error", "fail", "exception", "fallback", "retry", "graceful",
    "handle", "recover", "warning",
})

# Keywords suggesting success criteria
SUCCESS_KEYWORDS: frozenset[str] = frozenset({
    "success", "complete", "done", "finish", "output", "return",
    "produce", "deliver", "result", "criteria",
})

# Verbosity-constraint detection (C-006a). Blanket word-count caps on agent
# responses can silently degrade quality -- Anthropic's April 2026 postmortem
# documented a 3% coding regression from a "keep responses to <=25 words" cap.
# We flag captured word counts at or below this threshold as advisory only.
VERBOSITY_CONSTRAINT_MAX_WORDS = 200

# WARNING at/below this many words; INFO above it (up to the max threshold).
VERBOSITY_CONSTRAINT_WARNING_WORDS = 50

VERBOSITY_POSTMORTEM_URL = "https://www.anthropic.com/engineering/april-23-postmortem"

# Regex list from the #431 architect design, with two corrections so the
# canonical no-space postmortem phrasing ("<=25", "<=100") is caught:
#   - pattern 1 tolerates an optional <=/≤ symbol before the digits
#   - pattern 2 uses \s* (not \s+) after the symbol alternation
# Without these the exact string the feature targets goes undetected (dead feature).
VERBOSITY_CONSTRAINT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:keep|limit|restrict|cap)\b.{0,40}\b(?:to|under|at most|no more than)"
        r"\s+(?:≤|<=)?\s*(\d+)\s+words",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:≤|<=|at most|max(?:imum)?|no more than)\s*(\d+)\s+words",
        re.IGNORECASE,
    ),
    re.compile(r"(\d+)\s+words?\s+(?:or fewer|max(?:imum)?|limit)", re.IGNORECASE),
    re.compile(r"respond\s+(?:in|with)\s+(\d+)\s+(?:words|tokens)\b", re.IGNORECASE),
]

# Strips fenced code blocks (```...```) so constraint examples inside code
# samples don't trigger a recommendation.
_FENCED_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)


def _detect_verbosity_constraints(body: str) -> list[tuple[int, str]]:
    """Find blanket word-count constraints in a prompt body.

    Returns ``(word_count, matched_text)`` pairs for matches at or below
    ``VERBOSITY_CONSTRAINT_MAX_WORDS``. Fenced code blocks are stripped before
    scanning. Overlapping matches (one constraint caught by multiple patterns)
    are de-duplicated by span so each distinct constraint yields one result.
    """
    scrubbed = _FENCED_CODE_BLOCK.sub("", body)

    spans: list[tuple[int, int, int, str]] = []  # start, end, word_count, text
    for pattern in VERBOSITY_CONSTRAINT_PATTERNS:
        for match in pattern.finditer(scrubbed):
            word_count = int(match.group(1))
            if word_count <= VERBOSITY_CONSTRAINT_MAX_WORDS:
                spans.append((match.start(), match.end(), word_count, match.group(0)))

    # Earliest start first, longest match first, then greedily accept only
    # non-overlapping spans so a single constraint isn't reported twice.
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    results: list[tuple[int, str]] = []
    last_end = -1
    for start, end, word_count, text in spans:
        if start >= last_end:
            results.append((word_count, text))
            last_end = end
    return results


def _score_description(config: AgentConfig) -> tuple[int, list[ConfigRecommendation]]:
    """Score the agent description quality (0-25)."""
    score = 0
    recs: list[ConfigRecommendation] = []
    desc = config.description.strip()

    # Present (5 pts)
    if desc:
        score += 5
    else:
        recs.append(ConfigRecommendation(
            dimension="description",
            severity=Severity.CRITICAL,
            message="Agent has no description.",
            suggested_action="Add a description that explains when to invoke this agent.",
        ))
        return score, recs

    # Length >= 20 chars (5 pts)
    if len(desc) >= 20:
        score += 5
    else:
        recs.append(ConfigRecommendation(
            dimension="description",
            severity=Severity.WARNING,
            message=f"Description is only {len(desc)} characters.",
            current_value=desc[:50],
            suggested_action="Expand the description to at least 20 characters.",
        ))

    # Contains action verbs (5 pts)
    desc_lower = desc.lower()
    if any(v in desc_lower for v in ACTION_VERBS):
        score += 5
    else:
        recs.append(ConfigRecommendation(
            dimension="description",
            severity=Severity.INFO,
            message="Description lacks action verbs.",
            current_value=desc[:80],
            suggested_action="Include verbs like 'review', 'analyze', 'create' to clarify purpose.",
        ))

    # Specific to task -- more than 50 chars suggests specificity (5 pts)
    if len(desc) >= 50:
        score += 5
    else:
        recs.append(ConfigRecommendation(
            dimension="description",
            severity=Severity.INFO,
            message="Description could be more specific.",
            current_value=desc[:80],
            suggested_action="Add details about what tasks this agent handles and when to use it.",
        ))

    # Distinguishes from other agents -- mentions "Do NOT" or exclusions (5 pts)
    if "not" in desc_lower or "don't" in desc_lower or "skip" in desc_lower:
        score += 5

    return score, recs


def _score_tool_restrictions(config: AgentConfig) -> tuple[int, list[ConfigRecommendation]]:
    """Score tool access restrictions (0-25).

    Awards points for having explicit tool restrictions (both allowlist
    and denylist), which is a best practice for agent safety.
    """
    score = 0
    recs: list[ConfigRecommendation] = []

    has_allowlist = len(config.tools) > 0
    has_denylist = len(config.disallowed_tools) > 0

    # Has an allowlist (10 pts)
    if has_allowlist:
        score += 10
    else:
        recs.append(ConfigRecommendation(
            dimension="tool_restrictions",
            severity=Severity.WARNING,
            message="No tools allowlist defined.",
            suggested_action="Add a 'tools' list to restrict which tools the agent can use.",
        ))

    # Has a denylist (5 pts)
    if has_denylist:
        score += 5

    # Has either restriction (5 pts bonus for having any restriction at all)
    if has_allowlist or has_denylist:
        score += 5
    else:
        recs.append(ConfigRecommendation(
            dimension="tool_restrictions",
            severity=Severity.CRITICAL,
            message="Agent has no tool restrictions at all.",
            suggested_action=(
                "Add 'tools' (allowlist) and/or 'disallowedTools' (denylist) "
                "to control agent capabilities."
            ),
        ))

    # Has MCP server awareness (5 pts) -- shows intentional integration config
    if config.mcp_servers:
        score += 5

    return score, recs


def _score_model_selection(config: AgentConfig) -> tuple[int, list[ConfigRecommendation]]:
    """Score model selection (0-25).

    Awards points for specifying a model and flags clearly mismatched
    model-task combinations.
    """
    score = 0
    recs: list[ConfigRecommendation] = []

    # Model is specified (10 pts)
    if config.model:
        score += 10
    else:
        recs.append(ConfigRecommendation(
            dimension="model_selection",
            severity=Severity.WARNING,
            message="No model specified -- defaults will be used.",
            suggested_action="Specify a model to control cost and capability.",
        ))
        return score, recs

    # Model-task complexity heuristic (15 pts)
    # Only flag clearly wrong: expensive model + ALL tools are read-only
    tools_set = set(config.tools)
    all_read_only = tools_set and tools_set.issubset(READ_ONLY_TOOLS)

    if config.model in EXPENSIVE_MODELS and all_read_only:
        score += 5
        recs.append(ConfigRecommendation(
            dimension="model_selection",
            severity=Severity.INFO,
            message=f"Using {config.model} with only read-only tools.",
            current_value=config.model,
            suggested_action=(
                "Consider a cheaper model (Sonnet/Haiku) if the task is "
                "primarily reading and searching."
            ),
        ))
    else:
        score += 15

    return score, recs


def _score_prompt_body(config: AgentConfig) -> tuple[int, list[ConfigRecommendation]]:
    """Score prompt body quality (0-25)."""
    score = 0
    recs: list[ConfigRecommendation] = []
    body = config.prompt_body.strip()

    # Present and non-empty (5 pts)
    if body:
        score += 5
    else:
        recs.append(ConfigRecommendation(
            dimension="prompt_body",
            severity=Severity.CRITICAL,
            message="Agent has no prompt body.",
            suggested_action="Add a prompt body with instructions for the agent.",
        ))
        return score, recs

    # Length >= 100 chars (5 pts)
    if len(body) >= 100:
        score += 5
    else:
        recs.append(ConfigRecommendation(
            dimension="prompt_body",
            severity=Severity.WARNING,
            message=f"Prompt body is only {len(body)} characters.",
            suggested_action="Expand the prompt to at least 100 characters with instructions.",
        ))

    # Has structured sections (5 pts)
    if SECTION_PATTERN.search(body):
        score += 5
    else:
        recs.append(ConfigRecommendation(
            dimension="prompt_body",
            severity=Severity.INFO,
            message="Prompt body has no markdown sections.",
            suggested_action="Add ## sections to organize the prompt (e.g., responsibilities).",
        ))

    # Mentions error handling (5 pts)
    body_lower = body.lower()
    if any(kw in body_lower for kw in ERROR_KEYWORDS):
        score += 5
    else:
        recs.append(ConfigRecommendation(
            dimension="prompt_body",
            severity=Severity.INFO,
            message="Prompt body doesn't mention error handling.",
            suggested_action="Add guidance for how the agent should handle errors or failures.",
        ))

    # Defines success criteria (5 pts)
    if any(kw in body_lower for kw in SUCCESS_KEYWORDS):
        score += 5
    else:
        recs.append(ConfigRecommendation(
            dimension="prompt_body",
            severity=Severity.INFO,
            message="Prompt body doesn't define success criteria.",
            suggested_action="Add criteria for what constitutes a successful outcome.",
        ))

    # Verbosity-constraint overlay (C-006a): advisory only, no score change.
    for word_count, matched_text in _detect_verbosity_constraints(body):
        severity = (
            Severity.WARNING
            if word_count <= VERBOSITY_CONSTRAINT_WARNING_WORDS
            else Severity.INFO
        )
        recs.append(ConfigRecommendation(
            dimension="prompt_body",
            severity=severity,
            message=(
                f"Prompt contains a blanket word-count constraint "
                f"('{matched_text}') that may degrade output quality. "
                f"Anthropic's April 2026 postmortem documented a 3% quality "
                f"regression from similar constraints. See: "
                f"{VERBOSITY_POSTMORTEM_URL}"
            ),
            current_value=matched_text,
            suggested_action=(
                "Remove or relax the word-count constraint, or scope it to a "
                "specific output field rather than all responses."
            ),
        ))

    return score, recs



def score_agent(config: AgentConfig) -> ConfigScore:
    """Score an agent configuration against the best-practices rubric.

    Four dimensions, each 0-25 points, for a total of 0-100.
    """
    all_recs: list[ConfigRecommendation] = []
    dimension_scores: dict[str, int] = {}

    for dimension_name, scorer in [
        ("description", _score_description),
        ("tool_restrictions", _score_tool_restrictions),
        ("model_selection", _score_model_selection),
        ("prompt_body", _score_prompt_body),
    ]:
        dim_score, dim_recs = scorer(config)
        dimension_scores[dimension_name] = dim_score
        all_recs.extend(dim_recs)

    overall = sum(dimension_scores.values())

    return ConfigScore(
        agent_name=config.name,
        overall_score=overall,
        dimension_scores=dimension_scores,
        recommendations=all_recs,
    )
