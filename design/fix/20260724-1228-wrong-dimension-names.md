# Fix: AMIO writes data variables on synthetic dimension names (ex7, threads ≥ 2)

## Goal

Fix the AMIO netCDF writer so output variables always land on the
standard named dimensions regardless of write-task completion order:
ex7 (`amio_worker_threads: 2`) currently produces
`nox(time, lev, nox_dim2, lon)` with a detached `lat` coordinate, which
now fails the runner's `test_nc_variable_dimensions` and broke plotting
(`KeyError: 'lat'`). The fix lives in **AMIO** (the CECE submodule at
`extern/helm/libs/amio`), with a new AMIO unit test that fails before
and passes after; the harness is not modified — its new
dimension assertion is the acceptance gate: **ex7 passes**.

## Background (diagnosed this session; recorded in
design/feat/20260724-1013-run-multiple-suite-configs.md)

First real `ex[0-9]-suite.yaml` run (CAMS data pre-downloaded to
`<CECE>/data`): ex1–ex6 write standard `(time, lev, lat, lon)`
variables; only ex7 is malformed. ex1 shares ex7's `F360` grid — the
correlation is `amio_worker_threads: 2`, not the grid. The output file
contains **both** `nox_dim2` (720, used by `nox`) and `lat` (720,
bearing the coordinate values, attached to nothing).

## Pre-design audit (fresh facts, 2026-07-24)

1. **The define path resolves dimensions from "what exists right
   now"** (`NetCDF_Driver::write`, netcdf_driver.cpp ~452–600, all
   under `g_nc_driver_mutex`): when a variable is first written it is
   defined on the spot; for each of its dims the driver
   (a) special-cases rank-1 vars named `lon`/`lat`/`lev`/`time` to
   define a dim of that name; (b) for data variables, collects
   *existing* dims of matching length — exactly one → blind reuse,
   several → axis-role scoring that prefers canonical names
   (`lon`/`x`, `lat`/`y`, `lev`/`z`/`level`, `time`/`t`, roles from
   position: `d == rank-1` → X, `rank-2` → Y, `rank-3` → Z,
   `rank-4` → T); (c) **no match → a synthetic
   `<var>_dim<d>` is created**. `time` dims are created
   `NC_UNLIMITED`.
2. **Nothing ever creates a canonical dim for a data variable** — the
   canonical names only arise from the rank-1 coordinate special case,
   so a data variable defined *before* its coordinate finds no
   matching dim and mints the synthetic name. Definition order is the
   whole game.
3. **The client writes coordinates first, but asynchronously**:
   `CeceStandaloneWriter::WriteTimeStep` enqueues `amio_write` for
   `lon`, `lat`, `lev`, `time`, then the data fields, then
   `amio_flush`. Each write is a Worker_Pool task; with
   `amio_worker_threads >= 2` **completion order is nondeterministic**
   — `g_nc_driver_mutex` serializes the netCDF calls but does not
   order the tasks. The observed file is the interleaving where `nox`
   defined its dims while `lat`'s task had not yet run (its `lon` slot
   bound correctly because `lon`'s task had).
4. **This is an ordering bug, not a data race** — the earlier
   `describe_variable` lock fix (`fix/amio-thread-segv`, user-committed
   `42fe1ad`) completed the mutex discipline; nothing here corrupts
   memory. A synchronization band-aid (client waits on coordinate
   handles before enqueueing fields) would mask the driver's
   order-dependence and leave every other AMIO client exposed.
