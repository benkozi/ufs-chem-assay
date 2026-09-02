# Feature: opt-in example execution (`--run-examples`) + failure report

## Goal

Run the CECE driver's shipped example configs
(`<CECE_ROOT_DIR>/scripts/examples/cece_config_ex*.yaml`) as first-class
pytest tests, **off by default** behind `--run-examples`, with input data
fetched by the shipped `scripts/data_download/download_ex*.sh` scripts.
Examples run **verbatim** — they are external artifacts under test, and
honest failures are expected and reported, never masked. The deliverable
beyond the tests is a **report** in `design/artifacts/` covering every
download and execution failure plus consolidation recommendations —
**CECE itself is not modified**; fixes happen later, in that repo, guided
by the report.

## Relation to the superseded design

This supersedes the July 17 examples design
(`20260717-0938-optional-run-examples.md`, refined but never implemented
and since removed from `design/feat/`). Two things changed:

1. **The runner moved to its own repository.** Examples and download
   scripts now live in the *external* CECE checkout, located via
   `settings.root_dir` — discovery, downloads, and execution all require
   it, and this repo cannot carry script fixes.
2. **Report-only posture.** The old plan's "fix download scripts in
   place" task is replaced by requirement: *no modifications to CECE at
   this point*. The raw note's carried-over "attempt to fix download
   scripts if possible" line is resolved accordingly: fixes are
   *diagnosed and prescribed* in the report, not applied.

## Pre-design audit

Re-verified 2026-07-20 against the CECE checkout on branch
`fix/all-examples-pass` @ `3f546b1` (content-identical to its `helm`
branch point; presumably where the report-prescribed fixes will later
land).

1. **All six `scripts/examples/` configs still use the stale
   `cdeps_inline_config:` schema** (0 of 6 use `cece_data:`, which is all
   the parser reads); no `driver:`/`output:` blocks. Expectation: vacuous
   runs or failures — the suite documents that truth; the report carries
   it.
2. **Download scripts changed since the July 17 audit**: they now fetch
   via `./scripts/download_hemco_data.py` (a python helper) instead of
   raw curl, still `cd`-assuming the **CECE repo root** (`mkdir -p data`,
   `./scripts/...` relative paths) — so the runner must invoke them with
   `cwd=settings.root_dir`.
3. **Duplication confirmed**: `scripts/download_ex{1..6}.sh` duplicates
   `scripts/data_download/download_ex{1..6}.sh` — ex2–ex6 byte-identical,
   **ex1 differs** between the copies. Per the notes,
   `scripts/data_download/` is authoritative for this feature.
4. **Root `examples/` has diverged further**: now `cece_config_ex1..7` +
   `cece_config_advanced.yaml` + its own README — a second, differently
   versioned example set. Consolidation is a report recommendation, not
   work done here.
5. **The `hourly` data gap persists**: ex1/ex2 reference hourly-scalefactor
   data no `data_download` script fetches — a permanent honest failure
   unless a source is found (report item).
6. **Runner-side context is new**: `.env` supplies `root_dir` on this
   machine; the collection-time root_dir guard currently keys on
   `driver_run` items only; sessionstart always loads a suite (the
   default selector) even for suite-independent example runs — harmless,
   already exercised by harness-only runs.

## Design

### Test module: `src/tests/test_examples.py`

- **Discovery**: glob `scripts/examples/cece_config_ex*.yaml` under
  `settings.root_dir` at collection, parameterized by file stem
  (`test_example_execution[cece_config_ex3]`); new files join
  automatically. With `root_dir` unset, discovery yields nothing and
  gating (below) reports why.
- **One test per example**: run the driver in docker against the file
  verbatim — `<driver> /work/scripts/examples/<name>.yaml`, workdir
  `/work` (matches the scripts' repo-root assumption and the configs'
  relative `data/...` paths). Exit 0 is the sole pass criterion. Captured
  stdout/stderr → `<output-root>/examples/<stem>.out` (the `examples/`
  subdirectory keeps combo-id directories unambiguous).
