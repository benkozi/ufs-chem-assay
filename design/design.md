# ufs-chem-assay — Design

## Goal

A standalone pytest-based test suite that exercises `cece_standalone_driver`
across combinations of the enum-valued configuration options defined in
`src/models/cece_config.py`. The combinations to sweep are
declared in a YAML **suite configuration** validated by pydantic. Each
combination is rendered to a driver YAML config and executed in an isolated
Docker container with its stdout/stderr captured to a per-combo `.out` file;
unwrapped per-combo tests then assert on the outcome (driver exit code,
NetCDF file count and names, per-species variable attributes), and an
analysis step computes descriptive statistics and spatial plots.

**Identity (2026-09-01).** The harness was renamed `ufs-chem-assay` when its
scope widened to testing, verification, and benchmarking across UFS-Chem
applications (the previous identity is recorded in the spike below). The name
is used consistently everywhere: `HARNESS_NAME` in `src/logs.py` (logger
namespace, plot footer), the `pyproject.toml` name, the image tags, the CI
output root, and — underscored, because mypy requires a valid package name —
the harness test package `src/tests/ufs_chem_assay/`. See
`design/spike/20260901-1229-rename-and-plan-for-catchem.md` and
`design/feat/20260901-1701-rename-repo.md`.

## Non-goals

- pytest's command line is the *test* entry point. The `ufs-chem-assay
  run` command (`src/cli/`, 2026-09-03) orchestrates environment, CECE
  build, data, and the pytest invocation from one run config — it never
  re-implements test logic (see
  `design/feat/20260903-1453-run-on-rdhpc.md`).
- No dependency on existing CECE Python infrastructure; the runner lives in
  its **own repository** with its own `uv`-managed environment. The CECE
  checkout (driver build, input data) is external, located via the
  `root_dir` setting (`--cece-root-dir` flag or `CECE_ROOT_DIR` env var)
  and mounted at `/work` in the driver container (see
  `design/fix/20260717-1029-portability-external-cece.md`).
- No online baseline retrieval or baseline manifest yet — baselines are
  local directories keyed by ULID (see
  `design/feat/20260716-1113-compare-with-baseline.md`); no stats-CSV
  diffing (the comparison targets the NetCDF files themselves).

## Suite configuration

The sweep is defined in a YAML file loaded into a pydantic model — not
hardcoded. Each entry names an enum dimension and lists the values to sweep;
enums absent from the file are **not swept** and stay at their base-config
values. The combination space is the cartesian product of the listed values.

```yaml
# simple-maccity-suite.yaml — initial suite
name: simple-maccity                       # unique suite name (lowercase slug)
config_path: ../cece/simple-maccity.yaml   # base driver config (suite-relative)
timeout_s: 10                              # per combination; capped by CECE_RUN_TIMEOUT_S
assertions:
  expected_nc_file_count: null             # null = derive from the combo config
  validate_filenames: true                 # false skips the filename tests
  validate_file_count: true                # false skips the file-count test
  validate_dimensions: true                # output variables carry the standard
                                           #   (time, lev, lat, lon) dimensions
  species:                                 # per-species output expectations
    co:
      attributes:                          # full attribute-dictionary match
        exact: true                        # false = expected is a subset
        expected:
          units: kg m-2 s-1               # string = exact; null = absent;
          long_name: carbon_monoxide_emission_flux  # "__ignore__" = any value
          coordinates: time lev lat lon
plotting:
  enabled: true                            # session-end spatial plots per NetCDF
  gif_enabled: true                        # per-variable animated GIF
baseline_comparisons:                      # optional; each entry pairs one combination
  - sweep_selector:                        # mirrors `sweep`; regexes (fullmatch) at leaves
      cece_data:
        streams:
          - name: MACCITY
            mapalgo: consd
    ulid: 01KXNXCJ86E8Z2FKVAXRER5ND4       # baseline under CECE_BASELINE_ROOT_DIR
    atol: 0.0                              # per entry; 0 = bit-for-bit (default)
    plot: true                             # per entry; bias plots + GIF at session end
sweep:
  cece_data:
    streams:
      - name: MACCITY                          # selector: which base-config stream
        mapalgo: [bilinear, consd, passthrough]
```

```python
class StreamSweep(StrictModel):        # attaches to a stream by name
    name: str
    taxmode / tintalgo / mapalgo: list[...] | None

class SpeciesEntrySweep(StrictModel):  # attaches by list position: index i -> species.<name>[i]
    operation / category / vdist_method: list[...] | None

class Sweep(StrictModel):              # mirrors the driver-config structure
    cece_data: CeceDataSweep | None    # .streams: list[StreamSweep]
    species: dict[str, list[SpeciesEntrySweep]] | None

