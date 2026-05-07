# Threshold calibration

Jupyter notebook that empirically validates the threshold constants in
`agentfluent.diagnostics.delegation` and
`agentfluent.diagnostics.model_routing` against real agent session
data from `~/.claude/projects/`.

**Tracks:** [#140](https://github.com/frederick-douglas-pearce/agentfluent/issues/140)

## What it produces

- `threshold_validation.ipynb` — committed with rendered outputs so
  GitHub shows the analysis directly. Contains distribution plots, a
  per-threshold decision rationale, and a summary table of chosen
  values.
- Updates to comment blocks next to each calibrated constant in the
  source modules, pointing back at the notebook.

## ⚠️ Single-dataset limitation

As of the v0.3.0 calibration, this notebook was run against one
contributor's session data. Treat chosen values as informed starting
points, not settled defaults. Re-run when:

- New contributors with representative data can add projects
- Feature code changes shift what a threshold means
- Observed false-positive / false-negative rates climb

## Run it

```bash
uv sync                                               # pulls notebook deps
uv run jupyter lab threshold_validation.ipynb
```

Or regenerate from scratch (picks up any code changes to the builder):

```bash
uv run python scripts/calibration/build_notebook.py
```

The builder executes every cell and writes the notebook with outputs.
Commit the updated `.ipynb` as part of the PR that proposes new
threshold values.

## Point at a different Claude config dir

By default the notebook reads from `~/.claude/`. To run against a
different install (e.g., a colleague's shared dataset):

```bash
export CLAUDE_CONFIG_DIR=/path/to/other/.claude
uv run python scripts/calibration/build_notebook.py
```

Or edit the `config_dir` assignment in the setup cell directly.

## What's calibrated

**Delegation clustering** (`diagnostics/delegation.py`):
- `MIN_TEXT_TOKENS` — text-length filter
- `LSA_COMPONENTS` — TF-IDF dimensionality reduction
- `DEFAULT_MIN_CLUSTER_SIZE` — minimum cluster size
- `DEFAULT_MIN_SIMILARITY` — dedup threshold
- `_SILHOUETTE_K_MAX` — upper bound on silhouette-selected k
- `_CONFIDENCE_HIGH_SIZE`, `_CONFIDENCE_HIGH_COHESION`,
  `_CONFIDENCE_MEDIUM_COHESION` — confidence tier boundaries

**Model routing** (`diagnostics/model_routing.py`):
- `_MIN_INVOCATIONS_FOR_ANALYSIS` — per-agent sample floor
- `_SIMPLE_MAX_TOOL_CALLS`, `_SIMPLE_MAX_TOKENS` — simple-tier
  boundaries
- `_COMPLEX_MIN_TOOL_CALLS`, `_COMPLEX_MIN_TOKENS`,
  `_COMPLEX_MIN_ERROR_RATE` — complex-tier triggers

**Quality signals** (`diagnostics/quality_signals.py`, calibrated in #274):
- `MIN_CORRECTIONS_PER_SESSION`, `MIN_CORRECTION_RATE` — OR-gated
  USER_CORRECTION emission floors
- `_FILE_REWORK_THRESHOLD`, `POST_COMPLETION_BOOST` — FILE_REWORK
  threshold and post-completion boost (boolean: drops threshold by 1)
- `MIN_FINDING_KEYWORDS`, `_SUBSTANTIVE_RESPONSE_MIN_CHARS`,
  `MIN_REVIEWER_CAUGHT_RATE` — REVIEWER_CAUGHT precision gates
- Manual labels for precision/recall live in
  `scripts/calibration/quality_labels.json` (see notebook section 12)

**Not calibrated** (algorithmically fixed, not data-driven):
- `retry.py::SIMILARITY_THRESHOLD` (retry-detection similarity)
- `_SMALL_N_THRESHOLD` (silhouette k-range collapse boundary)

## Notebook ↔ source contract

Every calibrated constant in source carries a brief comment pointing
at this notebook. When the notebook's chosen value changes, update
both the constant and its comment in the same PR.