5. **Latent even at one thread**: blind single-length-match reuse can
   misbind (e.g. `lev` reusing a `time` dim when both have length 1 and
   the canonical dim doesn't exist yet) — the deterministic resolution
   below removes the order-dependence generally, not just for the
   racing case.
6. **Runner-side detection exists and is the gate**:
   `test_nc_variable_dimensions` (assertions.validate_dimensions,
   default on) fails ex7 today with the full signature in the report;
   no runner change is needed or wanted for this fix.
7. **AMIO working-tree state**: branch `fix/amio-thread-segv` plus the
   uncommitted `test_netcdf_describe_thread_safety` regression test and
   the `AMIO_ROOT` superbuild path fixes in `tests/unit/CMakeLists.txt`
   — this fix stacks on that state (the user commits; depth-first
   amio → helm → CECE).
8. **Build/verify machinery from the previous arc still applies**: the
   scratch superbuild (`-DAMIO_BUILD_TESTING=ON`,
   `-DCMAKE_DISABLE_FIND_PACKAGE_rapidcheck=ON` + Catch2 off) builds
   and runs unit tests in the cece-dev container; the checkout's
   canonical `build/` (mounted at `/work/build`) is what the runner's
   driver invocations execute, so ex7 verification needs that build
   refreshed after the AMIO change (no image rebuild; **never build
   ESMF**).

## Design — deterministic axis-role dimension resolution

### The fix (netcdf_driver.cpp, define path)

For each dimension `d` of a variable being defined, resolve in this
order (replacing today's match-then-synthesize flow; the rank-1
coordinate special case stays first and unchanged):

1. **Canonical-name lookup**: map `d` to its axis role exactly as the
   existing scorer does (X → `lon`, Y → `lat`, Z → `lev`, T → `time`).
   If a dim with the canonical name exists **and its length matches**
   (any length for the `NC_UNLIMITED` time record dim), bind it.
2. **Canonical-name creation**: if no dim of that name exists,
   **create it with the canonical name** (time as `NC_UNLIMITED`, as
   today) and bind it. This is the heart of the fix: the *first*
   writer of an axis — coordinate or data variable, whichever task
   runs first — establishes the canonical dim, and every later writer
   (including the rank-1 coordinate special case, which already looks
   up by name before defining) finds and reuses it. Definition order
   stops mattering.
3. **Fallback for non-canonical lengths**: if the canonical name
   exists with a *different* length (a second grid in one file —
   possible for AMIO generally, not the CECE layout), fall back to
   today's behavior: length-matching with axis-role scoring, then the
   synthetic `<var>_dim<d>` as the last resort. The fallback is kept
   for API generality, not exercised by CECE.

Roles apply to ranks ≥ 2 via the existing position convention
(`rank-1` → X … `rank-4` → T); rank > 4 leading dims keep the current
fallback path. Rank-1 data variables (the coordinate special case
aside) are untouched.

### Why not order the client's writes instead

`WriteTimeStep` could `amio_wait` the four coordinate handles before
enqueueing fields — but that serializes the writer around a driver
implementation detail, fixes only this client, and leaves the driver
order-dependent for every other AMIO consumer. The driver owns its
file layout; it should be deterministic under any task order (audit
item 4/5). (The client-side wait remains available as belt-and-braces
for CECE later; out of scope here.)

### AMIO unit test (red first)

`tests/unit/test_netcdf_dimension_names.cpp`, mirroring the
describe-thread-safety test's structure (driver compiled directly into
the binary, `conf::Config::from_string`, zarr/grib2 registrar stubs,
`g_amio_parent_comm` definition, no eckit):

- **The race, replayed deterministically**: open a write driver and
  write the **data variable first** — rank 4, extents
  `(1, 1, NY, NX)` — then the rank-1 coordinate variables
  `lon`/`lat`/`lev`/`time`, then close. This is exactly the task
  order the ≥ 2-worker pool can produce, without needing threads —
  red on current code (variable lands on `<var>_dim2`-style names),
  green after.
- **Assertions via the netCDF C API** on the reopened file: the data
  variable's dim names are exactly `time`, `lev`, `lat`, `lon`
  (`nc_inq_vardimid` + `nc_inq_dimname`); the file contains **no**
  `*_dim*` dimensions; each coordinate variable is bound to its
  same-named dim; `time` is the record (unlimited) dim.
- **Coordinates-first still works**: a second file written in the
  conventional order asserts the same postconditions (no regression
  for the path that worked).
- Registration in `tests/unit/CMakeLists.txt` following the
  thread-safety test's block (netCDF + MPI gate, `AMIO_ROOT` paths,
  labels `unit;write;netcdf4;dimensions;regression`, timeout 60).

### Verification

- **AMIO unit test**: build the new target in the scratch superbuild
  and run it red (pre-fix) then green (post-fix). Per the
  requirements, only the new test must be run — the 7 known
  pre-existing Not-Run unit targets and the PBT superbuild clash are
  upstream issues out of scope (documented in the thread-segv arc).
- **ex7 end-to-end** (the acceptance gate; examples requested by this
  design): rebuild the checkout's canonical `build/` in the cece-dev
  container, then `--suite-config=ex7-suite.yaml` — **all 7 tests
  pass**, including `test_nc_variable_dimensions`; repeat the suite
  **3× consecutively** (ordering bug: one pass is an interleaving,
  not proof). Inspect one output file: `nox(time, lev, lat, lon)`,
  no `nox_dim*` dims, `lat` attached.
- **No-regression sweep**: `simple-maccity-suite.yaml` integration
  (21/21) on the rebuilt driver; a spot-check example suite at default
  threads (ex3) stays green.
- Runner unit tests + `--dry-run` are unaffected (no runner changes)
  but run once as the standing gate; pre-commit hooks pass (runner
  repo: design-doc changes only).

## Constraints

- Fix and test live in the AMIO submodule only; **no harness
  code changes** (design docs updated at implementation:
  this doc's notes + the 1013 doc's conversational updates when ex7
  goes green).
- No CECE-side writer changes; no Dockerfile/image changes; never
  build ESMF.
- Never commit — the user commits (amio → helm → CECE, depth-first);
  the fix stacks on the uncommitted `fix/amio-thread-segv` state.
