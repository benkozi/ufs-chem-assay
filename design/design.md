# ufs-chem-assay — Design

## Goal

A standalone pytest-based test suite that exercises an application's
driver (CECE's `cece_standalone_driver` today) across combinations of the
enum-valued configuration options its driver config declares (for CECE,
`src/applications/cece/config.py`). Everything that knows the application
lives in an **application adapter** (see Applications below); the
combinations to sweep are declared in a YAML **suite configuration**
validated by pydantic. Each
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
`design/spike/20260901-1229-rename-and-plan-for-catchem/20260901-1229-rename-and-plan-for-catchem.md` and
`design/feat/20260901-1701-rename-repo/20260901-1701-rename-repo.md`.

## Non-goals

- pytest's command line is the *test* entry point. The `ufs-chem-assay
  run` command (`src/cli/`, 2026-09-03) orchestrates environment, the
  application's build, data, and the pytest invocation from one run config
  — it never re-implements test logic (see
  `design/feat/20260903-1453-run-on-rdhpc/20260903-1453-run-on-rdhpc.md`).
  Since 2026-09-23 that run config is the **complete YAML surface** of a
  run: `harness:` mirrors every harness-wide setting plus the pytest
  options, each `applications.<name>:` section mirrors that adapter's
  settings plus how to obtain and build it (a mirror test enforces both),
  and command-line arguments are overrides on it (`--override
  key:path=value`, YAML-scalar values merged before validation; precedence
  override > `--platform`/`--root-dir` > file > derived defaults). Every
  invocation writes the effective configuration to
  `<root_dir>/scripts/run-config.yaml`. Stages render per application as
  `<NN>-<stage>-<application>.sh`; a run config naming several
  applications runs one pytest session per application.
- No dependency on existing CECE Python infrastructure; the runner lives in
  its **own repository** with its own `uv`-managed environment. The
  application checkout (driver build, input data) is external, located via
  the adapter's `root_dir` setting (`CECE_ROOT_DIR` env var, `.env`, or
  the run config — there is no command-line flag) and mounted at the
  adapter's container workdir (`/work` for CECE) in the driver container (see
  `design/fix/20260717-1029-portability-external-cece/20260717-1029-portability-external-cece.md`).
- No online baseline retrieval or baseline manifest yet — baselines are
  local directories keyed by ULID (see
  `design/feat/20260716-1113-compare-with-baseline/20260716-1113-compare-with-baseline.md`); no stats-CSV
  diffing (the comparison targets the NetCDF files themselves).

## Applications

Every piece of code that knows the application under test sits behind
one adapter — `applications/base.py:Application`, an abstract base class —
selected by name from a registry (`applications/registry.py`, a plain
dict; `cece` is the only entry and the default). The shared code
(enumeration, the pytest session, the runtimes, stats, plots, comparison,
reporting, the CLI's harness stage) imports only the base module and
calls through the adapter; the adapters (`applications/<name>/`) import
the shared modules freely. Full rationale in
`design/feat/20260914-1643-application-agnostic/20260914-1643-application-agnostic.md`
(Phase B of the CATChem plan in
`design/spike/20260901-1229-rename-and-plan-for-catchem/20260901-1229-rename-and-plan-for-catchem.md`).

The adapter surface: constants (`name`, `env_prefix` — its settings
namespace and the `${<PREFIX>ROOT_DIR}` suite token, `container_workdir`,
`default_suite`, `checkout_dirname`, `standard_dimensions`), the models it
subclasses (`settings_model`, `config_model`, `suite_model` with the
application's sweep and selector schemas, `run_section_model`), an
optional `examples` support, and the methods the shared code needs:
`dimensions` (sweep → the generic `(Dimension, values)` list),
`build_config`, `effective_parameters` (combos.csv rows), the derived
expectations (`expected_output_count`, `expected_output_filenames`,
`output_variable_names`), `selector_matches` (baseline selectors), and
`stage_lines` (the CLI's source/build/data bodies). Each adapter narrows
the generic base types it receives with one `isinstance` per method — a
wrong `application:` cannot reach it, since its suite model validated the
input.

**One application per pytest session.** Every suite names its application
(`application:`, default `cece`); `--application` (or `ASSAY_APPLICATION`,
the flag winning) restricts a session to that application's suites and,
with no `--suite-config`, runs the adapter's default suite; without it
the selected suites must agree. A comprehensive run over several
applications is the CLI's job (see the run config under Non-goals): one
section per application, stages rendered per application, one pytest
session each.

**Two settings namespaces.** Harness-wide settings (`settings.Settings`)
read `ASSAY_*`, with the pre-adapter `CECE_*` spellings accepted as
fallbacks until the second adapter lands; each adapter's
`ApplicationSettings` subclass (`CeceSettings`: `root_dir`, `docker_image`,
`driver_path`, `modulefile`) reads its own prefix. Both classes ignore the
other's keys in the shared `.env`.

**Every artifact names the application.** An `application` column sits
immediately before `suite` in `combos.csv`, `test-report.csv`, and the
stats and comparison CSVs; `run.yaml` records `application`,
`application_commit` (the checkout's HEAD), and the harness's own
`harness_version` and `harness_commit` (`identity.py`; `-dirty` when the
working tree is edited, null when not run from a git checkout).

## Suite configuration

The sweep is defined in a YAML file loaded into a pydantic model — not
hardcoded. Each entry names an enum dimension and lists the values to sweep;
enums absent from the file are **not swept** and stay at their base-config
values. The combination space is the cartesian product of the listed values.

```yaml
# simple-maccity-suite.yaml — initial suite
name: simple-maccity                       # unique suite name (lowercase slug)
config_path: ../cece/simple-maccity.yaml   # base driver config (suite-relative)
application: cece                          # optional; selects the adapter (default cece)
timeout_s: 10                              # per combination; capped by ASSAY_RUN_TIMEOUT_S
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
    ulid: 01KXNXCJ86E8Z2FKVAXRER5ND4       # baseline under ASSAY_BASELINE_ROOT_DIR
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

class SuiteConfig(StrictModel):  # generic (models/suite_config.py); unknown keys rejected
    name: str           # unique suite name; convention: X lives in X-suite.yaml
    application: str    # registry name, default "cece"; the loader picks the subclass
    config_path: Path   # base driver config; relative → suite-file dir
    timeout_s: int      # per-combination driver timeout (seconds)
    sweep: SweepBase    # the adapter's schema; absent/empty → the single "base" combination

class CeceSuiteConfig(SuiteConfig):  # applications/cece/suite.py
    sweep: CeceSweep    # the models above; baseline selectors mirror them likewise
```

`applications.registry.load_suite` reads the file's `application` key,
validates the document with that adapter's suite model, and resolves
`config_path`; `RunManifest.suites` is typed `SerializeAsAny`, so
`run.yaml` records each suite with its adapter's schema.

A sweep value list may instead be a **regex string**, expanded (fullmatch)
against the enum's values into the sorted matching list at load time — so
`mapalgo: ".*"` always means every value, including ones added after the
suite was written, and `run.yaml` records the expanded list (the run stays
reproducible as enums grow). A regex matching nothing, like an invalid one,
fails the load. See
`design/feat/20260716-1647-exhaustive-maccity/20260716-1647-exhaustive-maccity.md`, whose
`exhaustive-maccity-run-only-suite.yaml` sweeps `".*"` on every
driver-meaningful dimension and pins the inert `category` label to
`undefined` (240 combinations, run on demand — typically with `--dry-run`
first).

Duplicate sweep values, duplicate stream names, and unknown keys are all
rejected at load; sweep selectors (stream names, species keys, entry counts)
are validated against the loaded base config at session start, before any
container runs. See
`design/feat/20260709-1131-attach-sweeps-to-streams/20260709-1131-attach-sweeps-to-streams.md`.

A suite file fully describes a run: which base scenario (`config_path`),
the per-combination timeout, and which sweep. Full `config_path` resolution
and timeout semantics live in
`design/feat/20260707-1515-use-cece-config-directory/20260707-1515-use-cece-config-directory.md`.

`sweep:` is optional: a suite attaching no dimensions runs its base config
as the single combination named `base` (its id a runtime ULID like every
combo's).
A `config_path` starting with the literal `${CECE_ROOT_DIR}` token — the
adapter's own root variable, `${<PREFIX>ROOT_DIR}` — anchors on the
application checkout (the adapter settings' `root_dir`) — how the checked-in
`ex1-suite.yaml` … `ex7-suite.yaml` run the checkout's shipped example
configs (`examples/config/cece_config_ex*.yaml`) as ordinary suites with
the full pipeline; using such a suite without a configured root is the
standard root-dir usage error. Generated combo configs also always point
`driver.log_file` into the combo's output directory, so no base config —
the examples set relative paths — can write a log into the checkout. See
`design/feat/20260724-0907-examples-as-suites/20260724-0907-examples-as-suites.md`.

Reusing the enums from the adapter's config model means invalid values fail
at suite-load time with a pydantic error, before any container runs.

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
`design/feat/20260716-1647-exhaustive-maccity/20260716-1647-exhaustive-maccity.md`): `Mapalgo` holds only the
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
`design/feat/20260724-1013-run-multiple-suite-configs/20260724-1013-run-multiple-suite-configs.md`.

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

**Requirement — all config construction goes through the adapter's config
model.** Every generated driver config is built as an instance of the
adapter's `DriverConfig` subclass (`CeceConfig`; base config and per-combo
mutations alike) and written to disk only via its `to_yaml()`. No
hand-assembled dicts, string templates, or direct `yaml.dump` calls
anywhere in the generator. This guarantees every config the driver
receives has passed pydantic validation, and keeps serialization behavior
(`exclude_none`, key ordering, the YAML 1.2 boolean handling in
`models/yaml.py`, shared with command-line override parsing) in one place.
If a combination needs a field the model doesn't have, the fix is to
extend the config model — not to bypass it.

## Directory layout (runtime artifacts)

**By default, all test-generated data — combo yamls, captured output, NetCDF —
is written to a pytest-managed temporary directory** (session-scoped
`tmp_path_factory`, the machinery behind the `tmp_path` fixture). Nothing
lands in the repo checkout and nothing needs git-ignoring; pytest keeps the
last few runs under its base temp dir and prunes older ones.

Passing `--combo-output-root=PATH` opts out of the temp default: the path is
then interpreted as container-relative, resolved against `/work` (the mounted
CECE checkout), so results persist in that checkout. Resolving it requires
the adapter's `root_dir`, so an explicit output root without
`CECE_ROOT_DIR` fails at session start — even under `--dry-run`.

Layout under the output root is the same either way:

```
<output-root>/                 # default: pytest tmp dir; else /work-relative
  run.yaml                     # RunManifest: session ULID, application,
                               #   application_commit (the checkout's HEAD SHA; null
                               #   only when no checkout is configured — an
                               #   unresolvable SHA for a configured root fails the
                               #   session at start), harness_version, harness_commit,
                               #   platform, runtime, modulefile, and every resolved
                               #   suite in selection order
  combos.csv                   # effective-parameter table: run_id, combo_id,
                               #   application, suite, name, target, field, value, swept
  test-report.csv              # every combo-test's outcome: pytest_name, application,
                               #   suite, combo_id, combo, result (passed/failed/skipped)
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
combo's artifacts — directives, `CECE_MODULEFILE` load, `ASSAY_JOB_ENV`,
and the driver behind `srun --ntasks=1` — so the harness venv never sees
the module environment and a failed job is reproducible by hand; and
`native` — the driver as a host process, `cwd` = the application checkout,
prefixed by the `launcher` setting, for a session inside an allocation. The platform is detected from the
hostname (`ASSAY_PLATFORM` overrides; `local` when nothing matches) and
`run.yaml` records `platform`, `runtime`, and `modulefile`. The path model is one
abstraction: `ComboRoots.driver` is the output root *as the driver sees
it* — a container path under docker, the host path natively — and
generated configs carry it in `output.directory` and `driver.log_file`.
The base config's data path is cwd-relative (`data/MACCity_4x5.nc`) so
it resolves under both runtimes. See
`design/feat/20260903-1453-run-on-rdhpc/20260903-1453-run-on-rdhpc.md`.

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

The `-v` flag carries the host→container mapping: the host-side
application checkout — the adapter's `root_dir` setting, supplied by
`CECE_ROOT_DIR` (environment, `.env`, or the run config's export) — maps to
the adapter's container workdir (`/work`). The runner lives in a separate
repository, so there is no derivable default: driver execution without a
configured `root_dir` (or with one that is not an existing directory)
fails at collection time with a `UsageError`, before any test runs. The
`-w` flag takes a container path only — it sets the driver's working
directory to the mounted application root, so the relative `./build/...` driver
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
  `design/feat/20260708-1055-add-assertions-for-file-counts/20260708-1055-add-assertions-for-file-counts.md`.
- **Fail fast vs. continue** uses pytest built-ins — no custom flags.
  **Continue is the default and the desired behavior**: a plain `pytest`
  invocation runs every combination to completion regardless of individual
  failures, so one bad combo never hides results for the rest. Fail-fast is
  opt-in via `pytest -x` (first failure) or `--maxfail=N`.
- **Custom options** (registered in `conftest.py` via `pytest_addoption`;
  none is application-specific):
  - `--application=NAME` — the session's application (overrides
    `ASSAY_APPLICATION`): suites of other applications are dropped from
    the selection, none left is a `UsageError`, and the default suite
    becomes the adapter's. Without it, the selected suites' `application`
    values must agree. An unknown name lists the registry.
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
    Default: the application's `default_suite` (`simple-maccity-suite.yaml`
    for cece) — a literal filename fullmatches only itself, so a bare
    `pytest` always runs exactly that suite no matter how many suites the
    roots contain.
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
  - `--run-examples` — flag, off by default; runs the application checkout's (CECE's)
    shipped `examples/config/cece_config_ex*.yaml` through the checkout's
    own `examples/run-example.py` entrypoint, wrapped in docker by this
    runner (the entrypoint is container-agnostic and never spawns docker
    itself; exit 0 = pass). Data comes from one session-scoped pass of
    `examples/download-example-data.py`, invoked per example id with
    `--dst-dir <root>/data` (a failing download is logged and recorded,
    never fatal). All
    examples are expected green since the consolidation fix
    (`design/fix/20260720-1500-fix-cece-examples/20260720-1500-fix-cece-examples.md`). ex1/ex7's
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
  - The application checkout root has **no flag** (the former
    `--cece-root-dir` was retired with the adapter extraction): the
    adapter's `ROOT_DIR` variable, `.env`, or the run config's export is
    its one source, so each process has one precedence chain
    (environment > `.env` > default). Required to execute the driver: when
    combo tests are collected without `--dry-run`, a missing or nonexistent
    `root_dir` raises `UsageError` in `pytest_collection_modifyitems` —
    collection time rather than sessionstart, so harness-only runs (which
    collect no `driver_run` tests) stay green with no environment, while
    the failure still lands before any test executes.
