# Value Review: #437 Verbosity-Constraint Scanner (Track C-006a, epic #431)

**Status:** PR #571 HELD at merge gate pending this review. Analysis only — no merge, no new issues, no label/body changes.
**Date:** 2026-07-01
**Author:** PM agent, at Fred's request after re-reading the source article.

---

## 1. What the feature does

`_detect_verbosity_constraints()` in `src/agentfluent/config/scoring.py` regex-scans an
agent's **own** prompt body for word-count caps (e.g. "keep responses to ≤25 words").
For each match with captured count ≤200 it emits an **advisory** `ConfigRecommendation`
(no score deduction): WARNING if ≤50 words, else INFO. Fenced code blocks are excluded.
The recommendation copy cites Anthropic's April 2026 postmortem and its "3% quality
regression" figure. The issue explicitly states "No false-positive heuristics in v1 —
flag and let the user decide."

## 2. What the source article actually establishes

Verified against https://www.anthropic.com/engineering/april-23-postmortem (2026-07-01):

| Claim | Reality |
|---|---|
| Where the constraint lived | Anthropic's **own** Claude Code system prompt (the harness), added while tuning for Opus 4.7. NOT model training. NOT anything a customer controls. |
| Exact text | "Length limits: keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless the task requires more detail." |
| Impact | ~3% coding-quality drop on Opus 4.6 **and** 4.7, found via ablation. |
| Fix | Entirely Anthropic-side, reverted in the April 20 release. **No user action required.** |
| Guidance to developers | **None.** No generalizable lesson about blanket length caps is articulated beyond this one incident. |

**Evidence base: n=1, internal, self-corrected, coding-specific, with no generalization claimed by the source.**

### The locus mismatch (the core problem)

The scanner reads the **user's own agent prompts**. The incident occurred in
**Claude Code's harness system prompt** — a surface the user does not author and the
scanner cannot see. So the feature does not detect the documented incident class. It
detects a *hypothesized analog*: "what if a user hand-wrote a similar blanket cap on
their own agentic prompt." That analog may be real, but it is unproven and is not what
the article documents.

## 3. User-story map

**Backbone activity:** "Audit my agent config for silent quality regressions before shipping."

| # | Story | Prevalence | Precision (v1) | Holds? |
|---|---|---|---|---|
| S1 | As an Agent SDK dev, I want to be warned when my **coding / tool-using** agent has a **blanket** response-length cap, so I don't silently degrade reasoning the way Anthropic's harness did. | LOW but non-zero. Only story mapping to the actual incident. | v1 doesn't know if the agent has tools or if the cap is blanket vs scoped — flags all ≤200-word caps identically. | Concept HOLDS; **implementation FAILS** (no agent-type / blanket discrimination). |
| S2 | As a subagent author, I want a word cap on my **title / commit-message / summarizer / classifier** subagent to **not** be flagged, because that cap is correct and required. | HIGH. Word caps are common *and correct* on non-agentic tasks — likely the majority of all matches. | v1 flags these by design ("flag and let the user decide"). | **FAILS.** This is the dominant real-world case and the feature gets it backwards. |
| S3 | As a dev, I want the recommendation to tell me **what to change**, so I can act without more research. | — | v1 says "review whether this constraint is scoped to a single field" — hands the judgment back to the user. | **FAILS.** Directly contradicts the product tagline ("tells you what to change"). |
| S4 | As a dev, I want cited evidence to be **relevant to my situation**. | — | v1 cites the coding-agent 3% figure on every match, including summarizers where a cap is correct. | **FAILS.** Misapplied evidence (see §5). |

**Score: 1 of 4 stories holds conceptually; 0 of 4 are delivered well by v1. The one story that holds (S1) is also the rarest by prevalence, and v1 lacks the discrimination to serve it.**

## 4. Value verdict

**Is the kernel real?** Yes. AgentFluent's thesis — catch *silent* config-level quality
regressions a dev would never notice because agents run blind — is sound, and a blanket
length cap on an agentic prompt is a legitimate member of that class. Even Anthropic's
experts shipped one by accident.

**Does the current implementation deliver it?** No. v1:
- cannot see the surface where the documented incident occurred (harness ≠ user prompt);
- targets a hypothesized analog with **zero demonstrated prevalence** in real configs;
- inverts precision — the majority case (S2, correct scoped caps) is flagged, and the
  rare valid case (S1) is not distinguished;
- hands adjudication back to the user (S3), contradicting the tagline;
- attaches a misapplied statistic (S4) that damages recommendation credibility.

Net: v1 ships **noise plus a trust-eroding citation**, not the kernel.

## 5. Is the "3% regression" citation defensible messaging?

**No, not as written.** The figure is coding-agent-specific, internal-harness-specific,
and model-version-specific (Opus 4.6/4.7), and the source **explicitly declines to
generalize**. Citing a bare "3% quality regression" on every matched constraint —
including non-coding agents where a word cap is correct — manufactures false authority.