class SuiteConfig(StrictModel):  # via models/base.py; unknown keys rejected
    name: str           # unique suite name; convention: X lives in X-suite.yaml
    config_path: Path   # base CECE driver config; relative → suite-file dir
    timeout_s: int      # per-combination driver timeout (seconds)
    sweep: Sweep        # optional; absent/empty → the single "base" combination
```

A sweep value list may instead be a **regex string**, expanded (fullmatch)
against the enum's values into the sorted matching list at load time — so
`mapalgo: ".*"` always means every value, including ones added after the
suite was written, and `run.yaml` records the expanded list (the run stays
reproducible as enums grow). A regex matching nothing, like an invalid one,
fails the load. See
`design/feat/20260716-1647-exhaustive-maccity.md`, whose
`exhaustive-maccity-run-only-suite.yaml` sweeps `".*"` on every
driver-meaningful dimension and pins the inert `category` label to
`undefined` (240 combinations, run on demand — typically with `--dry-run`
first).

Duplicate sweep values, duplicate stream names, and unknown keys are all
rejected at load; sweep selectors (stream names, species keys, entry counts)
are validated against the loaded base config at session start, before any
container runs. See
`design/feat/20260709-1131-attach-sweeps-to-streams.md`.

A suite file fully describes a run: which base scenario (`config_path`),
the per-combination timeout, and which sweep. Full `config_path` resolution
and timeout semantics live in
`design/feat/20260707-1515-use-cece-config-directory.md`.

`sweep:` is optional: a suite attaching no dimensions runs its base config
as the single combination named `base` (its id a runtime ULID like every
combo's).
A `config_path` starting with the literal `${CECE_ROOT_DIR}` token anchors
on the CECE checkout (`settings.root_dir`) — how the checked-in
`ex1-suite.yaml` … `ex7-suite.yaml` run the checkout's shipped example
configs (`examples/config/cece_config_ex*.yaml`) as ordinary suites with
the full pipeline; using such a suite without a configured root is the
standard root-dir usage error. Generated combo configs also always point
`driver.log_file` into the combo's output directory, so no base config —
the examples set relative paths — can write a log into the checkout. See
`design/feat/20260724-0907-examples-as-suites.md`.

Reusing the enums from `cece_config.py` means invalid values fail at suite-load
time with a pydantic error, before any container runs.

The **initial suite** sweeps only `Mapalgo` over `bilinear`, `consd`, and
`passthrough` — 3 combinations. The full 6-enum product (864 combinations)
remains expressible later purely by editing the suite YAML.

The suite file path is a pytest option (`--suite-config`, default:
`src/tests/config/suite/simple-maccity-suite.yaml`,
checked in with the initial sweep).

## Combination space

Each swept dimension attaches to an explicit target; the sweep says where:

| Enum          | Values | Attaches to                                    |
|---------------|--------|------------------------------------------------|
| `Operation`   | 2      | a species entry (`species.<name>[<entry>]`)    |
| `Category`    | 7      | a species entry                                |
| `VdistMethod` | 5      | a species entry                                |
| `Taxmode`     | 2      | a stream, selected by `name`                   |
| `Tintalgo`    | 2      | a stream, selected by `name`                   |
| `Mapalgo`     | 6      | a stream, selected by `name`                   |

The enum values are hand-mirrored from the driver C++ (audited in
`design/feat/20260716-1647-exhaustive-maccity.md`): `Mapalgo` holds only the
regridder's canonical values (`passthrough, nn, bilinear, cubic, conss,
consd` — unknown strings silently regrid with the default method, so no
others may exist here) and `VdistMethod` holds the parser's lowercase
strings (`single, range, pressure, height, pbl` — the uppercase validator
whitelist is dead code in standalone mode, and unknown strings silently run
as `single`). `Category` is a pure label the driver never reads; its
`undefined` value exists for suites that need the dimension without meaning
(pinned instead of swept in the exhaustive suite).

Sweeping `vdist_method` builds the **nested `vdist:` block** the driver
parses (a `Vdist` sub-model on the species entry; flat `vdist_*` keys are a
removed schema the driver silently ignored), with the companion fields each
method needs to take effect:

- `height` → `h_start` / `h_end` (0.0 / 100.0 m)
- `pressure` → `p_start` / `p_end` (100000.0 / 90000.0 Pa)
- `range` → `layer_start` / `layer_end` (0 / 2 — 0-based inclusive
  model-level indices)
- `single` → `layer_start` (0); `pbl` → no companions

### Combination naming and ids

Each combination gets a deterministic canonical name built from the **swept
dimensions only**, as target-qualified segments joined by `__`. The sweep is
normalized first (targets and value lists sorted; species targets before
stream targets; fields in fixed order `op, cat, vd` / `tax, tint, map`), so
declaration order in the suite yaml never affects names or ids:

- Initial suite: `MACCITY.map-bilinear`, `MACCITY.map-consd`, …
- A two-dimension sweep: `co.op-add__MACCITY.map-consd`, …

The name is the pytest parameter id — so `pytest -k <expr>` selects slices
of the suite for free (multi-suite sessions qualify it as
`<suite>/<combo>`) — but names grow with sweep dimensions, so **storage
uses a runtime ULID combo id instead**: minted per combo at enumeration
(runtime-only, like the session `run_id`), unique across every suite in the
session (the flat output root needs no nesting), and time-ordered so combo
directories list in creation order. Ids carry no content semantics and are
not stable across runs; **cross-run joins use the recorded parameters**:
`<output-root>/combos.csv` is the effective-parameter table — for every
combo, one row per sweepable dimension (per-stream
`taxmode`/`tintalgo`/`mapalgo`, per-species-entry
`operation`/`category`/`vdist_method`) with the value from the combo's
generated config and a `swept` flag — so pinned parameters and sweep-less
`base` combos join exactly like swept ones (within a run on `combo_id`,
across runs on `suite` + `combo` name or the parameter columns). See
`design/feat/20260724-1013-run-multiple-suite-configs.md`.

## Base configuration

Combinations are diffs applied to a **base config** — a known-good driver
config selected by the suite's `config_path` (the initial
`src/tests/config/cece/simple-maccity.yaml`, modeled on
`examples/cece_config_ex1.yaml`: single species `co`, single `MACCITY` stream
reading `/work/data/MACCity_4x5.nc`, coarse global grid, three-hour run). Base
configs live inside this repository, preserving zero runtime dependency
on files elsewhere in the repo. The base config's `output.fields` entries
declare each field to write together with its NetCDF attributes (a plain
string is shorthand for a field with no configured attributes; the map form
nests an `attributes` map under the field `name`) — unconfigured fields get
none, which is what the per-species attributes assertion verifies. For each
combination the generator:

1. Loads the base config via `CeceConfig.from_yaml(config_path)`.
2. Applies the swept enum values (plus companion vdist fields) at the
   injection points above.
3. Points `output.directory` at the combo's own directory (see layout below).
4. Serializes with `CeceConfig.to_yaml()`.

**Requirement — all config construction goes through `cece_config.py`.**
Every generated driver config is built as a `CeceConfig` model instance
(base config and per-combo mutations alike) and written to disk only via
`CeceConfig.to_yaml()`. No hand-assembled dicts, string templates, or direct
`yaml.dump` calls anywhere in the generator. This guarantees every config the
driver receives has passed pydantic validation, and keeps serialization
behavior (`exclude_none`, key ordering, the YAML 1.2 boolean handling in
`cece_config.py`) in one place. If a combination needs a field the model
doesn't have, the fix is to extend `cece_config.py` — not to bypass it.

## Directory layout (runtime artifacts)

**By default, all test-generated data — combo yamls, captured output, NetCDF —
is written to a pytest-managed temporary directory** (session-scoped
`tmp_path_factory`, the machinery behind the `tmp_path` fixture). Nothing
lands in the repo checkout and nothing needs git-ignoring; pytest keeps the
last few runs under its base temp dir and prunes older ones.

Passing `--combo-output-root=PATH` opts out of the temp default: the path is
then interpreted as container-relative, resolved against `/work` (the mounted
CECE checkout), so results persist in that checkout. Resolving it requires
`root_dir`, so an explicit output root without `--cece-root-dir` /
`CECE_ROOT_DIR` fails at session start — even under `--dry-run`.

Layout under the output root is the same either way:

```
<output-root>/                 # default: pytest tmp dir; else /work-relative
  run.yaml                     # RunManifest: session ULID, cece_commit (the CECE
                               #   checkout's HEAD SHA; null only when no checkout is
                               #   configured — an unresolvable SHA for a configured
                               #   root fails the session at start), and every
                               #   resolved suite in selection order
  combos.csv                   # effective-parameter table: run_id, combo_id,
                               #   suite, name, target, field, value, swept
  test-report.csv              # every combo-test's outcome: pytest_name, suite,
                               #   combo_id, combo, result (passed/failed/skipped)
  descriptive_stats.csv        # all combos' statistics, concatenated at session end
  stats-comparison.csv         # all combos' baseline-comparison rows, concatenated
  01K0Z8FJX2.../               # one directory per combination (runtime ULID;
    <combo_id>.yaml            #   flat root — every suite's combos side by side)
    <combo_id>.out             # captured driver stdout+stderr
    cece.log                   # the driver's tee'd run log (log_file always
                               #   redirected here by build_config)
    <combo_id>-stats.csv       # per-NetCDF descriptive statistics
    <combo_id>-stats-comparison.csv # comparison record: one row per file x variable
    plots-overview/            # session-end spatial plots + per-variable GIF
      co__cece_..._010000.png  #   (suite-wide exact min/max color scale,
      co.gif                   #    derived from that suite's stats slice)
    plots-baselines/           # bias maps (realization - baseline) + GIF,
                               #   RdBu_r symmetric suite-wide scale from
                               #   that suite's comparison slice; compared combos only
    *.nc                       # driver NetCDF output (output.directory in
                               #   the yaml points here)
