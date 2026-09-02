# Feature: timestamp-part columns in the descriptive stats CSV

## Goal

Time summaries over the stats CSVs (group by hour, by day, …) currently
require parsing timestamps out of filenames. Add the file's timestamp to
every stats row as **individual part columns down to seconds** — `year`,
`month`, `day`, `hour`, `minute`, `second` — plus a full ISO-8601 `time`
column as the ready-made sort/join key.

## Design

### Timestamp source: the NetCDF time coordinate, not the filename

Each stats row gains the timestamp of the file it describes, read from the
file's `time` coordinate (first value) while the dataset is already open in
`compute_file_stats` — **not** parsed out of the filename:

- Filename parsing would couple the analysis to `filename_pattern` and to
  the known stamp bug; the coordinate is what the data actually claims.
- Side benefit: with the driver's hour-0 stamp bug outstanding, these
  columns reveal whether the *internal* time coordinate agrees with the
  (wrong) filename or with the expected write time — genuinely useful
  diagnostic data either way.

Files whose stats flatten multiple timesteps get the **first** time value
(driver output is one file per write, so single-timestep in practice; the
choice only matters for hypothetical aggregate files). A missing or
non-datetime `time` coordinate yields null part columns rather than an
error.

### Model and CSV schema

`VariableStats` (pydantic, frozen) gains seven fields; CSV column order
follows field order:

```python
class VariableStats(BaseModel):
    combo: str
    file: str
    time: str | None      # ISO-8601, e.g. "2010-01-01T01:00:00"
    year: int | None
    month: int | None
    day: int | None
    hour: int | None
    minute: int | None
    second: int | None
    variable: str
    count: int
    ...                   # existing stats unchanged
```

`write_combo_stats_csv` / `concatenate_stats_csvs` need no changes — columns
derive from `VariableStats.model_fields`.

## Ripples (standing process rules)

- **Harness tests** (`src/tests/ufs_chem_assay/`): the fabricated
  driver-like NetCDF already has a time coordinate — assert the extracted
  `time`/part columns match it; a fabricated dataset *without* a time
  coordinate exercises the null path; `test_analysis` row-builder helpers
  gain the new fields; CSV column-order test updates automatically via
  `model_fields`.
- **`design.md`**: no schema description lives there today; confirm and
  leave, or touch only if the analysis blurb needs it.
- **`README.md`**: the results section mentions the stats CSVs — add a
  one-line description of the columns (identity + timestamp parts + stats),
  since the CSV schema is this feature's public API.
- Pydantic models, never dataclasses (already the case here).

## Non-goals

- No per-timestep rows for multi-timestep files (row semantics stay one row
  per file × variable).
- No timezone handling — driver times are naive; parts are emitted as-is.
- No derived summary tables (grouping is the consumer's job; these columns
  just make it trivial).

## Acceptance criteria

- Harness: extracted `time`/`year`/…/`second` match the fabricated NetCDF's
  time coordinate; a time-less dataset yields nulls; all harness tests pass.
- Integration: per-combo and suite-level CSVs carry the new columns
  populated from the real driver output's time coordinate (values reported
  as observed — they may expose the stamp bug's internal counterpart).
- Existing stat values and row counts unchanged.