- **Test report.** A `pytest_runtest_makereport` hookwrapper collects every
  combo-parameterized test's outcome (phases combine failed > skipped >
  passed via `report.worst_result`); `pytest_sessionfinish` writes
  `test-report.csv` (pytest_name, application, suite, combo_id, combo,
  result) first in its artifact pipeline, whenever combinations ran. Non-combo (harness) tests
  are not reported.
- **Existing explicit output root is an error by default.** When
  `--combo-output-root` is given, the runner checks at session start — before
  any configs are generated or containers run — whether that root exists on
  the host. If it does and `--combo-clean-root` was not given, the session
  fails immediately with a clear message — prior results are never silently
  mixed with or overwritten by a new run. With `--combo-clean-root`, the
  existing root is deleted wholesale and recreated — but only when it is a
  previous harness root (`run.yaml` at its top); any other directory is
  refused, because an absolute root under the native/slurm runtimes can
  point anywhere. The rmtree targets only the resolved output root, never
  its parent. The default temp root needs no
  guard: `tmp_path_factory` allocates a fresh directory every session.
- **Selection**: `pytest -k <expr>` against the combo-name ids runs subsets.

## Settings

Two `pydantic-settings` classes supply environment-derived configuration,
keeping the pytest CLI for run-shaping options only (`--application` is
the one flag that feeds a setting; see Pytest integration):

