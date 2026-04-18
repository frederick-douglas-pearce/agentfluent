# Secrets handling for AgentFluent and Claude Code

This document is the canonical reference for protecting API keys and other credentials from leaking into Claude Code's local session store. It applies whether you are developing AgentFluent or using it to analyze your own Claude Code sessions.

## The leak vector

Claude Code persists every tool call and its output to a JSONL transcript at `~/.claude/projects/<project-slug>/<session-id>.jsonl`. When Claude (or any of its subagents) reads a file containing secrets — a `.env`, a `credentials.json`, an SSH private key, or a shell rc file that exports `ANTHROPIC_API_KEY` — the file's contents land in two places in that transcript:

- `.toolUseResult.stdout` — the raw tool output
- `.message.content[0].content` — the tool_result block fed back to the model

Both copies are plaintext and permanent. `.gitignore` prevents the file from being committed to git. It does **not** prevent Claude Code from persisting its contents locally.

This is not a Claude Code bug. Session persistence is a product feature — you want Claude to remember what it saw. The problem is only that credentials are in the set of things Claude sometimes sees.

## Defense-in-depth architecture

AgentFluent ships two Claude Code hooks in `.claude/settings.json` to reduce this risk:

### PreToolUse block (primary defense)

Script: `.claude/hooks/block_secret_reads.py`

Denies Read, Edit, Write, Grep, Glob, NotebookEdit, and Bash tool calls whose target file path (or Bash command text) matches a list of known credential-file patterns:

- `.env`, `.env.*`, `.envrc`
- `credentials`, `credentials.json`
- `secrets.yaml`, `secrets.yml`, `secrets.json`
- `*.pem`, `id_rsa`, `id_ed25519`, `id_ecdsa`, `id_dsa`
- `.bashrc`, `.bash_profile`, `.profile`, `.zshrc`, `.zshenv`, `.zprofile`

**This is the only layer that actually prevents the on-disk leak.** Because it blocks the tool call before it executes, the file's contents never enter the JSONL transcript.

### PostToolUse detect-and-block (secondary defense)

Script: `.claude/hooks/detect_secrets_in_output.py`

Scans Read, Grep, and Bash tool output for known API key patterns (`sk-ant-*`, `sk-proj-*`, `ghp_*`, `github_pat_*`, `AKIA*`, `AIza*`). If a match is found, the hook emits a block signal that tells Claude not to reason about, echo, or summarize the output, even though the tool result itself still arrives in-session (PostToolUse fires after execution — Claude technically receives the output and then the block signal on top of it).

**Important caveat.** PostToolUse fires *after* the tool has already executed, which means the raw output has already been written to the JSONL transcript. This layer prevents Claude from reasoning about the leaked value in the current session (which stops it from propagating into summaries or follow-up prompts), but it does **not** prevent the on-disk leak. For that, only the PreToolUse layer works.

If a PostToolUse block fires, treat the underlying value as compromised and rotate it. The fact that it leaked once means it is in your local JSONL store forever.

### Known bypass surface (documented, accepted)

The hooks use pattern matching, which is not airtight. These cases are not caught:

- Glob expansion: `cat .e*` or `cat ~/.env*`
- Heredoc / Python tricks: `python -c "print(open('.env').read())"`
- Indirection through a shell variable whose name does not contain `.env`
- Base64-encoded or otherwise obfuscated filenames

The hook is defense-in-depth against normal Claude usage, not a guarantee against a motivated adversary. Pair it with the discipline rules below.

## Required discipline

### Keep secrets in `.env` files only

- Never `export ANTHROPIC_API_KEY=...` (or any other secret) in `.bashrc`, `.bash_profile`, `.profile`, `.zshrc`, `.zshenv`, or `.zprofile`. Environment exports leak via `env` / `printenv` / any subprocess that prints its environment in an error message — independently of file-read hooks.
- Store secrets in a per-project `.env` file. Load them into your application at startup (`python-dotenv`, `dotenv-rs`, `direnv` with explicit unload, etc.) — your code reads `.env`, Claude does not.
- `.env` must be in `.gitignore`. That protects commits. The hook protects Claude's transcript.

### Never paste raw secrets into Claude prompts

No amount of hook configuration catches you pasting `sk-ant-...` into Claude's input. If you need Claude to debug credential handling, describe the shape of the value (`sk-ant-api03-<108 chars>`), never the value itself.

### Rotate keys suspected of prior leakage

If Claude read a `.env` or exported environment at any point before you installed these hooks, assume the key is leaked on disk. Rotate it:

