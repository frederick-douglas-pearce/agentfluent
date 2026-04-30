# Agent Portfolio Analysis — agentfluent dogfood run

**Date:** 2026-04-29
**Author:** Claude (Opus 4.7) with Fred Pearce
**Source:** `uv run agentfluent analyze --project agentfluent` against
~/.claude/projects/-home-fdpearce-Documents-Projects-git-agentfluent
**Sessions analyzed:** 11
**agentfluent version:** v0.4.0 (`a299a8c`)

## Purpose

First dogfood run: use agentfluent's own output to identify high-leverage
subagents to add to this project. Optimize for development workflow (cheaper,
faster, more reliable) — not for marketing copy. The output of this run also
serves as the baseline for measuring impact of subsequent agents we add.

## Method

Ran `analyze` and `analyze --json` against the `agentfluent` project.
Cross-referenced top-level metrics, agent invocation breakdown, diagnostic
signals, and clustering suggestions. Spot-checked verbose output for
representative tool-use evidence.

## Key numbers

**Totals across 11 sessions:**

- Total cost: **$861**
- Total tokens: **1.33B**
- API calls: **4,095**
- Cache efficiency: **98.4%**

**Token distribution (the headline finding):**

- Parent thread: **~99.5%** of tokens (~$855)
- Subagents combined: **~0.5%** (~$5 across 209 invocations)

This shapes everything else. Subagent costs are not the cost story.

**Agent breakdown:**

| Agent | Calls | Tokens | Avg cost/call | Avg duration/call | tool_error_seqs | retry_loops |
|---|---|---|---|---|---|---|
| Explore (built-in) | 92 | 2.7M | $0.02 | 210s | **47** | 26 |
| general-purpose (built-in) | 62 | 2.1M | $0.02 | 98s | 22 | **29** |
| pm (custom, opus-4-6) | 26 | 1.1M | $0.03 | **999s** (~16min) | 7 | 5 |
| architect (custom, opus-4-6) | 22 | 1.1M | $0.03 | 138s | 12 | 9 |
| Plan (built-in) | 5 | 178k | $0.02 | 132s | 0 | 1 |
| claude-code-guide (built-in) | 2 | 35k | $0.01 | 32s | 1 | 0 |

**Diagnostic signal totals:**

- 89 `tool_error_sequence`
- 70 `retry_loop`
- 15 `permission_failure`
- 14 `token_outlier`
- 11 `duration_outlier`
- 1 `model_mismatch`

**Top retry-prone tools (across all agents):**

- `Read`: 41 retries (Explore=12, general-purpose=19, pm=4, architect=5, Plan=1)
- `Bash`: 14 retries (mostly Explore=13)
- `Edit`: 6 retries (general-purpose=6)
- `mcp__github__get_issue`: 4 retries (architect=3, pm=1)

**Strongest delegation cluster from the clustering pass:**

- `tests-unit` — **medium** confidence, 6 invocations, tools: Bash, Edit, Glob, Grep, Read, Write
- `py-src` — low, 14 invocations, broader Python source editing
- `cost-pricing` — low, 5 invocations, includes WebFetch/WebSearch
- `py-analytics` — low, 10, read-only investigation
- `code-signal` — low, 10, read-only investigation

## Honest read

**The cost story this data tells is weak.** $5 of $861 is from subagents.
Adding new subagents will not measurably reduce spend. Any "save N% on cost"
narrative built on this dataset would collapse under scrutiny.

**The wall-time and reliability story is real.** 70 retry loops + 89
consecutive-error sequences = a meaningful amount of work happening
twice. The `pm` agent averaging **16+ minutes per invocation** is the most
striking single number in the output and deserves its own investigation.
The aggregate effect on a developer's experience is "agents that feel slow
or flaky" — and that's something an end-user will notice and care about.

**The strongest delegation signal is `tests-unit`** (medium confidence, 6
invocations). This is currently routing through `general-purpose`, which
contributes disproportionately to the retry/error counts. Worth specializing.

**The `py-src` cluster size (14) is the more interesting signal** even at
low confidence. 14 recurring "edit Python source" invocations *in the
parent thread* is why the parent dominates token spend. The biggest
long-term lever may not be "create more subagent flavors" — it may be
"delegate more of the work currently happening parent-thread."

**`permission_failure` signals are noisy here.** Most "blocked" hits
come from the secret-blocking PreToolUse hook (CLAUDE.md, see
`.claude/hooks/block_secret_reads.py`). That's intended behavior, not a
real failure. Worth filtering or annotating in a future agentfluent
release.

