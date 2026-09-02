# Feature: run CECE examples as suites (checked-in example suite configs)

## Goal

Make each shipped CECE example config runnable as an **ordinary suite**:
a checked-in suite YAML per example (`ex1-suite.yaml` …
`ex7-suite.yaml`) in the built-in suite folder, each pointing its
`config_path` at the corresponding
`examples/config/cece_config_ex*.yaml` in the CECE checkout. Selection,
execution, assertions, stats, plots, baselines-someday, timeouts —
everything is the existing single-suite pipeline; an example run is
just `--suite-config ex7` away. **Nothing exceptional**: no new flag,
no combo-machinery extensions, no generation step. The work reduces to
(1) `CeceConfig` covering the examples' schema, (2) sweep-less suites
enumerating one combination, and (3) writing the seven suite files
plus the one resolution mechanism they need to reach an external
checkout portably.

Why this beats the verbatim `--run-examples` path (which stays,
untouched, for checkout-native verbatim runs):

- **Real verification via existing tests**: derived file-count and
  filename assertions, per-combo stats, plots, report rows — free.
- **Schema coverage**: the example configs load through `CeceConfig`
  every time their suite runs.
- **Suite-native knobs**: per-example `timeout_s`, assertions, and
  (later) `baseline_comparisons` live in ordinary suite files — no
  scoping rules, no overrides hooks.
- **Output isolation**: `build_config` already redirects
  `output.directory` into the combo directory, ending the
  `cece_output/` checkout pollution the verbatim path has.

## Pre-design audit (fresh facts, 2026-07-24)

Verified against the current checkouts (runner `feat/initial-impl`,
CECE `fix/amio-thread-segv` working tree).

1. **`CeceConfig` loads 5 of 7 examples today.** Programmatic check:
   ex2–ex6 validate clean; ex1 fails (13 errors) and ex7 fails (23
   errors), on exactly five missing schema features —
   `driver.log_file`, `driver.amio_worker_threads`,
   `driver.grid.grid_name` (with `nx`/`ny` then absent),
   `stream.cadence`, `stream.data_model`.
2. **Driver semantics for the missing fields** (from `src/main.cpp`,
   `src/core/cece_config_parser.cpp`, `src/driver/cece_driver_facade.cpp`,
   `axis/src/topology/named_grid_registry.cpp`):
   - `grid_name`: parameterized, **not a closed set** — the axis
     registry parses family letter + any positive integer (`F360`,
     `R180`, …; families O/F/N/R plus registered NOAA `grid<num>`
     names), and CECE's `main.cpp` accepts only families `F` and `R`
     as structured target grids (nx=4·N, ny=2·N). So no enum is
     possible; the strict equivalent is a **pattern-constrained
     string** (see Phase 1). When `nx`/`ny` are also declared the
     driver validates them against the name and errors on mismatch.
   - `cadence` (per stream): `hourly` | `weekly` | `monthly`
     (case-insensitive; absent = no cadence mechanism) — closed set,
     enum.
   - `data_model` (per stream): `classic` | `enhanced` | `auto`
     (case-insensitive; invalid values warn and fall back to auto) —
     closed set, enum.
   - `amio_worker_threads`: int ≥ 1 (values < 1 warn and default to 1);
     parsed under **both** `driver:` and `output:` nodes — only the
     `driver:` form appears in examples and only it is modeled here.
   - `log_file` (driver): output tee'd to this path; relative paths
     resolve against the container cwd (`/work`) — i.e. today an
     example writes `/work/cece.log` into the checkout (ex1 and ex7
     set it).
   - The driver's silent 4×4 default grid (neither `grid_name` nor
     `nx`/`ny` given) is **a known driver bug with a fix in flight**;
     the model's either/or grid validation simply matches the intended
     contract.
3. **`run_driver` already fits examples**: it mounts the CECE root at
   `/work` and runs with `-w /work`, so the examples' relative
   `data/...` stream paths resolve exactly as `run-example.py` resolves
   them. All 7 examples have an `output:` block, so `build_config`'s
   `assert config.output is not None` holds.