```

Everything produced by or for a combination — config, captured output,
NetCDF — lives in that combination's directory.

The output root must not exist when a session starts: an existing root fails
the run immediately unless `--combo-clean-root` is passed, which removes the
old root first (see Pytest integration). Each run therefore always starts
from an empty root. This check only has teeth for an explicit
`--combo-output-root`; the default pytest temp root is freshly created every
session and can never pre-exist.

## Execution model

Three **runtimes** (`settings.runtime`, `src/platforms.py`): `docker`, the
default on the `local` platform and described first below; `slurm`, the
default on every other platform (RDHPC machines have no docker) — pytest
runs on a login node and each driver call is one `sbatch --wait` job
from a rendered `<combo_id>.sbatch` (Jinja2 template) kept beside the
combo's artifacts — directives, `CECE_MODULEFILE` load, `CECE_JOB_ENV`,
and the driver behind `srun --ntasks=1` — so the harness venv never sees
the module environment and a failed job is reproducible by hand; and
`native` — the driver as a host process, `cwd` = the CECE checkout,
prefixed by the `launcher` setting, for a session inside an allocation. The platform is detected from the
hostname (`CECE_PLATFORM` overrides; `local` when nothing matches) and
`run.yaml` records `platform`, `runtime`, and `modulefile`. The path model is one
abstraction: `ComboRoots.driver` is the output root *as the driver sees
it* — a container path under docker, the host path natively — and
generated configs carry it in `output.directory` and `driver.log_file`.
The base config's data path is cwd-relative (`data/MACCity_4x5.nc`) so
it resolves under both runtimes. See
`design/feat/20260903-1453-run-on-rdhpc.md`.

Under docker each combination runs independently in a fresh container using the image built
by `setup.sh` (`cece/cece-dev`, assumed already built — the runner never
builds it). One driver invocation per container, container removed on exit
(`--rm`):

```
docker run --rm \
    -v <host-cece-repo-root>:/work \   # bind mount: <host path>:<container path>
    -w /work \                         # working directory inside the container
    -e OMPI_ALLOW_RUN_AS_ROOT=1 \
    -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
    cece/cece-dev \
    ./build/cece_standalone_driver <output-root>/<combo-name>/<combo-name>.yaml
