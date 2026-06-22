"""Hello-world Agent SDK probe (#518).

Spins up a Claude Agent SDK agent, makes exactly ONE tool call (a single Read of
a synthetic, secret-free fixture), and exits. The point is not the agent -- it is
to learn, with real bytes on disk, WHERE the SDK writes its session file and what
an SDK session looks like, before investing in the representative agent (#522).

Run:  uv run --group research python research/agent-sdk-probe/probe.py

Throwaway research scaffolding. Not part of the published `agentfluent` package
(the SDK lives in the `research` dependency-group, never in `[project.dependencies]`).
"""

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query

PROBE_DIR = Path(__file__).parent
# Explicit non-default model so we can observe how ClaudeAgentOptions.model
# surfaces in the trace (#112 open question 3).
MODEL = "claude-haiku-4-5-20251001"


async def main() -> None:
    options = ClaudeAgentOptions(
        model=MODEL,
        allowed_tools=["Read"],  # single tool, auto-approved -> no prompt
        cwd=str(PROBE_DIR),  # isolates the session under its own project slug
        permission_mode="bypassPermissions",
    )
    prompt = "Read the file fixture.txt and reply with only the magic string it contains."
    async for message in query(prompt=prompt, options=options):
        print(type(message).__name__, message)


if __name__ == "__main__":
    asyncio.run(main())
