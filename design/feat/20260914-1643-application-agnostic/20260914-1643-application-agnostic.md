# Feature: extract the `Application` adapter (Phase B of the CATChem plan)

Refines https://github.com/benkozi/ufs-chem-assay/issues/12 — Phase B of
`design/spike/20260901-1229-rename-and-plan-for-catchem/20260901-1229-rename-and-plan-for-catchem.md`.
Phase A (the rename) landed in
`design/feat/20260901-1701-rename-repo/20260901-1701-rename-repo.md`.

## Goal

Make the harness application-agnostic without changing what it does: every
piece of code that knows CECE — its driver config model, its sweep and
selector schemas, its settings namespace, its driver path, image, container
mount point, log name, derived output expectations, examples tooling, and
the CLI's clone/build/data steps — moves behind one `Application` adapter
selected by name from a registry. The registry holds one entry, `cece`,
and every suite and run config selects it by default, so the tree behaves
exactly as before. Enumeration, the pytest session machinery, the three
runtimes, stats, plots, comparison, and reporting stay shared and never
import anything CECE-shaped again.

Deliverables:

1. **The adapter and registry** (`src/applications/`): an abstract base
   class `Application`, the generic base models the shared code types
   on (`ApplicationSettings`, `DriverConfig`, `SweepBase`,
   `SweepSelectorBase`, `SuiteConfig`, `RunConfig`), and the CECE
   implementation as the only registered entry.
2. **`application:` as a discriminator** in suite files and run configs
   (default `cece`), plus a **session-level switch** — `--application`
   on pytest, `ASSAY_APPLICATION` in the environment, `application:` in
   the run config — that restricts a session to one application's
   suites (cece-only today, catchem-only in Phase C).
3. **Two settings namespaces**: harness-wide settings move to `ASSAY_*`
   (the `CECE_*` spellings keep working as fallbacks); application
   settings keep the adapter's own prefix (`CECE_ROOT_DIR`,
   `CECE_DOCKER_IMAGE`, `CECE_DRIVER_PATH`, `CECE_MODULEFILE`).
4. **Zero behaviour change, proven**: every checked-in suite's `--dry-run`
   output root is byte-identical before and after apart from ULIDs and
   one deliberate addition — the `application` tag in every CSV and in
   `run.yaml` (section 12; user decision, 2026-09-14) — and the harness
   suite (313 today) and the real `simple-maccity` docker run are green
   on both trees.
5. **Vocabulary**: README, the run-config templates, the runbook, and
   option help say "the application" / "the target driver"; CECE is named
   only where CECE is meant (its adapter, its settings, its suites, the
   concrete Ursa commands).
6. **Fully YAML-configured runs, CLI arguments as overrides**: the run
   config carries every harness and application setting a run needs,
   and `ufs-chem-assay run` takes `--override key:path=value` entries
   applied onto the loaded file before validation; the named flags are
   sugar for specific overrides. The effective configuration is written
   beside the rendered scripts.

Out of scope: CATChem itself (Phase C: its adapter, settings, config
model, a sweep-less smoke suite), plotting capability flags for column
output, multi-application sessions, the run.yaml provenance map, and
entry-point plugin discovery — each noted under Future work where this
design leaves a hook for it.

## Pre-design audit (fresh facts, 2026-09-14)

Verified on `feat/application-agnostic` at `6f36598`; harness suite
**313 passed** (`uv run pytest src/tests/ufs_chem_assay`).

1. **Where CECE lives in `src/`** (case-insensitive hits, code only):
   `combos.py` 21, `settings.py` 17, `models/suite_config.py` 15,
   `cli/run_config.py` 15, `cli/stages.py` 34, `examples.py` 10,
   `assertions.py` 7, `runner.py` 6, `resolution.py` 4,
   `models/cece_config.py` (the whole file), `tests/conftest.py` 24,
   `platforms.py` 2 (docstrings), `comparison.py` 2 (`selector.cece_data`),
   `cli/main.py` 1 (`CECE_LOG_LEVEL`), `analysis.py`/`plotting.py` 1 each
   (provenance comments only). Already generic: `report.py`, `logs.py`,
   `resolution.select_suites`, `analysis.py`, `plotting.py`,
   `comparison.py` apart from the selector walk, the enumeration core in
   `combos.py` (`Dimension`, `Combo`, the cartesian product, naming, ULIDs).
2. **The config model is self-contained.** `CeceConfig` (`StrictModel`,
   `from_yaml` with the YAML-1.2 boolean loader, `to_yaml` with
   `exclude_none` / `sort_keys=False`) is imported by `combos.py`,
   `assertions.py`, `runner.py` (`DriverRunResult.config`),
   `models/suite_config.py` (the six enums), `tests/conftest.py`
   (`GeneratedCombo.config`, `base_config`), and ten harness test files.
   `models/__init__.py` re-exports it; nothing in `src/` uses the
   re-export path.
3. **The sweep and selector models mirror CECE's config** by design:
   `StreamSweep`/`SpeciesEntrySweep`/`CeceDataSweep`/`Sweep` and their
   `*Selector` mirrors in `suite_config.py`, consumed by
   `combos._species_dimensions`/`_stream_dimensions` (which also
   validate selectors against the base config) and by
   `comparison._selector_matches` (walks `selector.cece_data.streams` and
   `selector.species` against `Dimension.group/key/index`). `Dimension`
   already carries behaviour as data (`apply` callables) and generic
   attachment metadata (`group`, `key`, `index`) — the enumeration core
   needs no change.
4. **Config generation and the parameter table are CECE-shaped**:
   `combos.build_config` loads `CeceConfig`, applies values, sets
   `output.directory` and `driver.log_file = <dir>/cece.log`;
   `_effective_parameter_rows` enumerates `_SPECIES_FIELDS`/`_STREAM_FIELDS`
   from the generated config. `write_combos_csv` itself only needs the
   rows.
5. **Derived assertions read CECE fields**: `derive_expected_nc_file_count`
   (driver start/end/timestep, output frequency), `expected_nc_filenames`
   (`filename_pattern`), `_output_field_names` (`output.fields`), and
   `STANDARD_DIMENSIONS = (time, lev, lat, lon)`. `assert_species_attributes`
   and the attribute-diff logic are generic already.
6. **Settings mix two concerns under one prefix.** `Settings`
   (`env_prefix="CECE_"`, frozen, `.env`) holds harness-wide values —
   `platform`, `runtime`, `launcher`, `sbatch_args`, `slurm_queue_wait_s`,
   `job_env`, `run_timeout_s`, `log_level`, `baseline_root_dir`,
   `enable_baseline_comparisons`, `dask_nworkers`, `config_search_path`,
   `suite_config_search_path` — beside four application values:
   `root_dir`, `docker_image` (`cece/cece-dev`), `driver_path`
   (`./build/cece_standalone_driver`), `modulefile`. `get_cece_commit_sha`
   is generic git-on-root_dir. The pytest flag `--cece-root-dir` feeds
   `root_dir` as an init kwarg; the harness tests scrub `CECE_ROOT_DIR`,
   `CECE_ROOT`, `CECE_SUITE_CONFIG_SEARCH_PATH`, and (autouse)
   `CECE_PLATFORM`/`CECE_RUNTIME`/`CECE_LAUNCHER`.
