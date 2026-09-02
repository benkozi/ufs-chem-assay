# Feature: post-run assertions — NetCDF file count — plus logging

## Goal

Start the evaluation step promised in the main design: after each driver run,
assert on what the run actually produced instead of trusting exit code 0
alone. The first assertion is **NetCDF output file count**
(`expected_nc_file_count`); the structure it lands in must accommodate the
further assertions that will follow. **Each assertion is its own test** —
the driver runs once per combination in a shared fixture, and every
(combination × assertion) pair reports pass/fail independently. Alongside
it, introduce a proper logger for the runner with an environment-controlled
level.

## Current behavior

- A test passes iff the driver exits 0 within the timeout. A driver that
  exits cleanly having written nothing (or written too much) still passes.
- No logging; visibility is limited to the printed driver output.

## Design

### Assertions block in the suite config

Assertions are suite-level configuration, grouped under an `assertions` key
so future assertions have an obvious home (the notes call for multiple
assertions eventually):

```yaml
# simple-maccity-suite.yaml
config_path: ../cece/simple-maccity.yaml
timeout_s: 10
assertions:
  expected_nc_file_count: null   # null/absent = derive from the combo config
sweep:
  mapalgo: [bilinear, consd, passthrough]
```

```python
class Assertions(BaseModel):
    expected_nc_file_count: int | None = Field(None, ge=0)

class SuiteConfig(BaseModel):
    config_path: Path
    timeout_s: int
    assertions: Assertions = Assertions()   # section optional; defaults apply
    sweep: Sweep
```

The `assertions` section configures *what to expect*, never *how to run* —
there is deliberately **no `fail_fast` field**. Because each assertion is a
separate test (below), "run all assertions" is simply pytest's default
behavior, and fail-fast remains pytest's existing `-x` / `--maxfail`, per
the long-standing design decision. There is no opt-out flag either; the
"derive" default makes the count assertion meaningful without configuration.

### Test structure: one test per assertion

The single `test_driver_combo` unwraps into one test per assertion, each
parameterized by combo:

- `test_driver_execution[<combo>]` — the driver ran to completion with exit
  code 0 (the assertion previously implied by the test body).
- `test_nc_file_count[<combo>]` — this feature's file-count assertion.
- (future) `test_nc_timestamps[<combo>]` — expected to fail against the
  known driver stamp bug; as a separate test it can carry an `xfail` marker
  without poisoning the other assertions for that combo.

The **driver runs once per combination**, not once per test: execution moves
into a combo-parameterized, session-scoped fixture. Pytest instantiates a
parameterized session fixture once per param value and groups the dependent
tests, so each combo's container still launches exactly once.

**Fail-fast granularity (accepted trade-off)**: `-x` is global — the first
failed assertion anywhere stops the session, including other combos. There
is no per-combo "stop asserting, keep testing other combos" mode. Accepted:
fail-fast will in general not be used.

### Driver-run fixture: result capture, explicit failure detection, logging

Driver execution can fail outright — timeout, nonzero exit, docker error —
before any assertion can run. That must be detected *explicitly*, not
surface as incidental setup errors on downstream tests. So the fixture
**never raises**; it captures the outcome:

```python
class DriverRunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    combo: InstanceOf[Combo]           # isinstance-validated (enumeration machinery)
    out_path: Path                     # captured driver output (.out)
    combo_dir: Path                    # host-side combo directory
    config: CeceConfig                 # the generated config the driver ran
    error: InstanceOf[Exception] | None  # None on success; CalledProcessError /
                                         # TimeoutExpired / OSError otherwise
```

Data carriers are frozen **pydantic models**, not dataclasses, consistent
with the config models. Types pydantic cannot deep-validate (`Combo`,
`Exception`) use `InstanceOf` isinstance checks.

- `test_driver_execution` fails iff `result.error is not None` — execution
  failure is always reported by exactly one clearly named test.
- Assertion tests (`test_nc_file_count`, …) **skip with an explicit reason**
  (`driver run failed: <error>`) when `result.error` is set: the failure is
  already reported once, and a skip marks the assertion as *not evaluated*
  rather than failed or silently green.

The fixture logs around the run (using the logger below):

- INFO before launch: combo name, container yaml path, effective timeout —
  e.g. `running combo map-consd (timeout=10s)`.
- INFO on completion: outcome and wall-clock duration —
  `combo map-consd completed in 6.2s` /
  `combo map-consd FAILED after 10.0s: TimeoutExpired`.
  Failures are also logged at ERROR so they stand out at any log level.

### `expected_nc_file_count` modes