For a diagnostics product whose entire value is the *credibility* of its recommendations,
one visibly-bogus citation discounts every other recommendation the tool makes (the
"cried wolf" effect). If the feature ever ships, the copy must: (a) drop the bare "3%"
number; (b) condition on the agent being tool-using / agentic; (c) describe the
*mechanism* ("blanket response caps can suppress intermediate reasoning in tool-using
agents") rather than borrow a headline statistic from an unrelated context.

**Corroborating tell:** PR #571 had to correct the issue's own regex — the canonical
`≤25` / `≤100` no-space postmortem phrasing did not match the shipped patterns until
fixed in the PR. The feature's motivating example didn't match its own detector. That is
a symptom of a spec written *abstractly* against an article rather than against real
instances in a corpus.

## 6. Recommendation: **DEFER** (revert to backlog; gate re-entry on corpus evidence)

Among KEEP-AS-IS / RESCOPE-NARROW / DEFER:

**DEFER.** Park PR #571 (it is HELD, not merged, so this is ~free), keep #437 open, and
gate re-entry on **finding real blanket-cap-on-tool-having-agent instances in the dogfood
corpus**. Wire the detector as a read-only / dry-run pass over the corpus Fred already
runs each release; let measured prevalence decide.

**Reasoning:**
1. **The binding constraint is evidence of prevalence, not implementation quality.** We
   have n=1, and it is not even in the class the scanner can observe. Everything downstream
   (severity, copy, FP guards) is premature until we know the signal fires on real data.
2. **Cost asymmetry favors waiting.** DEFER costs nothing (branch parks, issue stays open,
   fully reversible). KEEP costs trust (worst, and hard to walk back). RESCOPE costs real
   dev time hardening a signal whose prevalence is unconfirmed.
3. **A prevalence oracle already exists at zero marginal cost.** Fred runs the dogfood
   corpus after every release. Run the detector across it once. If it finds zero blanket
   caps on tool-having agents, that is decisive evidence the feature isn't worth shipping.
   If it finds some, those instances become the calibration set for a precise v2.
4. **Avoids the classic trap:** building FP machinery before validating demand.

**Second-best: RESCOPE-NARROW.** If Fred wants to keep momentum rather than park it:
restrict flagging to blanket caps on **tool-having** agents (skip agents with no/only
read tools and obvious summarizer/classifier roles), add scoped-field detection to
suppress "title: max 10 words"–style matches, and rewrite the copy per §5 (no bare 3%
claim, mechanism-based, conditional). This preserves S1 and kills S2/S3/S4 — but spends
the effort *before* confirming a single real instance exists, which is why it ranks below
DEFER. It only becomes first-best if the dogfood pass in DEFER surfaces real instances.

**Reject KEEP-AS-IS:** it ships the S2/S3/S4 failures and the misapplied citation into a
product whose value is recommendation credibility, with no evidence any user config
contains the pattern.

## 7. Proposed decision-log entry (for `.claude/specs/decisions.md`)

> Note: `decisions.md` is append-only. Proposed text below — append during implementation,
> do not merge this review's file content into it.

```
## D0XX: Defer verbosity-constraint scanner (#437) pending corpus prevalence evidence

**Date:** 2026-07-01
**Context:** #437 (PR #571, HELD at merge gate) ships a regex scanner flagging ≤200-word
caps in the user's own agent prompts, citing Anthropic's April 2026 postmortem "3% coding
regression." Value re-review found: (a) the incident was in Anthropic's harness system
prompt, a surface the scanner cannot see (locus mismatch); (b) evidence is n=1, internal,
self-corrected, coding-specific, with no generalization claimed by the source; (c) v1 flags
the common+correct case (word caps on summarizers/classifiers) and cannot distinguish the
rare valid case (blanket caps on tool-using agents); (d) v1 hands judgment back to the user,
contradicting the "tells you what to change" tagline; (e) the bare 3% citation is misapplied
evidence that erodes recommendation credibility.
**Decision:** DEFER. Park PR #571, keep #437 open, gate re-entry on finding real
blanket-cap-on-tool-having-agent instances in the dogfood corpus (run the detector as a
read-only pass). If prevalence is confirmed, re-enter as RESCOPE-NARROW (tool-having gate +
scoped-field FP guard + mechanism-based copy with no bare 3% stat).
**Rationale:** Binding constraint is unproven prevalence, not implementation quality. PR is
HELD so DEFER is ~free and reversible; shipping a trust-damaging citation is not.
**Second-best:** RESCOPE-NARROW immediately — ranks below DEFER because it hardens a signal
whose prevalence is still unconfirmed.
```

## 8. Handoff notes

- No code, labels, or issue bodies changed. PR #571 remains HELD.
- If DEFER is accepted: the cheap next action is a one-off read-only run of the detector
  over the dogfood corpus to measure prevalence — that datum decides DEFER-vs-RESCOPE.
- Full-length recommendation copy rewrite (§5) is only needed if/when the feature re-enters.

---

## 9. Prevalence check result (2026-07-01) — DEFER confirmed

Ran the corrected detector (PR #571 patterns incl. the non-greedy fix) as a read-only pass
over every discoverable agent definition under `$HOME` (`~/.claude/agents/*.md` + all
project `.claude/agents/*.md`; 7 parseable agent defs after excluding non-agent `.md`).

**Scanner flags: 0 / 7.** Zero blanket caps; zero on tool-having agents.

Context scan (raw `N word(s)` mentions) — the corpus's *only* word-count constraints, all
**legitimate scoped output specs**, none a blanket response cap:
- `marketer`: "~800–1200 words" (blog), "350–450 words" (LinkedIn), "~1000–2500 words" (case study), "~400–800 words" (dev.to)
- `anthropic-research`, `candidate-verifier`, `jsonl-format-research`: "short run summary (under 200 words)" — scoped to the return summary (the "scope it to a field" GOOD pattern)

**Interpretation:** the harmful population the feature targets is empty in real configs, while
the population it risks mis-flagging is well-populated. The three "under 200 words" summaries
dodge the flag only by phrasing luck ("under" isn't in a pattern) and sit right at the ≤200
threshold — the precision inversion in §3/§4 is concrete, not hypothetical. Per the pre-agreed
rule (0 blanket caps on tool agents → DEFER), this resolves the DEFER-vs-RESCOPE fork toward
**DEFER**. Executed: #437 closed (misdirected-as-written), PR #571 closed unmerged, successor
#572 filed (unmilestoned, evidence-gated), decision **D046** recorded.
