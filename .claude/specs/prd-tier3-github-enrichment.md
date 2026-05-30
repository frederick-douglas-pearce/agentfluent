# PRD: Tier 3 GitHub Enrichment — Scoping Spike

**Issue:** #352 (research-only; no v0.7 implementation)
**Status:** Spike output. Gates v0.8 Tier 3 implementation.
**Parent epic context:** `prd-quality-axis.md` Section 3 (Tier 3 signal list); Tier 1 shipped in v0.6; Tier 2 (local git) under #275.

---

## Why this spike

Tier 3 quality signals (PR review comment density, CI-failure-on-first-push rate, post-merge issue references, review-comment topic clustering) are the richest quality-axis evidence available. They are also the only signals in the quality-axis epic that require **auth**, **external network calls**, **rate-limit handling**, and **user privacy consent**. Tier 1 (JSONL-only) and Tier 2 (local `git log`, #275) carry none of those concerns.

Deferring all Tier 3 work to v0.8 means v0.8 starts with these unresolved design questions consuming the first sprint instead of the implementation. This spike resolves them now so v0.8 can begin coding on day one.

**This is design-only. No code, no `pyproject.toml` changes, no auth infrastructure.**

---

## 1. Auth model

Three viable auth surfaces:

| Approach | How it works | Pros | Cons |
|---|---|---|---|
| **`gh` CLI subprocess** | Shell out to `gh api <path>`. Inherits the user's existing `gh auth` state. | Zero new auth UX. Users who already use `gh` get it free. Token handling lives in `gh`, not AgentFluent. | Adds a runtime dependency on the `gh` binary. Output is text/JSON, not typed. Rate-limit error messages are `gh`'s, not ours. |
| **GitHub MCP server** | Connect through the user's existing MCP config; call tools like `mcp__github__get_pull_request_comments`. | Aligns with AgentFluent's MCP-first posture. Users with MCP already configured get it free. Tool calls are structured. | MCP server config is optional in Claude Code; many users won't have it. Adds AgentFluent → Claude Code coupling for a CLI tool. |
| **Personal access token** | Read `GITHUB_TOKEN` env var or a config file. Use a typed Python client (e.g. `httpx` + thin wrapper). | Pure-Python, no external binary. Typed responses. Works in CI/headless. | Yet another credential to manage. Token-handling code is a security surface we now own. |

**Recommendation: `gh` CLI first, PAT fallback in v0.8.1+.**

`gh` is the path of least surprise: it's already the standard in this repo (`.github/PULL_REQUEST_TEMPLATE.md`, `CONTRIBUTING.md` prereqs, all maintainer workflows). The dogfood loop already assumes its presence. Detecting absence and falling back to a friendly error ("Tier 3 needs `gh` — install via [link]") is straightforward and avoids us building a token-storage layer in the first release.

MCP is rejected as the first surface because it inverts the dependency: AgentFluent is a CLI that should work standalone, and gating Tier 3 on the user's Claude Code MCP config makes Tier 3 invisible to anyone running AgentFluent in CI or outside an active Claude Code session.

PAT is the right v0.8.1+ follow-up because it covers the CI / headless case where `gh` may not be installed. Defer because (a) v0.8 doesn't have to cover CI from day one, (b) typed-client work is its own design exercise (which client library, how do we handle secondary rate limits, etc.), and (c) we want one auth path stable in production before adding a second.

### Detection sequence (v0.8)

```
1. Is the `gh` binary on PATH?              -> no  -> error: install gh
2. Does `gh auth status` exit 0?            -> no  -> error: run `gh auth login`
3. Does the current dir have a git remote
   on github.com that matches a project's
   first-commit author/repo?                -> no  -> warn + skip Tier 3
4. Run the Tier 3 pipeline.
```

The dir-to-repo mapping is the subtle bit: AgentFluent's projects are `~/.claude/projects/<slug>` dirs, not git repos. We'll need a mapping (probably: the slug-to-disk-path resolution that already exists in `config.mcp_discovery.resolve_project_disk_path` — reuse it).

---

## 2. Optional dependency strategy

AgentFluent's current optional-extras pattern (see `[project.optional-dependencies]` in `pyproject.toml`) is `agentfluent[clustering]` for scikit-learn. The pattern works: runtime tries the import, sets a module-level `SKLEARN_AVAILABLE` flag, and the CLI gates clustering-tuning flags on that flag.

Tier 3 with the `gh`-first auth model has **no Python dependencies**. `gh` is invoked as a subprocess via `subprocess.run`; output is parsed with stdlib `json`. So the extras question reduces to: do we need an `agentfluent[github]` extra at all?

**Recommendation: no extra in v0.8. Skip the extras layer.**

The `gh`-only path has no third-party imports to gate. A `--github` CLI flag and a runtime check for `gh` on PATH is the minimum viable surface. If v0.8.1 adds the PAT path, an `agentfluent[github]` extra wrapping the HTTP client makes sense at that point.

If a future signal requires a Python parsing dependency (e.g., `PyGithub` for typed responses, or a topic-clustering library beyond scikit-learn), revisit then. Don't preemptively add an extra for a hypothetical need.

### CLI gating

`--github` flag on `analyze`, off by default. Mirrors how `--diagnostics` is on by default but `--git` (in #275) is off — the rule is: **anything that calls out beyond `~/.claude/projects/` is opt-in.**

```
agentfluent analyze --project P --diagnostics                  # local only (default)
agentfluent analyze --project P --diagnostics --git            # + local git (#275)
agentfluent analyze --project P --diagnostics --git --github   # + GitHub Tier 3 (v0.8)
```

Single-flag granularity is fine for v0.8. Per-signal flags (`--github-pr-reviews`, `--github-ci`) would be premature without dogfood data on which signals users want to disable independently.

---

## 3. Rate-limit and caching approach

GitHub's REST API rate limits:

- **Authenticated (via `gh`):** 5,000 requests/hour per user across all `gh` invocations on that machine.
- **Unauthenticated:** 60 requests/hour. Not a viable target.
- **Secondary limits:** Concurrent requests, large-result-set pagination, and abuse-detection limits exist on top of the primary limit. `gh api` handles these less gracefully than a typed client would.

A typical AgentFluent run analyzes ~10–50 sessions. If Tier 3 makes ~5 API calls per session (PR detail, PR reviews, PR commits, CI status, post-merge issue search), that's 50–250 calls per run — well under the per-hour limit but enough to be slow without caching.

### Caching strategy

**File-backed TTL cache at `~/.cache/agentfluent/github/`.**

Two cache tiers with different TTLs:

| Resource | TTL | Rationale |
|---|---|---|
| Closed/merged PRs, their reviews, their commits | 7 days | Closed PRs don't change. Long TTL is safe and shifts almost all reads to cache. |
| Open PRs, CI status, issue search results | 15 minutes | Mutable; users re-running AgentFluent during a feedback loop want fresh data. |
| Repo metadata (default branch, languages) | 24 hours | Slow-changing; small. |

Cache key: `(endpoint, query_params, auth_user_login)`. Include the auth user so a shared machine doesn't leak between users.

Cache invalidation: TTL-only for v0.8. A `--github-no-cache` flag for the dogfood case where someone wants to bypass. Manual cache wipe is `rm -rf ~/.cache/agentfluent/github/` — no built-in command in v0.8.

### Batch vs. on-demand

**On-demand with eager prefetch per session.**

When Tier 3 fires for a session, prefetch *all* GitHub calls for that session in one batch (PRs touching files in the session window, etc.), populate the cache, then run signal extractors against the cache. This keeps each individual signal extractor simple (cache lookups only) while bounding latency to one round-trip cluster per session.

Reject pure on-demand-per-extractor: would multiply round-trip latency by the number of extractors.

Reject pre-run global prefetch: don't know which sessions Tier 3 is relevant for until we've parsed them.

### Graceful degradation when rate-limited

When `gh api` returns a 403 with rate-limit headers (or a 429):

1. Log the rate-limit hit at WARNING level with the reset time.
2. Skip the affected signal extractor (emit no signal for that session).
3. Continue with other extractors that have already-cached data.
4. Exit code 0 — partial Tier 3 output is better than a hard fail when local-only signals also ran.

The rate-limit-hit case should be visible in the output so the user understands why Tier 3 findings are sparse. Add a `tier3_degraded: bool` field to the analyze JSON envelope when this happens.

---

## 4. Signal selection

Tier 3 signal list from `prd-quality-axis.md` Section 3:

1. **PR review comment density on Claude-authored PRs**
2. **CI-failure-on-first-push rate**
3. **Post-merge issue references**
4. **Review-comment topic clustering**

Scoring by signal quality × API cost × complexity:

| Signal | Quality | API cost per session | Implementation complexity | Verdict for v0.8 |
|---|---|---|---|---|
| **CI-failure-on-first-push rate** | **High** — direct quality miss: tests broke immediately. Hard to fake. | 1 call (CI status of first push for each PR) | Low — single endpoint, boolean per push | **Ship first.** |
| **PR review comment density** | **High** — reviewer caught things. Density (per-line-changed) is interpretable. | 2 calls per PR (PR detail + reviews) | Low-Medium — straightforward but requires per-PR normalization | **Ship in v0.8.** |
| **Post-merge issue references** | Medium — strong when present, but quiet repos won't have post-merge issues regardless of quality. Risk: false-negative-heavy. | 1 search per merged PR (`is:issue mentions:#N created:>MERGE_DATE`) | Medium — search API has its own rate-limit; result ranking is opaque | **Defer to v0.8.1.** Cost/quality ratio worse than the first two; needs dogfood data to calibrate the noise floor. |
| **Review-comment topic clustering** | **Highest** — directly produces actionable subagent recommendations ("add a security-review-style agent because reviewers keep flagging error handling"). | High — needs ALL review comment bodies, then sklearn TF-IDF + clustering. | High — depends on (1) and (2) for source data; clustering already exists in `diagnostics/_clustering.py` but topic-clustering is a different application | **Defer to v0.8.2 or v0.9.** Wait for (1)/(2) dogfood before investing here. |

**v0.8 cut: CI-failure-on-first-push + PR review comment density.** Two signals, both `priority: high` for quality, both fit comfortably in the rate-limit budget, both produce single-PR-grained findings that compose cleanly with the existing per-agent recommendation aggregation.

### Plumbing the new signals

Both new signal types extend `SignalType` enum and produce `DiagnosticSignal` entries with `axis_scores.quality > 0`. They feed into the existing `aggregation.py` priority scorer as additional quality-axis evidence. No structural changes to the diagnostics pipeline — Tier 3 is additive at the signal-extraction layer, same shape as Tier 1 and Tier 2.

New `SignalType` members:
- `CI_FAILURE_FIRST_PUSH`
- `PR_REVIEW_COMMENT_DENSITY`

Add to `GLOSSARY_CATEGORIES` (signal_type category) when implementing.

---

## 5. Privacy model

### What leaves the machine

When `--github` is set, AgentFluent sends API requests to `api.github.com` containing:

- **Repo identifiers** — owner and repo name derived from the local git remote.
- **PR numbers** the user has touched in the analyzed window.
- **Commit SHAs** when querying CI status.
- **Issue search strings** for post-merge bug detection (deferred — but worth noting now: search queries become server-side log entries).
- **Standard request metadata** — `User-Agent: agentfluent/v0.8.x` plus whatever `gh` adds, the user's GitHub username via auth headers.

What **does not** leave:
- JSONL session contents (prompts, tool inputs, model outputs).
- File contents.
- Agent definitions or `~/.claude/` config.

### What is cached locally

The full response body of every `gh api` call goes to `~/.cache/agentfluent/github/`. This includes review comment text, PR titles, and CI logs (truncated by `gh` to summary). A user inspecting this cache directory will see GitHub-visible content about their own repos — nothing they couldn't see in the GitHub UI, but worth documenting that the cache exists.

Cache file naming: SHA-256 hash of `(endpoint, query_params, auth_user_login)` so filenames don't leak repo/PR numbers in directory listings.

### User consent

Three-layer opt-in:

1. **`--github` CLI flag.** Default off. Explicit per-invocation consent.
2. **First-run prompt** when `--github` is passed for the first time. Show what data will be sent to GitHub, where the cache lives, how to wipe it. Stored consent at `~/.config/agentfluent/github-consent.json` so the prompt doesn't fire every run.
3. **Config-file disable.** A `tier3.enabled: false` in `~/.config/agentfluent/config.yaml` (a config file AgentFluent doesn't have yet — note for v0.8 to add or skip the config layer entirely) hard-disables Tier 3 even if `--github` is passed. Useful for corporate environments.

The first-run prompt is the load-bearing piece. It's the only moment the user is explicitly told "AgentFluent is about to call GitHub on your behalf and cache the results locally." Skip it and we've shipped a tool that quietly hits GitHub.

### Data minimization

- Request only the fields needed for each signal. `gh api --jq` can filter server-side; pass `--jq` to keep the cached payload small and avoid storing full PR bodies when only `mergeable_state` is needed.
- Do not log PR titles or comment bodies in stderr/verbose output unless `--verbose` AND the user explicitly asked. Diagnostic output should reference PR numbers, not titles.
- No telemetry. Tier 3 makes no calls to AgentFluent-controlled endpoints. GitHub is the only external destination.

### What we explicitly will NOT do

- No background polling or daemon mode.
- No analysis of PRs the user didn't author or review.
- No upload of any session content to GitHub or any other service.
- No sharing cache across users (cache is per-user under `~/.cache`, scoped by auth user in the cache key).

---

## Open questions for v0.8 scoping

These came up during the spike and need a PM/architect call before v0.8 implementation starts:

1. **Repo discovery from session.** When `--github` is set, how do we map a session to "the PR(s) this session contributed to"? Options: (a) infer from git remote + commit timestamps in the session window, (b) require a `--repo` flag, (c) infer from `~/.claude/projects/<slug>` filename pattern. Recommendation: try (a) first, fall back to a clear error pointing to (b).
2. **First-run consent UX.** Modal `[y/N]` prompt is unfriendly in CI. Should `--github` in non-TTY contexts require an explicit `--accept-github-tos` flag? Or assume `--github` itself is consent enough in non-TTY? Recommendation: `--github` in non-TTY = consent. First-run interactive prompt only in TTY.
3. **Config file layer.** Does AgentFluent want a `~/.config/agentfluent/config.yaml` for Tier 3 disable and similar? This is a "do we want this kind of file at all" question larger than Tier 3. Recommendation: skip the config layer for v0.8; document that `--github` is the only Tier 3 gate. Revisit if user feedback asks for a persistent disable.
4. **MCP path resurrection.** If `gh` proves friction-heavy for some users, the MCP path (rejected above) could come back as an alternative auth surface. Worth a calibration check after v0.8 ships: how many users adopted `--github`, and what's the friction profile?

---

## Acceptance criteria check

- [x] Design doc exists at `.claude/specs/prd-tier3-github-enrichment.md`
- [x] All five sections (auth, optional deps, rate-limit/caching, signal selection, privacy) addressed with a recommendation and alternatives considered
- [x] No implementation code produced
- [x] Time spent bounded to 2-3 dev days (spike scope)
- [x] Document is actionable: a v0.8 implementer can begin without further design conversations

## Non-Goals (confirmed)

- No Tier 3 signal implementation
- No auth infrastructure
- No `pyproject.toml` changes
- No API call prototyping

---

## Implementation outcome (v0.8.0)

Shipped in v0.8.0 per epic [#398](https://github.com/frederick-douglas-pearce/agentfluent/issues/398). The spike's recommendations carried through to implementation; resolutions to the four open questions are recorded below for the next Tier 3 iteration.

**Shipped (resolved spike recommendations):**

- Auth: `gh` CLI subprocess wrapper (`agentfluent.github.client.gh_api`). No AgentFluent-managed token. PAT fallback explicitly deferred per the original recommendation.
- Optional deps: no new runtime dependency in `pyproject.toml`. `gh` binary required only when `--github` is passed; detection + actionable error wired in `agentfluent.github.detection`.
- Rate-limit + caching: file-backed TTL response cache with three explicit TTL tiers; rate-limit-degraded runs continue and surface `tier3_degraded: bool` on the JSON envelope. Per-PR error handling skips affected PRs rather than failing the run. Cache key includes `jq_filter` per architect review of [#399](https://github.com/frederick-douglas-pearce/agentfluent/issues/399).
- Privacy: `gh` CLI is the only network surface (no AgentFluent-issued requests). First-run consent recorded under `~/.config/agentfluent/`. `--github-no-cache` bypasses the cache without disabling the call.
- Signals: `CI_FAILURE_FIRST_PUSH` ([#400](https://github.com/frederick-douglas-pearce/agentfluent/issues/400)) and `PR_REVIEW_COMMENT_DENSITY` ([#401](https://github.com/frederick-douglas-pearce/agentfluent/issues/401)) shipped; full GLOSSARY entries at [`docs/GLOSSARY.md`](../../docs/GLOSSARY.md#ci_failure_first_push) / [`#pr_review_comment_density`](../../docs/GLOSSARY.md#pr_review_comment_density). Two additional signals (post-merge issue references, review-comment topic clustering) deferred to v0.9+.

**Open-question resolutions (§ "Open questions for v0.8 scoping"):**

1. **Repo discovery from session.** Shipped (a) + (b): infer GitHub remote from the project's git config; `--repo OWNER/NAME` override for the cases where inference fails (non-GitHub remote, non-repo source directory).
2. **First-run consent UX.** Shipped the recommended split: interactive consent prompt in TTY contexts; `--github` in non-TTY (CI) is treated as consent itself. Consent state is recorded under `~/.config/agentfluent/`.
3. **Config file layer.** Partial: the `~/.config/agentfluent/` directory was established for consent state via `agentfluent_config_dir()`. No general-purpose user config file (e.g. `config.yaml`) was added — `--github` and its companion flags remain the only Tier 3 gates. The directory exists if a config layer is added later.
4. **MCP path resurrection.** Open. Revisit after the post-v0.8 dogfood pass — collect friction reports on the `gh` requirement and decide whether the MCP `mcp__github__*` path is worth resurrecting as an alternative auth surface.
