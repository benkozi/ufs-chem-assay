# Feature: descriptive statistics for NetCDF output

## Goal

An **analysis step** alongside the assertion steps: compute descriptive
statistics for every NetCDF file a combination produces, write one CSV per
combination, and concatenate all combinations' CSVs into a single suite-level
CSV at the root output folder. Modeled on the stats portions of
`cece-data-viewer/app/analysis.py` (the nan-aware per-variable stat set and
its xarray + dask compute pattern) — plots, diffs, and metadata dumps from
that module are out of scope.

The analysis is implemented **as a test** (per combination): today it passes
when the stats compute and the CSV is written; it is the seam where future
baseline comparisons will attach.

## Design

### `analysis` block in the suite config

A new suite-config section, parallel to `assertions`:

```yaml
analysis:
  compute_descriptive_stats: true   # default; false skips the analysis tests
```

```python
class Analysis(BaseModel):
    compute_descriptive_stats: bool = True

class SuiteConfig(BaseModel):
    ...
    analysis: Analysis = Analysis()   # section optional; defaults apply
```

When `false`, the per-combo analysis test **skips** with an explicit reason
(same pattern as `validate_filenames`), and the end-of-session concatenation
is skipped too.

### Statistics computed

For each NetCDF file in the combo directory, for each data variable: the
nan-aware stat set from the guide —
**count, sum, mean, std, min, max, median** — over all values (all
dims flattened; a `lev` dimension, when present, is included in the flatten
rather than sliced). Results are pydantic models (per the repo convention —
the guide's dataclasses/TypedDicts are translated, not copied):

```python
class VariableStats(BaseModel):
    model_config = ConfigDict(frozen=True)
    combo: str
    file: str        # NetCDF filename (not path)
    variable: str
    count: int
    sum: float
    mean: float
    std: float
    min: float
    max: float
    median: float
```

### CSV outputs (pandas)

- **Per combination** — one CSV holding *all* of that combo's NetCDF files
  (never one CSV per file): `<combo-dir>/<combo-name>-stats.csv`, one row
  per (file, variable), built with `pandas.DataFrame` from the
  `VariableStats` rows (`model_dump()`).
- **Suite level** — at session end, every per-combo CSV that exists is
  concatenated (`pandas.concat`) and written to
  `<output-root>/descriptive_stats.csv`. The `combo` column makes the
  combined file self-describing. Partial runs (`-k`, failed combos)
  concatenate whatever exists.

### Execution structure

- **`test_descriptive_stats[<combo>]`** — a fourth test on the shared
  `driver_run` fixture: skips when the driver run failed
  (`driver run failed: ...`), skips when `compute_descriptive_stats` is
  false, otherwise computes stats over the combo's `*.nc` files, writes the
  per-combo CSV, and asserts the write happened (a row per file × variable).
  No value assertions yet — baselines come later.
- **Concatenation** — in `pytest_sessionfinish` (conftest): glob the output
  root for per-combo stats CSVs, concatenate, write the suite-level CSV, log
  the row count and path at INFO. A hook rather than a test because pytest
  provides no reliable "runs last" test ordering across parameterized
  session fixtures.

### Dask distributed

Statistics are computed with **distributed dask**: a session-scoped
`dask_client` fixture starts a local `distributed.Client`; NetCDFs are
opened with `xarray.open_dataset(..., chunks="auto", engine="netcdf4")` so
the stat reductions are dask graphs, and — following the guide's pattern —
each file's per-variable stats are gathered into one
`dask.compute(*deferred)` call, executed on the client. Grossly oversized
for the 72×46 test grids; deliberately so, since this establishes the
compute path that real-sized output will need.

**Worker count** comes from a new setting:

| Setting        | Env var             | Default                      |
|----------------|---------------------|------------------------------|
| `dask_nworkers`| `CECE_DASK_NWORKERS`| unset → all available workers |

Unset (default), the `LocalCluster` is created without `n_workers`, letting
dask size the cluster to the machine (all available cores). When set, the
value must be a **positive, nonzero integer** (`int`, `gt=0` — pydantic
rejects 0 and negatives at settings load) and is passed as `n_workers`.

The client is created only when analysis actually runs (lazy fixture), so
`compute_descriptive_stats: false` and harness runs that don't touch
analysis never pay cluster startup.

### New module and dependencies

- `src/analysis.py`: `compute_file_stats(nc_path) -> list[VariableStats]`,
  `write_combo_stats_csv(...)`, `concatenate_stats_csvs(...)` — logging via
  the `ufs-chem-assay.analysis` logger in the established style.
- New dependencies: `pandas`, `xarray`, `netcdf4`, `dask[distributed]`.
  This is the runner's first heavyweight dependency set — acceptable, since
  the future baseline-comparison work needs the same stack.

## Harness tests (mocked/local, `src/tests/ufs_chem_assay/`)

- A helper writes a **temporary NetCDF matching real driver output** (`co`
  variable on a small lat/lon grid with a time dimension, written via
  xarray/netcdf4) — per the notes, fabricated output stands in for a driver
  run.
- `compute_file_stats`: stat values verified against numpy computed directly
  on the fabricated array (including nan handling); pydantic model fields
  populated correctly.
- CSV round-trip: per-combo CSV has one row per file × variable;
  concatenation of two fabricated combo CSVs produces the suite CSV with all
  rows and the `combo` column intact.
- The dask client fixture is exercised by the stats tests themselves (the
  harness pays one small LocalCluster startup).

## Non-goals

- No plots, GIFs, spatial maps, diff stats, or metadata YAML dumps from the
  guide module.
- No baseline comparison or tolerance assertions — this feature produces the
  CSVs that baselines will later compare against.
- No per-variable/level selection config — all data variables, all values.

## Acceptance criteria

- Integration (checked-in suite, analysis default-on): each combo directory
  gains `<combo>-stats.csv` with one row per NetCDF × variable, and the
  output root gains `descriptive_stats.csv` containing all combos' rows.
  `test_descriptive_stats` passes for all three combos (filename tests stay
  red per the known driver bug — unrelated).
- A suite with `compute_descriptive_stats: false`: analysis tests skip with
  an explicit reason; no stats CSVs are written.
- Harness tests pass without docker, verifying stat correctness against a
  fabricated NetCDF.
- Stats execute through a `distributed.Client`; no computation falls back to
  the synchronous scheduler.
