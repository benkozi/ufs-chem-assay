# Feature: unify CECE example download + run into Python entrypoints

## Goal

Replace CECE's per-example shell download scripts and the geos-chem-only
`download_hemco_data.py` with two stdlib-Python entrypoints under
`examples/` — `download-example-data.py` and `run-example.py` — driven by
a single example→data mapping, with simple sequential (blocking)
downloads, shared enums/logging in `examples/common.py`, and
example configs moved to `examples/config/`. The combo-test-runner switches to the new
entrypoints for both download and execution. `<CECE>/data` is left
intact (known-missing S3 files — CAMS — stay expected-fail).

## Pre-design audit

1. **Current state** (post `design/fix/20260720-1500`): seven
   `scripts/data_download/download_ex*.sh` (cache-aware, `set -euo
   pipefail`; sector files via direct `curl` against `noaa-ufs-srw-pds`,
   the rest via `scripts/download_hemco_data.py` → geos-chem; CAMS keys
   aspirational/404). Nine configs at `examples/*.yaml` (ex1–ex7 +
   `advanced` + `megan3`). Runner: `examples.py` globs
   `examples/cece_config_ex*.yaml`, runs every `download_ex*.sh`, and
   executes examples through its own `run_driver` docker invocation.
   Gate: 7/7 green (local CAMS copies).
2. **Consumers the requirements don't mention** (all need handling):
   - `tests/test_scripts.py` — CECE-side pytest that imports/tests
     `download_hemco_data` (and `verify_hemco_data`, which stays).
   - `docs/scripts.md` — documents `download_hemco_data.py` with a
     `--config` flag that no longer even exists (stale twice over).
   - `scripts/setup_hemco_examples.sh` — a *generator* that recreates
     the old-schema examples and per-example download scripts; if left,
     it resurrects everything this feature deletes.
   - A stray duplicated tree `scripts/scripts/data_download/` (7 script
     copies) — accidental nesting, delete.
3. **Python floors**: container 3.12.3, host 3.13.9 → `enum.StrEnum`
   (3.11+) and modern stdlib `asyncio` are safe everywhere the scripts
   run.
4. **Entry-point naming constraint**: dash-named files
   (`download-example-data.py`) are not importable modules — so all
   testable logic (enums, mapping, download/run machinery, logging)
   lives in `examples/common.py`, and the two entrypoints are
   thin argparse wrappers. This is also how "a mapping … maintained in
   the script" and "share enums … in common.py" reconcile: the mapping
   lives in `common.py`, the single source both entrypoints (and tests)
   import.
5. **Plain blocking downloads** (async dropped by direction — see
   appendix): chunked `urllib.request` streaming, one file at a time.
   The typical use is a single example's handful of files, so
   sequential is fine and the code stays trivially simple. Side win
   either way: the `curl` binary dependency disappears entirely.

## Design — CECE side

### Layout

```
examples/
  config/                      # all nine cece_config_*.yaml move here
  download-example-data.py     # thin argparse entrypoint
  run-example.py               # thin argparse entrypoint
  common.py                    # enums, mapping, download/run logic, logging
  README.md
```

(Originally specified as `helpers/common.py`; flattened to
`examples/common.py` by direction — see conversational updates.)

### Config portability: relative data paths

Because `run-example.py` executes the driver directly (no docker), the
configs' container-absolute `file: "/work/data/…"` references would
break outside the container. **All example configs switch to relative
paths** (`file: "data/…"`), resolved against the driver's working
directory — the repo root in every environment (`-w /work` in the
container, `cwd=repo_root` natively). Verified by the final gate; if the
driver turned out to require absolute paths, that is a blocker to report,
not to paper over (expected to work: the container runs already resolve
`./build/…` relative to the same cwd).

### `common.py`