```

The `-v` flag carries the host→container mapping: the host-side CECE repo
root — the `root_dir` setting, supplied by `--cece-root-dir` or
`CECE_ROOT_DIR` (flag wins) — maps to `/work` in the container. The runner
lives in a separate repository, so there is no derivable default: driver
execution without a configured `root_dir` (or with one that is not an
existing directory) fails at collection time with a `UsageError`, before
any test runs. The
`-w` flag takes a container path only — it sets the driver's working
directory to the mounted CECE root, so the relative `./build/...` driver
path and `/work`-relative config paths resolve correctly.

When the output root is the default pytest temp directory, it lies outside
the repo and therefore outside the `/work` mount — the command gains a second
bind mount, `-v <host-tmp-root>:/combo_runs`, and the generated configs and
driver arguments reference the output root as `/combo_runs`. With an explicit
`--combo-output-root` the output root already lives under `/work` and no
extra mount is added.

Invoked with `subprocess.check_output(..., stderr=subprocess.STDOUT)` so the
driver's combined stdout/stderr is captured. The runner writes the captured
output to `<combo-name>.out` in the combo directory **whether the run passes
or fails** (on failure, `CalledProcessError.output` carries the text; the
runner writes the capture, then re-raises so the test fails). A nonzero driver
exit is the failure condition. The environment variables mirror `setup.sh`
(the container runs as root and the driver calls `MPI_Init`).

## Pytest integration

- **One test per assertion, parameterized by combo.** A session-scoped step
  loads the suite config and generates all combo YAML files up front. The
  driver runs once per combination in a combo-parameterized, session-scoped
  fixture that captures the outcome without raising; `test_driver_execution`
  asserts exit 0, and each post-run assertion (`test_nc_file_count`, …) is
  its own test that skips explicitly when the run failed. See
  `design/feat/20260708-1055-add-assertions-for-file-counts.md`.
- **Fail fast vs. continue** uses pytest built-ins — no custom flags.
  **Continue is the default and the desired behavior**: a plain `pytest`
  invocation runs every combination to completion regardless of individual
  failures, so one bad combo never hides results for the rest. Fail-fast is
  opt-in via `pytest -x` (first failure) or `--maxfail=N`.
- **Custom options** (registered in `conftest.py` via `pytest_addoption`):
  - `--suite-config=SELECTOR` — selects the suites to run. An existing
    file path is used verbatim (escape hatch); otherwise the value is a
    regex fullmatched (the sweep-regex convention) against each candidate
    suite's file name or search-root-relative posix path. Candidates are
    every `*.yaml` discovered recursively under the
    `suite_config_search_path` directories plus the built-in
    `src/tests/config/suite/` (always the final root), deduplicated by
    resolved path. **Every match runs** — one match is a single-suite
    session, several a multi-suite session over the same flat output root
    (per-suite timeouts/assertions/plots via each combo's owning
    `SuiteContext`; test ids `<suite>/<combo>`-qualified when several;
    no guard on broad selectors). Zero matches raise `UsageError` listing
    the candidates, duplicate suite names among the matches fail at
    sessionstart; selection lives in `resolution.select_suites` (pure).
    Default: `simple-maccity-suite.yaml` — a literal filename fullmatches
    only itself, so a bare `pytest` always runs exactly that suite no
    matter how many suites the roots contain.
  - `--combo-output-root=PATH` — root artifact directory (container-relative
    semantics as above). Default: unset, meaning a pytest-managed temporary
    directory via session-scoped `tmp_path_factory`.
  - `--combo-clean-root` — flag; if an explicitly given output root already
    exists, remove it (`shutil.rmtree`) before generating configs. Has no
    effect with the default temp root, which is always freshly created.
  - `--dry-run` — flag; the full session **minus driver execution**. Suite
    load, regex expansion, enumeration, selector/baseline resolution,
    `run.yaml`, `combos.csv`, every combination's generated config, and
    `test-report.csv` all happen for real; the driver-run fixture skips
    right before the docker invocation, so every combo test skips (a
    skip-only session exits 0, docker is never touched, and the lazy dask
    client never starts). Validates any suite — notably the exhaustive
    one — before paying for containers. With the default temp output root
    it needs no environment at all — no CECE checkout required.
  - `--run-examples` — flag, off by default; runs the CECE checkout's
    shipped `examples/config/cece_config_ex*.yaml` through the checkout's
    own `examples/run-example.py` entrypoint, wrapped in docker by this
    runner (the entrypoint is container-agnostic and never spawns docker
    itself; exit 0 = pass). Data comes from one session-scoped pass of
    `examples/download-example-data.py`, invoked per example id with
    `--dst-dir <root>/data` (a failing download is logged and recorded,
    never fatal). All
    examples are expected green since the consolidation fix
    (`design/fix/20260720-1500-fix-cece-examples.md`). ex1/ex7's
    CAMS-TEMPO inputs have no public download source yet: they run from
    local `data/` copies, and their download-script fetches 404 on a
    fresh machine until the data is published.
    Examples are **external artifacts under test**: they are
    deliberately not loaded through `CeceConfig` (they may use schemas the
    driver no longer reads — the documented exception to the
    config-construction rule, which governs generated configs only), and
    failures are honest, never masked. Outputs land in `examples/` under
    the output root (`<stem>.out` per example plus a session
    `examples-report.md`); examples carry no combo_id and stay out of
    `test-report.csv`. `--dry-run` wins over `--run-examples`; without a
    configured root, `--run-examples` fails at collection via the root_dir
    guard (example tests don't request `driver_run`, so the guard carries
    a separate examples condition).
  - `--cece-root-dir=PATH` — host path of the external CECE repository
    root, mounted at `/work`. Optional; precedence is **flag >
    `CECE_ROOT_DIR` env var > unset**, wired through pydantic-settings'
    native init-kwargs-beat-env behavior (`Settings(root_dir=option)` when
    the flag is given), so `settings.root_dir` is the single resolved
    source of truth. Required to execute the driver: when combo tests are
    collected without `--dry-run`, a missing or nonexistent `root_dir`
    raises `UsageError` in `pytest_collection_modifyitems` — collection
    time rather than sessionstart, so harness-only runs (which collect no
    `driver_run` tests) stay green with no environment, while the failure
    still lands before any test executes.
- **Test report.** A `pytest_runtest_makereport` hookwrapper collects every
  combo-parameterized test's outcome (phases combine failed > skipped >
  passed via `report.worst_result`); `pytest_sessionfinish` writes
  `test-report.csv` (pytest_name, combo_id, combo, result) first in its
  artifact pipeline, whenever combinations ran. Non-combo (harness) tests
  are not reported.
- **Existing explicit output root is an error by default.** When
  `--combo-output-root` is given, the runner checks at session start — before
  any configs are generated or containers run — whether that root exists on
  the host. If it does and `--combo-clean-root` was not given, the session
  fails immediately with a clear message — prior results are never silently
  mixed with or overwritten by a new run. With `--combo-clean-root`, the
  existing root is deleted wholesale and recreated. The rmtree targets only
  the resolved output root, never its parent. The default temp root needs no
  guard: `tmp_path_factory` allocates a fresh directory every session.
- **Selection**: `pytest -k <expr>` against the combo-name ids runs subsets.

## Settings

`pydantic-settings` (`BaseSettings`, env prefix `CECE_`) supplies
environment-derived configuration, keeping the pytest CLI for run-shaping
options only (`--cece-root-dir` is the one override that feeds a setting;
see Pytest integration). The prefix is deliberately `CECE_` rather than
something runner-specific: the settings class may later host other variable
groups beyond the test runner. `Settings` is **frozen**: constructed once at
sessionstart and read-only thereafter, so `root_dir` resolution happens at
exactly one point.

A cwd-relative **`.env` file** (gitignored — it carries per-machine absolute
paths) supplies values below real environment variables; precedence, highest
first: init kwargs (the `--cece-root-dir` flag) → environment → `.env` →
field default. Matching is case-insensitive, so lowercase `cece_root_dir=`
keys work. pydantic-settings ships `python-dotenv`; no extra dependency.

## Type checking

mypy runs over everything under `src/` (config in `pyproject.toml`
`[tool.mypy]`): `disallow_untyped_defs` / `disallow_incomplete_defs` (no
untyped functions or methods, tests included), `warn_return_any` and
`warn_unused_ignores` (no `Any` leaking, no stale ignores), and the
**pydantic mypy plugin** (`init_typed`, `init_forbid_extra` — without the
plugin, mypy's generic `dataclass_transform` view misreads `Field(...)`
defaults and reports spurious required-argument errors). `cartopy.*` is the
only missing-import override (no stubs exist); `pandas-stubs` and
`types-PyYAML` are dev dependencies. `Any` is avoided throughout; the one
sanctioned exception is `CeceConfig`'s scheme `options` (an open driver
surface, commented in place).

**Session state on `pytest.Config` uses typed `pytest.StashKey`s** (module
level in the root conftest), written once at sessionstart and read through
`config.stash[...]` — fixtures expose it to test modules (e.g.
`baseline_comparisons`). The former pattern of dynamic `config._combo_*`
attributes with `type: ignore` is retired; don't reintroduce it.

Pre-commit (`.pre-commit-config.yaml`, install with
`uv run pre-commit install`) runs ruff check, ruff format, a whole-`src/`
mypy pass, and YAML formatting/linting (`yamlfix` in `[tool.yamlfix]`,
`yamllint` in `.yamllint.yaml` — the pair kept coherent: no `---` document
start, 120-column lines, block-style sequences) as **local hooks through
`uv run`** — the project venv is the single source of tool versions, so
the hooks see the pydantic plugin and all dependencies (a mirrors-mypy
isolated environment would not). Whitespace trimming
(`trailing-whitespace` with the Markdown line-break exception,
`end-of-file-fixer`) comes from the canonical `pre-commit-hooks` repo —
the one sanctioned remote exception, having no project-dependency
coupling. `design/` (records, not maintained code) and `uv.lock`
(generated) are excluded from the YAML/whitespace hooks.

| Setting          | Env var               | Default              |
|------------------|-----------------------|----------------------|
| `docker_image`   | `CECE_DOCKER_IMAGE`   | `cece/cece-dev`  |
| `root_dir`       | `CECE_ROOT_DIR`       | unset — required to run the driver; `--cece-root-dir` overrides |
| `driver_path`    | `CECE_DRIVER_PATH`    | `./build/cece_standalone_driver` |
| `run_timeout_s`  | `CECE_RUN_TIMEOUT_S`  | 300 — caps the suite's `timeout_s` when smaller |
| `log_level`      | `CECE_LOG_LEVEL`      | `INFO`               |
| `baseline_root_dir` | `CECE_BASELINE_ROOT_DIR` | unset → cwd; baselines at `<root>/<ulid>/` |
| `enable_baseline_comparisons` | `CECE_ENABLE_BASELINE_COMPARISONS` | `true`; false skips all comparison tests |
| `dask_nworkers`  | `CECE_DASK_NWORKERS`  | unset → all available; else int > 0 |
| `config_search_path`       | `CECE_CONFIG_SEARCH_PATH`       | unset |
| `suite_config_search_path` | `CECE_SUITE_CONFIG_SEARCH_PATH` | unset → built-in suite dir only; `os.pathsep`-separated list, searched recursively for suite selection |

`config_search_path`, when set, overrides normal config resolution: the
search directory is prepended to the suite's relative `config_path`, kept
whole so nested directories work (full semantics in
`design/feat/20260707-1515-use-cece-config-directory.md`; that doc's
**suite-side** prepend semantics are superseded by the regex selector —
see `design/feat/20260717-1052-default-suite.md`). `suite_config_search_path`
feeds the selector's search roots as described under Pytest integration.

## Code layout

All code under `src/`; the project is `uv`-managed with its own
`pyproject.toml` at the repository root:

```
<repo root>/
  pyproject.toml          # uv project: pytest, pytest-mock, pydantic>=2, pydantic-settings, pyyaml
  README.md               # user-facing setup + run instructions
  docs/ursa-runbook.md    # manual native run on Ursa (what the CLI automates)
  config/                 # run-config templates: local.yaml (docker), ursa.yaml (native + slurm)
  design/design.md
  src/
    models/
      base.py             # StrictModel: extra="forbid" base for all config models
      cece_config.py      # existing pydantic model of the driver config
      suite_config.py     # SuiteConfig / Sweep / RunManifest models + YAML loader
    cli/                  # `ufs-chem-assay run`: run config model, stage scripts,
                          #   bash/sbatch execution (console script via hatchling)
    platforms.py          # Platform / Runtime enums, hostname detection
    templates/driver-job.sbatch.j2  # the slurm runtime's per-driver job script
    analysis.py           # descriptive stats (dask distributed), CSV writing
    assertions.py         # post-run assertions (NetCDF file count, filenames)
    combos.py             # sweep → combinations, combo naming, config generation
    examples.py           # example discovery, data downloads, examples report
    logs.py               # namespace logger, level from CECE_LOG_LEVEL
    plotting.py           # session-end spatial plots + GIFs (cartopy/matplotlib)
    report.py             # test-report.csv: row model, outcome precedence, writer
    resolution.py         # pure path-resolution rules (suite path, output roots)
    runner.py             # driver command per runtime (docker run / native +
                          #   launcher), check_output, .out writing, DriverRunResult
    settings.py           # pydantic-settings
    tests/
      config/
        cece/simple-maccity.yaml          # base driver config
        suite/simple-maccity-suite.yaml   # initial suite (--suite-config default)
        suite/exhaustive-maccity-run-only-suite.yaml  # every enum value via ".*"
                                          #   regex sweeps; on-demand, run-only
      ufs_chem_assay/     # the harness's own tests: mocked process call, no docker
      conftest.py         # options, session fixture (generate yamls), param fixture
      test_driver_combos.py               # integration tests (real docker)
      test_examples.py                    # shipped-example execution (--run-examples)