- **`None` (default) — derived.** The expected count is computed from the
  *generated combo config* (the `CeceConfig` the driver actually ran):

  ```
  n_steps  = (end_time - start_time) / timestep_seconds
  expected = n_steps // output.frequency_steps
  ```

  using `driver.start_time`, `driver.end_time`, `driver.timestep_seconds`
  (ISO-8601 strings parsed with `datetime.fromisoformat`), and
  `output.frequency_steps`. If the `output` section is absent or
  `output.enabled` is false, the derived expectation is 0.

  For the initial suite: 1 hour / 3600 s = 1 step, `frequency_steps: 1` →
  1 file, which matches observed driver behavior (one
  `cece_20100101_000000.nc` per combo).

- **Explicit int.** The count is asserted exactly as given; `0` means *no*
  NetCDF files are expected.

**Found count** = the number of `*.nc` files directly in that combination's
output directory (no recursion — everything the driver writes for a combo
lands flat in its directory).

The assertion is evaluated only for a successful driver run; when execution
failed, the assertion test skips explicitly (see the fixture section above)
and the failure is reported by `test_driver_execution`.

### Assertion module

New `src/assertions.py`, keeping test bodies thin and giving future
assertions a home:

```python
def assert_nc_file_count(combo_dir: Path, config: CeceConfig, expected: int | None) -> None
```

Derives `expected` when `None`, counts, logs (below), then asserts equality
with a pytest-friendly failure message naming the combo directory, expected,
and found counts.

### Logging

- Standard `logging` with per-module loggers under a shared
  `ufs-chem-assay` namespace.
- **Level from the environment**: new setting `log_level`
  (`CECE_LOG_LEVEL`, default `INFO`) on `settings.py`, e.g. `DEBUG` vs
  `INFO`. Applied once at session start (conftest) when configuring the
  namespace logger's handler.
- **Format includes a timestamp with seconds** (`%(asctime)s`), plus level
  and logger name.
- What gets logged:
  - the assertion itself, INFO, in the form from the notes:
    `testing expected_nc_file_count=1, found 1 files`
  - the derivation inputs when deriving, including the **timestep in
    seconds** — e.g.
    `deriving expected_nc_file_count: timestep_seconds=3600 n_steps=1 frequency_steps=1`
    (INFO; finer detail may sit at DEBUG as assertions grow).
- Visibility follows pytest's log capture: failure reports include
  "Captured log call" automatically; live output via `-s` or pytest's
  `log_cli` options.

## Non-goals

- No per-combination assertion overrides (suite-level only; per-combo
  expectations can come with the richer suite configuration future work).
- No content inspection of the NetCDF files or `.out` parsing — count only.
  Later assertions extend the `Assertions` model.

## Acceptance criteria

- Plain `uv run pytest`: 6 tests (`test_driver_execution` +
  `test_nc_file_count`, × 3 combos), all passing, with exactly one container
  launch per combo; each count test's log shows
  `testing expected_nc_file_count=1, found 1 files` (derived mode).
- A suite with `expected_nc_file_count: 0` fails `test_nc_file_count`
  against a driver run that produces files, with a clear assertion message
  (and passes when nothing is produced, e.g. output disabled).
- When a driver run fails (e.g. forced timeout), `test_driver_execution`
  fails for that combo and `test_nc_file_count` **skips** with a
  `driver run failed: ...` reason — never errors, never passes silently.
- Fixture logging shows launch and completion lines (with durations) per
  combo; `CECE_LOG_LEVEL=DEBUG` raises verbosity; default INFO stays concise.

## Ripple effects

- Main `design.md`: the "Pytest integration" section's "one test,
  parameterized by combo" becomes "one test per assertion, parameterized by
  combo, sharing a per-combo driver-run fixture"; the assertions block joins
  the suite-configuration section.
- README: mention the new test ids and the skip-on-execution-failure
  behavior.

## Resolved

- **Derivation formula confirmed**: `n_steps // frequency_steps`, no `+1`.
  The driver writes one file per `frequency_steps` timesteps — for the
  initial suite, exactly one file at the end of the first (only) timestep.
  The observed `cece_20100101_000000.nc` stamp does *not* indicate a t=0
  write: it is a **known bug in the driver under test** — that file should
  be stamped at hour 1 (`cece_20100101_010000.nc`, start time + one
  timestep). File *count* is unaffected, so this assertion passes despite
  the bug.

## Next assertion (planned, not this feature)

Output **timestamp verification**: assert the `.nc` filenames carry the
expected timestamps (first file at `start_time + timestep_seconds`, then
every `frequency_steps × timestep_seconds`). Against the current driver this
assertion is expected to *fail* on the hour-0 stamp, catching the bug above —
that is its purpose. It slots into the `Assertions` model as a second field.