4. **`config_path` cannot reach the checkout portably today**: relative
   values resolve against the suite file's directory or the global
   `CECE_CONFIG_SEARCH_PATH` — but the example configs live in the
   *external* CECE checkout at a machine-specific location
   (`settings.root_dir`, from `.env`/flag), and setting
   `CECE_CONFIG_SEARCH_PATH` globally would break the existing maccity
   suites' `../cece/...` paths. Checked-in example suites need a
   root-dir-anchored resolution (Phase 3).
5. **Sweep-less suites don't exist yet**: `SuiteConfig.sweep` is
   required, and `enumerate_combos` returns `[]` when no dimensions
   attach — an empty-sweep suite would enumerate zero combinations and
   run nothing. (A trivial one-value sweep per example suite could
   dodge this, but it misdescribes the intent and adds a fake
   dimension to names/CSVs — rejected as a hack.)
6. **Per-suite knobs already solve the earlier drafts' wrinkles**:
   `timeout_s` is a required suite field (each example sets its own —
   no session-cap collision with maccity's 10 s), and assertions /
   analysis / plotting / future `baseline_comparisons` are per-suite
   config — no scoping rules needed.
7. **Example data is a prerequisite, not suite machinery**: the plain
   combo session has no download step. The checkout's own entrypoint
   (`examples/download-example-data.py`, also reachable via
   `--run-examples`) fetches per-example data; the suite files
   document it, and a missing input is an honest driver failure.
8. **ex7 remains an expected failure** (CAMS-TEMPO weights not publicly
   downloadable; datasets deliberately not substituted) — its suite
   runs and fails honestly at `test_driver_execution`; dependent tests
   skip exactly as any failed combo's do.

## Design

### Phase 1 — `CeceConfig` covers the example schema (red-green)

Red: a parametrized unit test loads fixture YAML snippets using each new
field (and a full-file test asserting ex1/ex7-shaped configs validate);
fails against the current model. A `build_config` test asserts
`driver.log_file` always points into the combo output directory —
whether the base config sets it, sets it relative, or omits it.

Green — extend `src/models/cece_config.py`, all with
`Field(description=...)`:

- New enums: `Cadence` (`hourly`, `weekly`, `monthly`) and `DataModel`
  (`classic`, `enhanced`, `auto`) — lowercase members exactly as the
  examples write them (the driver lowercases; the model stays strict,
  matching the existing Mapalgo/VdistMethod posture of canonical values
  only).
- `Stream`: `cadence: Cadence | None = None`,
  `data_model: DataModel | None = None`.
- `Driver`: `log_file: str | None = None`,
  `amio_worker_threads: int | None = Field(None, ge=1)` (the driver's
  "< 1 warns and runs as 1" branch is a silent correction the model
  refuses, consistent with rejecting silently-defaulting values
  elsewhere).
- `Grid`: `grid_name: str | None = Field(None, pattern=r"^[FR][1-9][0-9]*$", ...)`
  — a pattern, not an enum, because the name space is parameterized
  (family × any positive integer; audit item 2). Deliberately narrower
  than the axis registry (no O/N families, no `grid<num>`): those are
  exactly the names `main.cpp` rejects as structured CECE target
  grids. `nx`/`ny` become `int | None = None`; a model validator
  requires **either** `grid_name` **or** both `nx` and `ny` — matching
  the driver contract once its silent-4×4-fallback bug fix lands
  (audit item 2). The F/R dimension arithmetic stays the driver's job —
  the model does not re-derive nx/ny from the name.
- `build_config` additionally **always sets** `driver.log_file` to
  `<container combo dir>/cece.log` — for every combo of every suite,
  regardless of what the base config sets (ex1/ex7's relative
  `cece.log` would otherwise land in the checkout at `/work/cece.log`;
  configs without the field simply gain the log artifact). The driver
  tees, so stdout capture (`.out`) is unaffected; each combo directory
  now carries a consistent `cece.log` alongside its other artifacts.

### Phase 2 — sweep-less suites enumerate one combination (red-green)

Red: unit tests — a `SuiteConfig` without a `sweep:` key loads; an
empty sweep enumerates exactly one combination; its name and id are
stable; `build_config` on it applies only the output override.

Green:

- `SuiteConfig.sweep: Sweep = Field(default_factory=Sweep, ...)` —
  absent or empty means "run the base config as the single
  combination".
- `enumerate_combos`: when no dimensions attach, return
  `[Combo(values=())]` instead of `[]`.
- `Combo.name`: return `"base"` for an empty values tuple (an empty
  string is unusable as a pytest id and directory-name seed);
  `combo_id` stays the content hash. `write_combos_csv` writes zero
  dimension rows for it — acceptable; `run.yaml` records the resolved
  suite including its `config_path`.

### Phase 3 — checked-in example suites + root-dir path anchor (red-green)

Red: unit tests — a suite whose `config_path` starts with the
`${CECE_ROOT_DIR}` token resolves against `settings.root_dir`, errors
clearly when `root_dir` is unset, and existing relative/absolute
behavior is untouched; a selection test finds the new suites.

Green:

- **Resolution anchor**: `SuiteConfig.from_yaml` accepts the CECE root
  and expands a literal `${CECE_ROOT_DIR}` prefix in `config_path`
  (sessionstart passes `settings.root_dir`; using the token with
  `root_dir` unset is the standard "pass `--cece-root-dir` or set
  `CECE_ROOT_DIR`" usage error). Suite-dir-relative,
  `CECE_CONFIG_SEARCH_PATH`, and absolute behavior are unchanged
  (audit item 4).
- **Seven suite files** in `src/tests/config/suite/`:
  `ex1-suite.yaml` … `ex7-suite.yaml`, each an ordinary suite —

  ```yaml
  # Runs CECE's shipped ex7 example as a suite (see examples/README.md
  # in the CECE checkout). Prerequisite: example data downloaded, e.g.
  #   python3 <CECE>/examples/download-example-data.py --example ex7
  name: ex7
  config_path: ${CECE_ROOT_DIR}/examples/config/cece_config_ex7.yaml
  timeout_s: 120  # per-example, tuned per suite file
  ```

  No `sweep:` (Phase 2), default assertions (derived file count +
  filename validation — real checks), default analysis/plotting.
  Per-example notes go in the file header (ex7: expected failure on
  the missing CAMS data; ex1: data availability decides). Timeouts
  tuned per example from observed run times.
- **Selection**: nothing to do — the built-in suite directory is
  always searched, so `--suite-config ex7-suite.yaml` (or a regex like
  `'ex7.*'`; the selector is a **fullmatch**, so a bare `ex7` does not
  match the filename) selects it; the default suite stays
  `simple-maccity-suite.yaml`.
- **Docs**: README gains an "examples as suites" subsection (selection
  command, data prerequisite, relation to `--run-examples`);
  `design/design.md` updated.

### Out of scope (deliberately)

- Auto-generation of the suite files (they are seven small, stable,
  reviewable files; regeneration machinery can come back if the
  example set churns).
- Any new pytest flag; any change to the `--run-examples` verbatim
  flow or the CECE checkout.
- Sweeps in the example suites (add one to a suite file later —
  they're ordinary suites, it just works).
- Baseline entries (future arc: add `baseline_comparisons` to the
  relevant `exN-suite.yaml` — ordinary suite mechanism, no new
  design needed).
- Multi-suite sessions ("run all suites" stays the outer loop of
  single-suite invocations).

## Verification

- Unit tests (new + existing) via `uv run pytest src/tests/ufs_chem_assay`.
- All checked-in suites pass `--dry-run` — now including
  `ex1-suite.yaml` … `ex7-suite.yaml` (requires `root_dir` for the
  `${CECE_ROOT_DIR}` expansion; `.env` provides it here and CI has the
  checkout).
- `simple-maccity-suite.yaml` without `--dry-run` (driver integration).
- Examples run only when requested; when requested:
  `--suite-config ex3` (and a spot-check of others) through the full
  pipeline — assertions, stats, plots in the standard session
  artifacts; ex7 failing honestly with dependent tests skipping.
- Pre-commit hooks pass.

## Constraints

- pydantic models with `Field(description=...)`; no `Any` beyond the
  sanctioned `PhysicsScheme.options` exception.
- No CECE-checkout modifications.
- Never commit — the user commits.
- README.md and `design/design.md` updated as part of implementation.

## Acceptance criteria

1. `CeceConfig.from_yaml` loads all 7 shipped example configs.
2. A suite YAML without `sweep:` loads and enumerates exactly one
   combination named `base`.
3. `ex1-suite.yaml` … `ex7-suite.yaml` exist in the built-in suite
   directory; `--suite-config ex7` selects and runs that example
   through the unchanged single-suite pipeline (generated config,
   `.out`, `cece.log`, stats CSV, plots, report rows, `run.yaml`,
   `combos.csv`), with nothing written into the CECE checkout
   (`cece_output/`, `cece.log`).
4. `${CECE_ROOT_DIR}` in `config_path` resolves against
   `settings.root_dir` and produces the standard usage error when the
   root is unset; existing suites' resolution is byte-for-byte
   unchanged.
5. All suites — including the seven new ones — pass `--dry-run`;
   maccity integration stays green; `--run-examples` behavior is
   untouched.

## Implementation notes

**Outcome: implemented 2026-07-24; all phases green.**

- **Phase 1 (schema)**: `Cadence`/`DataModel` enums, `Stream.cadence`/
  `Stream.data_model`, `Driver.log_file`/`Driver.amio_worker_threads`
  (ge=1), `Grid.grid_name` (`^[FR][1-9][0-9]*$`) with `nx`/`ny` now
  optional behind the either/or model validator — all in
  `src/models/cece_config.py`. All **7 real example configs validate**
  (re-checked against the checkout; previously 5 of 7).
- **Universal log redirect**: `build_config` unconditionally sets
  `driver.log_file = <output_directory>/cece.log` (posix join), per the
  conversational update — every combo of every suite now carries a
  `cece.log`; verified in the maccity integration run.
- **Phase 2 (sweep-less)**: `SuiteConfig.sweep` defaults to an empty
  `Sweep`; `enumerate_combos` returns `[Combo(values=())]` when no
  dimensions attach; `Combo.name` is `"base"` for the empty tuple. The
  prior `test_empty_sweep_yields_no_combos` asserted the old `[]`
  behavior and was rewritten to assert the identity combo.
- **Phase 3 (anchor + files)**: `SuiteConfig.from_yaml` gained
  `root_dir`; a `config_path` whose first component is the literal
  `${CECE_ROOT_DIR}` resolves against it (ValueError naming the
  standard root-dir remedy when unset — sessionstart now converts
  ValueError as well as FileNotFoundError to UsageError and passes
  `settings.root_dir`). Seven suite files checked in with per-example
  headers (data prerequisite; ex7 expected-failure note); timeouts
  start at 300 s uniformly (= the settings default cap) pending
  observed per-example run times.
- **Design correction found while implementing**: `select_suite` is a
  fullmatch — `--suite-config ex7` does *not* select `ex7-suite.yaml`;
  use the filename or `'ex7.*'`. The Design section was fixed
  accordingly.
- **Tests**: new `ufs_chem_assay/test_example_suites.py` (schema
  fields red-green, log-redirect 3-case parametrization, anchor
  resolution/error/priority, per-example checked-in-suite validation
  against a fabricated checkout tree); `test_combos.py` identity-combo
  tests; `test_suite_config.py` sweep-optional test; `suite_dir`
  fixture in the harness conftest.
- **Coverage guard (added post-implementation, user request)**:
  `test_every_runnable_example_has_a_suite_file` globs the real
  checkout's `examples/config/cece_config_*.yaml` (deliberately
  broader than the `--run-examples` `ex*` glob, so non-`ex`-prefixed
  arrivals are seen too) and demands a checked-in `<id>-suite.yaml`
  for every id outside `UNCOVERED_EXAMPLES = {"advanced", "megan3"}`
  (shipped but not runnable — remove an entry to demand its suite).
  The one test in the module touching the checkout; skips cleanly
  when `CECE_ROOT_DIR` is unset or missing.
- **Verification**: 201 unit tests pass; `--dry-run` green for all 10
  checked-in suites (maccity 18, exhaustive 2×1440, ex1–ex7 6 each);
  `simple-maccity-suite.yaml` integration **18/18** without dry-run
  (docker driver); pre-commit hooks all pass. Examples themselves not
  executed this session (run only when requested); the ex suites'
  driver-level outcomes (ex1 data availability, ex7 expected CAMS
  failure) remain to be observed on a requested run.
- **Docs**: README Running section (example-suite invocation with data
  prerequisite), `--suite-config` option (sweep-less + anchor
  semantics, fullmatch caveat), Results tree (`cece.log`);
  `design/design.md` Suite configuration section updated with the
  optional sweep, the anchor, and the universal log redirect.

---

# appendix: original notes

## always do

- include updating design.md as part of the implementation
- update the harness tests (`src/tests/ufs_chem_assay`) in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- use pydantic models as opposed to dataclasses
  - all pydantic fields should include a description like `... = Field(description="<description content here>", ...`
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original design sections when refining design docs - create an appendix
  - summarize conversational updates in the appendix following original refinement target
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code - the user always commits

### testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- we need to be able to run CECE examples as suites
- let's have the suites auto-generated from the CECE example YAML files
- no need for sweeps or anything
- new flag for test examples will be `--as-suites`
- keep it simple for now

## conversational updates

- 2026-07-24: `grid_name` cannot be an enum — the axis
  `NamedGridRegistry` parses family + any positive integer (and
  `main.cpp` accepts only families F/R as target grids), so the model
  uses a pattern-constrained string (`^[FR][1-9][0-9]*$`) instead.
- 2026-07-24: scope expanded from execution-only to the full pipeline —
  assertions, stats, plots; baselines anticipated.
- 2026-07-24: the driver's silent 4×4 grid fallback is a known driver
  bug with a fix in flight; the model's either/or grid validation is
  framed as matching the intended contract, not compensating.
- 2026-07-24: architecture pivot #1 — examples as identity combos
  appended to the current session via `--as-suites` (superseded, see
  below).
- 2026-07-24: **architecture pivot #2 (final): checked-in suite files,
  nothing exceptional.** One ordinary suite YAML per example in the
  built-in suite folder; expect them to exist and run them via the
  unchanged single-suite pipeline. This supersedes auto-generation,
  the `--as-suites` flag, `Combo` label/config_path extensions, and
  all scoping rules — per-suite `timeout_s`/assertions cover those
  needs natively. Work reduces to the `CeceConfig` schema extension,
  sweep-less suite enumeration, a `${CECE_ROOT_DIR}` `config_path`
  anchor (checked-in suites must reach the external checkout
  portably), and the seven suite files. The original "new flag
  `--as-suites`" requirement is retired: suite selection
  (`--suite-config ex7`) replaces it.
- 2026-07-24: **`log_file` redirect is universal**, not conditional —
  `build_config` always sets `driver.log_file` into the combo's output
  directory for every combo of every suite, so each combo carries a
  `cece.log` artifact and no config can write a log into the checkout.
- 2026-07-24 (post-implementation): **example-coverage test added** —
  every shipped example config must have a checked-in suite file,
  excluding `advanced` and `megan3` for now (they do not run); the
  exclusion list is the place a newly-runnable example graduates from.