## Three nuances on the parent-thread offloading thesis

The headline finding (parent thread = 99.5% of spend) suggests an obvious
fix: offload more work to subagents on cheaper models. That's broadly
correct but has three real tradeoffs worth surfacing before we act on
the recommendations below:

**1. Cache efficiency is a hidden tradeoff.** This session's 98.4% cache
efficiency is exceptional — the parent thread reuses its prompt prefix
tightly. Each subagent invocation starts fresh, paying full input rate
on its own (smaller) prefix. Naively offloading many small tasks can
lose more in cache hits than it saves in cheaper model rates. The win
concentrates on **larger** offloaded tasks where work-per-call dwarfs
the prompt-rebuild cost.

**2. Wall-time is a separate axis from dollars.** Max and Pro plan users
feel the wall-time cost of slow agents but not the dollar cost. API,
Bedrock, and Vertex users feel both. Same offloading techniques have
different value props for different audiences. The recommendations and
narrative should treat these separately.

**3. Delegation has a fixed parent-thread tax.** Every Agent invocation
costs the parent: the delegation prompt + the returned summary land in
parent context. For very small tasks (run a one-line bash command, read
a single file), that tax exceeds the benefit. There's a "right size"
floor for offloading; below it, parent-thread is the correct choice.

## Recommendations

### Agent 1: `tester` — fix existing test failures (claude-sonnet-4-6)

The strongest signal in the output. Six clustered invocations in
test-running territory; today they route to `general-purpose` and
contribute to its 22 tool_error_sequences and 29 retry_loops.

**Scope:** fix existing test failures only. Run pytest, parse the
failure, read the relevant test + source, propose a minimal Edit. Does
NOT write new tests (that's a wider scope that needs a more capable
model and forecloses the eventual Haiku downgrade we want to leave
open).

**Tools:** `Read, Edit, Bash, Grep` (no Write).

**Model:** `claude-sonnet-4-6` initially. The codebase is Python with
`mypy --strict` — failure diagnosis sometimes requires reading complex
type errors. Start conservative; let agentfluent's `model_mismatch`
signal tell us to downgrade to Haiku once we have observation data.

**Why it'll help:** narrower scope → fewer wandering Read/Glob retries
on the unit-test path → less wall-time per loop. Cost-side: shifts
test-loop work from Opus-via-general-purpose to Sonnet-via-tester,
which is also a meaningful (if small) win.

**Why tester is well-sized for offloading** (per the tradeoffs above):
test loops involve multiple Read+Bash+Edit cycles per invocation, easily
clearing the delegation tax. Each test failure is also its own
relatively contained context, so cache loss vs the parent thread is
minimal. Both alignments mean tester should produce a measurable
parent-thread reduction without falling into the small-task trap.

**What to measure (next agentfluent run, after using `tester` for
several test loops):**

- `general-purpose` invocation count: should drop
- `tester` should appear with low retry_loop / tool_error_sequence
  counts (target: zero, realistic: <2)
- `tester` avg_duration should be substantially below the current
  general-purpose avg of 98s on test workflows
- Watch for `model_mismatch: overspec` on `tester` after 3+ invocations
  — that's the Haiku-downgrade signal

### Agent 2: `gh-watcher` — poll for CI completion (claude-haiku-4-5)

Observed directly in this session: `gh pr checks --watch` polling for
5+ minutes while the parent thread sat idle and burned cache TTL. Real
pattern, recurring, doesn't fit any existing agent.

**Scope:** Watch a PR's CI checks until pass/fail and return a concise
summary. Owns the polling loop so the parent thread can keep working.

**Tools:** `Bash, ScheduleWakeup` (gh-only Bash + sleep — no edit
surface, minimal blast radius).

**Model:** `claude-haiku-4-5`. Pure I/O orchestration; Haiku is the
right tier.

**Why it'll help:** parent thread is your most expensive token consumer
($855 of $861). Anything that lets it work in parallel with CI is
high-leverage. Different value than `tester`: this saves wall-time and
parent-thread tokens, where `tester` saves subagent retry waste.

**What to measure:** parent-thread cache_efficiency on PR-cutting
sessions (should rise — fewer TTL expirations from idle waits).

### Not-a-new-agent recommendation: delegate more `py-src` work