- Anthropic: <https://console.anthropic.com/settings/keys>
- OpenAI: <https://platform.openai.com/api-keys>
- GitHub: <https://github.com/settings/tokens> (classic) or <https://github.com/settings/personal-access-tokens/fine-grained>
- AWS IAM: rotate the access key pair and revoke the old one.

## Verifying the hook is active

In a session in this repo, ask Claude to read a file named `.env` (create one with throwaway content for testing). The tool call should return a block message starting with `"Blocked by AgentFluent secrets-protection hook"`.

You can also inspect the settings file directly:

```bash
cat .claude/settings.json
```

and confirm the `PreToolUse` and `PostToolUse` entries are present.

## Auditing your existing session store for historical leaks

Run this one-liner against `~/.claude/projects/` to surface any JSONL files that contain known secret patterns:

```bash
grep -rlE '(sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{35})' ~/.claude/projects/ 2>/dev/null
```

If it returns matches, rotate every key that appears and then decide how to handle the on-disk copies (see next section).

## Historical JSONL cleanup

If the audit surfaces leaked keys in existing JSONL files, you have three options:

1. **Leave the files alone.** After rotation, the leaked keys are inert — they no longer authenticate. Residual risk: someone with local filesystem access could see the old key values, which may be useful for forensic reconstruction of your history but grants no API access.
2. **Scrub the affected JSONL in place.** Write a script that rewrites each line, redacting values matching the secret patterns. Preserves the session structure for tools like AgentFluent and CodeFluent that read this data. Risk: any bug in the scrubbing script could corrupt legitimate session data.
3. **Delete the affected session files entirely.** Clean but loses the analysis history.

The default recommendation is option 1 (leave alone, after rotation). Only choose scrubbing or deletion if you have a specific reason — e.g., you know you will share a backup of `~/.claude/` with someone who should not see the old values.

## Deploying the hooks at user scope as well

The hooks shipped in `.claude/settings.json` protect contributors working on this repo. They do not protect your other work in other projects. Mirror the same hook configuration into `~/.claude/settings.json` to cover every Claude Code session on your machine, regardless of project:

```bash
# One-time setup: copy the hook scripts to a user-scoped location
mkdir -p ~/.claude/hooks
cp .claude/hooks/block_secret_reads.py ~/.claude/hooks/
cp .claude/hooks/detect_secrets_in_output.py ~/.claude/hooks/

# Then add matching PreToolUse / PostToolUse entries to ~/.claude/settings.json,
# pointing the `command` fields at ~/.claude/hooks/*.py with absolute paths.
```

### How project-level and user-level hooks interact

Both scopes load. Per the Claude Code hooks documentation, *"all matching hooks run in parallel, and identical handlers are deduplicated automatically. Command hooks are deduplicated by command string."* That means:

- If you copy the same hook configuration (same `command` string) into both `~/.claude/settings.json` and the project's `.claude/settings.json`, it runs once, not twice.
- If the command strings differ (e.g. user-scope uses an absolute path like `python3 /home/you/.claude/hooks/block_secret_reads.py` while the project uses the relative path `python3 .claude/hooks/block_secret_reads.py`), both commands fire.

Deploying at both scopes is safe and is the right move for layered coverage: project scope protects contributors on this repo; user scope protects every other Claude Code session on your machine.

One subtlety: Claude Code's settings precedence rule — *"more specific scopes take precedence"* — applies to scalar settings like permissions (e.g. a project-scope `deny` overrides a user-scope `allow`). The hooks documentation describes its own merge behavior (parallel execution with command-string deduplication), which is why copying a hook into both scopes does not cause double execution.

## Forward-compatibility rule for AgentFluent features

Any future AgentFluent feature that surfaces raw session content to the user — verbose diagnostics, prompt diff viewers, regression analysis, recommendation engine snippets that quote session data — **must re-apply secret-pattern redaction at the display layer**.

The reason: historical JSONL files on a user's machine may still contain leaked values from before the hooks were installed. A feature that reads session JSONL and shows text without redacting risks surfacing those historical leaks in UI output, log files, or exported reports.

Implementers of such features should import and reuse the regex patterns from `.claude/hooks/detect_secrets_in_output.py` (or an equivalent helper extracted into the main codebase when the first consumer lands), rather than writing new pattern lists.

## Scope limits

These hooks protect the Claude-Code-transcript leak vector. They do not protect:

- Secrets at rest on your filesystem (that is a filesystem permissions problem; use `chmod 600` on `.env` and similar)
- Secrets in your shell history (`~/.bash_history`, `~/.zsh_history`)
- Secrets logged by your own applications into log files
- Secrets transmitted in network requests
- Screen-capture or shoulder-surfing attacks

For those threats, use the appropriate OS and application-level controls. This document and these hooks address only the narrow case of Claude Code persisting credentials into its session store.