- **Enums** (`@unique`, `StrEnum`):
  - `Example`: `EX1..EX7`, `ADVANCED`, `MEGAN3` (values `"ex1"…"ex7"`,
    `"advanced"`, `"megan3"`).
  - `Bucket`: `GEOS_CHEM = "geos-chem"`,
    `NOAA_UFS_SRW_PDS = "noaa-ufs-srw-pds"`, each exposing a `base_url`
    property (`https://<bucket>.s3.amazonaws.com`).
- **One configuration object, not scattered globals** (by direction —
  see conversational updates): a frozen `ExamplesConfig` dataclass holds
  the paths (`repo_root` + derived `config_dir`/`default_dst_dir`),
  tunables (`chunk_bytes`), env-var names, and the example→data mapping,
  exposed as the single module-level `CONFIG` instance that functions,
  entrypoints, and tests read (`CONFIG.example_data`,
  `CONFIG.config_path(...)`, `CONFIG.driver_path()`, …).
- **Mapping** (frozen dataclasses — sanctioned here: stdlib-only
  constraint, the runner keeps its pydantic models):
  `DataFile{bucket, key}` with a `filename` property (basename);
  `CONFIG.example_data: dict[Example, tuple[DataFile, ...]]` (built by a
  factory so the intermediate sector/weight constants aren't module
  globals either) — ex1/ex7: five
  NOAA HTAP v2015-03 sector files + three aspirational geos-chem CAMS
  keys (expected 404, documented in place); ex2/ex3/ex5: MACCity/CEDS/
  mask files; ex4: HTAPv3 2018; ex6: EDGARv43 POW + CEDS ALK4;
  `advanced`/`megan3`: entries exist (enum completeness) with the data
  requirements they're known to have — incomplete knowledge documented
  inline rather than invented.
- `config_path(example) -> Path`: `examples/config/cece_config_<id>.yaml`.
- **Download machinery**: `def download(files, dst_dir) ->
  list[DownloadOutcome]` — dedups files (ex1/ex7 share everything),
  skips existing targets (cache-aware, like today), streams each URL to
  disk sequentially via chunked `urllib.request`, **attempts every file
  even after failures** (an improvement over `set -e`: one 404 no longer
  hides later fetches), and returns per-file outcomes
  (`DownloadOutcome{file, ok, detail}`). Entry point exit code: 0 iff
  all succeeded.
- **Run machinery**: `run_example(example, repo_root) -> int` —
  **executes the driver binary directly, never docker** (per direction —
  see appendix): `./build/cece_standalone_driver
  examples/config/cece_config_<id>.yaml` with `cwd=repo_root`, OMPI
  root-run env vars set (harmless natively, required in-container),
  output streamed through, driver exit code returned. The driver path
  defaults to `build/cece_standalone_driver` under the repo root,
  overridable via `CECE_EXAMPLES_DRIVER_PATH` for nonstandard build
  locations (HPC installs). The script is environment-agnostic: it runs
  *inside* the dev container, on an HPC node with a native build, or
  anywhere the driver exists — it never spawns a container itself.
- **Logging**: `configure_logging()` (stdlib `logging.basicConfig`,
  level via `CECE_EXAMPLES_LOG_LEVEL` env default INFO) — called by both
  entrypoints; all progress/failure reporting goes through loggers, not
  bare prints.

### Entrypoints

- `download-example-data.py`: `--example ex1[,ex7,…]` (validated against
  `Example`; required unless `--all`), `--all`, `--dst-dir PATH`
  (default `<repo>/data`, created if missing). Resolves the union of
  mapped files, downloads concurrently, logs a per-file summary, exits
  non-zero if any fetch failed.
- `run-example.py`: **same arguments** (shared argparse builder from
  `common.py`). Phase 1: the same download (cache-aware — a no-op when
  data is present). Phase 2: run each selected example via
  `run_example`, in id order; per-example pass/fail summary; exit
  non-zero if any run failed. Downloads failing (e.g. the expected CAMS
  404s) do **not** abort phase 2 — the driver then fails honestly on
  the missing file, which is the truthful outcome.

### Deletions & doc updates