- **Verbatim means no pydantic**: example configs are inputs under test,
  deliberately not loaded through `CeceConfig` (they would fail strict
  validation — audit #1 — and the point is testing the driver against its
  own shipped files). Documented exception to the config-construction
  rule, which governs *generated* configs only.
- **Gating**, in order:
  1. no `--run-examples` → skip: "examples disabled; pass --run-examples".
  2. `--dry-run` → skip: "dry run: driver execution skipped" (no docker,
     no downloads — every suite and example run must pass `--dry-run`).
  3. `--run-examples` without a usable `root_dir` → `UsageError` at
     collection time: the existing root_dir guard in
     `pytest_collection_modifyitems` extends to fire when example tests
     are collected with `--run-examples` active (examples don't request
     `driver_run`, so today's guard would miss them and docker would fail
     obscurely later).
- **No expected-failure masking**: failing examples fail red. They carry
  no combo_id and stay out of `test-report.csv` (combo-keyed by design);
  the examples report (below) is their record.
- **Timeout**: `settings.run_timeout_s` per example (suite `timeout_s`
  governs combos only).

### `src/examples.py`: discovery, downloads, structured results

Logic testable without docker; pydantic models with described fields:

- `discover_examples(root_dir) -> list[Path]` — the glob, sorted.
- `download_example_data(root_dir) -> list[DownloadResult]` — run every
  `scripts/data_download/download_ex*.sh` with `cwd=root_dir`
  (audit #2), capturing output, never raising; one
  `DownloadResult{script, returncode, output}` per script. Failures log
  at WARNING and don't abort — a broken download must not hide other
  examples' results (the affected example then passes or fails on its own
  merits). Overwrite/caching behavior is the scripts'; not wrapped.
- `ExampleRunResult{example, returncode, out_path}` — one per executed
  example, collected by the test fixture.
- A session-scoped `example_data` fixture (requested only by example
  tests, so combo-only runs never pay for it) runs the downloads once
  when `--run-examples` is active and not `--dry-run`.

### Session-end examples report (runner artifact)

When examples executed, a session-end step writes
`<output-root>/examples/examples-report.md`: per download script —
returncode and trailing output on failure; per example — pass/fail,
returncode, `.out` path. Structured inputs are the pydantic results
above, so the writer is unit-testable. This makes every future
`--run-examples` run self-documenting, not just the first one.

### The `design/artifacts/` deliverable (implementation-time)