```

Dependencies: `pytest`, `pytest-mock`, `pydantic>=2`, `pydantic-settings`,
`python-ulid`, `pyyaml`, the analysis stack (`pandas`, `xarray`, `netcdf4`,
`dask[distributed]`), and the plotting stack (`matplotlib`, `cartopy`,
`pillow`). Nothing
imported from the CECE repo outside this repository.

## README (user documentation)

A `README.md` ships with v1 — deliberately simple at this
stage: enough for a user to set up and run the suite. It covers:

- **Prerequisites**: a local CECE checkout (external to this repository)
  with the `cece/cece-dev` image built (`./setup.sh` there) and the driver
  built at `./build/cece_standalone_driver`; `uv` installed; the checkout's
  path supplied via `.env` (`cece_root_dir=`), `CECE_ROOT_DIR`, or
  `--cece-root-dir`.
- **Setup**: `uv sync` + a `.env` file with the per-machine paths.
- **Running**:
  - full suite: `uv run pytest`
  - fail fast: `uv run pytest -x`
  - a subset: `uv run pytest -k map-consd`
  - alternate suite file / output root: `--suite-config`,
    `--combo-output-root`
  - rerun over an existing output root: `--combo-clean-root` (without it,
    an existing root is an error)
- **Where results land**: the per-combo directory layout (yaml, `.out`,
  NetCDF) under the output root.
- **Environment variables**: the `CECE_*` settings table.

The README grows alongside future features (evaluation step, richer suite
config) but stays a quick-start document; design rationale lives here, not
there.

## CI and releases

Full rationale in `design/feat/20260724-1449-basic-ci.md`; the load-bearing
mechanics:

- **One toolchain image** (`Dockerfile`, context allowlisted by
  `.dockerignore` to `pyproject.toml`/`uv.lock`/`.pre-commit-config.yaml` so
  `.env` can never enter): official uv base (`python3.14-bookworm-slim`),
  `git` + `g++` (cartopy has no CPython 3.14 wheels — source build), the
  frozen dependency set synced into `/opt/venv`
  (`UV_PROJECT_ENVIRONMENT`), and pre-baked pre-commit hook environments
  (`PRE_COMMIT_HOME=/opt/pre-commit`) so CI needs no network for hooks.
  Source is never baked: runs bind-mount the checkout at `/repo` and
  re-run `uv sync --frozen` (no-op when the lock matches the image; catches
  drift when it doesn't).
- **`ci.yaml`** (all PRs + pushes to `develop`/`main` as post-merge
  validation): a `build` job warms the buildx GitHub Actions cache; the
  `pre-commit` and `tests` jobs rebuild as pure cache hits — fresh job VMs
  cannot share a localhost registry, so the gha cache *is* the cross-job
  reuse — and push to a job-local `registry:2` service (the
  docker-container buildx driver holds results inside BuildKit; the
  localhost push is how the job's docker daemon gets the image without a
  tarball round-trip). Everything runs inside the container; no image
  leaves a runner. The harness suite runs with zero `CECE_*` environment —
  it mocks `CECE_ROOT_DIR` itself where needed.
- **Conventional commits, enforced twice**: the
  `conventional-pre-commit` commit-msg hook aborts a non-conforming commit
  as it is being made (`default_install_hook_types` makes plain
  `pre-commit install` cover it), and CI checks the PR title with the same
  baked hook (title passed via environment variable only — PR titles are
  attacker-controlled). Squash-merge subjects come from PR titles;
  python-semantic-release parses them to compute versions.
- **Releases are manual-only**: `semantic-release.yml` is
  `workflow_dispatch`, dispatched against `develop` (rc prereleases) or
  `main` (releases); a guard fails any other ref. The official PSR v10
  action (the one deliberate exception to "everything in our container" —
  it runs PSR in its own) reads `[tool.semantic_release]` from
  `pyproject.toml` and pushes the version-bump commit (`[ci skip]`), tag,
  and `CHANGELOG.md` with `permissions: contents: write`. `vcs_release:
  false` and the absence of any publish step mean nothing is released to
  GitHub Releases or PyPI. PSR is not a project dependency — the action
  is its only runner; local `--noop` previews use an ephemeral
  `uv tool run --from 'python-semantic-release>=10,<11'` invocation.

## Future work

- **Assertion / evaluation step**: post-run evaluation that inspects the
  captured driver output (already persisted per combo as `.out`), eventual
  real driver log files, and the produced NetCDF
  to assert on values and expected-error conditions, rather than exit code
  alone.
- **Richer suite configuration**: base-config overrides, per-combo excludes /
  expected-failure lists, multiple named sweeps in one file.
- Possible `pytest-xdist` parallelism — combinations are already fully isolated
  (own container, own directory), so `-n auto` should be safe.

## Resolved decisions

- Test-generated data goes to a pytest temp directory by default (mounted
  into the container at `/combo_runs`), so the repo checkout stays clean and
  no `.gitignore` entries are needed. An explicit `--combo-output-root` opts
  into a `/work`-relative root that persists in the checkout. Either way the
  root is bind-mounted, so artifacts survive `--rm`.
- Input data (`/work/data/MACCity_4x5.nc`) is guaranteed present in the
  mounted checkout.
- Exit code 0 is the sole pass criterion for v1; log/NetCDF inspection comes
  with the future evaluation step.
- The sweep is YAML-configured from day one; the initial suite covers only
  `mapalgo ∈ {bilinear, consd, passthrough}` (3 runs), not the 864-combo full
  product.
- **String-valued assertion fields use the `"__ignore__"` sentinel**
  (`suite_config.IGNORE_VALUE`) to mean "don't check", and it is their
  default. A plain `null` cannot serve — it already means "assert the value
  is absent". Every future string assertion follows this three-way
  convention: sentinel = skip, null = assert absent, string = assert equal.
- Every YAML-backed config model (the full `CeceConfig` and `SuiteConfig`
  hierarchies, plus `RunManifest`) inherits `models/base.py:StrictModel`
  (`extra="forbid"`): unknown keys at any nesting level fail at load time
  instead of being silently dropped. Each run is identified by a runtime
  ULID — logged at session start, stamped into every stats row (`run_id`),
  and recorded with the resolved suite in `<output-root>/run.yaml`; it is
  never read from configuration.
- Data-carrying objects (`ComboRoots`, `GeneratedCombo`, `DriverRunResult`)
  are frozen pydantic models, consistent with the config models — not
  dataclasses. The exception is the enumeration machinery in `combos.py`
  (`Dimension`, `Combo`), which holds callables and generic enum members
  that pydantic cannot deep-validate; those stay dataclasses and are
  isinstance-checked (`InstanceOf`) where they appear as model fields.