The `py-src` cluster (14 invocations) tells us recurring Python source
editing is happening parent-thread. This is why parent-thread tokens
dominate. A focused `py-src` agent on Sonnet 4.6 with
`[Read, Edit, Grep, Glob, Bash]` would shift the work off the parent.

Holding off until `tester` and `gh-watcher` performance data is in.
Adding too many agents at once makes attribution impossible.

## Decisions made this session

- **Sequence:** one agent at a time. Build `tester` first; observe for
  ~1 week of normal use; re-run agentfluent; assess; then build
  `gh-watcher`. Single-axis change keeps the diff between agentfluent
  runs interpretable.
- **`tester` model:** start at Sonnet 4.6, plan to downgrade to Haiku
  if `model_mismatch: overspec` fires after enough observations.
- **`tester` scope:** fix existing failures only. No new test
  authorship.
- **Doc location:** `.claude/specs/analysis/<date>-*.md`. New
  subfolder under specs for analysis snapshots; keeps the existing
  PRD/backlog/decisions structure clean.

## Open questions / followups

- **Why does `pm` average 999s/call?** Worth a per-invocation drill-down
  separate from this analysis. Could be: (a) genuinely heavy PM tasks,
  (b) wasted exploration cycles, (c) waiting on user clarification mid-
  invocation. Currently flagged as a duration_outlier with model
  recommendation; the model isn't necessarily the issue.
- **Filtering hook-induced `permission_failure` noise.** The "blocked"
  signals from the secret-reads hook are intended behavior. Should
  agentfluent learn to recognize these and downgrade severity / hide
  by default? Possible v0.5 issue.
- **`py-src` delegation.** Defer until tester + gh-watcher land and we
  have a sense of the real impact. If they help meaningfully,
  py-src delegation is the obvious next step.
- **Calibration follow-up:** after `tester` is in use, this is a real
  data point for `model_routing` calibration (#140). The complexity
  classifier currently classifies every observed agent as "complex" on
  v0.3 single-dataset calibration — `tester` should classify as
  "simple" or "moderate" if the thresholds are set sensibly.
- **Cross-project validation of the parent-thread thesis.** The 99.5%
  parent-share number is one data point from one developer's workflow.
  Worth running agentfluent on:
  - `codefluent` (already in `~/.claude/projects/`, 11 sessions) — same
    developer, different project. Tests "is this Fred-specific or
    workflow-stable?"
  - `sportswear-esg-news-classifier`
    (`~/Documents/Courses/DataTalksClub/projects/`) — different domain
    (ML / ESG news classification), pre-subagent maturity stage (no
    custom agents yet). Tests the thesis at a different point on the
    developer-tooling adoption curve. Likely shows the thesis even more
    starkly (no subagents = 100% parent thread by definition).

  Each cross-project run is a chance to refine: where does 99.5%
  generalize, where doesn't it? Either outcome strengthens the
  eventual narrative.

## Marketing notes (out of scope, for the eventual blog post)

The product narrative the data supports is **not** "save 50% on cost."
It's "see what your delegation is silently wasting." The output above is
genuinely useful in a way developers don't currently have access to —
nobody's `pm` agent averaging 16 minutes/call gets surfaced anywhere
else. That's the pitch.

**The parent-thread-bloat angle is the most generalizable thesis.**
Most Claude Code users (especially newer ones) don't use subagents
at all, which means 100% of their spend is parent-thread. The story
"the parent thread is silently expensive — here's how to see what
your subagents *aren't* doing yet" probably resonates with the
average user, not just power users. Worth developing further with
cross-project validation.

**Audience nuance for the dollar-savings sub-narrative.** The
"save real money" framing applies most cleanly to:

- API customers (paying per-token via Anthropic API)
- Bedrock and Vertex customers (per-token via cloud bills)

It applies less directly to:

- Pro and Max plan users — feel wall-time cost, not dollar cost
- Enterprise plans — vary; some seat-based, some token-based

The wall-time-and-reliability angle is the *universal* one. Lead with
that for broad audiences; lead with dollars when targeting the
budget-sensitive subset (e.g., engineering leads at API-billed orgs).

Concrete write-up worth doing once `tester` and `gh-watcher` have a few
weeks of data:

- Before/after retry rates on `general-purpose`
- Before/after parent-thread token share (target: meaningful drop)
- Real wall-time savings from `gh-watcher`
- The story of *what* surfaced in the agentfluent output that we
  didn't already know — e.g., `pm` duration outlier was genuinely
  surprising

That's a Show HN-shaped post with verifiable numbers.