- `settings.Settings` — **harness-wide**, env prefix `ASSAY_` with the
  pre-adapter `CECE_` spellings accepted as fallbacks through layered
  sources (init kwargs > `ASSAY_` environment > `CECE_` environment >
  `ASSAY_` `.env` > `CECE_` `.env` > default); the fallback goes away when
  the second adapter lands.
- the adapter's `ApplicationSettings` subclass (`CeceSettings`, prefix
  `CECE_`) — the checkout root, image, driver path, modulefile, and the
  checkout's commit SHA for run.yaml.

Both are **frozen** (constructed once at sessionstart) and `extra="ignore"`,
since the one cwd-relative **`.env` file** (gitignored — it carries
per-machine absolute paths) holds both namespaces' keys. Matching is
case-insensitive, so lowercase `cece_root_dir=` keys work. pydantic-settings
ships `python-dotenv`; no extra dependency.

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

Harness-wide (`Settings`; `platform`, `runtime`, `launcher`, `sbatch_args`,
`slurm_queue_wait_s`, `job_env` are described under Execution model):

| Setting          | Env var               | Default              |
|------------------|-----------------------|----------------------|
| `application`    | `ASSAY_APPLICATION`   | unset → inferred from the selected suites; `--application` overrides |
| `run_timeout_s`  | `ASSAY_RUN_TIMEOUT_S` | 300 — caps the suite's `timeout_s` when smaller |
| `log_level`      | `ASSAY_LOG_LEVEL`     | `INFO`               |
| `baseline_root_dir` | `ASSAY_BASELINE_ROOT_DIR` | unset → cwd; baselines at `<root>/<ulid>/` |
| `enable_baseline_comparisons` | `ASSAY_ENABLE_BASELINE_COMPARISONS` | `true`; false skips all comparison tests |
| `dask_nworkers`  | `ASSAY_DASK_NWORKERS` | unset → all available; else int > 0 |
| `config_search_path`       | `ASSAY_CONFIG_SEARCH_PATH`       | unset |
| `suite_config_search_path` | `ASSAY_SUITE_CONFIG_SEARCH_PATH` | unset → built-in suite dir only; `os.pathsep`-separated list, searched recursively for suite selection |

