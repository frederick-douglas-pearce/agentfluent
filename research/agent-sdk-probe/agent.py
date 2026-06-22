"""Representative Agent SDK data-generation agent (#522).

Builds on the #518 probe findings: SDK sessions land in
``~/.claude/projects/<cwd-slug>/<id>.jsonl``, are marked ``entrypoint == "sdk-py"``,
and inherit the developer's local ``~/.claude`` environment unless constrained.

This agent is chosen purely to maximize the JSONL **format surface** S3 (#520)
can study -- the agent's *answer* is irrelevant; we grade the bytes it emits.
Three variants:

* ``flat``     -- multi-tool (Glob/Grep/Read/Bash), multi-turn, one natural
                 ``is_error: true`` tool result.
* ``subagent`` -- forces a delegation to a programmatically-defined subagent, to
                 observe whether the SDK reproduces Claude Code's
                 ``<id>/subagents/agent-*.jsonl`` + ``isSidechain`` layout and how
                 the parent points at the child (``toolUseResult.agentId`` vs
                 ``parent_tool_use_id`` vs an ``agentId:`` text trailer -- a #522
                 discovery question, so capture all candidates).
* ``large``    -- emits an oversized tool result (``Bash: seq 1 500000``) to test
                 whether the SDK spills large tool output to a separate on-disk
                 location (the "tool output subfolder") and how the JSONL line
                 references it.

Pure SDK agent: ``setting_sources=[]`` (no inherited config/MCP/skills/agents),
no MCP servers, web tools disallowed -> corpus is trivially anonymizable. The
env-inheriting representativeness run is a #519 config-matrix axis, not here.

NOTE (architect review, #522): ``setting_sources=[]`` only reliably suppresses
env inheritance on Python SDK > 0.1.59 (older builds treated ``[]`` as "omitted"
and loaded the full local env). Verified clean on the version pinned in README;
re-check the init event if the SDK is downgraded.

Run:
    uv run --group research python research/agent-sdk-probe/agent.py flat
    uv run --group research python research/agent-sdk-probe/agent.py subagent
    uv run --group research python research/agent-sdk-probe/agent.py large

Each run prints a machine-readable ``RESULT ...`` line (variant, session_id,
resolved JSONL path) so #519 can build its config->file manifest mechanically.

Throwaway research scaffolding. Not part of the published ``agentfluent`` package.
"""

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ResultMessage,
    project_key_for_directory,
    query,
)

PROBE_DIR = Path(__file__).parent
MODEL = "claude-haiku-4-5-20251001"  # explicit non-default; cheap for multi-turn

FLAT_PROMPT = """\
Explore the synthetic directory ./sampledata one tool call at a time, in order:
1. Glob all files under sampledata/.
2. Grep for the line containing 'MARKER' in sampledata/notes.txt.
3. Read sampledata/data.csv.
4. Run Bash: wc -l sampledata/does-not-exist.txt
   (this file is intentionally missing -- note the error and continue).
5. Run Bash: wc -l sampledata/data.csv
Then give a one-paragraph summary. Do not use any network tools."""

# Name the AGENT, not the delegation tool -- the tool's name has changed across
# CLI versions (Task vs Agent), so we let the model pick and allow both below.
SUBAGENT_PROMPT = """\
Delegate to the 'file-summarizer' subagent to summarize the file
sampledata/notes.txt, then report the subagent's summary verbatim."""

LARGE_PROMPT = """\
Run this exact Bash command. Do NOT add pipes, redirects, head/tail, wc, or any
filtering -- the full multi-megabyte output must come back as the tool result:
seq 1 500000
After it runs, reply with just the word DONE."""

SUMMARIZER = AgentDefinition(
    description="Summarizes a single text file in two sentences.",
    prompt=(
        "You are a file summarizer. Read the file you are asked about and return "
        "a two-sentence summary. Use only the Read tool."
    ),
    tools=["Read"],
    model=MODEL,
)

PROMPTS = {"flat": FLAT_PROMPT, "subagent": SUBAGENT_PROMPT, "large": LARGE_PROMPT}


def build_options(variant: str) -> ClaudeAgentOptions:
    allowed = ["Read", "Grep", "Glob", "Bash"]
    agents = None
    if variant == "subagent":
        allowed += ["Task", "Agent"]  # tool renamed across CLI versions; allow both
        agents = {"file-summarizer": SUMMARIZER}
    return ClaudeAgentOptions(
        model=MODEL,
        allowed_tools=allowed,
        disallowed_tools=["WebFetch", "WebSearch"],  # AC: no network surface
        mcp_servers={},  # AC: no MCP surface
        setting_sources=[],  # pure SDK agent -- no inherited ~/.claude env
        agents=agents,
        cwd=str(PROBE_DIR),
        permission_mode="bypassPermissions",
        max_turns=12,
    )


def resolved_path(session_id: str) -> Path:
    slug = project_key_for_directory(str(PROBE_DIR))
    return Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl"


async def main(variant: str) -> None:
    async for message in query(prompt=PROMPTS[variant], options=build_options(variant)):
        print(type(message).__name__, message)
        if isinstance(message, ResultMessage):
            path = resolved_path(message.session_id)
            print(f"RESULT variant={variant} session_id={message.session_id} file={path}")


if __name__ == "__main__":
    variant = sys.argv[1] if len(sys.argv) > 1 else "flat"
    if variant not in PROMPTS:
        sys.exit("usage: agent.py [flat|subagent|large]")
    asyncio.run(main(variant))
