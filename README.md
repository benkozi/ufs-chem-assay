# ufs-chem-assay

The UFS-Chem testing, verification, and benchmarking harness. Today it is a
combinatorial pytest suite for CECE's `cece_standalone_driver`; CATChem is
planned as the second application (see
[design/spike/20260901-1229-rename-and-plan-for-catchem.md](design/spike/20260901-1229-rename-and-plan-for-catchem.md)).

Combinations of enum-valued driver options (declared in a suite file, e.g.
`src/tests/config/suite/simple-maccity-suite.yaml`) are rendered to YAML
configs and each runs in its own Docker container, followed by per-combo
assertions on the output (exit code, file counts/names, attributes) and a
statistics/plotting analysis step. Design rationale lives in
[design/design.md](design/design.md).

## Prerequisites

- A local checkout of the CECE repository (this runner lives in its own
  repository; the CECE checkout is external), with:
  - Docker and the `cece/cece-dev` image available locally (build it via
    `./setup.sh` in the CECE checkout);
  - the driver built at `./build/cece_standalone_driver` (relative to the
    CECE checkout root).
- [uv](https://docs.astral.sh/uv/) installed.

## Setup

```sh
uv sync                       # includes dev tools (mypy, pre-commit, stubs)
uv run pre-commit install     # ruff check/format + mypy on every commit, plus
                              #   the conventional-commit message gate (the
                              #   commit-msg hook type installs automatically)

# per-machine configuration lives in a .env file at the repo root
# (gitignored; read when running pytest from the repo root):
cat > .env <<'EOF'
cece_root_dir=/path/to/CECE
cece_baseline_root_dir=/path/to/cece-baselines
EOF
```

Real environment variables override `.env` values, and the
`--cece-root-dir` flag overrides both.

## Running

```sh
uv run pytest                      # everything: integration + runner harness tests
uv run pytest -vs                  # show each driver's output as it runs
uv run pytest -x                   # fail fast: stop at the first failure
uv run pytest -k map-consd         # run a subset by combo name
uv run pytest --combo-clean-root   # delete an existing output root first

uv run pytest src/tests/ufs_chem_assay                  # harness only: fast, no docker
uv run pytest src/tests/test_driver_combos.py  # integration only (real docker)

uv run mypy                        # type checking (all of src/; zero errors expected)
uv run pre-commit run --all-files  # everything the commit hook runs: ruff check/format,
                                   #   mypy, yamlfix/yamllint, whitespace trimming

# everything except driver execution (no docker needed); all combo tests skip
uv run pytest src/tests/test_driver_combos.py --dry-run

# the CECE checkout's shipped examples, verbatim (off by default; downloads
# data via the checkout's scripts/data_download first — all seven pass;
# CAMS-TEMPO inputs need local copies until a public source exists)
uv run pytest src/tests/test_examples.py --run-examples

# a shipped CECE example as an ordinary suite (ex1..ex7): full pipeline —
# derived file-count/filename assertions, stats, plots, report rows.
# Needs CECE_ROOT_DIR (the suite's config_path anchors on the checkout)
# and the example's data downloaded first, e.g.
#   python3 $CECE_ROOT_DIR/examples/download-example-data.py --example ex3
uv run pytest src/tests/test_driver_combos.py --suite-config=ex3-suite.yaml

# the exhaustive run-only suite: every enum value on every driver-meaningful
# dimension, the inert category label pinned to "undefined" (240 combinations,
# on demand only — dry-run it first; the real run takes ~25-30 min).
# Suites are selected by name (see --suite-config below):
uv run pytest src/tests/test_driver_combos.py --dry-run \
  --suite-config=exhaustive-maccity-run-only-suite.yaml
```

To rebuild the driver and run the CECE C++ tests in the container (this
suite is separate — run it with `uv run pytest` as above), from any
directory:

```sh
$CECE_ROOT_DIR/scripts/build-and-test-container.py            # build + C++ tests
$CECE_ROOT_DIR/scripts/build-and-test-container.py --clean    # wipe build dirs first
$CECE_ROOT_DIR/scripts/build-and-test-container.py --test-filter Configured  # gtest subset
# --no-build / --no-test skip a phase; --mount and --image override defaults
```

Driver output is printed after every driver call: with `-vs` (or `-s`) it
appears in the terminal as the suite runs; without `-s`, passing tests stay
quiet and failing tests include the output in their report under
"Captured stdout call".

Each combination produces one test per assertion/analysis step —
`test_driver_execution` (driver exits 0), `test_nc_file_count` (expected
NetCDF output count; `validate_file_count: false` skips it),
`test_nc_filenames` (filenames match
`filename_pattern` at the expected write times), `test_nc_variable_dimensions`
(every configured output variable carries the standard
`(time, lev, lat, lon)` dimensions in every NetCDF — a synthetic dimension
like `nox_dim2` where `lat` belongs means the writer failed to associate
the coordinate; `validate_dimensions: false` skips it), `test_species_attributes`
(the species variable's full attribute dictionary, one test per combo ×
configured species; `exact: true` — the default — requires the dictionaries
to match exactly, `exact: false` checks the expectation as a subset; per
value, `null` asserts absence and `"__ignore__"` allows any value),
`test_descriptive_stats` (per-NetCDF statistics via distributed dask,
written to `<combo_id>-stats.csv`; all combos concatenated into
`descriptive_stats.csv` at the output root when the session ends), and
`test_baseline_comparison` (nccmp-style comparison against a per-combination
baseline: each `baseline_comparisons` entry carries a `sweep_selector` —
mirroring the sweep structure with regexes at the leaves — that must select
exactly one combination, a baseline `ulid` under `CECE_BASELINE_ROOT_DIR`,
an optional per-entry `atol`, and a `plot` switch for bias plots; structure
and attributes exact, data bit-for-bit or within `atol`; RMSE and
difference statistics recorded per file x variable in
`<combo_id>-stats-comparison.csv`, concatenated to `stats-comparison.csv`
at the root; bias maps + GIF render at session end into `plots-baselines/`
on a suite-wide symmetric color scale; unselected combinations skip) — with the
driver running once per combination. If the driver run fails, its
`test_driver_execution` fails and that combination's assertion tests are
skipped with a `driver run failed: ...` reason.

Options:

- `--suite-config=SELECTOR` — selects the suites to run. The selector
  is a regex fullmatched against each discovered suite's file name or its
  search-root-relative path; candidates are every `*.yaml` found
  recursively under the `CECE_SUITE_CONFIG_SEARCH_PATH` directories plus
  the built-in `src/tests/config/suite/` (always searched last). A literal
  filename is its own selector (`--suite-config=exhaustive-maccity-run-only-suite.yaml`);
  an existing file path is used verbatim. **Every match runs**: one match
  is the classic single-suite session; several matches run as one
  multi-suite session (e.g. `--suite-config='ex[0-9]-suite.yaml'` runs all
  seven example suites) over a single flat output root, each combo under
  its own suite's timeout, assertions, and plotting switches, with test
  ids suite-qualified (`ex3/base`) so `-k` selects per suite. There is no
  guard on broad selectors — a regex matching everything runs everything.
  Zero matches fail immediately with a listing; duplicate suite *names*
  among the matches fail at session start. Default:
  `simple-maccity-suite.yaml` — a bare `uv run pytest` always runs exactly
  that one suite, however many suites exist. Use the `--suite-config=...`
  form (with `=`), not a space.

  The suite YAML defines the suite's unique `name` (lowercase slug; by
  convention suite `X` lives in `X-suite.yaml`), the base driver config
  (`config_path`), the per-combination timeout (`timeout_s`), and the
  sweep — which mirrors the driver-config structure, attaching swept
  values to named streams (or positional species entries). A sweep value
  may be a regex string instead of a list — `mapalgo: ".*"` expands
  (fullmatch, against the enum's values) to every value at load time,
  including values added after the suite was written; `run.yaml` records
  the expanded list. The checked-in `exhaustive-maccity-run-only-suite.yaml`
  sweeps `".*"` on every dimension.

  `sweep:` is optional: absent (or attaching no dimensions) the suite runs
  its base config as the single combination named `base`. A `config_path`
  starting with the literal `${CECE_ROOT_DIR}` token anchors on the CECE
  checkout (`--cece-root-dir` / `CECE_ROOT_DIR`) — how the checked-in
  `ex1-suite.yaml` … `ex7-suite.yaml` reference the shipped example
  configs (`examples/config/cece_config_ex*.yaml`) portably; using such a
  suite without a configured root fails immediately with the standard
  root-dir message. ex7 is an expected failure until its CAMS-TEMPO
  inputs have a public source.
- `--dry-run` — everything except driver execution: the suite loads, combos
  enumerate, `run.yaml`/`combos.csv`/every generated driver config/
  `test-report.csv` are all written, and every combo test skips. No docker
  required — use it to validate a suite (notably the exhaustive one) before
  paying for containers. With the default output root it also needs no
  `CECE_ROOT_DIR`.
- `--cece-root-dir=PATH` — host path of the CECE checkout, mounted at
  `/work` in the driver container. Overrides `CECE_ROOT_DIR` when both are
  set. One of the two is required to execute the driver (and to resolve an
  explicit `--combo-output-root`); missing or nonexistent paths fail
  immediately, before any test runs.
- `--combo-output-root=PATH` — root artifact directory; relative paths
  resolve against `/work` in the container, so results persist in the CECE
  checkout. Default: a pytest-managed temporary directory (nothing is
  written to the checkout).
- `--combo-clean-root` — with an explicit `--combo-output-root`, remove an
  existing output root before running. Without it, an existing root is an
  error — prior results are never mixed with a new run.
- `--run-examples` — run the CECE checkout's shipped
  `examples/config/cece_config_ex*.yaml` via the checkout's
  `examples/run-example.py` entrypoint, docker-wrapped by this runner
  (exit 0 = pass), after one session pass of
  `examples/download-example-data.py` per example (cached fetches;
  download failures are recorded, never fatal). Off by default;
  `--dry-run` wins.
  The examples are external artifacts under test and this flag is their
  regression gate — all seven pass. Caveat: ex1/ex7's CAMS-TEMPO inputs
  have no public download source yet, so on a fresh machine their
  downloads 404 until local copies are placed in the checkout's `data/`. Outcomes land under the output root in `examples/`
  (`<stem>.out` per example plus a session `examples-report.md`); they
  are not part of `test-report.csv`.

## CI and releases

Commit messages (and PR titles) must follow
[Conventional Commits](https://www.conventionalcommits.org/) — `feat: …`,
`fix: …`, etc. Locally the `commit-msg` pre-commit hook rejects a
non-conforming message at commit time; in CI the PR title is checked with
the same hook (squash-merge subjects come from PR titles, and
semantic-release parses them to compute versions).

Every pull request (and every push to `develop`/`main`, as post-merge
validation) runs `.github/workflows/ci.yaml`: the toolchain container is
built from `Dockerfile` (cached via the GitHub Actions buildx cache;
never pushed off-runner), then pre-commit and the harness tests run
inside it with no `CECE_*` environment at all. Reproduce exactly what CI
runs locally:

```sh
docker buildx build --load -t ufs-chem-assay:dev .
docker run --rm -v "$PWD":/repo -w /repo ufs-chem-assay:dev \
  sh -c 'git config --global --add safe.directory /repo \
         && uv sync --frozen && uv run pre-commit run --all-files'
docker run --rm -v "$PWD":/repo -w /repo ufs-chem-assay:dev \
  sh -c 'git config --global --add safe.directory /repo \
         && uv sync --frozen && uv run pytest src/tests/ufs_chem_assay'
```

Pull requests targeting `develop` (and pushes to `develop`, which exist
to warm the shared caches) additionally run
`.github/workflows/integration.yaml`: the CECE repository and ref named
in the workflow's `env` block are cloned (nested submodules), the
container image built through the buildx cache and loaded as
`cece/cece-dev`, the driver compiled in the container (`build/` cached
by CECE commit), the maccity dataset downloaded via CECE's own `ex3`
data set (cached), and `simple-maccity-suite.yaml` runs for real.
Baseline-comparison tests skip in CI
(`CECE_ENABLE_BASELINE_COMPARISONS=false`: the baseline store has no
public download source yet — re-enabling is a standing TODO). The full
output root uploads as a workflow artifact on success and failure
alike; `run.yaml` records the exact CECE commit (`cece_commit`). Mirror
it locally:

```sh
CECE_ENABLE_BASELINE_COMPARISONS=false uv run pytest \
  src/tests/test_driver_combos.py --suite-config=simple-maccity-suite.yaml
```

Releases are **manual only**: dispatch `.github/workflows/semantic-release.yml`
from the Actions tab, picking `develop` (produces `X.Y.Z-rc.N` release
candidates) or `main` (full releases) in the branch selector — any other
branch fails the run's guard. python-semantic-release v10 (configured in
`[tool.semantic_release]` in `pyproject.toml`) then pushes a version-bump
commit (`[ci skip]`), a git tag, and the `CHANGELOG.md` update back to
that branch. Nothing is published: no GitHub Release object, no PyPI, no
container registry. Dispatch only on a green branch — the release
workflow deliberately does not gate on CI. semantic-release is not a
project dependency (the action runs it); preview the next version
locally with an ephemeral run:

```sh
uv tool run --from 'python-semantic-release>=10,<11' semantic-release --noop version
```

## Results

By default results land in a pytest temp directory (printed paths in test
failures point there; pytest keeps the last few runs under e.g.
`/tmp/pytest-of-<user>/`). With `--combo-output-root=combo_runs` they land in
`combo_runs/` at the CECE checkout root (`CECE_ROOT_DIR`). Either way, one
directory per combination:

```
<output-root>/
  run.yaml                       # run manifest: session ULID, the CECE checkout's
                                 #   HEAD commit SHA (cece_commit; null only when no
                                 #   checkout is configured — a configured root that
                                 #   is not a git checkout fails the session at
                                 #   start), and every resolved suite in selection
                                 #   order (one-element list when single)
  combos.csv                     # effective-parameter table: one row per sweepable
                                 #   dimension per combo (columns: run_id, combo_id,
                                 #   suite, name, target, field, value, swept)
  test-report.csv                # per combo-test outcome: pytest_name, suite,
                                 #   combo_id, combo, result (passed/failed/skipped)
  descriptive_stats.csv          # all combos' statistics, concatenated (suite-stamped)
  stats-comparison.csv           # all combos' comparison rows, concatenated
  01K0Z8FJX2.../                 # one directory per combination (runtime ULID);
    <combo_id>.yaml              #   generated driver config
    <combo_id>.out               #   captured driver stdout+stderr
    cece.log                     #   the driver's own tee'd run log (log_file always
                                 #     points here, whatever the base config says)
    <combo_id>-stats.csv         #   per-NetCDF descriptive statistics
    <combo_id>-stats-comparison.csv  # comparison rows (when configured)
    plots-overview/              #   spatial plot per NetCDF + per-variable GIF
    plots-baselines/             #   bias maps + GIF (compared combos only)
    *.nc                         #   driver NetCDF output
```

Test ids stay human-readable (`MACCITY.map-consd`, target-qualified;
`<suite>/<combo>` in multi-suite sessions); directories are runtime ULIDs —
minted per combo per run, time-ordered (directories list in creation order),
never derived from content. The output root stays flat however many suites
run. Cross-run joins use the recorded parameters, not ids: `combos.csv` is
the **effective-parameter table** — for every combo, one row per sweepable
dimension (per-stream `taxmode`/`tintalgo`/`mapalgo`, per-species-entry
`operation`/`category`/`vdist_method`) with the value from the combo's
generated config, `swept` marking actual sweep dimensions — so pinned
parameters and sweep-less `base` combos join exactly like swept ones (join
stats to parameters on `run_id` + `combo_id`, or across runs on `suite` +
`combo` name).

Every run gets a runtime-generated ULID (`run_id`) — logged at session
start, written to `run.yaml`, and stamped into every stats row so CSVs from
different runs stay distinguishable. It is never set via configuration;
unknown keys in suite or driver config files are rejected at load time.

Spatial plots render at session end (suite `plotting.enabled`, default on;
`gif_enabled` controls the per-variable GIF). All plots of a variable share
one **exact suite-wide min/max color scale** derived from the descriptive
statistics — so plotting requires `compute_descriptive_stats`. First-time
boundary rendering downloads Natural Earth coastline/border data; offline,
plots degrade to data-only maps with a warning.

Stats CSV columns: `run_id`, `suite` (the suite's unique `name` from its
yaml, e.g. `simple-maccity`), identity (`combo_id`, `combo`, `file`,
`variable`), the file's
timestamp from its NetCDF time coordinate as `time` (ISO-8601) plus part
columns `year`/`month`/`day`/`hour`/`minute`/`second` for easy time
summaries (null if the file has no time coordinate), and the nan-aware
statistics (`count`, `sum`, `mean`, `std`, `min`, `max`, `median`).

## Environment variables

All `CECE_*` variables can also be set (lowercase works) in a gitignored
`.env` file at the repo root, read when pytest runs from there; real
environment variables override `.env`, and `--cece-root-dir` overrides both.

| Env var                         | Meaning                                        | Default                          |
|---------------------------------|------------------------------------------------|----------------------------------|
| `CECE_DOCKER_IMAGE`             | container image                                | `cece/cece-dev`              |
| `CECE_ROOT_DIR`                 | host CECE checkout root mounted at /work       | unset — required to run the driver; `--cece-root-dir` overrides |
| `CECE_DRIVER_PATH`              | driver path inside the container               | `./build/cece_standalone_driver` |
| `CECE_RUN_TIMEOUT_S`            | caps the suite `timeout_s` when smaller        | `300`                            |
| `CECE_LOG_LEVEL`                | runner log level (`DEBUG`, `INFO`, ...)        | `INFO`                           |
| `CECE_DASK_NWORKERS`            | dask workers for the stats cluster (int > 0)   | unset → all available cores      |
| `CECE_BASELINE_ROOT_DIR`        | baselines live at `<root>/<ulid>/`             | unset → current working directory |
| `CECE_ENABLE_BASELINE_COMPARISONS` | global switch; `false` skips comparison tests | `true`                           |
| `CECE_CONFIG_SEARCH_PATH`       | prepended to relative `config_path` values     | unset                            |
| `CECE_SUITE_CONFIG_SEARCH_PATH` | colon-separated dirs searched recursively for `--suite-config` selection | unset → built-in suite dir only |