The CECE adapter (`CeceSettings`):

| Setting          | Env var               | Default              |
|------------------|-----------------------|----------------------|
| `root_dir`       | `CECE_ROOT_DIR`       | unset — required to run the driver |
| `docker_image`   | `CECE_DOCKER_IMAGE`   | `cece/cece-dev`      |
| `driver_path`    | `CECE_DRIVER_PATH`    | `./build/cece_standalone_driver` |
| `modulefile`     | `CECE_MODULEFILE`     | unset                |

`config_search_path`, when set, overrides normal config resolution: the
search directory is prepended to the suite's relative `config_path`, kept
whole so nested directories work (full semantics in
`design/feat/20260707-1515-use-cece-config-directory/20260707-1515-use-cece-config-directory.md`; that doc's
**suite-side** prepend semantics are superseded by the regex selector —
see `design/feat/20260717-1052-default-suite/20260717-1052-default-suite.md`). `suite_config_search_path`
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
    applications/
      base.py             # Application ABC, ApplicationSettings, DriverConfig, SweepBase,
                          #   SweepSelectorBase, ApplicationRunSection, ExamplesSupport
      registry.py         # REGISTRY (name -> adapter), get_application, load_suite
      cece/
        application.py    # CeceApplication: the wiring, registered by name
        config.py         # the pydantic model of CECE's driver config (CeceConfig)
        settings.py       # CeceSettings (CECE_*), the /work container workdir
        suite.py          # CeceSweep + selectors, CeceSuiteConfig, selector matching
        combos.py         # dimensions, build_config (cece.log), effective-parameter rows
        assertions.py     # expected count/filenames/variable names, STANDARD_DIMENSIONS
        examples.py       # example discovery, download, run command
        cli.py            # CeceRunSection, source/build/data stage bodies
    models/
      base.py             # StrictModel: extra="forbid" base for all config models
      suite_config.py     # generic SuiteConfig, Assertions, Analysis, Plotting,
                          #   BaselineComparison, RunManifest
      yaml.py             # the YAML-1.2-boolean loader (configs, override values)
    cli/                  # `ufs-chem-assay run`: run config model (applications map),
                          #   overrides, per-application stage scripts, bash execution
    identity.py           # HARNESS_NAME, HARNESS_ROOT, harness_version/commit
    platforms.py          # Platform / Runtime enums, hostname detection
    templates/driver-job.sbatch.j2  # the slurm runtime's per-driver job script
    analysis.py           # descriptive stats (dask distributed), CSV writing
    assertions.py         # generic post-run assertions (expectations passed in)
    combos.py             # Dimension, Combo, enumerate_combos, write_combos_csv
    comparison.py         # baseline resolution (via the adapter) + NetCDF comparison
    examples.py           # generic example result models + the session report
    logs.py               # namespace logger, level from ASSAY_LOG_LEVEL
    plotting.py           # session-end spatial plots + GIFs (cartopy/matplotlib)
    report.py             # test-report.csv: row model, outcome precedence, writer
    resolution.py         # pure path-resolution rules (suite path, output roots)
    runner.py             # driver command per runtime (docker run / native +
                          #   launcher / sbatch), check_output, .out writing
    settings.py           # harness-wide pydantic-settings (ASSAY_*, CECE_* fallback)
    tests/
      config/
        cece/simple-maccity.yaml          # base driver config
        suite/simple-maccity-suite.yaml   # initial suite (--suite-config default)
        suite/exhaustive-maccity-run-only-suite.yaml  # every enum value via ".*"
                                          #   regex sweeps; on-demand, run-only
      ufs_chem_assay/     # the harness's own tests: mocked process call, no docker
        applications/cece/  # the CECE adapter's tests (test_cece_*.py)
        stubs.py          # an inert second adapter for several-application tests
      conftest.py         # options, session fixture (generate yamls), param fixture
      test_driver_combos.py               # integration tests (real docker)
      test_examples.py                    # shipped-example execution (--run-examples)
