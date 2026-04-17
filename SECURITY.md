# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities via GitHub's private vulnerability reporting on the [Security tab](https://github.com/frederick-douglas-pearce/agentfluent/security) of this repository.

**Do not open a public issue for security concerns.**

(Private vulnerability reporting must be enabled for this repo in Settings -> Code security -> "Private vulnerability reporting". If the Security tab does not show a reporting form, please email the maintainer listed in `pyproject.toml` instead.)

We aim to acknowledge reports within 5 business days.

## Scope

AgentFluent is a local-first CLI that reads session JSONL files from `~/.claude/projects/` and agent definitions from `~/.claude/agents/` on the user's own machine. It does not make network requests, store credentials, or transmit user data. Security concerns likely to be in scope:

- Path traversal or symlink attacks against session/config parsing
- Command injection via session content, agent names, or CLI arguments
- Insecure deserialization of JSONL or YAML content
- Supply-chain issues in the distributed sdist/wheel

## Supported Versions

Pre-1.0 releases receive security fixes on the latest minor line only. Upgrade to the newest `0.x` release to receive patches.