- Delete: `scripts/data_download/` (7 scripts), the stray
  `scripts/scripts/` tree, `scripts/download_hemco_data.py` (refactored
  into the new entrypoint), `scripts/setup_hemco_examples.sh` (its only
  purpose is regenerating the superseded layout), and
  `scripts/verify_hemco_data.py` (30 lines of exists+non-empty checking,
  no operational callers; its one useful behavior is absorbed — the
  download cache guard is *skip-if-exists-and-non-empty*, so truncated
  files re-fetch).
- `examples/README.md`: new entrypoint usage (`download-example-data.py`
  / `run-example.py`, the `--example`/`--all`/`--dst-dir` forms),
  `examples/config/` paths, and both execution environments — inside the
  dev container (`docker run … python3 examples/run-example.py …`) and
  natively (HPC: `python3 examples/run-example.py …` beside a built
  driver, `CECE_EXAMPLES_DRIVER_PATH` for nonstandard build dirs). Per
  the standing rule, **no combo-test-runner mentions**.
- `docs/scripts.md`: replace the stale `download_hemco_data.py` and
  `verify_hemco_data.py` sections (both document flags the scripts never
  had) with the new entrypoints.
- `tests/test_scripts.py`: retarget at `examples/common.py` —
  mapping sanity (every `Example` member has a mapping entry and an
  existing config file; buckets/keys well-formed), arg validation, the
  non-empty cache-guard behavior, and a network HEAD-check of every
  mapped key (available keys must 200; the aspirational CAMS keys are
  asserted *known-missing* so the test documents rather than fails on
  them), satisfying the "downloads tested for known S3 targets" note.
  The `verify_hemco_data` test block is removed with its script.

## Design — runner side

- `examples.py`:
  - `EXAMPLES_SUBDIR` → `examples/config` (discovery glob pattern
    unchanged; `advanced`/`megan3` still outside the `cece_config_ex*`
    gate).
  - `download_example_data(root_dir)` now invokes the entrypoint
    **per discovered example id** — `sys.executable
    <root>/examples/download-example-data.py --example <id> --dst-dir
    <root>/data` — keeping per-example `DownloadResult` granularity in
    the session report (the `script` field's description updates to "the
    download invocation target (example id)"; capture/timeout semantics
    unchanged).
  - `test_examples.py` runs examples through the new entrypoint
    **inside the container** — the docker wrapping stays the runner's
    job (the entrypoint itself is docker-free): `docker run --rm -v
    <root>:/work -w /work <image> python3
    examples/run-example.py --example <id>` (subprocess capture to
    `<stem>.out`, `run_timeout_s`, returncode → `ExampleRunResult`).
    The runner's `build_command`/`run_driver` machinery for *combos*
    stays untouched; the examples path swaps the driver argument for the
    entrypoint invocation. The entrypoint's phase-1 re-download is a
    cache-aware no-op in practice (and runs in-container against the
    mounted `data/`).
- Harness tests: gating fake trees gain `examples/config/…yaml` and
  executable python stubs for the two entrypoints (marker-file
  semantics preserved); unit tests update discovery paths and mock the
  new invocation shape.
- `design.md`/runner `README.md`: path + invocation updates in the
  `--run-examples` descriptions.

## TDD & verification plan

- Runner-side red first: discovery under `examples/config/`;
  `download_example_data` invoking the python entrypoint per id (mocked
  subprocess: command shape, cwd, per-id results, failure tolerance);
  gating subprocess tests with stubbed entrypoints (no flag / dry-run /
  no-root gating unchanged, markers prove no downloads).
- CECE-side: updated `tests/test_scripts.py` green (mapping sanity +
  HEAD checks); implementation-time record of every mapped key's HEAD
  status in this doc (expected: all 200 except the three CAMS keys).
- Behavioral: `download-example-data.py --example ex3 --dst-dir
  <tmp>` fetches MACCity into a fresh dir (small, real network);
  `--all` against the warm cache is a fast no-op; `run-example.py
  --example ex3` passes end to end.
