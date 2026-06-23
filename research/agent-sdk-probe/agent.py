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
* ``nested``   -- main -> ``delegator`` subagent (granted the Agent/Task tool) ->
                 ``leaf-summarizer`` sub-subagent, i.e. agent->subagent->subagent.
                 Claude Code forbids subagents from delegating, so this layout is
                 unobservable there; the SDK permits nesting (depth cap 5), making
                 this the only way to learn how a second-level trace is recorded.
                 Open question (claude-code-sessions ref, open item #1): does the
                 grandchild trace land FLAT in ``<id>/subagents/agent-*.jsonl``
                 alongside the child, or NESTED under
                 ``<id>/subagents/<child>/subagents/...``, and what field carries
                 the child->grandchild parent link? This variant emits the bytes
                 that answer it.

Pure SDK agent: ``setting_sources=[]`` (no inherited config/MCP/skills/agents),
no MCP servers, web tools disallowed -> corpus is trivially anonymizable. The
env-inheriting representativeness run is a #519 config-matrix axis, not here.

NOTE (architect review, #522): ``setting_sources=[]`` only reliably suppresses
env inheritance on Python SDK > 0.1.59 (older builds treated ``[]`` as "omitted"
and loaded the full local env). Verified clean on the version pinned in README;
re-check the init event if the SDK is downgraded.

Run (``[model]`` and ``[subagent_model]`` are optional; both default to the cheap
haiku tier -- the ``subagent_model`` axis only applies to the ``subagent`` variant):
    uv run --group research python research/agent-sdk-probe/agent.py flat
    uv run --group research python research/agent-sdk-probe/agent.py \
        subagent claude-sonnet-4-6 claude-haiku-4-5-20251001
    uv run --group research python research/agent-sdk-probe/agent.py large

Each run prints a human ``RESULT ...`` line and a machine-readable ``RESULT_JSON
{...}`` line (variant, models, session_id, source path, config snapshot, init
event) so ``run_matrix.py`` (#519) can build the config->file corpus manifest
mechanically. The model is threaded into both the main agent and the subagent so
the child's ``toolUseResult.resolvedModel`` is a recorded, controllable input.

Throwaway research scaffolding. Not part of the published ``agentfluent`` package.
"""

import asyncio
import json
import sys
from pathlib import Path

import claude_agent_sdk
from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    project_key_for_directory,
    query,
)

PROBE_DIR = Path(__file__).parent
MODEL = "claude-haiku-4-5-20251001"  # explicit non-default; cheap for multi-turn
MAX_TURNS = 12
SDK_VERSION = getattr(claude_agent_sdk, "__version__", "unknown")

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

# Two-layer delegation: main delegates to 'delegator', which must itself delegate
# to 'leaf-summarizer'. The middle prompt is explicit that it may NOT read the
# file itself -- it has to spawn the leaf -- so the trace exercises a genuine
# child->grandchild invocation, not just the middle agent doing the work.
NESTED_PROMPT = """\
Delegate to the 'delegator' subagent and ask it to produce a summary of the file
sampledata/notes.txt. Report whatever the 'delegator' subagent returns, verbatim."""

# A REALISTIC middle agent: it does its own tool work (Grep/Bash/Read, including
# one natural error) AND delegates a sub-task to the leaf. This is the case the
# trivial delegate-only middle agent could not validate -- we need a level-1
# subagent whose trace contains BOTH its own tool_result blocks and an Agent-spawn
# tool_result, to test whether the leaf-spawn result carries a `toolUseResult`
# rollup at depth >= 2 (or whether rollup metadata is genuinely top-level-only).
DELEGATOR_PROMPT = """\
You are a research worker. Do these steps in order, one tool call at a time:
1. Grep for the line containing 'MARKER' in sampledata/notes.txt.
2. Run Bash: wc -l sampledata/does-not-exist.txt (intentionally missing -- note
   the error and continue).
3. Read sampledata/data.csv.
Then delegate to the 'leaf-summarizer' subagent: ask it to summarize the file
sampledata/notes.txt. Finally, reply with a short report that combines your own
findings from steps 1-3 with the leaf-summarizer's summary."""

def make_summarizer(model: str) -> AgentDefinition:
    # The subagent's model is a #519 matrix axis, NOT a frozen constant -- the
    # child's `toolUseResult.resolvedModel` is the #112 model-routing artifact, so
    # it must be a controllable, recorded input. Parameterize it (architect review,
    # #519): a sonnet-parent / haiku-child run yields a genuine divergence sample.
    return AgentDefinition(
        description="Summarizes a single text file in two sentences.",
        prompt=(
            "You are a file summarizer. Read the file you are asked about and "
            "return a two-sentence summary. Use only the Read tool."
        ),
        tools=["Read"],
        model=model,
    )


def make_delegator(model: str) -> AgentDefinition:
    # The middle agent of the nested chain. It is granted the delegation tool
    # (Task/Agent) -- the property Claude Code subagents lack -- so it can spawn
    # the leaf. Whether the SDK actually honors a subagent's delegation tool is
    # itself part of what this variant verifies.
    return AgentDefinition(
        description="Researches a file with its own tools, then delegates a summary.",
        prompt=DELEGATOR_PROMPT,
        # Realistic mix: own work tools + the delegation tool (renamed across CLI
        # versions, so allow both). The middle agent must be able to BOTH act and
        # delegate for finding #5 to be validly tested.
        tools=["Read", "Grep", "Bash", "Task", "Agent"],
        model=model,
    )


PROMPTS = {
    "flat": FLAT_PROMPT,
    "subagent": SUBAGENT_PROMPT,
    "large": LARGE_PROMPT,
    "nested": NESTED_PROMPT,
}


def build_options(
    variant: str, model: str, subagent_model: str
) -> tuple[ClaudeAgentOptions, dict]:
    """Return the SDK options plus a JSON-serializable config snapshot for the
    #519 manifest (config -> file provenance)."""
    allowed = ["Read", "Grep", "Glob", "Bash"]
    agents = None
    if variant == "subagent":
        allowed += ["Task", "Agent"]  # tool renamed across CLI versions; allow both
        agents = {"file-summarizer": make_summarizer(subagent_model)}
    elif variant == "nested":
        allowed += ["Task", "Agent"]  # main must be able to spawn 'delegator'
        # Both nested agents share the subagent_model so the chain is a single
        # controllable model axis. 'delegator' is granted the delegation tool;
        # 'leaf-summarizer' is the grandchild that does the actual Read.
        agents = {
            "delegator": make_delegator(subagent_model),
            "leaf-summarizer": make_summarizer(subagent_model),
        }
    options = ClaudeAgentOptions(
        model=model,
        allowed_tools=allowed,
        disallowed_tools=["WebFetch", "WebSearch"],  # AC: no network surface
        mcp_servers={},  # AC: no MCP surface
        setting_sources=[],  # pure SDK agent -- no inherited ~/.claude env
        agents=agents,
        cwd=str(PROBE_DIR),
        permission_mode="bypassPermissions",
        max_turns=MAX_TURNS,
    )
    config = {
        "allowed_tools": allowed,
        "disallowed_tools": ["WebFetch", "WebSearch"],
        "mcp_servers": [],
        "setting_sources": [],
        "agents": sorted(agents) if agents else [],
        "permission_mode": "bypassPermissions",
        "max_turns": MAX_TURNS,
        "cwd": str(PROBE_DIR),
    }
    return options, config


def resolved_path(session_id: str) -> Path:
    slug = project_key_for_directory(str(PROBE_DIR))
    return Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl"


def _jsonable(value: object) -> object:
    """Best-effort coerce an SDK message payload to something json.dumps can emit."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


async def run_one(variant: str, model: str, subagent_model: str) -> dict:
    """Run a single variant and return a manifest-ready record. Captures the
    runtime-only `SystemMessage(init)` event -- the only place the non-model
    options surface (they are not persisted to the JSONL)."""
    options, config = build_options(variant, model, subagent_model)
    init: dict | None = None
    session_id: str | None = None
    async for message in query(prompt=PROMPTS[variant], options=options):
        print(type(message).__name__, message)
        if isinstance(message, SystemMessage) and getattr(message, "subtype", None) == "init":
            init = {k: _jsonable(v) for k, v in getattr(message, "data", {}).items()}
        if isinstance(message, ResultMessage):
            session_id = message.session_id
    if session_id is None:
        raise RuntimeError(f"variant {variant!r} produced no ResultMessage")
    return {
        "variant": variant,
        "main_model": model,
        "subagent_model": subagent_model if variant in ("subagent", "nested") else None,
        "session_id": session_id,
        "source_jsonl": str(resolved_path(session_id)),
        "prompt": PROMPTS[variant],
        "config": config,
        "sdk_version": SDK_VERSION,
        "init": init,
    }


async def main(variant: str, model: str, subagent_model: str) -> None:
    record = await run_one(variant, model, subagent_model)
    # Human-readable line (standalone use) + machine-readable line (run_matrix.py
    # parses RESULT_JSON to assemble the corpus manifest).
    print(
        f"RESULT variant={record['variant']} model={record['main_model']} "
        f"session_id={record['session_id']} file={record['source_jsonl']}"
    )
    print("RESULT_JSON " + json.dumps(record))


if __name__ == "__main__":
    args = sys.argv[1:]
    variant = args[0] if args else "flat"
    if variant not in PROMPTS:
        sys.exit("usage: agent.py [flat|subagent|large|nested] [model] [subagent_model]")
    cli_model = args[1] if len(args) > 1 else MODEL
    cli_subagent_model = args[2] if len(args) > 2 else cli_model
    asyncio.run(main(variant, cli_model, cli_subagent_model))
