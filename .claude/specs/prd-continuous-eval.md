# PRD — Continuous agent-quality evaluation for external users (`agentfluent watch` + thin on-ramp plugin)

**Status:** Roadmap / Draft (2026-07-12) · **Owner:** Fred + Claude Code (PM) · **Epic:** `epic:continuous-eval`
**Not being built now.** This is a well-scoped roadmap item, tractable when picked up after MVP maturity. It will be handed to the architect for design review before any implementation.

> Generalize the maintainer-only **dogfood-runner** (#590 / "S0") into a feature *other people* can install and run: continuous, local-first agent-quality evaluation over their own repo's Claude Code / Agent SDK sessions, with minimal setup. Collapse the painful multi-step private workflow (install CLI → hand-write cron → author a synthesis skill → wire a prompt → tune a gate) into **"install + config."**

---

## 1. Problem & motivation

The dogfood-runner (#590, D050) is a live local cron on the maintainer's machine. Each day it runs `agentfluent` over a bounded rolling window of the repo's own `~/.claude/projects/` sessions, applies a deterministic **anti-false-green gate**, and synthesizes agent-quality findings. It is genuinely useful — it automates the post-release dogfood ritual and points AgentFluent's regression-detection value proposition inward.

But it is **private plumbing**, not a product. Reproducing it on another person's machine today means: install the CLI, hand-write a cron entry (that assumes cron exists), author a synthesis skill, wire a prompt, and tune the gate thresholds. Every one of those steps is a place to get it wrong, and the whole thing drifts from the original with no shared upgrade path.

The product opportunity is an **adoption on-ramp**: turn "here's a pile of private infra you'd have to reverse-engineer" into "install the CLI, install the plugin, edit ~20 lines of config, and you have continuous agent-quality evaluation." This is the same reuse pressure that drove the release-loop plugin (D051 / #611) — but pointed at the *analytics* engine instead of the *dev-loop* engine.

**This feature is a sibling to the loop-plugin epic (#611), not a subset of it.** The loop-plugin packages the supervised *development* loop; this packages the periodic *evaluation* loop. They share the "thin Claude-Code plugin glue over a real engine" shape and MUST compose with the #612 generic-loop-engine seam (see §7), but they ship different engines and different value.

## 2. The decided shape — "Shape A" (do not re-litigate)

**The product decision is already made.** The CLI owns the loop primitive; the plugin is thin glue. Recorded as decision **D052** (see §12; append-ready block in the handoff).

**Shape A — the CLI owns the loop primitive; the plugin is thin glue.**
- The periodic evaluation loop (**schedule → analyze window → gate → synthesize findings → write output**) lives INSIDE the `agentfluent` CLI as a first-class subcommand (working name **`agentfluent watch`** — see §9 naming note). Pure Python, cross-platform, testable, no cloud dependency, respects local-first.
- The Claude Code **plugin is thin**: it ships the Claude-Code-native glue — a skill/command to start and configure the watch, the synthesis agent prompt, and a config scaffold — and **assumes `agentfluent` is already installed**. It detects the CLI and prompts the user to install it if missing. The plugin does NOT own the pip/uv install of `agentfluent`.

**Shape B — a fat plugin that owns the Python install + cron — is explicitly rejected** (see §11 Alternatives). Crossing the Claude-Code-artifact ↔ PyPI-package boundary on strangers' machines (no uv, wrong Python, no venv, PATH issues, Windows) is the single most fragile support surface, and owning it buys nothing the thin detect-and-prompt approach doesn't.

Product ladder, unchanged: **plugin = on-ramp, CLI = engine, webapp = dashboard.**

## 3. Value-prop guardrails (every design choice serves these)

AgentFluent's identity is **local-first, CLI-centric, cost-conscious, lightweight, secure, no telemetry.** These are guardrails, not aspirations:

- **Local-first / no telemetry.** All analysis and synthesis run on the user's machine over local session data. Nothing is uploaded. No usage beacons. A cloud/remote executor is a non-goal (§ Non-goals), because it would both break local-first and be unable to see the local corpus (D050 established this for the maintainer runner).
- **Cost-conscious.** The synthesis step spends the *user's* Claude tokens. Spend must be opt-in, throttled, and transparent up front (§6).
- **Lightweight & secure.** Minimal moving parts, no long-running privileged daemon if avoidable, no secret handling beyond the user's existing Claude auth, session paths (which embed absolute filesystem paths) scrubbed from any emitted artifact.
- **Honest.** A false green shipped to a stranger is worse than no tool. The gate (§5) is a hard requirement, not a nicety.

## 4. Goals & non-goals

**Goals**
- A first-class `agentfluent watch` subcommand: the self-contained, cross-platform evaluation-loop primitive (§ requirements R1).
- A **productized anti-false-green gate** that refuses to emit "clean" unless it proves it had real signal AND the pipeline actually ran (R2).
- **Cost controls & transparency** as a first-class concern: opt-in, throttled, dry-run/estimate mode (R3).
- A **small config surface** (~repo paths, window, cadence, thresholds, cost caps, opt-in) that is the entire per-user setup burden (R6).
- A **thin plugin** that detects the installed CLI and scaffolds config — the on-ramp (R7).
- **Cross-corpus safety**: findings fail-safe to under-detection on unfamiliar setups, never confident-but-wrong (R5).

**Non-goals (explicit)**
- **The plugin owning the `agentfluent` pip/uv install (Shape B).** The install prerequisite is *documented*, detected, and prompted — never automated by the plugin.
- **Cloud / remote execution, or any telemetry.** Local-only, always.
- **Auto-applying recommended fixes.** That is a separate open question in the delivery strategy and out of scope here.
- **Building this now.** Roadmap item pending MVP completion; ACs below are directional, not sprint-ready.
- **Re-litigating Shape A vs Shape B.** Decided (§2, D052).
- **Shipping new diagnostic *signals*.** This feature packages and schedules the *existing* analysis surface; it does not invent new detectors. (Cross-corpus safety may *gate off* existing signals — R5 — but adds none.)

## 5. HARD REQUIREMENT — the anti-false-green gate (R2)

This is a **product requirement, not an implementation detail.** It is the load-bearing reason the feature is safe to ship to strangers. The maintainer version (D050) already reads CLI `returncode` deterministically and keeps the LLM off the correctness path; the productized version must go further, because it runs over *unfamiliar* corpora where "found nothing" is ambiguous.

**Core guarantee:** `watch` must never emit a "clean / no problems found" verdict unless it can prove it (a) had real signal to analyze AND (b) actually ran the pipeline end-to-end. When it cannot prove both, it emits an explicit **"couldn't evaluate + why"** result instead of a green one.

**Failure modes the gate must guard against:**
1. **Thin / empty corpus** — too few sessions or tool-calls in the window to say anything. → emit `insufficient-data — not evaluated`, not green.
2. **Silent analyzer failure** — an exception path returns empty results that *look* like "nothing found." → the gate must distinguish "ran and found nothing" from "didn't really run."
3. **Degraded fallback synthesis over zero inputs** — synthesis producing a reassuring narrative over no actual findings.

**Required gate behaviors:**
- **Volume floor.** A configurable minimum (sessions and/or tool-calls) in the window. Below it → `insufficient-data — not evaluated`, with the observed vs required counts stated.
- **Pipeline-liveness assertion.** Structured analyzer output must actually have been produced (not an empty dict from an exception path). Deterministic, code-side — never inferred from LLM narrative.
- **Positive-control canary (target behavior; feasibility is an architect question — §13).** Each run seeds a known-bad pattern into the analyzed set; if the analyzer fails to flag it, the run is emitted as `pipeline-unverified` rather than green. This converts "the detector silently broke" from an invisible false-green into a loud, explicit non-result.

**Verdict vocabulary (directional):** `findings` · `clean-verified` (green, and *proven*) · `insufficient-data` · `pipeline-unverified`. The last two are first-class outcomes, not errors — surfaced with the *why*.

**The point:** convert a silent no-op into an explicit "couldn't evaluate, and here's why." A stranger who sees `insufficient-data` trusts the tool; a stranger who sees a false `clean` and later hits an obvious agent bug never trusts it again.

## 6. Cost controls & transparency (R3)

The synthesis step spends the **user's** Claude tokens. On someone else's account, surprise spend is a trust-killer and a direct violation of the cost-conscious guardrail.

- **Opt-in.** Synthesis (the token-spending step) is off by default or requires explicit enablement in config. Analysis + gate (deterministic, cheap/free) can run without it, producing structured findings the user can read directly.
- **Up-front spend transparency.** Before a run that will spend tokens, `watch` states the expected model(s), the window size, and an estimate. A **dry-run / estimate mode** reports what *would* be analyzed and the projected spend without calling the model.
- **Throttle / rate-limit.** A per-run and per-period token/spend cap in config; the loop refuses (or degrades to analysis-only) when a cap would be exceeded, and says so.
- **Cheap model default for synthesis.** Mirror the maintainer runner's parent-Opus / child-Haiku split spirit: default synthesis to an inexpensive model, let the user opt up.

Cost is called out as a **first-class concern** in docs and in the run summary, not buried.

## 7. Scheduling substrate (R4) — framed as a design question

How does the loop recur on the user's machine? This is the messiest cross-platform surface and is **flagged as an open architect question** (§13), but the guardrails are:

- **Local cron is not universal.** Windows has no cron; some users won't want a crontab entry. The maintainer runner (D050) ships a local-cron installer, valid because the maintainer's environment is known — that assumption does not hold for strangers.
- **Cloud / schedule-skill routines conflict with local-first** and are blind to the local corpus (D050 eliminated them for exactly this reason). Non-starter here too.
- **Shape A sidesteps much of this.** Because `watch` is a self-contained CLI primitive, "recur every N hours" can be satisfied several ways (a self-managed `watch --daemon`/`--interval` loop, an OS scheduler entry the plugin/`init` helps write, or a manual on-demand `watch` run). The CLI being self-contained means the scheduling substrate is a *thin outer wrapper*, not part of the engine.
- **Cross-platform story is required**, not optional — the feature ships to strangers, and "Linux/macOS cron only" would exclude a large slice of the target audience.

## 8. Cross-corpus safety (R5) — a constraint and a non-goal boundary

The maintainer runner analyzes *one known corpus* (the maintainer's own). Shipping to strangers means running over **unfamiliar agent setups** — different tools, different subagent topologies, different prompt styles. This directly amplifies the known **overfitting-to-single-corpus risk** (see the standing "overfitting to single corpus" concern in the project memory / D-series calibration work).

- **Fail-safe to under-detection.** On an unfamiliar corpus, a missed finding is recoverable; a confident-but-wrong finding destroys trust. When a signal's precision depends on corpus-specific vocabulary or on assumptions that may not hold externally, it must **under-detect rather than over-claim.**
- **Signal gating.** Some existing signals are corpus-invariant enough to ship externally; others (those keyed on corpus-specific phrasing, or validated only against the maintainer's data) should be **gated off or down-weighted** for unfamiliar setups until proven. *Which signals fall on which side is an architect question* (§13) — this PRD names the constraint, not the partition.
- **Non-goal boundary:** this feature does **not** add new signals or re-tune detector thresholds to chase external corpora. It packages the existing surface with a conservative, fail-safe posture. Signal precision work lives in the diagnostics epics, not here.

## 9. Config surface (R6) — the entire setup burden

The whole per-user setup should be **install + edit a short config.** Config lives in the target repo (e.g. `${CLAUDE_PROJECT_DIR}/.claude/watch.config.md` or equivalent — final path an architect/impl decision, aligned with the loop-plugin's `loop.config.md` convention). It carries:

- **Repo path(s) / project slug(s)** to analyze (which `~/.claude/projects/` slugs).
- **Window** — the bounded rolling window length (default `7d`, per D050; a tuning value, not a filing decision).
- **Cadence** — how often the loop runs (subject to the §7 scheduling substrate).
- **Gate thresholds** — the R2 volume floor (min sessions / tool-calls).
- **Cost caps & synthesis opt-in** — the R3 controls (per-run / per-period token or spend cap, synthesis on/off, synthesis model).
- **Output location** for findings.

**Naming note:** working command name is **`agentfluent watch`**. `agentfluent loop` was considered and set aside to avoid collision with the release-loop / `/loop` harness, where "the loop" already means the supervised dev loop in this ecosystem. Final name is a small open question (§13) but `watch` is the recommendation.

## 10. Delivery / packaging (R7) — thin plugin + CLI detection

- **Thin plugin contents:** a start/configure command or skill, the synthesis agent prompt, and the config scaffold. Bundled reads via `${CLAUDE_PLUGIN_ROOT}`; per-project config + output via `${CLAUDE_PROJECT_DIR}` (same packaging facts confirmed for the loop-plugin, `prd-loop-plugin.md` §3).
- **CLI detection, not CLI install.** The plugin checks for an installed, compatible `agentfluent` and, if missing or too old, prints clear install/upgrade guidance and stops. It never runs pip/uv itself (Shape B non-goal).
- **Document the prerequisite.** The install of `agentfluent` is a documented step in the plugin README, not an automated one.
- **Plugin ↔ CLI version/compatibility contract.** The plugin declares the minimum `agentfluent` version it targets; the detection step enforces it. Exact contract shape is an architect question (§13).

## 11. Alternatives considered

- **Shape B — fat plugin owns Python install + cron (REJECTED).** A plugin that pip/uv-installs `agentfluent` and writes the user's cron. Rejected because crossing the Claude-Code-artifact ↔ PyPI-package boundary on unknown machines is the hardest, most failure-prone support surface: no uv present, wrong Python version, no virtualenv, PATH resolution, and Windows all break it, and every failure lands as a support burden on the maintainer. The thin detect-and-prompt approach (Shape A) gets the same on-ramp value without owning the fragile boundary. Decided in D052.
- **Port the maintainer runner as-is to a "copy me" example (REJECTED implicitly).** That is the status quo (private plumbing). It drifts with no shared upgrade path — the same failure mode D051 rejected for the release-loop.
- **Cloud/hosted evaluation service (REJECTED).** Violates local-first + no-telemetry and cannot see the local corpus (D050).

## 12. Decision record — D052 (Shape A)

Recorded here because the PM tool set can only overwrite `decisions.md` (a paraphrase hazard on a 1184-line append-only log). The **append-ready D052 block is provided in the handoff** for the developer to append verbatim via Edit. Summary:

**D052 — Generalized continuous-eval ships as a thin Claude Code plugin over a CLI-owned `watch` loop primitive (Shape A), not a fat install-owning plugin (Shape B).** Rationale: the CLI-owned loop keeps the engine pure-Python, cross-platform, testable, and local-first; the plugin stays a thin on-ramp that detects (never installs) the CLI, deliberately avoiding the fragile Claude-Code-artifact ↔ PyPI-package install boundary. Sibling to D051/#611 (loop-plugin), composes with the #612 generic-engine seam; generalizes the #590/D050 maintainer dogfood-runner. Anti-false-green gate is a hard product requirement, not an implementation detail.

## 13. Open questions for architect review

Framed problems for the architect (this PRD deliberately does not decide these):

1. **Loop primitive location & composition with #612.** Where does the `watch` loop primitive live in the CLI, and how does it compose with the #612 generic-loop-engine seam — genuine reuse of that engine, or a parallel evaluation-loop that shares only the config-seam *pattern*? (The two loops differ: dev-loop is supervised + human-gated + mutating; watch is autonomous + read-only + periodic. Reuse vs. parallel is the call.)
2. **Scheduling substrate tradeoffs.** Self-contained `watch --daemon`/`--interval` vs local cron vs an OS-scheduler entry vs `/loop`-style in-session — and the cross-platform (Windows) story. Which is the default, which are supported?
3. **Anti-false-green gate mechanics.** Concrete volume-floor thresholds; the pipeline-liveness assertion mechanism; and the **feasibility + per-run cost of the positive-control canary** (is seeding a known-bad pattern every run cheap and reliable enough to be default-on?).
4. **Cost-control mechanism design.** Token budgeting model, throttle enforcement point, and the dry-run/estimate accuracy target.
5. **Cross-corpus signal partition.** Which existing signals are corpus-invariant enough to ship externally default-on, vs. which should be gated off / down-weighted for unfamiliar setups until proven.
6. **Plugin ↔ CLI version/compatibility contract.** How the plugin declares and enforces its minimum `agentfluent` version, and how breaking changes to `watch`'s output contract are versioned.

## 14. Requirements → stories map

| R | Requirement | Story |
|---|-------------|-------|
| R1 | `agentfluent watch` CLI loop primitive (schedule-agnostic: analyze window → gate → synthesize → write) | S1 |
| R2 | Anti-false-green gate as a hard, productized guarantee (volume floor, liveness, canary) | S2 |
| R3 | Cost controls & transparency (opt-in, throttle, dry-run/estimate) | S3 |
| R6 | Config surface (paths, window, cadence, thresholds, cost caps, opt-in) | S4 |
| R4 | Scheduling substrate (cross-platform; architect-gated design) | S5 |
| R5 | Cross-corpus safety (fail-safe to under-detection; signal gating) | S6 |
| R7 | Thin plugin packaging + CLI detection + version contract | S7 |
| —  | Docs / onboarding quickstart (install + config on-ramp) | S8 |

**Priority spine (roadmap):** S1 → S2 (S2 is the safety gate that makes external shipping defensible) → S4/S3 → S6 → S5 → S7 → S8. S5 (scheduling) and S6 (cross-corpus) carry the heaviest open design questions and should get architect review before they leave the roadmap.

## 15. References

- Source workflow: **#590** (S0 maintainer dogfood-runner) and **D050** (local-cron scheduling + deterministic anti-false-green gate — the private version this generalizes).
- Sibling epic: **#611** / **D051** (release-loop → Claude Code plugin) and the **#612** generic-engine ↔ per-project-config seam this composes with.
- Loop-plugin packaging facts: `prd-loop-plugin.md` §3 (`.claude-plugin/plugin.json`, `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PROJECT_DIR}`, marketplace distribution).
- Governing decisions: **D001** (Python-only), **D024/D025** (`--since` date-range surface reused for the window), **D045** (pricing base + overlay — relevant to cost estimation).
- Standing risk: overfitting-to-single-corpus (project memory + D-series calibration work) — the direct motivation for R5.
- Delivery strategy open question: auto-apply-fix automation (out of scope here).
</content>
</invoke>