- Final gates: `uv run pytest src/tests/test_examples.py
  --run-examples` → **7/7** (local CAMS copies in `data/`, which stays
  intact); runner harness suite, `--dry-run` suites, simple-maccity
  integration, pre-commit — all green.

## Ripples (standing process rules)

- Runner `design.md` + `README.md` per above; this doc's appendix
  collects any conversational updates that follow the refinement.
- CECE-side commits are the user's (never commit).
- The AMIO spike's scratch-config guidance (`cece_scratch_*.yaml`
  invisible to the glob) survives the `examples/config/` move — the
  glob's directory changes but the pattern still excludes scratch names.

## Acceptance criteria

- One mapping in `examples/common.py` drives everything; the
  two entrypoints accept `--example` (comma-separated, validated),
  `--all`, `--dst-dir` (created when missing); downloads run
  sequentially, attempt every file, and report per-file outcomes with
  stdlib logging.
- `scripts/data_download/`, `scripts/scripts/`,
  `download_hemco_data.py`, and `setup_hemco_examples.sh` are gone;
  configs live in `examples/config/`; `examples/README.md` and
  `docs/scripts.md` document only the new entrypoints (no runner
  mentions in CECE docs); `tests/test_scripts.py` targets the new
  module and passes.
- The runner discovers configs in `examples/config/` and uses the two
  entrypoints for download and execution; harness suite green without
  docker; `--run-examples` gate 7/7 with `<CECE>/data` untouched.
- Everything is stdlib-only on the CECE side (dataclasses sanctioned);
  `StrEnum` members exist for every example and bucket.
- `run-example.py` never spawns a container (greppable: no `docker` in
  `examples/`); it executes the driver binary directly and runs
  unmodified both inside the dev container and docker-free (HPC); every
  example config references data by relative `data/…` paths.

## Implementation notes

- Implemented as designed (post-revision: blocking downloads, no docker
  in `run-example.py`, `verify_hemco_data.py` deleted; the `helpers/`
  package was then flattened per direction — `examples/common.py`, with
  the `REPO_ROOT` parent depth adjusted and every import/doc reference
  updated; re-verified after the move: CECE script tests 9/9, entrypoint
  smoke, gate 7/7). Review follow-ups: globals consolidated into the
  frozen `ExamplesConfig`/`CONFIG` singleton, and the entrypoints' bare
  `print()` summaries replaced with logger calls (the design's
  no-bare-prints rule had been violated in the first pass). Layout, enums,
  mapping, cache guard (exists-and-non-empty), shared argparse builder,
  and logging all live in `examples/common.py`; the entrypoints
  are thin wrappers inserting `examples/` on `sys.path`.
- **Relative data paths verified**: all nine configs moved to
  `examples/config/` with `/work/data/…` → `data/…`, and the driver
  resolves them fine — the full gate is **7/7 green** through the new
  entrypoints, closing the design's one flagged risk.
- CECE-side deletions executed: `scripts/data_download/` (7),
  `scripts/scripts/` stray tree, `download_hemco_data.py`,
  `setup_hemco_examples.sh`, `verify_hemco_data.py`.
  `tests/test_scripts.py` retargeted (kept the surviving
  `hemco_to_cece`/`visualize_stack` tests; added mapping-sanity, CLI,
  cache-guard, and network HEAD tests — the HEAD test asserts CAMS keys
  *known-missing* and everything else 200; skippable via
  `CECE_EXAMPLES_SKIP_NETWORK_TESTS=1`). `docs/scripts.md`'s Data
  Management section replaced; `examples/README.md` rewritten around the
  entrypoints (native + container invocations, no runner mentions).
- Runner: `EXAMPLES_SUBDIR = examples/config`, new `DOWNLOAD_ENTRYPOINT`
  / `RUN_ENTRYPOINT` constants, `example_id()` helper,
  `download_example_data` invoking the entrypoint per id (per-example
  report granularity kept), `run_example_command()` building the docker
  wrap; `test_examples.py` captures the entrypoint run directly (no more
  `run_driver` for examples; combos untouched). Harness tests updated
  (fake trees use a python stub entrypoint).