- Do not add driver bugs to the runner README's known bugs.

## Acceptance criteria

1. New AMIO unit test `unit.netcdf_dimension_names` fails on the
   pre-fix driver (data-variable-first write order yields synthetic
   dim names) and passes post-fix; the conventional order also passes.
2. `--suite-config=ex7-suite.yaml` passes 7/7 — including
   `test_nc_variable_dimensions` — on 3 consecutive runs, with
   `nox(time, lev, lat, lon)` and no synthetic dims in the output.
3. `simple-maccity-suite.yaml` integration stays 21/21 on the rebuilt
   driver; ex3 stays green.
4. No harness source changes; runner unit tests and
   `--dry-run` unaffected.

## Implementation notes

**Outcome: fixed — ex7 passes 7/7 (5 passed + 2 structural skips) on 3
consecutive runs; `nox(time, lev, lat, lon)`, no synthetic dims.**

- **Red was richer than the field observation**: on an empty file the
  data-first order broke *all four* slots, not just lat —
  `nox(nox_dim0, nox_dim0, nox_dim2, nox_dim3)`, including the
  audit-predicted blind reuse (the lev slot bound to the length-1
  `nox_dim0` via single-length-match). 7 expectations violated
  pre-fix; coords-first was green pre-fix, confirming the
  order-dependence diagnosis exactly.
- **The fix** (`netcdf_driver.cpp`, define path, one block): for
  non-coordinate variables of rank ≥ 2, resolve each slot's canonical
  axis name (position convention, same as the scorer: X→lon, Y→lat,
  Z→lev, T→time) **first** — bind it when present with the right
  length (any current length for the unlimited `time` record dim),
  claim it for creation when absent (the existing shared creation
  path already defines `time` as `NC_UNLIMITED`). The length-matching
  scan + axis scoring + synthetic fallback survive only for a
  canonical name existing at a *different* length (unconventional
  layouts) — and creation-over-scan also closes the square-grid
  hazard where a blind single-length match would steal the wrong
  same-length dim.
- **AMIO unit test**: `tests/unit/test_netcdf_dimension_names.cpp` +
  CMake registration (same pattern as the describe-thread-safety
  test: driver compiled in, `conf::Config::from_string`, registrar
  stubs, netCDF C API assertions). Asserts, for both write orders:
  `nox` on exactly `(time, lev, lat, lon)`; no `*_dim*` dimensions
  anywhere; each coordinate variable bound to its same-named dim;
  `time` is the record dimension. Red pre-fix (data-first),
  **green post-fix (both orders)**.
- **No collateral**: `unit.netcdf_describe_thread_safety` re-run green
  on the changed driver. Per the requirements, other AMIO suites were
  not run (their pre-existing gaps are documented in the thread-segv
  arc).
- **End-to-end**: checkout `build/` rebuilt in the cece-dev container
  (driver only; no image rebuild, no ESMF);
  `--suite-config=ex7-suite.yaml` **3× consecutive: 5 passed, 2
  skipped** each (skips are baseline-not-configured and the empty
  species parameter set — structural, not failures);
  `test_nc_variable_dimensions[base]` passed; output inspected:
  `nox(time, lev, lat, lon)`, dims {time 1, lev 1, lat 720,
  lon 1440}, no synthetic dims, `lat` attached.
- **AMIO diff (uncommitted, stacks on `fix/amio-thread-segv`)**: the
  canonical-resolution block in
  `src/drivers/netcdf/netcdf_driver.cpp`, the new test file, and its
  registration block in `tests/unit/CMakeLists.txt`. No
  harness code changes (per requirements); runner-side
  design docs updated only.

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

## testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- fix the dimension mismatch issue in amio identified in design/feat/20260724-1013-run-multiple-suite-configs.md
- add a unit test to amio
  - ensure the added unit test passes
  - no need to run other tests in amio
- ensure ex7 passes in the harness
  - no changes to the harness should be needed
- as you noted, this issue is probably related to amio threads >= 2

## conversational updates

- 2026-07-24: per user review, the role-mapping if-chain became a
  `static constexpr std::array<const char *, 4>` (`lon`, `lat`, `lev`,
  `time`) indexed by the slot's offset from the fastest-varying
  dimension. Re-verified after the refactor: both AMIO unit tests
  green, canonical driver rebuilt, ex7 5 passed + 2 structural skips.
- 2026-07-24: per user review, `kCanonicalAxisNames` moved to file
  scope with a new `is_canonical_axis_name(name)` helper replacing all
  three `meta.name ==/!= "lon" && …` chains in the define path (the
  rank-1 coordinate special case and both non-coordinate guards). The
  scorer's fuzzy `name == "lon" || name == "x"` matching is different
  logic (arbitrary foreign dim names, not the canonical four) and
  deliberately untouched. Re-verified: both AMIO unit tests green,
  driver rebuilt, ex7 5 passed + 2 structural skips.