`design/artifacts/20260720-0950-examples-report.md`, authored from a real
`--run-examples` session on this machine (curating the generated report
plus inspection of each failure — the note's "inspect the script
output"). Contents:

1. **Download failures**: per script — what happened, root cause where
   determinable, prescribed fix (to be applied in CECE later).
2. **Execution failures**: per example — outcome, driver output excerpt,
   root cause bucket (stale `cdeps_inline_config` schema, missing
   `hourly` data, other).
3. **Consolidation recommendations** (requirement): the duplicate
   `scripts/download_ex*.sh` vs `scripts/data_download/` sets (ex1's
   copies differ — flag which is current); the diverged root `examples/`
   (ex1–7 + advanced, own README) vs `scripts/examples/` (ex1–6, stale
   schema); a single-source-of-truth proposal (one example directory, one
   download entry point, schema-current configs, CI-exercised via this
   flag).

No CECE file is touched; `design/` stays excluded from the yaml/trimming
pre-commit hooks, so the report and artifacts are preserved verbatim.

## TDD plan (red first)

- `discover_examples`: finds the six yamls sorted by stem under a tmp
  root; empty when none exist.
- `download_example_data` (mocked `subprocess.run`): invokes every
  `download_ex*.sh` with `cwd=root_dir`; a nonzero script yields a
  `DownloadResult` carrying its output plus a WARNING log, later scripts
  still run; nothing raises.
- Report writer: given fabricated `DownloadResult`/`ExampleRunResult`
  lists, writes the markdown with failures called out; empty-inputs case.
- Models: described fields, unknown-key rejection.
- Gating via pytest subprocess (neutral-cwd harness style, no docker):
  - no `--run-examples` → example tests all skip; no downloads.
  - `--run-examples --dry-run` → still all skip, no downloads.
  - `--run-examples` (no dry-run) with no root_dir configured →
    collection-time `UsageError` naming `--cece-root-dir`/`CECE_ROOT_DIR`.
- Existing guard tests stay green (the guard extension must not affect
  combo-only or harness-only collections).

Real example execution (docker + network downloads) happens only when
explicitly requested, per the standing testing rules — it is the
implementation-time step that produces the `design/artifacts/` report.
Standing integration check: `simple-maccity-suite.yaml` real run stays
green (examples off by default must not perturb combos).

## Ripples (standing process rules)

- **`design.md`**: `--run-examples` in the pytest-options list; example
  tests, the `examples/` output subdirectory, and `examples-report.md` in
  the layout; `examples.py` in the code layout; the verbatim-execution
  exception beside the config-construction rule; the root_dir guard
  description gains the examples condition.
- **`README.md`**: `--run-examples` in Options — data downloaded from the
  CECE checkout's scripts, examples may legitimately fail, report
  location; the invocation in Running.
- Pydantic models with described fields; mypy/pre-commit stay green; TDD
  red-green.

## Acceptance criteria

- `uv run pytest src/tests/test_examples.py` (no flag) collects the six
  example tests and skips them all — no docker, no network, no root_dir
  needed.
- `--run-examples --dry-run` also skips everything, downloads included;
  `--run-examples` without root_dir fails fast at collection with the
  standard message.
- `uv run pytest src/tests/test_examples.py --run-examples` (run only
  when requested) downloads what the scripts can fetch (cwd = the CECE
  checkout), executes all six examples in docker, writes
  `examples/<stem>.out` and `examples/examples-report.md` under the
  output root, and reports pass/fail honestly.
- `design/artifacts/20260720-0950-examples-report.md` exists, covering
  every download/execution failure with root causes and prescribed fixes,
  plus the consolidation recommendations — and no file under
  `CECE_ROOT_DIR` is modified.
- Harness suite green without docker; combo behavior and `test-report.csv`
  unchanged; both exhaustive suites and the default suite still pass
  `--dry-run`.

## Implementation notes

- Implemented as designed. One structural choice: the session report is
  written from a fixture teardown in `test_examples.py`
  (`example_results` yields the accumulator list, teardown calls
  `write_examples_report`) rather than a conftest sessionfinish hook —
  the examples machinery stays localized and the conftest stash keys stay
  private. Gating lives in the session `example_downloads` fixture, so
  fixture setup can never trigger downloads on a gated-off run (verified
  by the marker-file harness test).
- **Real-run findings differ from the audit's expectations** — see the
  deliverable, `design/artifacts/20260720-0950-examples-report.md`:
  downloads are **6/6 ok** (the `download_hemco_data.py` rewrite
  evidently fixed the old breakage; the "inspect/fix scripts" concern is
  moot), while executions are **0/6** — every example dies on the same
  uncaught `YAML::TypedBadConversion` → abort (exit 133), the stale-schema
  bucket, before the `hourly` data gap can even be reached. The report
  adds a driver-robustness finding (uncaught parse exception) and the
  consolidation recommendations. No CECE file modified; the temporary
  output root used for the run was removed from the checkout afterwards.
- Verified: harness 158/158 (new unit + gating tests included, no
  docker); mypy 0 errors in 35 files; pre-commit all-hooks green;
  exhaustive dry-run 1440 skips; `simple-maccity-suite.yaml` integration
  18/18 (combos unperturbed with examples off); real `--run-examples`
  session executed downloads + all six examples and wrote
  `examples/<stem>.out` + `examples-report.md`.

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
- maintain original `always do` and `requirements` sections when refining design docs

### testing

- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks

## requirements

- provide an option to run examples located in scripts/examples as part of the pytest suite
  - maybe a flag `--run-examples` that is off by default
- data will need to be downloaded using `scripts/data_download`
  - note some scripts might not work so inspect the script output
  - attempt to fix download scripts if possible
- all examples might not pass
- create a report detailing what failed with the download and execution - no modifications to cece at this point. we will do the fixes at a later time
  - put report in design/artifacts
- there also seems to be considerable duplication with cece examples. make some recommendations on how to consolidate examples and download scripts