- Verified: behavioral fresh-dir download (ex3 → MACCity fetched);
  warm-cache invocation is a fast no-op (exit 0 — local CAMS copies
  count as cached; the 404s only bite genuinely fresh machines); CECE
  `tests/test_scripts.py` 9/9 incl. live HEAD checks; runner harness
  161/161; mypy clean; pre-commit green; exhaustive dry-run 1440 skips;
  `simple-maccity-suite.yaml` integration 18/18; **`--run-examples`
  gate 7/7**.

---

# appendix: original notes

## always do

- include updating design.md as part of the implementation
- update combo-test-runner tests in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original design sections when refining design docs - create an appendix
  - summarize conversational updates in the appendix following original refinement target
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code - the user always commits

### testing

- combo-test-runner run examples works
- example downloads should be tested for the data files that are available in known S3 targets

## requirements

- refactor scripts/download_hemco_data.py --> rename to <CECE>/examples/download-example-data.py
- arguments should be:
  - required if "--all" is not provided: "--example <id>" where <id> is the example id like "ex1" or "megan3"
    - can be a comma-separated list of ids
  - optional: "--all" downloads all examples
  - optional: "--dst-dir <path>" create path if it does not exist
- a mapping for data requirements should be maintained in the script
- download script can remain S3 specific
- add a unique str enum for each example and S3 bucket name
- use async python calls for downloading allowing multiple simultaneous downloads
- delete the individual download scripts for each example
- create an <CECE>/examples/run-example.py script
  - accepts all arguments for download script
- share enums or other code in an examples/helpers/common.py file
- update examples/README.md documentation to use the new scripts
- update combo-test-runner to use the new python entrypoints for running the examples
- move examples yaml config files to examples/config/
- leave <CECE>/data intact (we know there are missing data files in S3 locations)
- add basic python logging to download and run
  - add the logging configuration to common.py
- we want this to work with stdlib python so dataclasses are okay in this case

## conversational updates

- 2026-07-22: **async requirement removed** — downloads are plain
  blocking/sequential. Rationale: the examples are small, most users run
  a single example, and a slightly slower download is an acceptable
  trade for simpler code. (Supersedes the original "use async python
  calls…" requirement above.)
- 2026-07-22: **`run-example.py` must not spawn docker.** It may run
  *inside* a container, but it executes the driver binary directly so it
  also works on HPC platforms without docker. Consequences: example
  configs switch from `/work/data/…` to relative `data/…` paths
  (resolved against the repo-root cwd, identical in-container and
  native); the combo-test-runner keeps the docker wrapping on its side
  and invokes the entrypoint inside the container;
  `CECE_EXAMPLES_DRIVER_PATH` overrides the default
  `build/cece_standalone_driver` location.
- 2026-07-22: **`scripts/verify_hemco_data.py` added to the deletions** —
  no operational callers, exists+non-empty is all it checked; that check
  moves into the download cache guard (truncated files re-fetch), and
  its test block and stale docs section go with it.
- 2026-07-22: **`scripts/setup_hemco_examples.sh` deletion confirmed by
  the user** (it was an audit finding, not an original requirement): as
  a generator it would resurrect the superseded download scripts and
  old-schema configs; git history preserves it.
- 2026-07-22: **`helpers/` flattened** — `examples/helpers/common.py`
  moves to `examples/common.py` and the `helpers/` package is deleted
  (one shared module doesn't need a package; supersedes the original
  "share … in an examples/helpers/common.py file" requirement).
- 2026-07-22: **globals consolidated** — the module-level path/env/tuning
  constants and the `EXAMPLE_DATA` mapping fold into one frozen
  `ExamplesConfig` dataclass exposed as `CONFIG`; only `logger`, the
  enums/dataclass types, `CONFIG`, and functions remain at module level.
