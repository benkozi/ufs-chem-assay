# Feature: baseline comparison improvements — diff statistics, bias plots, CSV records

## Goal

Deepen the baseline comparison from pass/fail into **quantified difference
analysis**: RMSE and descriptive statistics of the difference field per
variable, **bias plots** (realization − baseline) per file with an
accompanying GIF, and comparison records as **CSV** — per combination and
concatenated at the output root — replacing the per-combo YAML record.
Plot folders are reorganized: `plots/` becomes `plots-overview/`, and bias
plots live in a new `plots-baselines/`.

## Design

### Difference statistics (extends `VariableComparison`)

The per-variable dask batch in `_compare_variable` gains reductions over
`diff = realization − baseline` (nan-aware, same batching pattern):

- `rmse` — `sqrt(nanmean(diff**2))`
- `n_evaluated` — the number of elements evaluated (non-NaN difference
  values)
- `n_mismatched` — the number of elements **failing the data check under
  the entry's `atol`** (bitwise-unequal at `atol=0`; `|diff| > atol`
  otherwise; NaN-position mismatches included). Unlike the `diff_*`
  statistics it depends on the comparison rule, and it carries the
  invariant `data_match ⟺ n_mismatched == 0`.
- the established descriptive set applied to the difference:
  `diff_sum, diff_mean, diff_std, diff_min, diff_max, diff_median`
  (mirroring the descriptive-stats columns, so the two CSV families read
  alike)

All become described fields on `VariableComparison` (`None` when
shapes/dtypes prevented comparison). `max_abs_diff` stays (it also feeds
the bias color scale below).

### Comparison records: CSV replaces the YAML

Per the note, the `-comparison.yaml` is **converted to CSV and
concatenated** — the same two-layer pattern as descriptive stats:

- **Per combination**: `<combo_id>-stats-comparison.csv`, one row per
  (file × variable), flattening the result model: identity
  (`run_id, suite, combo_id, combo, baseline_ulid, atol`), file-level
  checks (`file, format_match, dimensions_match, variables_match,
  global_attributes_match, file_names_match`), per-variable checks
  (`variable, dtype_match, data_match, attributes_match`), the difference
  statistics above (`n_evaluated`/`n_mismatched` included), and `passed`.
- **Root level**: `stats-comparison.csv`, concatenated from every per-combo
  comparison CSV at `pytest_sessionfinish` (exactly like
  `descriptive_stats.csv`; partial runs concatenate what exists).
- The pydantic result models remain the comparison's in-memory product
  (the test still asserts `result.passed`, failure summaries unchanged);
  only the *artifact* changes from YAML to CSV rows built via
  `model_dump()` — the `-comparison.yaml` is no longer written.

### Bias plots: `plots-baselines/`

For every compared combination (per file, per variable): a spatial map of
`realization − baseline`, plus a per-variable GIF stringing the files in
timestamp order. **Plots and GIFs are always created together** — no
separate GIF toggle (unlike the overview's `gif_enabled`).

- **Rendered at session end** (same forcing constraint as the overview: a
  shared color scale needs all combinations finished). Ordering in
  `pytest_sessionfinish`: stats concat → overview plots → comparison-stats
  concat → bias plots.
- **Color scale**: diverging `RdBu_r` centered on zero with a **suite-wide
  symmetric range per variable**, `±max(max_abs_diff)` from the
  concatenated `stats-comparison.csv` — exact, never percentile-clipped
  (evaluation-tool philosophy), with the established degenerate-range guard
  (identical data → all-zero bias → expand so matplotlib gets a valid
  range).
- Rendering reuses the `plotting.py` machinery (Agg, PlateCarree,
  boundaries with graceful degradation, footer); filenames
  `plots-baselines/<variable>__<nc-stem>.png` and
  `plots-baselines/<variable>.gif`. Failures log at ERROR and skip, never
  raise — same diagnostics-not-assertions stance.
- Requires the baseline files again at session end: the resolved
  combo→entry map and settings are already stashed on the pytest config.
- **Independent of the overview `plotting` block**: bias plots are governed
  solely by the baseline-comparison option below (they render even when
  `plotting.enabled: false`, and require no descriptive-stats dependency —
  their scale comes from the comparison CSV).

### Folder rename: `plots/` → `plots-overview/`

`plotting.PLOTS_DIRNAME` changes; the overview machinery is otherwise
untouched. Layout after this feature:

```
<output-root>/
  stats-comparison.csv                 # all combos' comparison rows
  9004a4e23c1dd90a/
    9004a4e23c1dd90a-stats-comparison.csv
    plots-overview/                    # was plots/: per-NetCDF maps + GIF
    plots-baselines/                   # bias maps + GIF (compared combos only)
```

### Per-comparison plotting switch

`BaselineComparison` gains the on/off option from the note:

```python
class BaselineComparison(StrictModel):
    sweep_selector: SweepSelector
    ulid: str
    atol: float = 0.0
    plot: bool = Field(True, description="Render bias plots + GIF for this comparison at session end")
```

Per entry — consistent with per-entry `atol`; an entry with `plot: false`
still compares and records CSV rows, it just renders nothing.

## TDD plan (red first)

- **Difference stats**: fabricated pair with known perturbations — `rmse`
  and every `diff_*` column verified against numpy computed directly;
  identical pair → `rmse == 0`, `diff_min == diff_max == 0`.
- **CSV records**: per-combo CSV has one row per file × variable with the
  full column set; concatenation matches the descriptive-stats pattern;
  the YAML artifact is no longer produced.
- **Bias plots**: PNG per (file, variable) and GIF with one frame per file
  under `plots-baselines/`; suite-wide symmetric scale equals
  `±max(max_abs_diff)` across fabricated combos; the all-zero-bias guard;
  `plot: false` renders nothing for that entry while others render.
- **Rename**: overview artifacts appear under `plots-overview/`
  (`test_plotting` expectations updated).
- Model defaults (`plot` true) and unknown-key rejection.

## Ripples (standing process rules)

- **`design.md`**: artifacts layout (both plot folders, both CSV layers,
  comparison YAML removed); suite example gains `plot`; sessionfinish
  ordering.
- **`README.md`**: results layout, the comparison test description
  (CSV artifact instead of YAML; bias plots), folder names.
- Pydantic models with described fields; runner logging for the new steps
  (scale line, per-plot lines, concat summary) in the house style.

## Non-goals

- No percentage/relative-bias plots or stats (absolute difference only,
  matching the `atol` philosophy).
- No per-timestep rows in the comparison CSV (one row per file × variable,
  like descriptive stats).
- No overview-plot changes beyond the folder rename.

## Acceptance criteria

- Integration with baselines: every compared combo dir gains
  `<combo_id>-stats-comparison.csv` and `plots-baselines/` with maps + GIF;
  the root gains `stats-comparison.csv`; `plots-overview/` replaces
  `plots/`; no `-comparison.yaml` is written.
- Bit-for-bit-identical baselines yield `rmse = 0` rows and flat bias maps
  (degenerate-scale guard exercised in production).
- An entry with `plot: false` records CSV rows but renders no bias plots;
  uncompared combos get neither.
- Harness passes without docker; all existing tests keep their outcomes
  (modulo the folder rename).
