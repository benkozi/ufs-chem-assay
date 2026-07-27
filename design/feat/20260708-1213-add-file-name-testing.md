# Feature: filename assertion for NetCDF output

## Goal

The second post-run assertion, following
`20260708-1055-add-assertions-for-file-counts.md`: assert that the NetCDF
files in a combination's directory carry the **expected names** — timestamps
rendered from the config's `filename_pattern` at the expected write times.
For simple-maccity that is exactly one file stamped at **hour 1**
(`cece_20100101_010000.nc`).

**This test is expected to fail against the current driver**: a known driver
bug stamps output starting at hour 0 (`..._000000.nc`). Catching that is the
assertion's purpose. A suite-level `validate_filenames` flag allows skipping
the assertion while the bug is outstanding.

## Design

### Expected filenames derivation

From the *generated combo config* (same source as the count derivation):

- Write times: `t_k = start_time + k × frequency_steps × timestep_seconds`
  for `k = 1 .. expected_nc_file_count` — i.e. the first file is written at
  the end of the first output interval (hour 1 for maccity), **not** at t=0.
- Each `t_k` is rendered into `output.filename_pattern`, replacing the
  tokens `{YYYY}` `{MM}` `{DD}` `{HH}` `{mm}` `{ss}` with zero-padded
  components of `t_k`.
- If `output` is absent or disabled, the expected set is empty.

The assertion compares the **set** of expected names against the set of
`*.nc` names actually present (non-recursive, same scope as the count
assertion) and fails on any difference, reporting missing and unexpected
names. Set semantics also make a token-free `filename_pattern` (every write
overwrites one file) degrade sanely: the expected set collapses to one name.

New functions in `src/assertions.py`, mirroring the count assertion:

```python
def expected_nc_filenames(config: CeceConfig) -> set[str]
def assert_nc_filenames(combo_dir: Path, config: CeceConfig) -> None
```

with INFO logging in the established style:
`testing expected filenames={'cece_20100101_010000.nc'}, found {'cece_20100101_000000.nc'}`.

### `validate_filenames` flag

The `Assertions` model gains:

```python
class Assertions(BaseModel):
    expected_nc_file_count: int | None = None
    validate_filenames: bool = True
```

- `true` (default): `test_nc_filenames` runs the assertion.
- `false`: `test_nc_filenames` is **skipped** with an explicit reason
  (`filename validation disabled by suite config`) — visible in the report
  as skipped, never silently green.

### Test structure

A third test alongside the existing two, same unwrapped pattern:

- `test_nc_filenames[<combo>]` — consumes the shared `driver_run` fixture
  (driver still runs once per combo); skips when the run failed
  (`driver run failed: ...`), skips when `validate_filenames` is false,
  otherwise asserts.

### The known driver bug

The checked-in `simple-maccity-suite.yaml` keeps `validate_filenames: true`
(explicitly, with a comment naming the bug). The default run is therefore
**expected to be red**: the three `test_nc_filenames` tests fail against the
current driver (`cece_20100101_000000.nc` found, `cece_20100101_010000.nc`
expected). This is the intended, accepted state — the failing tests are the
standing record of the driver bug, and they turn green the moment the driver
is fixed, with no suite edit required. `validate_filenames: false` remains
available for anyone needing a green run in the interim. The count assertion
is unaffected either way.

## Harness tests (mocked, `src/tests/combo_test_runner/`)

- `expected_nc_filenames`: maccity config → `{"cece_20100101_010000.nc"}`;
  multi-step config (6 h, `frequency_steps: 2`) → three names at hours
  2/4/6; output absent/disabled → empty set; token rendering is zero-padded.
- `assert_nc_filenames`: passes with the expected fabricated file; fails
  with a wrong-stamp file (the bug scenario), message names missing and
  unexpected files.
- Pipeline test: extend `test_maccity_pipeline` to fabricate the correctly
  stamped file and run the filename assertion.

## Non-goals

- No parsing of timestamps *out of* arbitrary filenames — expected names are
  rendered forward from the config and compared as strings.
- No per-combo overrides of `validate_filenames` (suite-level, like the rest
  of the assertions block).

## Acceptance criteria

- Harness tests pass (derivation, pass/fail paths, zero-padding), no docker.
- Integration, with the checked-in suite (`validate_filenames: true`): the
  three `test_nc_filenames` tests **fail** against the real driver with a
  message showing expected hour-1 vs found hour-0 names — the bug is caught,
  and this red state is the accepted outcome of the feature.
- Integration, with `validate_filenames: false` (a temp suite): the tests
  are reported as skipped with the explicit reason and everything else
  passes.
- Count assertion behavior unchanged; all non-filename tests still pass.
