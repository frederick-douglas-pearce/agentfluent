# Security Guidance — AgentFluent

Threat model and review checklist for the `security-guidance` plugin's
LLM-backed review layers (end-of-turn diff review and commit/push review).

> **Status:** Currently dormant. The LLM layers are disabled
> (`ENABLE_CODE_SECURITY_REVIEW=0` in `.claude/settings.json`), so only the
> free deterministic pattern layer runs today. This file activates the moment
> those layers are turned on — keep it in sync with the CI workflow's
> `custom-security-scan-instructions` in `.github/workflows/security-review.yml`
> so the in-session review and the PR-time review share one threat model.

## What AgentFluent is

A local-first Python CLI that reads user session data and agent definitions
from `~/.claude/projects/` and `~/.claude/agents/`. It stores no credentials and
transmits no user data, and runs no web server, HTML rendering, or webview. Its
**only** network egress and subprocess use is the `gh` / `git` CLI (the Tier 3
GitHub-signals feature in `src/agentfluent/github/` and `diagnostics/`), invoked
list-form with `shell=False`. No in-process HTTP client
(`requests`/`httpx`/`urllib`/`socket`) is imported anywhere outside
`src/agentfluent/github/`.

## Attack surfaces to focus on

1. **JSONL parser** (`src/agentfluent/core/parser.py`) — reads arbitrary
   user-controlled content. Check for unsafe deserialization, resource-exhaustion
   on malformed/oversized input (unbounded reads, pathological nesting), and any
   code path that evaluates or executes parsed strings.
2. **Agent definition parser** (`src/agentfluent/config/scanner.py`) — parses
   YAML frontmatter. Must use `yaml.safe_load` (never `yaml.load`) to prevent
   arbitrary code execution via YAML tags.
3. **Path handling in discovery** (`src/agentfluent/core/discovery.py`) —
   iterates user directories. Check for path traversal, symlink following that
   escapes the projects root, and TOCTOU races between stat and open.
4. **CLI argument parsing** (`src/agentfluent/cli/`) — user strings from
   `--project`, `--agent`, `--session` are matched against discovered data.
   Ensure none reach a shell, `subprocess`, or any eval-like API.

## Project-specific sensitivities

- **Secret hygiene in output.** AgentFluent reads session transcripts that may
  contain credential-looking values. Flag any new code path that echoes raw tool
  output, renders un-redacted file contents, or logs values matching
  `KEY|TOKEN|SECRET|PASSWORD`. (Two enforcement hooks already guard secret reads
  and output — see `docs/SECURITY.md`.)
- **Untrusted strings into rendering.** Sanitize user-controlled strings before
  passing them to Rich/terminal formatting; never interpolate them into shell
  commands.

## Low priority (typically non-issues here)

No web server, no HTML rendering, no webview, no authentication surface. The only
sanctioned network egress is the `gh` / `git` CLI (`src/agentfluent/github/`,
`diagnostics/`); findings that assume an in-process HTTP or web attack surface
elsewhere are almost certainly false positives — note them but rank low.

## Review checklist

- [ ] No `eval`, `exec`, `os.system`, `subprocess(..., shell=True)`, or
      `compile()` on parsed/user-derived strings.
- [ ] YAML loaded only via `yaml.safe_load`; no `pickle.load` /
      `pickle.loads` on file or session content.
- [ ] User-supplied paths are resolved and confined to their expected root
      (no `..` escape, no symlink escape).
- [ ] Parser bounds memory/CPU on malformed input — no unbounded buffering.
- [ ] No credential-shaped value is logged, printed, or rendered un-redacted.
- [ ] New external calls (file I/O, any future network) are wrapped in
      try/except with user-friendly, non-leaking error messages.