```

Dependencies: `pytest`, `pytest-mock`, `pydantic>=2`, `pydantic-settings`,
`python-ulid`, `pyyaml`, the analysis stack (`pandas`, `xarray`, `netcdf4`,
`dask[distributed]`), and the plotting stack (`matplotlib`, `cartopy`,
`pillow`). Nothing
imported from any application repository outside this one.

## README (user documentation)

A `README.md` ships with v1 — deliberately simple at this
stage: enough for a user to set up and run the suite. It covers:

- **Prerequisites**: a local CECE checkout (external to this repository)
  with the `cece/cece-dev` image built (`./setup.sh` there) and the driver
  built at `./build/cece_standalone_driver`; `uv` installed; the checkout's
  path supplied via `.env` (`cece_root_dir=`), `CECE_ROOT_DIR`, or the
  run config.
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
- **Environment variables**: the `ASSAY_*` (harness) and `CECE_*` (adapter)
  settings tables.
- **Configuring a run**: the run config as the complete YAML surface,
  `--override`, precedence, and the `run-config.yaml` artifact.

The README grows alongside future features (evaluation step, richer suite
config) but stays a quick-start document; design rationale lives here, not
there.

## CI and releases

Full rationale in `design/feat/20260724-1449-basic-ci/20260724-1449-basic-ci.md`; the load-bearing
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
  leaves a runner. The harness suite runs with zero `ASSAY_*`/`CECE_*`
  environment — it mocks `CECE_ROOT_DIR` itself where needed.
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
  hierarchies, the run config, plus `RunManifest`) inherits `models/base.py:StrictModel`
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
