# Fix: maccity config now runs 3 hours — update tests

## Standing process rules (apply to this and every future plan)

- **Always update `design.md`** as part of any implementation that changes
  behavior it describes.
- **Always update the `ufs_chem_assay` harness tests** in addition to any
  change to `test_driver_combos.py` — the harness is the always-run
  verification layer and must never lag the integration layer.
- **Always use pydantic models, never dataclasses** (established repo
  convention).

## Context

`src/tests/config/cece/simple-maccity.yaml` was changed (by the user) from a
1-hour to a **3-hour** run: `end_time: 2010-01-01T03:00:00`, still
`timestep_seconds: 3600`, `frequency_steps: 1` → **3 timesteps, 3 expected
NetCDF files** stamped at hours 1, 2, 3.

Seven harness tests fail because they hardcode the old 1-file/hour-1
expectations:

- `test_assertions.py`: `test_derived_count_for_maccity_is_one`,
  `test_assert_passes_with_derived_count`,
  `test_assert_fails_when_files_missing`,
  `test_expected_filenames_maccity_first_write_at_hour_one`,
  `test_assert_filenames_passes_with_expected_file`,
  `test_assert_filenames_fails_on_hour_zero_stamp`
- `test_maccity_pipeline.py`: `test_maccity_pipeline_runs_all_combos_mocked`

## Fix design

### Single-source expected-timestep constant

The expected timestep count is defined **once**, in the harness conftest:

```python
# src/tests/ufs_chem_assay/conftest.py
MACCITY_N_TIMESTEPS = 3  # simple-maccity.yaml: 3 h at 3600 s, frequency_steps 1
```

Deliberately a **literal**, not a value computed from the config with the
same arithmetic the code under test uses — otherwise
`derive_expected_nc_file_count` would be tested against itself and any bug
in the calculator would vanish. When the config's duration changes again,
this one line changes with it.

Derived helpers keep the rest of the tests literal-free: expected filenames
for the maccity config are the hours `1..MACCITY_N_TIMESTEPS`
(`cece_20100101_010000.nc` … `_030000.nc`), used for both the expected-set
assertions and the files the tests fabricate.

### Test updates

- Count tests assert `derive_expected_nc_file_count(...) ==
  MACCITY_N_TIMESTEPS`; fabricate that many files for the pass case (rename
  `test_derived_count_for_maccity_is_one` → `test_derived_count_for_maccity`).
- Filename tests build the expected hour-1..3 set from the constant; the
  hour-zero bug test fabricates hour-0..2 stamps and asserts hour 3 missing /
  hour 0 unexpected.
- The pipeline test fabricates all `MACCITY_N_TIMESTEPS` correctly stamped
  files per combo before running the count and filename assertions.
- `test_expected_filenames_multi_step` and the disabled/absent-output tests
  set their own driver times and are unaffected.

### Ripple: design.md and integration expectations

- `design.md`'s base-configuration description says "one-hour run" — update
  to three-hour (rule 1 above).
- Integration (`test_driver_combos.py` itself is unchanged — expectations
  are all derived): `test_nc_file_count` now expects 3 files. Whether the
  real driver produces 3 is unknown until run; if the hour-0 bug shifts all
  stamps (files at hours 0/1/2), the count passes and only the filename
  tests stay red. Any other outcome is a new driver finding to report, not
  hide.
- **Timeout stays at 10 s — no auto-adjustment.** The 1-hour run took ~6 s,
  so the 3-hour run may exceed the suite's `timeout_s: 10`. If it does, the
  resulting `test_driver_execution` timeouts (and downstream skips) are
  reported as the run's outcome, exactly like any other driver behavior —
  the suite config is not edited to make them go away.

## Acceptance criteria

- All harness tests pass with `MACCITY_N_TIMESTEPS` as the only
  duration-derived literal (`uv run pytest src/tests/ufs_chem_assay`).
- Integration, with `timeout_s` unchanged at 10: outcomes are reported as
  observed — expected shape is `test_driver_execution`,
  `test_nc_file_count`, and `test_descriptive_stats` passing with
  `test_nc_filenames` red per the known hour-0 bug, but timeouts or wrong
  file counts from the longer run are legitimate findings, reported not
  patched around.
- `design.md` no longer describes the base config as a one-hour run.
