# PRD: In-Product Glossary

**Issues:** #190 (Phase 1), #191 (Phase 2), #192 (Phase 3)
**Status:** PM-reviewed, awaiting Fred's answers to clarifying questions
**Milestone:** v0.4.0 (Phases 1-2); Phase 3 recommended for deferral to v0.5.0+

---

## Problem

AgentFluent's CLI output uses ~40 domain-specific terms that have no inline
definitions and no external documentation. Even experienced Claude Code users
lack priors on AgentFluent-invented vocabulary (signal types, confidence tiers,
recommendation targets, agent concerns). The first-time user journey is broken:
users see diagnostic output they cannot interpret without reading source code.

## User journeys

Three audiences, each with a different relationship to the glossary:

| Audience | Need | Phase that serves it |
|----------|------|---------------------|
| **First-time user** (orientation) | Build a mental model of AgentFluent's vocabulary before/during first analysis run. Reads the glossary front-to-back or scans by category. | Phase 1 (static markdown) |
| **Mid-analysis user** (lookup) | Saw a specific term in CLI output, wants an instant definition without leaving the terminal or opening a browser. | Phase 2 (`explain` subcommand) |
| **Programmatic consumer** (resolution) | Consuming `--format json` output, needs to resolve term strings to definitions mechanically. | Phase 2 (JSON envelope ref -- recommended for deferral) |
| **Repeat user** (discoverability) | Runs agentfluent regularly, may not realize new terms are glossary entries. | Phase 3 (inline hints -- recommended for deferral) |

## Surface architecture

```
Single source (Phase 2):
  src/agentfluent/glossary/terms.yaml
       |
       +---> docs/GLOSSARY.md          (generated, committed)
       +---> agentfluent explain <term> (CLI subcommand)
       +---> _glossary_ref in JSON      (deferred)
       +---> per-section footer hints   (deferred, Phase 3)

Phase 1 (interim):
  docs/GLOSSARY.md                     (hand-authored, later replaced)
```

Decision: single-source YAML with multiple consumers. Locked -- not
under discussion. The phased approach lets Phase 1 ship value immediately
while Phase 2 adds the automation layer.

## Term priority matrix

Based on "first-time confusion" analysis -- how likely a user is to
misinterpret or skip a term without a definition:

| Priority | Categories | Term count (approx) | Definition depth |
|----------|-----------|---------------------|-----------------|
| **P0** | Signal types (10), Severity (3), Confidence (3) | ~16 | 3-5 sentences, worked example with real CLI output, detection threshold from source |
| **P1** | Token types (4), Recommendation targets (4), Agent concerns (4), Model routing (3) | ~15 | 2-3 sentences, one example, cross-link to Anthropic docs where applicable |
| **P2** | Built-in tools (~15), Built-in agent types (6), Cluster metrics (2) | ~23 | 1-2 sentences, example |

P0 terms are where writing effort should concentrate. P2 terms are
lookup-only reference entries.

## Definition template (per term)

```markdown
### `term_name`

**Short:** One-sentence definition.

**Detail:** 2-5 sentences. When it appears in output, what triggers it,
what the user should do. For signals: detection threshold/heuristic.

**Example:** Realistic CLI output snippet.

**Related:** Cross-links to other glossary entries.
```

Phase 2 YAML schema extends this with machine-readable fields:
`category`, `aliases`, `severity_range`, `recommendation_target`,
`threshold`, `hint_worthy` (boolean for Phase 3 filtering).

## Phase summary and recommendations

### Phase 1 (#190) -- Ship in v0.4.0

Static `docs/GLOSSARY.md` + README link + CLI footer pointer.
Low risk, high value, no code complexity.

**Key PM recommendations:**
- Add a "Reading Guide" preamble for orientation readers
- Use the P0/P1/P2 tiering to calibrate writing depth
- Use consistent markdown structure (heading levels, backtick term names) to enable Phase 2 migration
- Footer line on `--diagnostics` and `config-check` only (not plain `analyze`)
- Omit source-code links (defer to Phase 2)

### Phase 2 (#191) -- Ship in v0.4.0 (after Phase 1 content is reviewed)

Structured YAML source + `agentfluent explain <term>` + generated markdown + CI drift check.

**Key PM recommendations:**
- Single-term lookup is the hero path; optimize UX for it
- Fuzzy match via underscore/hyphen normalization + substring, not Levenshtein
- Empty input = `--list` output (discoverable entry point)
- Defer JSON envelope `_definitions` field (no consumers yet)
- CI check as a pytest test, not a standalone script
- YAML should live in `src/agentfluent/glossary/` (ships with package, accessible via `importlib.resources`)
- Add `hint_worthy` boolean to schema now even if Phase 3 is deferred

### Phase 3 (#192) -- Recommend deferral to v0.5.0+

Inline contextual help (per-section footer listing glossary terms).

**Key PM recommendations:**
- Defer entirely until Phases 1+2 ship and there's user signal that discoverability is still a problem
- If kept: off-by-default (`--glossary-hints`), P0 terms only, footer-only (no column annotations)
- Auto-suppress when stdout is not a TTY (CI pipelines)
- Column-header `(?)` annotations should be dropped (breaks table layout)

## Decision log

| Decision | Rationale | Status |
|----------|-----------|--------|
| Single-source YAML with generated markdown | Eliminates drift; enables CLI + docs + JSON from one source | Locked |
| Phase 3 deferral recommendation | No usage data; Phases 1+2 likely sufficient; non-trivial integration cost | Awaiting Fred's decision |
| Fuzzy match: normalization + substring over Levenshtein | Users mistype separators, not characters; avoids misleading cross-category matches | Recommended |
| JSON envelope `_definitions` deferral | No programmatic consumers yet; speculative scope | Recommended |
| Footer on `--diagnostics` + `config-check` only | Plain `analyze` output has self-evident columns; domain terms concentrate in diagnostics | Recommended |
| `hint_worthy` field in Phase 2 YAML schema | Zero-cost future-proofing for Phase 3 regardless of deferral decision | Recommended |

## Open questions (awaiting Fred)

See clarifying questions on #190 (4 questions), #191 (4 questions), #192 (3 questions).
Critical path items:

1. Should the implementer grep for all enum values or use the issue body list as authoritative scope?
2. Should thresholds be cited as specific numbers or described qualitatively?
3. Should YAML live in `src/` (ships with package) or `docs/` (repo-only)?
4. Is Phase 3 deferred or kept with the trimmed scope?