7. **The runner's CECE knowledge is four values**: the image, the driver
   path, the `/work` mount point (`resolution.CONTAINER_WORK`,
   `docker_prefix`, conftest's `_CONTAINER_TMP_ROOT = /combo_runs` is the
   harness's own), and the `cece driver output` banner printed to stdout
   (not an artifact). The `OMPI_ALLOW_RUN_AS_ROOT*` pair is
   run-as-root-in-container MPI hygiene, not CECE's. The slurm job
   template is command-agnostic; `driver_command` reads
   `settings.driver_path`; `run.yaml` records `cece_commit`, `platform`,
   `runtime`, `modulefile`.
8. **The `${CECE_ROOT_DIR}` token** in `SuiteConfig.from_yaml` anchors the
   seven `ex*-suite.yaml` files on the checkout. `run.yaml` records
   `config_path` resolved (absolute), so the token never reaches an
   artifact.
9. **The CLI is CECE end to end**: `RunConfig.cece: CeceSection`
   (`git_url`, `ref`, `clone_dir`, `update_source`, `modulefile`,
   `cmake_args`, `build_jobs`, `targets`), `clone_dir` defaulting to
   `<root>/CECE`; the `source` stage guards `extern/helm/libs`; `build`
   runs CECE's `scripts/build-and-test-container.py` under docker or
   cmake with CECE's configure-log gates natively; `data` runs
   `examples/download-example-data.py`; `harness` exports `CECE_*` names
   and runs `src/tests/test_driver_combos.py`. `test_stages.py` pins the
   rendered export lines; `test_user_docs.py` pins the templates' location
   and placeholder-freedom.
10. **Examples are CECE's**: `examples.py` discovers
    `examples/config/cece_config_ex*.yaml` and wraps the checkout's two
    entrypoints; `write_examples_report` and the two result models are
    generic. `--run-examples` is refused under slurm.
11. **The dry-run artifact set** a suite produces (the byte-identity
    target): `run.yaml`, `combos.csv`, `test-report.csv`, one
    `<combo_id>/<combo_id>.yaml` per combo, plus `<combo_id>.sbatch` per
    combo under `runtime=slurm` with a configured root. With the default
    tmp root, generated configs carry `/combo_runs/<ulid>` paths under
    docker; `pytest --basetemp=DIR` pins where that root is created.
12. **The docs already lean this way**: README's prerequisites say "the
    application under test … generalizing them is the `Application`
    adapter follow-up"; `HarnessSection.output_root` says "application
    checkout"; the runbook and the Ursa template are concretely CECE and
    should stay so where they name commands.

## Design

### 0. Decisions

| # | decision | why |
|---|----------|-----|
| 1 | An **abstract base class** `Application`, not a `typing.Protocol` | The issue's "protocol" in the interface sense. One implementation, docstring-bearing methods, and pydantic `InstanceOf` checks need a nominal class; structural typing buys nothing here. |
| 2 | **Per-application subclasses of the generic models** where one document belongs to one application (`CeceSuiteConfig(SuiteConfig)`, `CeceSettings(ApplicationSettings)`, `CeceRunSection(ApplicationRunSection)`), dispatched by the registry on the `application:` value or the section key | The spike's "discriminated union" without a closed `Union[...]` type: the registry is the union, so a third application is a registry entry, not an edit to a type annotation. No sibling-peeking validators; mypy sees concrete fields inside each adapter. The run config is the one document that may name several applications, so it holds a keyed `applications:` map of sections rather than being subclassed itself. |
| 3 | **One application per pytest session** (kept by user decision, 2026-09-14); `application:` stays per suite; a **comprehensive run** (CECE and CATChem together) is a CLI-level run config naming several applications, executed as one pytest session per application | The suite needs the field (its sweep schema depends on it); a session needs one set of application settings, one checkout, one image, one mount convention. Everything that differs between applications — checkout, build, image, modulefile — is per stage anyway, so sequential sessions with a joined report lose nothing a mixed session would give. Mixed suite selections in one session are a usage error. |
| 4 | **`--application` / `ASSAY_APPLICATION` / run-config `application:`** as the top-level switch | A named session filter is how "cece-only" / "catchem-only" reads on the command line, in `.env`, and in a run config — independent of where suite files live. |
| 5 | **Harness-wide settings move to `ASSAY_*`**, `CECE_*` accepted as fallback aliases until Phase C lands | "Keep the CECE names only inside the CECE adapter and its settings" cannot hold while `CECE_PLATFORM` configures the harness. The docs and rendered scripts are being reworded now; doing the prefix later touches them twice. Aliases make it zero-change for existing `.env` files and CI. |
| 6 | **The `application` tag is in every artifact** (user decision, 2026-09-14): a column immediately before `suite` in every CSV; `application`, `application_commit`, `harness_version`, and `harness_commit` at the top of `run.yaml`; the suites' `application` field dumped — the one deliberate relaxation of the issue's byte-identity clause | Observability: once CSVs from two output roots are concatenated (comprehensive runs, Phase D's joined report), the application is otherwise recoverable only from a directory path, and nothing today records which harness produced a run. Adding both now means no interim run lacks them. `cece_commit` becomes `application_commit` so no CECE name is left outside the adapter; the diff against the old tree is one column and four keys, normalised away in verification. |
| 7 | **The `${CECE_ROOT_DIR}` token becomes `${<PREFIX>ROOT_DIR}`**, derived from the adapter's `env_prefix` | The seven example suites stay untouched, and the token names the variable that actually anchors it (`${CATCHEM_ROOT_DIR}` in a CATChem suite). |
| 8 | **Suite files stay where they are** (`src/tests/config/suite/`, flat) | Moving them would change the absolute `config_path` recorded in run.yaml. Per-application subdirectories are a Phase C layout choice; the `--application` filter reads the suite's field, not its path. |
| 9 | **Isinstance narrowing at the adapter boundary** instead of a four-way generic `Application[...]` | The shared code types on the base models; the CECE adapter asserts `isinstance(config, CeceConfig)` once per method. No `Any`, no invariance fights in the registry's type. |
| 10 | **The run config is the one YAML surface for a harness run; CLI arguments are overrides on it** (`--override key:path=value`, the regrid-wrapper pattern). pytest does not grow a config-file option | One loader, one precedence chain (`--override` > named flags > file > defaults), one place that turns YAML into pytest flags and `ASSAY_*`/`CECE_*` exports. pytest stays the low-level test entry point whose flags and variables the CLI derives — the design.md non-goal already says so. |
| 11 | **`--cece-root-dir` is retired** (user decision, 2026-09-14); pytest registers no application-specific option | The flag only overrode `CECE_ROOT_DIR` per invocation; the run config sets the root (`applications.cece.clone_dir`) and exports the variable, and a direct pytest user has the variable and `.env`. Each process keeps one precedence chain (environment > `.env` > default), and the registry needs no pytest-option hook. An input change only; no artifact changes. |
| 12 | **Comprehensive runs are orchestrated by the CLI**: `applications:` lists every application in the run; stages render per application (`<NN>-<stage>-<name>.sh`), the harness stage runs one pytest session per application with `--application=<name>` and a per-application output root when several are configured | Builds on decision 3; the Phase B tree ships the shape with one entry so Phase C adds a section, not a mechanism. A joined cross-application report is Phase D. |

### 1. The adapter and the registry (`src/applications/`)

```python
# applications/base.py
class Application(ABC):
    name: str                          # registry key; the `application:` value
    env_prefix: str                    # "CECE_": its settings namespace and root-dir token
    container_workdir: PurePosixPath   # where the checkout is mounted under docker (/work)
    default_suite: str                 # what a bare `--application=<name>` runs
    checkout_dirname: str              # default checkout under the run root ("CECE")
    settings_model: type[ApplicationSettings]
    config_model: type[DriverConfig]
    suite_model: type[SuiteConfig]
    run_section_model: type[ApplicationRunSection]   # its `applications.<name>:` section
    examples: ExamplesSupport | None   # None: the application ships no runnable examples
    standard_dimensions: tuple[str, ...]

    @property
    def root_dir_token(self) -> str: ...          # "${CECE_ROOT_DIR}"

    # enumeration + config generation (combos.py calls these)
    def dimensions(self, sweep: SweepBase, base_config: DriverConfig) -> list[tuple[Dimension, tuple[StrEnum, ...]]]: ...
    def build_config(self, combo: Combo, output_directory: str, config_path: Path) -> DriverConfig: ...
    def effective_parameters(self, combo: Combo, config: DriverConfig) -> list[tuple[str, str, str, bool]]: ...
    # derived assertions (assertions.py calls these)
    def expected_output_count(self, config: DriverConfig) -> int: ...
    def expected_output_filenames(self, config: DriverConfig) -> set[str]: ...
    def output_variable_names(self, config: DriverConfig) -> list[str]: ...
    # baseline selectors (comparison.py calls this)
    def selector_matches(self, selector: SweepSelectorBase, combo: Combo) -> bool: ...
    # CLI stage bodies (cli/stages.py calls this for source/build/data)
    def stage_lines(self, stage: Stage, config: RunConfig, section: ApplicationRunSection) -> list[str]: ...
```

Every method exists today as CECE-specific code (audit items 3–5, 9, 10);
the extraction moves the bodies, it does not rewrite them.

- `ApplicationSettings(BaseSettings)`: `root_dir: Path | None`,
  `docker_image: str`, `driver_path: str`, `modulefile: str | None`, all
  with `Field(description=…)`, frozen, `.env`-aware; `commit_sha()` is
  today's `get_cece_commit_sha` with the application name in its
  messages. `CeceSettings(ApplicationSettings)` sets
  `env_prefix="CECE_"` and the two CECE defaults; nothing else.
- `DriverConfig(StrictModel, ABC)`: abstract `from_yaml(path)` /
  `to_yaml(path)`. `CeceConfig` (moved verbatim to
  `applications/cece/config.py`, its `Any`-typed scheme `options`
  exception intact) inherits it.
- `SweepBase(StrictModel)` and `SweepSelectorBase(StrictModel)`: empty
  strict bases. The base `SuiteConfig` declares
  `sweep: SweepBase = Field(default_factory=SweepBase)` (the sweep-less
  suite); `CeceSuiteConfig` overrides `sweep: CeceSweep` and
  `baseline_comparisons: list[CeceBaselineComparison]` (whose
  `sweep_selector: CeceSweepSelector`). `RunManifest.suites` is
  `list[SerializeAsAny[SuiteConfig]]` — **without `SerializeAsAny`,
  pydantic v2 serializes a subclass instance through the declared base
  schema and run.yaml would lose the sweep**; a red test pins this.
- **Registry** (`applications/__init__.py`): `REGISTRY: dict[str, Application]`
  built from the adapters' `name`, `DEFAULT_APPLICATION = "cece"`,
  `get_application(name)` (unknown name → `ValueError` listing the
  registered names), `load_suite(path, *, config_search_path, root_dir)`
  (below). A plain dict, as the spike decided; entry-point discovery is a
  later hardening that changes nothing about the surface.
- `applications/cece/application.py` is the wiring: a `CeceApplication`
  whose methods delegate to `applications/cece/{combos,assertions,suite,
  examples,cli}.py`. The registry instantiates it at import.

### 2. Settings: two namespaces

| class | prefix | fields |
|-------|--------|--------|
| `settings.Settings` (harness-wide) | `ASSAY_` (fallback `CECE_`) | `application` (new, `str \| None`), `platform`, `runtime`, `launcher`, `sbatch_args`, `slurm_queue_wait_s`, `job_env`, `run_timeout_s`, `log_level`, `baseline_root_dir`, `enable_baseline_comparisons`, `dask_nworkers`, `config_search_path`, `suite_config_search_path` |
| `applications.cece.settings.CeceSettings` | `CECE_` | `root_dir`, `docker_image`, `driver_path`, `modulefile` |

- Fallback mechanics: each harness field carries
  `validation_alias=AliasChoices("ASSAY_<NAME>", "CECE_<NAME>")` through
  a one-line helper, so `ASSAY_PLATFORM` wins when both are set and a
  laptop `.env` with `cece_platform=` keeps working (case-insensitive
  matching applies to aliases; a test proves the `.env` path). Init
  kwargs (`Settings(application=…)`) need populate-by-name on. The
  fallback is removed when the second adapter lands (Phase C), when the
  ambiguity would become real; README says so.
- `platform`/`runtime` derivation, `launcher_argv`, `sbatch_argv`,
  `job_env_pairs` are unchanged. `cli/main.py` reads `ASSAY_LOG_LEVEL`
  with the same fallback.
- The application settings object is built **once at sessionstart for
  the session's application** from `app.settings_model()` — environment,
  `.env`, defaults, nothing else. **`--cece-root-dir` is retired**
  (decision 11): the pytest command line carries no application-specific
  option, `CECE_ROOT_DIR` (or `.env`) is the only root source for a
  direct pytest run, and the CLI exports it from the run config. The
  usage messages that said "pass --cece-root-dir or set CECE_ROOT_DIR"
  say "set CECE_ROOT_DIR". Session state:
  `_APPLICATION: StashKey[Application]`,
  `_APP_SETTINGS: StashKey[ApplicationSettings]`; `_APPLICATION_COMMIT`
  (run.yaml's `application_commit`) is read from
  `app_settings.commit_sha()`.
- The README's `.env` example becomes `cece_root_dir=` plus
  `assay_baseline_root_dir=`; the environment table splits into
  "harness" and "application (CECE)" halves; the integration workflow's
  `CECE_ENABLE_BASELINE_COMPARISONS` becomes `ASSAY_…` (works either
  way; renamed for consistency).

### 3. The suite file: `application:` and per-adapter schemas

```yaml
name: simple-maccity
application: cece            # NEW, optional, default cece; selects the sweep/selector schema
config_path: ../cece/simple-maccity.yaml
…
```

- `SuiteConfig` (base, `models/suite_config.py`) keeps every generic
  field — `name`, `config_path`, `timeout_s`, `assertions`, `analysis`,
  `plotting`, the plotting-requires-stats validator — plus
  `application: str = Field(DEFAULT_APPLICATION, …)` (dumped into
  run.yaml with the suite: decision 6) and `baseline_comparisons:
  list[BaselineComparison]` with the generic `ulid`/`atol`/`plot` fields
  and `sweep_selector: SweepSelectorBase`. `Assertions`, `Analysis`,
  `Plotting`, `AttributesAssertion`, `SpeciesAssertions`, `IGNORE_VALUE`
  stay where they are.
- `applications/cece/suite.py`: `StreamSweep`, `SpeciesEntrySweep`,
  `CeceDataSweep`, `CeceSweep(SweepBase)` (today's `Sweep`), the selector
  mirrors, `CeceBaselineComparison`, `CeceSuiteConfig` with
  `application: Literal["cece"] = "cece"`. Moved, not rewritten; the
  regex-expansion and uniqueness validators go with them.
- `load_suite(path, *, config_search_path, root_dir)` in the registry
  module replaces `SuiteConfig.from_yaml`: `yaml.safe_load`, read
  `application` (default `cece`), `get_application`, `app.suite_model.
  model_validate(raw)`, then today's `config_path` resolution with the
  adapter's `root_dir_token` (decision 7) and the same
  `FileNotFoundError`/`ValueError` messages, reworded to "the
  application checkout" and naming the adapter's option and variable.
  `SuiteConfig.resolve_config_path(…)` holds the resolution so it stays
  unit-testable without the registry.
- Zero change for every checked-in suite: none declares `application`,
  all default to `cece`.

### 4. The session: one application, and the switch to pick it

`pytest_sessionstart`, in order:

1. `Settings()` (with `application=` from `--application` when given —
   init kwargs beat the environment in pydantic-settings; the one
   remaining flag-fed setting now that the root flag is gone).
2. `select_suites(...)` as today. The `--suite-config` default becomes
   `None` and is resolved here: `app.default_suite` when the application
   is explicit, else `DEFAULT_APPLICATION`'s — so a bare `pytest` still
   runs `simple-maccity-suite.yaml`, and `pytest --application=catchem`
   will run that adapter's smoke suite in Phase C.
3. Each match is loaded through `load_suite`. **With `--application`
   set**, suites naming another application are dropped from the
   selection (one INFO line each, `suite X: application Y, skipped`);
   zero survivors → `UsageError` listing what was dropped. **Without
   it**, every loaded suite must name the same application; a mix →
   `UsageError` naming `--application`. The survivor is stashed as
   `_APPLICATION`.
4. `app.settings_model()` → `_APP_SETTINGS`; `commit_sha()` →
   `_APPLICATION_COMMIT` (run.yaml's `application_commit`, decision 6);
   `harness_version()` / `harness_commit()` → `_HARNESS_VERSION` /
   `_HARNESS_COMMIT` (section 12).
5. `app.config_model.from_yaml(suite.config_path)`,
   `enumerate_combos(app.dimensions(suite.sweep, base_config))`,
   `resolve_baseline_comparisons(app, suite.baseline_comparisons, combos)`
   — as today, through the adapter.
6. Roots: `resolve_output_roots(option, app_settings.root_dir, runtime,
   container_workdir=app.container_workdir)`; the "resolves against the
   CECE repository root" messages become "the application checkout" and
   name the adapter's option/variable.

Elsewhere in the session: `pytest_collection_modifyitems` guards
`app_settings.root_dir` with the adapter's words; `combo_roots` uses
`_CONTAINER_TMP_ROOT` (harness-owned, unchanged) under docker;
`generated_combos` calls `app.build_config` and `app.effective_parameters`
(through `write_combos_csv(entries)` whose entries carry the rows) and
`write_job_script(settings, app_settings, …)`; `driver_run` passes both
settings objects to `run_driver`. `pytest_generate_tests` for
`example_yaml` asks `app.examples` (a `None` with `--run-examples` is a
`UsageError`: "application cece ships no examples" — never true for
CECE; the branch exists so Phase C cannot forget it).

Test ids are unchanged (`MACCITY.map-consd`, `<suite>/<combo>` when
several suites run); `-k` keeps working. For the harness's own tests,
the adapter tests live under `src/tests/ufs_chem_assay/applications/cece/`,
so `uv run pytest src/tests/ufs_chem_assay/applications/cece` is the
cece-only harness run and the shared tests are everything else.

### 5. Enumeration, config generation, combos.csv (`combos.py`)

- Stays: `Dimension` (its `apply` typed `Callable[[DriverConfig, StrEnum], None]`,
  `group`/`key`/`index` documented as the adapter's attachment metadata),
  `Combo`, `NAME_SEPARATOR`, `_sorted_values`, `enumerate_combos` — now
  taking the `(Dimension, values)` list instead of `(sweep, base_config)`
  — and `write_combos_csv(entries, run_id, application, csv_path)`,
  whose entries become `(suite_name, combo, rows)`.
- Moves to `applications/cece/combos.py`: `_VDIST_COMPANIONS`,
  `_SPECIES_FIELDS`/`_STREAM_FIELDS`, `_apply_stream_field`,
  `_apply_species_field`, `_species_dimensions`, `_stream_dimensions`
  (their base-config validation errors unchanged — they are what the
  selector-validation tests pin), `build_config` (with the `cece.log`
  redirect: CECE's log, CECE's name), `_effective_parameter_rows`.

### 6. Assertions, comparison selectors, examples

- `assertions.py` keeps `assert_nc_file_count(combo_dir, expected)`,
  `assert_nc_filenames(combo_dir, expected)`,
  `assert_output_variable_dimensions(combo_dir, names, standard)`,
  `assert_species_attributes` (unchanged), `_render_filename_pattern`
  (generic `{YYYY}…` rendering; CATChem output naming can reuse it).
  `derive_expected_nc_file_count`, `expected_nc_filenames`,
  `_output_field_names`, `STANDARD_DIMENSIONS` move to
  `applications/cece/assertions.py`; `test_driver_combos.py` obtains the
  derived values through an `application` session fixture
  (`request.config.stash[_APPLICATION]`) and passes them in — the test
  module stays free of CECE imports.
- `comparison.py`: `resolve_baseline_comparisons(app, comparisons, combos)`
  keeps the resolution rules and messages; `_selector_matches`,
  `_stream_block_matches`, `_species_entry_matches` move to
  `applications/cece/suite.py` behind `app.selector_matches`. The
  NetCDF comparison itself is untouched.
- `examples.py` keeps `DownloadResult`, `ExampleRunResult`,
  `write_examples_report`, and a small `ExamplesSupport` base
  (`discover(root_dir)`, `example_id(path)`, `download(root_dir, timeout_s)`,
  `run_command(settings, app_settings, eid)`); the CECE bodies
  (`EXAMPLES_SUBDIR`, the two entrypoints, the `cece_config_` prefix, the
  slurm refusal) move to `applications/cece/examples.py`.
  `test_examples.py` goes through the session's `application` fixture.

### 7. Runner and path model

- `docker_prefix(settings, app_settings, workdir, output_mount)`: the
  bind mount `root_dir:<workdir>`, `-w <workdir>`, the run-as-root MPI
  pair (kept generic, documented as such), `app_settings.docker_image`.
  `build_command` / `driver_command` / `write_job_script` / `run_driver`
  / `_run_slurm_job` gain the `app_settings` parameter and read
  `root_dir`, `driver_path`, `modulefile` from it; argv and the rendered
  job script are byte-identical. The stdout banner drops the word
  `cece` (`----- driver output [...] -----`; not an artifact).
- `resolution.resolve_output_roots(option, root_dir, runtime, container_workdir)`:
  `CONTAINER_WORK` becomes the parameter; the docker rule ("relative or
  under `<workdir>`") is otherwise unchanged. `ComboRoots`,
  `GeneratedCombo` (typed `DriverConfig`), `DriverRunResult` (same) are
  unchanged in shape.
- `RunManifest`: unchanged fields; `suites` typed `SerializeAsAny`.

### 8. The CLI: run config and stages

```yaml
# config/ursa.yaml, after
platform: ursa
applications:                       # NEW: one section per application in the run, keyed by adapter
  cece:                             #   name; a comprehensive run lists several (Phase C: catchem)
    git_url: git@github.com:benkozi/CECE.git
    ref: fix/all-examples-pass
    clone_dir:                      # default <root_dir>/CECE (the adapter's checkout_dirname)
    update_source: false
    modulefile: cece_ursa.intelllvm # CECE_MODULEFILE
    docker_image:                   # CECE_DOCKER_IMAGE; null = the adapter's default
    driver_path:                    # CECE_DRIVER_PATH; null = the adapter's default
    cmake_args: [-DCMAKE_BUILD_TYPE=Release]
    build_jobs: 8
    targets: [cece_standalone_driver]
    examples: [ex3]                 # moved from data.examples: example ids are the application's
data:
  warm_cartopy: true                # harness-wide; `examples` left this section
baselines: …
harness: …                          # keys as today plus section 11's; comments say ASSAY_*
slurm: …
uv: …
```

- `RunConfig` (`cli/run_config.py`, generic, not subclassed):
  `platform`, `root_dir`, `applications: dict[str, SerializeAsAny[ApplicationRunSection]]`,
  `data`, `baselines`, `harness`, `slurm`, `uv`, the `runtime`/`uv_cache_dir`
  properties, and `checkout_dir(name)` = `section.clone_dir or root_dir /
  app.checkout_dirname`. A `field_validator("applications", mode="before")`
  validates each value with `get_application(key).run_section_model`
  (an unknown key lists the registered names; an empty map is an error),
  so the map is where the registry dispatches for the run config —
  `from_yaml` keeps the platform/root resolution and the override merge
  (section 11). `ApplicationRunSection(StrictModel)` carries the generic
  fields — `git_url`, `ref`, `clone_dir`, `update_source`, and the
  mirror of `ApplicationSettings` (`modulefile`, `docker_image`,
  `driver_path`; null means "not exported, the setting's default
  applies"); `CeceRunSection` adds `cmake_args`, `build_jobs`,
  `targets`, `examples` (today's `CeceSection` fields plus the example
  ids that leave `data:`). `HarnessSection` descriptions say `ASSAY_*`.
- **Application selection at the CLI**: every configured application
  runs; `--application NAME` (repeatable) narrows a comprehensive run
  config to a subset — a selection over the map, not an override, so it
  is a named flag rather than `-o`. Naming an application that is not
  configured is an error.
- `cli/stages.py` keeps `Stage`, `ShellScript`, `_q`, `_LMOD_INIT`,
  `_clean_python_env` (reads the section's `modulefile`), and
  `render_stage(stage, config, app, section)`. **Scripts render per
  application**: `<root_dir>/scripts/<NN>-<stage>-<name>.sh`
  (`05-harness-cece.sh`), stage-major order (every application's
  `source`, then every `build`, `data`, `harness`), so build failures
  surface before any pytest session starts. The **harness** body runs
  **one pytest session per application** with `--application=<name>`
  and the application's output root — `harness.output_root` as-is when
  one application is configured (today's paths, the runbook's
  `ufs-chem-assay-output/`), `<output_root>/<name>` when several — and
  exports `CECE_ROOT_DIR` (`f"{app.env_prefix}ROOT_DIR"`),
  `CECE_MODULEFILE` (same), and `ASSAY_PLATFORM`, `ASSAY_RUNTIME`,
  `ASSAY_SBATCH_ARGS`, `ASSAY_SLURM_QUEUE_WAIT_S`, `ASSAY_JOB_ENV`,
  `ASSAY_LAUNCHER`, `ASSAY_ENABLE_BASELINE_COMPARISONS`,
  `ASSAY_BASELINE_ROOT_DIR`, `ASSAY_RUN_TIMEOUT_S`, `ASSAY_DASK_NWORKERS`,
  and the pytest line gains `--application=<name>`. The `source`,
  `build`, `data` bodies (`_source`, `_build`, `_module_block`, `_data`)
  move to `applications/cece/cli.py` behind `app.stage_lines`; the
  unused `_cece_env_exports` goes. `data.warm_cartopy` stays a
  harness-wide line in the data stage (rendered once, under the first
  application's script).
- Both templates move their CECE keys under `applications: cece:`,
  gain reworded comments and — section 11 — every key the run config
  accepts, so the template is the documentation of the surface.
  `--config-file` help says "see config/". The effective-config
  artifact (section 11) records the whole map.

### 9. Code layout after

```
src/
  applications/
    __init__.py          # REGISTRY, DEFAULT_APPLICATION, get_application, load_suite
    base.py              # Application ABC, ApplicationSettings, DriverConfig, SweepBase, SweepSelectorBase
    cece/
      __init__.py        # re-exports CeceConfig and the enums (was models/__init__.py)
      application.py     # CeceApplication: the wiring, registered by name
      config.py          # was models/cece_config.py, verbatim
      settings.py        # CeceSettings: CECE_ROOT_DIR / DOCKER_IMAGE / DRIVER_PATH / MODULEFILE
      suite.py           # CeceSweep + selectors, CeceBaselineComparison, CeceSuiteConfig, selector matching
      combos.py          # dimensions, build_config (cece.log), effective parameter rows
      assertions.py      # expected count/filenames/variable names, STANDARD_DIMENSIONS
      examples.py        # example discovery, download, run command
      cli.py             # CeceRunSection, source/build/data stage bodies
  models/
    base.py              # StrictModel (unchanged)
    suite_config.py      # generic SuiteConfig, Assertions, Analysis, Plotting, BaselineComparison, RunManifest
  settings.py            # harness-wide Settings (ASSAY_*, CECE_* fallback)
  combos.py              # Dimension, Combo, enumerate_combos, write_combos_csv
  assertions.py          # generic assertion functions
  comparison.py          # resolution rules (via app.selector_matches) + NetCDF comparison
  examples.py            # ExamplesSupport base, result models, report
  runner.py / resolution.py / analysis.py / plotting.py / report.py / logs.py / platforms.py
  cli/{main,run_config,stages,shell}.py
  tests/
    conftest.py          # --application, one application per session, no app-specific options
    test_driver_combos.py / test_examples.py   # no CECE imports
    ufs_chem_assay/
      applications/
        test_registry.py           # lookup, unknown name, default, load_suite dispatch
        cece/                      # the adapter's tests (moved: combos, assertions, suite sweep/selectors, examples module, stages bodies)
      …                            # shared tests (settings split, dry run, runner, cli, …)
```

`models/cece_config.py` and `models/__init__.py` are deleted (moved).
`git mv` for every move so history follows.

### 10. Documentation

- `README.md`: prerequisites and the intro say "the application under
  test" / "the target driver" with CECE as the shipped adapter; the
  `--application` option; `--cece-root-dir` gone from every example
  and from the "flag overrides both" sentences (the root comes from
  `CECE_ROOT_DIR`, `.env`, or the run config); the `.env` example; the
  environment table split into harness (`ASSAY_*`, with the fallback
  note) and CECE (`CECE_*`) halves; the run-config `applications:` map
  and the comprehensive-run shape; the results section naming
  `05-harness-<name>.sh` and listing the `application` column and the
  `run.yaml` `application`/`application_commit`/`harness_version`/
  `harness_commit` keys (section 12); "the
  `Application` adapter follow-up" sentence removed.
- `docs/ursa-runbook.md`: header and step prose say "the target driver"
  and "the application's modulefiles"; the concrete CECE commands
  (clone URL, `cece_ursa.intelllvm`, `cece_standalone_driver`,
  `download-example-data.py`) stay — they are the CECE runbook. Env
  names in the text follow the split.
- `design/design.md` (implementation phase only): a new **Applications**
  section (adapter surface, registry, per-session application, the
  switch), the Settings table split, the layout, the suite-file
  `application:` key, a pointer to this doc; the config-construction
  rule reworded ("through the adapter's config model").
- Option help texts, error messages, and module docstrings follow the
  vocabulary rule.

### 11. Fully YAML-configured runs; CLI arguments are overrides

**The rule.** Everything a harness run reads — every harness-wide
setting, every application setting, every pytest option — has a key in
the run config, and the CLI can change any key from the command line
without editing the file. A run is reproducible from one YAML file plus
the override list, both of which the CLI records.

**The YAML surface, made complete.** The run config already carries
most of it (audit item 9); the gaps are closed with a mirror rule that a
harness test enforces:

- **`harness:` mirrors `Settings` field for field** — every `Settings`
  field is a `harness.<field>` key with the same type and description,
  except the three that live elsewhere by design: `application` and
  `platform` (top level), and the two `baselines.*` keys
  (`baseline_root_dir`, `enable_baseline_comparisons`). The keys this
  adds today: `log_level`, `config_search_path`,
  `suite_config_search_path`; already present: `runtime`, `launcher`,
  `run_timeout_s`, `dask_nworkers`, `env` (→ `ASSAY_JOB_ENV` under slurm,
  direct exports otherwise), and `slurm.*` (→ `ASSAY_SBATCH_ARGS`,
  `ASSAY_SLURM_QUEUE_WAIT_S`). The test is a set comparison over
  `model_fields` with that explicit exception map, so a setting added to
  `Settings` without a run-config key fails the suite.
- **The adapter's section mirrors `ApplicationSettings`** the same way:
  `applications.cece.clone_dir` is `root_dir` (kept: its
  null-means-`<root>/CECE` semantics are the clone's), and
  `docker_image`, `driver_path` join `modulefile` on the generic
  `ApplicationRunSection`. Same test, per registered adapter.
- **pytest options become keys**: `harness.suite_config`,
  `harness.output_root`, `harness.clean_root` exist; new are
  `harness.pytest_dry_run: false` (pytest's `--dry-run` — generate
  everything, execute no driver; distinct from the CLI's `--dry-run`,
  which renders scripts and runs nothing) and `harness.run_examples:
  false`. `harness.pytest_args` stays the escape hatch for anything
  pytest-native (`-x`, `-k`, `--lf`); the application root is the
  section's `clone_dir`, exported as `CECE_ROOT_DIR` (the retired
  `--cece-root-dir` has no replacement flag: the variable is the root's
  one source in the pytest process).
- The harness stage renders its exports **from the field names**
  (`export ASSAY_<FIELD>=<value>` for every set `harness:` key that maps
  to a `Settings` field, `export <PREFIX><FIELD>` for the application's)
  instead of the hand-written export list, so the mirror rule is also
  what produces the script. Keys left null in the YAML are not exported
  and fall back to the shell, `.env`, or the field default exactly as a
  bare pytest would; keys set in the YAML beat the shell and `.env`
  (environment > `.env`, as today) — a laptop `.env` still cannot win
  silently.

**Overrides.** `ufs-chem-assay run --config-file=X [-o|--override
KEY=VALUE ...]`, repeatable and accepting several per flag
(`action="extend", nargs="+"`), the regrid-wrapper shape:

```sh
uv run ufs-chem-assay run --config-file=config/ursa.yaml --stage harness \
  --override harness:suite_config=ex3-suite.yaml harness:pytest_args='[-x, -k, base]' \
             applications:cece:ref=develop slurm:qos=batch harness:env:OMP_NUM_THREADS=8
```

- `cli/override.py: apply_overrides(overrides, base)` — key path split on
  `:`, value after the first `=`, intermediate mappings created when
  absent; unlike the reference, descending through an existing
  **non-mapping** value is an error (`harness:suite_config:x=1` names a
  path that cannot exist) rather than a silent replacement, and a
  missing `=` is an error naming the entry. Nothing else: no list
  indexing, no globbing — the configuration is paths, integers, strings,
  and short lists.
- **Values are parsed as YAML scalars** with the harness's YAML-1.2
  boolean loader (`_StrictBoolLoader`, promoted from the CECE config
  module to `models/yaml.py` and shared): `8` is an int, `true`/`false`
  are booleans (never `yes`/`on`), `null`/`~` clears a key (`cece:clone_dir=null`
  restores the derived default), `[-x, -k, base]` is a list, and anything
  else is the string as typed — so a path with a colon or an equals sign
  in it survives. pydantic then validates the merged dict exactly as it
  validates the file; `extra="forbid"` turns a typo in a key path into
  the usual unknown-key error, naming the path.
- **Precedence, highest first**: `--override` entries in command-line
  order (last wins) > the named flags `--platform` / `--root-dir`
  (applied as `platform=` / `root_dir=` overrides before the list; kept
  as sugar because the runbook uses them) > the file > `from_yaml`'s
  derived defaults (platform detection, the harness checkout's parent)
  > model defaults. Order inside `RunConfig.from_yaml`: load the dict,
  apply the flag overrides, apply the `--override` list, fill the derived
  defaults for keys still absent, validate (the `applications` map
  dispatches each section through the registry). An override can add
  an application to a run (`applications:catchem:ref=main` creates the
  section, which then validates against that adapter's model and its
  required fields) or change one; which configured applications
  actually run is `--application`'s job, not an override's.
- **The effective configuration is an artifact.** Every invocation, dry
  runs included, writes the merged, validated config as
  `<root_dir>/scripts/run-config.yaml` (`model_dump(mode="json")`, keys
  in model order) beside the stage scripts, and logs the override list
  at INFO — the file re-runs the same configuration with no override
  list, and a review of "what actually ran" reads one file. With
  `--dry-run` it is the way to check an override landed.
- **pydantic remains the only gate.** Every configuration value —
  suite file, driver config, environment and `.env`, run config with
  its overrides merged in — reaches the code through a pydantic model
  (strict where the input is a file); the override helper handles
  syntax only and validates nothing. The pytest command line is the one
  argparse surface: its two configuration-carrying options (the
  application root, `--application`) are validated as settings init
  kwargs, the remaining selector/path/booleans are consumed by pure
  functions with their own checks.
- **Suite files are not overridden this way** — a suite describes what
  is tested, the run config how; `harness.suite_config` selects the
  suite. If a per-run suite tweak (say `timeout_s`) is ever wanted, the
  same helper applied to the suite dict is one more option (Future
  work).

### 12. Observability: the `application` tag in every artifact

Every table the harness writes carries the application it was produced
for, so a row is self-describing wherever it ends up — a concatenation
across output roots, a comprehensive run's two sessions, a cross-run
join. The rule is one column, one position:

| artifact | columns (the tag immediately before `suite`) |
|----------|----------------------------------------------|
| `combos.csv` | `run_id, combo_id, application, suite, name, target, field, value, swept` |
| `test-report.csv` | `pytest_name, application, suite, combo_id, combo, result` |
| `<combo_id>-stats.csv`, `descriptive_stats.csv` | `run_id, application, suite, combo_id, combo, file, variable, time, …` |
| `<combo_id>-stats-comparison.csv`, `stats-comparison.csv` | the same head, then the comparison columns |
| `run.yaml` | `run_id`, **`application`** (the session's), **`application_commit`** (was `cece_commit`; the application checkout's HEAD, null only without a checkout), **`harness_version`**, **`harness_commit`** (below), `platform`, `runtime`, `modulefile`, `suites` (each with its `application`) |

- Plumbing: `analysis.RunContext` gains `application` beside `run_id`
  and `suite` (the `run_context` fixture fills it from `_APPLICATION`),
  and the stats and comparison row writers stamp it as they stamp
  `run_id`; `report.TestReportRow` gains it, filled in
  `pytest_runtest_makereport` from the session; `write_combos_csv`
  takes it as a parameter like `run_id`; `RunManifest` gains
  `application: str` and renames `cece_commit` → `application_commit`
  (same required-but-nullable semantics and description). The concatenation
  steps carry the column through untouched; the per-suite plot slices
  (`stats["suite"] == …`) are unaffected — in a session the tag is
  constant, and the join keys are `run_id`, `application`, `suite`.
- Values are the adapter's registry name (`cece`), never a display
  string, so they match the suite files' `application:` and the run
  config's `applications:` keys exactly.
- **The harness identifies itself too** (user decision, 2026-09-14):
  `harness_version` is the installed package version
  (`importlib.metadata.version("ufs-chem-assay")` — the `pyproject.toml`
  version semantic-release bumps), and `harness_commit` is the harness
  checkout's HEAD SHA with a `-dirty` suffix when the working tree has
  uncommitted changes (the `git describe --dirty` convention — a run from
  an edited tree says so). Both come from a small `identity.py`
  (`HARNESS_NAME` moves there from `logs.py`, `HARNESS_ROOT` from
  `cli/run_config.py`, plus `harness_version()` / `harness_commit()`)
  so the pytest session never imports the CLI. `harness_commit` is
  **null, with one WARNING, when the harness does not run from a git
  checkout** (an installed wheel, or git missing) — unlike the
  application commit, that is a legitimate way to run and never fatal.
  Both are `run.yaml` keys only: every CSV row carries `run_id`, which
  is the join to them; repeating them per row would add nothing.
- `examples-report.md` names the application in its heading (it is
  markdown, not a table); plot filenames are unchanged (they live under
  the combo directory, whose tables carry the tag).
- README's results section lists the new column in each table it
  describes, and the "cross-run joins use the recorded parameters"
  paragraph names the three keys.

## Implementation plan (TDD, red → green → refactor)

Each step: failing harness tests first, then the code, then
`uv run pre-commit run --all-files`. Capture the **before** dry-run
artifacts (Verification, item 1) before step 1.

1. **Registry, ABC, settings split.** `test_registry.py`: `get_application("cece")`,
   unknown name lists the registry, `DEFAULT_APPLICATION`. `test_settings.py`:
   `Settings` reads `ASSAY_PLATFORM`, falls back to `CECE_PLATFORM`,
   `ASSAY_` wins when both are set, a `.env` with `cece_dask_nworkers=`
   still loads, `application` from env and from kwarg; `CeceSettings`
   reads `CECE_ROOT_DIR` and never `ASSAY_ROOT_DIR`, `commit_sha()` on a
   temp git repo and its failure modes (today's `get_cece_commit_sha`
   tests, relocated). The scrub fixtures scrub both prefixes.
2. **Move the config model and the sweep models.** `git mv`
   `models/cece_config.py` → `applications/cece/config.py`; `DriverConfig`
   base; `CeceSweep`/selectors/`CeceSuiteConfig` into
   `applications/cece/suite.py`; `load_suite` with the token from
   `env_prefix`. Tests: `test_suite_config.py` splits into the generic
   part (name pattern, timeouts, plotting-requires-stats, unknown keys,
   `application` default and unknown value, `SerializeAsAny` round trip
   of `RunManifest`) and `applications/cece/test_suite.py` (regex
   expansion, uniqueness, selectors — moved).
3. **Adapter methods.** `applications/cece/test_combos.py`,
   `test_assertions.py`, the selector-matching half of
   `test_comparison.py`, `test_examples_module.py`: moved, calling the
   adapter. Generic `combos.enumerate_combos(dimensions)`,
   `write_combos_csv(entries)`, `assertions.assert_*` with explicit
   expectations, `comparison.resolve_baseline_comparisons(app, …)`,
   `examples.ExamplesSupport`.
4. **Runner and roots.** `test_runner.py`/`test_native_runtime.py`/
   `test_slurm_runtime.py`/`test_resolution.py`: the same golden argv and
   job-script text with `(settings, app_settings)`; `container_workdir`
   as a parameter (`/work` for CECE); the banner without `cece`.
5. **Session.** `conftest.py`: `--cece-root-dir` removed (its
   `test_root_dir_guards.py` cases become environment-only: flag-less
   precedence, the "set CECE_ROOT_DIR" messages), `--application`, the
   default-suite resolution, the one-application rule,
   `_APPLICATION`/`_APP_SETTINGS`, the `application` fixture. **The
   `application` tag** (section 12): red tests in `test_report.py`
   (column order), `test_analysis.py` and `test_comparison.py` (rows
   stamped, concatenation keeps the column), `test_combos.py` (the
   combos.csv header), `test_suite_config.py` (`RunManifest` with
   `application`/`application_commit`/`harness_version`/`harness_commit`,
   the suite's field dumped), a new `test_identity.py` (`harness_version`
   equals the installed metadata; `harness_commit` on a temp checkout,
   `-dirty` with an edited file, null plus a WARNING with no `.git`),
   `test_dry_run.py` (every session CSV has `application == "cece"` in
   every row and `run.yaml` carries the four keys, `harness_commit`
   matching `git rev-parse HEAD` of the repo under test); then
   `identity.py`, `RunContext`, `TestReportRow`, the writers, the
   manifest.
   Tests: `test_dry_run.py` (unchanged expectations plus `--application=cece`
   and `ASSAY_APPLICATION=cece` variants; an unknown application fails
   with the registry listing), `test_suite_selection.py` (a suite
   naming another application is dropped under `--application`, mixed
   selection without it is a `UsageError`), `test_root_dir_guards.py`
   (messages say "application checkout"), `test_maccity_pipeline.py`
   and `test_example_suites.py` through the adapter.
6. **CLI.** `test_run_config.py`: both templates load with an
   `applications` map holding a `CeceRunSection`, an unknown section
   key and an empty map fail, `checkout_dir("cece")`, `examples` under
   the section; `test_stages.py`: the golden export lines with
   `ASSAY_*`/`CECE_*` names and `--application=cece` on the pytest
   line, source/build/data bodies unchanged in text (now from
   `app.stage_lines`), the per-application output root rule (as-is for
   one application, suffixed for two — the second via a stub adapter);
   `test_cli.py`: four scripts named `<NN>-<stage>-cece.sh`, stage-major
   order with a stub second adapter, `--application` narrowing and its
   unknown-name error; `test_user_docs.py`: templates carry
   `applications: cece:`, the runbook names `05-harness-cece.sh`.
7. **Overrides and the full YAML surface** (section 11).
   `test_override.py` (new): the reference cases — nested creation,
   deep paths, last-wins, a missing `=` raises, descending through a
   scalar raises, value parsing (`8` → int, `true` → bool, `null` →
   None, a flow list, a path containing `:` and `=`, `yes` stays a
   string). `test_run_config.py`: the mirror tests (every `Settings`
   field has a `harness:` key bar the exception map; every
   `ApplicationSettings` field has a key in the adapter's section);
   both templates set every accepted key; `from_yaml(path,
   overrides=[...])` precedence — `--override` beats `--platform`, beats
   the file, beats detection; `application=` override dispatches (and a
   CECE file under another name fails on its section). `test_stages.py`:
   exports are rendered from the field names (a golden line per new
   key: `ASSAY_LOG_LEVEL`, `ASSAY_CONFIG_SEARCH_PATH`,
   `ASSAY_SUITE_CONFIG_SEARCH_PATH`, `CECE_DOCKER_IMAGE`,
   `CECE_DRIVER_PATH`), `pytest_dry_run`/`run_examples` add the flags,
   null keys export nothing. `test_cli.py`: `-o`/`--override` repeated
   and space-separated, `run-config.yaml` written on a dry run with the
   overridden value in it, the override list logged. Then
   `cli/override.py`, `models/yaml.py` (the loader, shared with the
   CECE config), the new `HarnessSection`/`CeceSection` keys, the
   field-driven export rendering, the templates.
8. **Docs** (section 10), `design.md`, README (a "Configuring a run"
   subsection: the YAML surface, `--override`, precedence,
   `run-config.yaml`), runbook, CI env names.
9. **Verification** below; record the numbers in the implementation
   notes.

## Verification

1. **Byte-identity of dry-run artifacts.** Before step 1, on `6f36598`,
   for every checked-in suite (`simple-maccity`, the two exhaustive
   suites, `ex1`–`ex7`): `uv run pytest src/tests/test_driver_combos.py
   --dry-run --suite-config=<suite> --basetemp=<scratch>/before/<suite>`
   (default tmp root, pinned by `--basetemp`; the `ex*` suites need the
   checkout for `${CECE_ROOT_DIR}`); once more for `simple-maccity` with
   `CECE_RUNTIME=slurm` and a root, for the `.sbatch` artifacts. After
   the last step, the same commands (with `ASSAY_RUNTIME=slurm`) into
   `after/`. Normalise ULIDs — every 26-character Crockford token in
   `run.yaml`, `combos.csv`, `test-report.csv`, directory names,
   generated yaml paths, and `.sbatch` text — to `<ULID-n>` in order of
   first appearance; in the `after/` tree also drop the `application`
   column from every CSV and the `application:`, `harness_version:`,
   `harness_commit:` keys from `run.yaml`, and rename its
   `application_commit:` key back to `cece_commit:` (section 12, the
   one sanctioned difference — asserting first that the dropped column
   is `cece` in every row and that `harness_commit` names the tree
   under test); then `diff -r`. The diff must be empty. The
   normaliser is a throwaway script in the scratch space, not part of
   the tree.
2. `uv run pytest src/tests/ufs_chem_assay`: 313 + the new tests green;
   green again with the hostname forced to `ufe01` (the Ursa hermeticity
   check from the RDHPC doc).
3. `uv run pre-commit run --all-files` (ruff, mypy over `src/` including
   the new package, yaml hooks).
4. Every suite `--dry-run` (standing rule) — already covered by item 1.
5. `simple-maccity-suite.yaml` for real in docker
   (`ASSAY_ENABLE_BASELINE_COMPARISONS=false`): 18 passed, 3 skipped, as
   on the current tree; and once through the CLI with the untouched
   `config/local.yaml` (`--stage harness`), reading the rendered
   `05-harness-cece.sh` for the new export names.
6. `uv run ufs-chem-assay run --config-file=config/ursa.yaml --dry-run`
   on the laptop: four scripts render, the harness script exports
   `CECE_ROOT_DIR`, `CECE_MODULEFILE`, and the `ASSAY_*` names.
7. The CI image round-trip (README's docker commands) with the new
   package in the wheel: `uv sync --frozen --no-install-project` in the
   image, in-repo `uv sync --frozen` + harness suite in the container.
8. Overrides end to end on the laptop: `ufs-chem-assay run
   --config-file=config/local.yaml --stage harness --override
   harness:suite_config=simple-maccity-suite.yaml harness:pytest_args='[-k, map-consd]'`
   runs exactly one combination; `--override cece:docker_image=no-such-image
   --dry-run` shows the value in `run-config.yaml` and in the rendered
   harness script, and nothing runs.
9. **On Ursa (user, manual)**: see TODO.

## Risks and notes

- **`SerializeAsAny`** (decision 2) is the one place pydantic can
  silently eat data; the manifest round-trip test guards it. If a
  pydantic upgrade changes subclass serialisation, that test is the
  alarm.
- **Alias fallback edge cases**: pydantic-settings resolves
  `AliasChoices` per source in order; a value set as `CECE_X` in the
  environment and `ASSAY_X` in `.env` resolves to the environment one
  (environment beats `.env` as today). The `.env` test covers the
  lowercase path; the harness fixtures must scrub both spellings or a
  developer's `.env` leaks into tests exactly as the settings tests
  already guard for `CECE_ROOT_DIR`.
- **Import cycles**: `models/suite_config.py` must not import the
  registry (the adapters import it). `load_suite` and `RunConfig.from_yaml`'s
  dispatch live on the registry side; `combos.py`/`assertions.py`/
  `comparison.py` import only `applications.base`. mypy's
  `explicit_package_bases` handles the new package; the wheel's
  `only-include = ["src"]` picks it up.
- **The `examples` capability and slurm**: unchanged refusal; the
  `None` branch is untestable for CECE and is covered with a stub adapter
  in `test_registry.py`.
- **Isinstance narrowing** in the adapter: a wrong `application:` cannot
  reach it (the suite model already validated the sweep for that
  adapter), so the asserts are belt and braces, never a user-facing
  error path.
- **Rendered script golden lines change** (`ASSAY_*` exports,
  `--application=cece`): a deliberate, documented change to the CLI's
  output, not to any pytest dry-run artifact.
- **`--suite-config` default `None`**: `pytest --help` shows the
  resolved default in the help text ("`simple-maccity-suite.yaml` for
  the default application") rather than as argparse's default.

## Future work (hooks this design leaves)

- **Phase C — CATChem**: `applications/catchem/` with `CATCHEM_*`
  settings, `container_workdir=/opt/project`, a `CatchemConfig`, a
  sweep-less `CatchemSweep`, `default_suite` = the smoke suite,
  `examples=None`, `stage_lines` for its docker build; run.yaml needs
  nothing more — `application`, `application_commit`, and the harness
  keys already describe the session (section 12), one application per
  session; the `CECE_*`
  fallback aliases are removed; `capabilities` (spatial plots off for
  column output) joins the adapter surface.
- **Comprehensive-run report** (Phase D): a `report` subcommand joining
  the per-application output roots' `test-report.csv` and stats CSVs
  into one table keyed by application, suite, and combo — the
  cross-application view of a run that today is two directories.
- **Multi-application pytest sessions** are not planned: decision 3
  keeps one application per session and the CLI composes comprehensive
  runs. If a mixed session is ever wanted, the hooks are
  `SuiteContext.application` and per-application settings/commits keyed
  by name; the flat ULID root and suite-stamped CSVs need nothing.
- **Per-application suite directories** (`suite/cece/`, `suite/catchem/`)
  once run.yaml's `config_path` may change (Phase C).
- **Entry-point discovery** for third-party adapters, if ever needed:
  the registry dict becomes a loader; nothing else moves.
- **Suite overrides** (`--suite-override timeout_s=600`): the same
  `apply_overrides` helper on the suite dict inside `load_suite`, with
  the overridden suite recorded in run.yaml as today (it records the
  resolved suite). Not needed until a per-run suite tweak turns up.

## TODO

- [x] **Confirmed on Ursa, 2026-09-23** (user's `out.harness-run.20260923-171229`):
      `ufs-chem-assay run --config-file=config/ursa.yaml --stage harness`
      from the `feat/application-agnostic` clone at `13882f3` rendered
      `scripts/run-config.yaml` and `05-harness-cece.sh`, the session
      started as `application cece`, and the suite finished **18 passed,
      3 skipped** (the baseline comparisons, disabled) in 131 s — three
      `sbatch --wait` jobs, stats and plots on the login node. The only
      warnings are cartopy's known matplotlib deprecation. Wall time was
      65 s on 2026-09-11; the difference is queue wait, not the harness.
- [ ] ~~**Confirm on Ursa** (user)~~: after the extraction, run the maccity
      CECE example on Ursa — `uv run ufs-chem-assay run
      --config-file=config/ursa.yaml --stage harness` from a
      runbook-layout checkout — and check that it is green (18 passed,
      3 baseline skips as on 2026-09-04), that the rendered
      `05-harness-cece.sh` exports `CECE_ROOT_DIR`/`CECE_MODULEFILE` and the
      `ASSAY_*` names, and that `run.yaml` records `platform: ursa`,
      `runtime: slurm`, `modulefile: cece_ursa.intelllvm`. Record the
      outcome in this document's implementation notes.

## Implementation notes (2026-09-23)

Built from the design at `239d47e`, red → green in the order of the plan,
on `feat/application-agnostic`. Before the first code change every
checked-in suite's `--dry-run` root was captured on the untouched tree
(`--basetemp`, ten suites plus the slurm variant of `simple-maccity`).

- **Harness suite 313 → 395 passed**, hooks pass (ruff check/format, mypy
  over the new package, yamlfix/yamllint), and the suite is green again
  with the hostname forced to `ufe01`. New test modules: `test_identity.py`,
  `test_override.py`, `applications/test_registry.py`, and the adapter's
  `applications/cece/test_cece_*.py` (moved from the generic modules where
  they tested CECE code: combos, assertions, the sweep schema, selectors,
  examples, the pipeline, the example suites, settings).
- **Byte-identity**: the after tree, with the sanctioned additions
  stripped (the `application` column in every CSV; `application`,
  `harness_version`, `harness_commit` in run.yaml, its
  `application_commit` renamed back, the suites' `application` field) and
  ULIDs plus the pinned basetemp path normalised, diffs empty against the
  before tree for all eleven variants. The normaliser stays in the scratch
  space.
- **Real runs**: `simple-maccity` in docker through pytest directly —
  18 passed, 3 baseline skips — and once more through the CLI
  (`config/local.yaml --stage harness --override
  applications:cece:clone_dir=<checkout>`, the first override in anger);
  `run.yaml` records `application: cece`, `application_commit`,
  `harness_version: 0.1.0`, `harness_commit: <sha>-dirty`. Both templates
  dry-run to four `<NN>-<stage>-cece.sh` scripts plus `run-config.yaml`;
  the CI image rebuilt and ran the harness suite in-container (394 passed,
  1 skipped, the usual no-checkout skip; the console script resolves).
- **Deviations from the design, all small.** (1) The registry lives in
  `applications/registry.py`, not `applications/__init__.py`: importing
  `applications.base` from the shared models would otherwise execute the
  package `__init__`, which imports the adapters, which import the shared
  models — a cycle. (2) `SuiteConfig.baseline_comparisons` is a
  `Sequence` (covariant) so `CeceSuiteConfig` can narrow the entry type;
  `resolve_baseline_comparisons` takes a `Sequence` too. (3)
  `RunManifest.suites` also dispatches on each entry's `application` when
  a run.yaml is read back (a `mode="before"` validator with a local
  registry import), so the round-trip test — and a future report tool —
  gets the adapter's schema, not the base one. (4) The run-as-root MPI
  environment stays in the generic docker prefix (harmless where not
  needed), as the design allowed. (5) The `examples` ids moved from
  `data:` into the adapter's section (`applications.cece.examples`), since
  they are the application's; `data.warm_cartopy` stays harness-wide and
  renders once, under the first application's data script. (6) The
  YAML-1.2 boolean loader moved to `models/yaml.py`, shared by the CECE
  config model and override-value parsing. (7) The adapter tests carry
  unique basenames (`test_cece_*.py`) and no `__init__.py`: a package
  marker under `src/tests/ufs_chem_assay/applications/` made pytest name
  the module `applications.cece.test_…`, shadowing the real package. (8)
  A second, inert adapter for the several-applications tests is
  `tests/ufs_chem_assay/stubs.py`, registered per test with
  `monkeypatch.setitem(REGISTRY, …)`; the mixed-selection `UsageError` in
  the pytest session (two applications among the selected suites) cannot
  be exercised end to end with one real adapter and is covered by the
  unknown-application paths only — Phase C's adapter makes it testable.
- **Settings mechanics**: the `CECE_*` fallback is two extra
  pydantic-settings sources (`EnvSettingsSource` / `DotEnvSettingsSource`
  with `env_prefix="CECE_"`) layered below the `ASSAY_*` ones, not
  aliases; both settings classes are `extra="ignore"` because the shared
  `.env` carries the other namespace's keys (pydantic-settings otherwise
  rejects them as extras). Verified empirically before writing:
  `ASSAY_` env > `CECE_` env > `ASSAY_` `.env` > `CECE_` `.env`.
- **The user's `.env`** (gitignored) still works as written — its
  `cece_root_dir` is the adapter's key and `cece_baseline_root_dir` the
  fallback spelling of a harness key; its comments mention the retired
  flag and are the user's to update.
- **CI workflows**: `integration.yaml` exports
  `ASSAY_ENABLE_BASELINE_COMPARISONS`; nothing else needed changing
  (`CECE_ROOT_DIR` is still the adapter's variable).
- **Ursa**: confirmed green on 2026-09-23 (see the TODO section).

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
  - refinements never create files. they only edit the target document that is being refined.
  - `design.md` may be updated during the _implementation_ phase only.
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code or use git actions that perform updates - the user always updates
- always prefer the harness's logging implementation over raw python print statements
- no need for wrapping when writing to the `./design` folder. wrapping will be handled by the user's ide

## testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- refine from: https://github.com/benkozi/ufs-chem-assay/issues/12
- as a todo, remind me to confirm that a test runs successfully on ursa for the maccity cece example

## conversational updates

- 2026-09-14 — refinement (this document): the original notes above
  and issue #12 were refined against the tree at `6f36598` (313 harness
  tests). Decisions taken in the refinement: an ABC rather than a
  `typing.Protocol`; per-application subclasses of the generic
  suite/run-config/settings models dispatched by a registry dict; one
  application per session with `application:` per suite; harness-wide
  settings under `ASSAY_*` with `CECE_*` fallbacks until Phase C;
  `run.yaml` byte-identical (the `cece_commit` name and the excluded
  suite `application` field are the deliberate leftovers); the
  `${CECE_ROOT_DIR}` token generalised as `${<PREFIX>ROOT_DIR}` so the
  example suites stay untouched; suite files not moved.
- 2026-09-14 — **application-level switch** (user question: "are we
  accounting for a high level switch to run an application-specific
  suite of tests, e.g. catchem-only or cece-only?"): yes, added as
  deliverable 2 and section 4 — `--application=NAME` on pytest
  (`ASSAY_APPLICATION` in the environment, the flag wins; `application:`
  in the run config, passed through by the harness stage). With it, only
  suites declaring that application survive selection, so
  `--suite-config='.*' --application=catchem` is "every CATChem suite"
  and a bare `--application=catchem` runs that adapter's declared
  `default_suite`; without it the application is inferred from the
  selected suites, which must agree. The harness's own adapter tests
  live under `src/tests/ufs_chem_assay/applications/<name>/`, so a
  pytest path gives a cece-only harness run.
- 2026-09-14 — **fully YAML-configured runs, CLI arguments as overrides**
  (user): every harness run must be configurable from the YAML file
  alone, with command-line arguments acting as overrides on it, in the
  shape of regrid-wrapper's `apply_overrides` (`key:nested=value`
  entries merged into the loaded dict before validation; no need for
  every edge case, values are paths, integers, and the like). Added as
  deliverable 6, decision 10, and section 11: the run config becomes
  the complete surface (`harness:` mirrors `Settings`, the adapter's
  section mirrors `ApplicationSettings`, pytest options get keys —
  enforced by a mirror test), `ufs-chem-assay run` gains repeatable
  `-o/--override KEY=VALUE` with YAML-scalar value parsing and the
  precedence override > named flag > file > derived > default, the
  effective config is written as `<root_dir>/scripts/run-config.yaml`,
  and the harness stage renders its exports from the field names.
  pytest itself takes no config file (decision 10); suite overrides are
  future work.
- 2026-09-14 — **pytest stays a separate entry point; `--cece-root-dir`
  retired; comprehensive runs** (user): keeping pytest separate from
  the CLI is fine (decision 10 stands). Of pytest's custom options, only
  `--cece-root-dir` fitted better upstream — it duplicated
  `CECE_ROOT_DIR`, which the run config exports — and the user agreed
  to retire it (decision 11): pytest registers no application-specific
  option and the registry needs no pytest hook. The user also assumes
  CECE and CATChem can run together in a "comprehensive run": designed
  at the CLI level (decision 12, section 8) — the run config holds an
  `applications:` map of per-adapter sections (`applications.cece.…`,
  with `examples` moved there from `data:`), scripts render per
  application as `<NN>-<stage>-<name>.sh`, the harness stage runs one
  pytest session per application with `--application=<name>` and a
  per-application output root when several are configured, and a
  repeatable `--application` on the CLI narrows a comprehensive run.
  One application per pytest session is kept; a joined report is Phase D.
- 2026-09-14 — **`application` in every CSV; byte-identity relaxed**
  (user question: is the application tag in every CSV for
  observability? — it was not; the user chose to add it and relax the
  issue's byte-identity clause for it): section 12 and decision 6
  rewritten — an `application` column immediately before `suite` in
  `combos.csv`, `test-report.csv`, the per-combo and concatenated stats
  and comparison CSVs; `application` and `commit` at the top of
  `run.yaml` (the `cece_commit` → `commit` rename is the refinement's
  addition, so no CECE name stays outside the adapter — veto if the old
  key should stay); the suites' `application` field is dumped rather
  than excluded. Verification normalises the one sanctioned difference
  away, so the before/after diff is still required to be empty.
- 2026-09-14 — **the harness records itself** (user, after the question
  whether `commit` is application-specific — it is: the application
  checkout's HEAD; nothing recorded the harness's own version): `run.yaml`
  gains `harness_version` (installed package version) and
  `harness_commit` (harness checkout HEAD, `-dirty` when edited, null
  with a warning when not a git checkout); `commit` is renamed
  `application_commit` for symmetry; both live in a new `identity.py`
  with `HARNESS_NAME`/`HARNESS_ROOT` so pytest never imports the CLI.
  Keys in `run.yaml` only — CSV rows join to them on `run_id`. Verification
  normalises the extra keys away as it does the `application` column.
