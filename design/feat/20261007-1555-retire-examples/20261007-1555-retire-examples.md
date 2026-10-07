# Feature: retire "example" — the harness tests specific configurations of an application; suites declare their inputs (a maccity-only reset; baseline sourcing deferred)

Refined 2026-10-07 from https://github.com/benkozi/ufs-chem-assay/issues/11 ("Suites declare their own inputs and baseline sources; retire \"example\""), with the user's direction at refinement time: **reset the CECE examples rather than carry them forward** (the seven `ex*-suite.yaml` files go; the harness focuses on the maccity suites), and **keep the scope to retiring "example"** — the harness tests specific configurations for an application, CECE being the one in use; baseline syncing is **not implemented** in this change, but every piece of knowledge gained about it while refining is recorded below for the follow-up. The second requirement — can the integration job be cached by CECE hash, and the uv installation unless the lock changed — is Part B.

# session

- **Session ID**: `74462254-d492-4e73-a00d-6924d18e3484`
- **Resume Command**: `agy --resume 74462254-d492-4e73-a00d-6924d18e3484`

## Goal

1. **"Example" is no longer a thing.** `--run-examples`, `src/examples.py`, `applications/cece/examples.py`, `src/tests/test_examples.py`, the `examples-report.md`, the `ExamplesSupport` adapter hook, the run-config keys, their tests — and, by the user's reset, the seven `ex1-suite.yaml` … `ex7-suite.yaml` files and the "every runnable example has a suite" coverage guard — are removed. What the harness tests is a **specific configuration of an application**, declared by a suite; for CECE that is the three maccity suites (`simple-maccity`, `exhaustive-maccity-asserted`, `exhaustive-maccity-run-only`), all on the harness's own base configuration — today `src/tests/config/cece/simple-maccity.yaml`, after this change `src/tests/config/cece/maccity/maccity.yaml` (see the layout decision) — which reads one file: `data/MACCity_4x5.nc`.
2. **A suite says what data its configuration reads.** Every suite declares its input files (`inputs:`, S3 objects in public or private buckets); the harness stages exactly what the *selected* suites need through the existing `s3_sync` wrapper — which gains `--no-sign-request` for publicly readable buckets — skips files already present with a matching checksum, and records what it found in `run.yaml`. This replaces the one piece of example machinery that did real work — fetching the maccity file through CECE's `download-example-data.py` in the CLI's `data` stage, the integration workflow, and the Ursa runbook — and it is the smallest replacement that keeps those three paths working once that tooling is gone.
3. **The integration job stops rebuilding CECE** when the CECE commit has not changed: the existing `actions/cache` of the built tree, keyed by CECE SHA, already hits — the build step is now **skipped outright on an exact hit** instead of re-running and recompiling everything on top of the restored tree. The uv cache is keyed on `uv.lock` (it already is — see the audit; this change makes it explicit).

Out of scope, deferred with its findings recorded (see "Deferred: baseline sourcing"): `baseline_comparisons` sourcing from S3, the local-cache scan, stale-baseline handling, and CI credentials for baselines. Baseline comparisons keep today's behaviour exactly: local directories under `ASSAY_BASELINE_ROOT_DIR`, "configured but missing" fails the comparison test, `ASSAY_ENABLE_BASELINE_COMPARISONS=false` in CI and the Ursa template.

## Pre-design audit (fresh facts, 2026-10-07)

Verified on `feature/retire-examples` at `d2fac3e` (`0.1.0-rc.2` plus the S3 wrapper), the local CECE checkout at `/Users/bkoziol/sandbox/git-benkozi/CECE`, GitHub (`gh`), and the latest green integration run.

1. **The example machinery today.** `src/examples.py` (result models, `write_examples_report`), `src/applications/cece/examples.py` (`CeceExamples`: discover, `example_id`, download via the checkout's `examples/download-example-data.py`, docker-wrapped `run-example.py`), `src/tests/test_examples.py` (`--run-examples` gating, one test per discovered config), `applications/base.py` (`ExamplesSupport` ABC, `Application.examples` ClassVar), `src/tests/conftest.py` (the `--run-examples` option, the examples clause of the root-dir collection guard, the `example_yaml` parametrization), `src/tests/ufs_chem_assay/stubs.py` (`examples = None`), `cli/run_config.py` (`HarnessSection.run_examples`), `cli/stages.py` (`--run-examples` pass-through), `applications/cece/cli.py` (`CeceRunSection.examples`, default `["ex3"]`; the `data` stage body calls CECE's download entrypoint), `runner.docker_prefix` (docstring: "the driver and the examples entrypoint share it"), the templates `config/local.yaml` / `config/ursa.yaml`, `docs/ursa-runbook.md` step 5, the README and `design/design.md`, and `integration.yaml`'s download step. Tests: `test_examples_gating.py`, `test_examples_module.py`, `applications/cece/test_cece_examples.py`, `applications/cece/test_cece_example_suites.py` (the coverage guard plus the `${CECE_ROOT_DIR}` anchor tests and the ex7-shaped `CeceConfig` surface tests), `test_stages.py` (data stage and `--run-examples` assertions), `test_run_config.py` (`cece.examples == ["ex3"]`), `test_user_docs.py` (`"examples" not in raw["data"]`).
2. **The issue's `data.examples` has already moved.** It is `applications.cece.examples` in the run config since the application-agnostic refactor (2026-09-14); the `test_user_docs` assertion guards that move. `CECE_ENABLE_BASELINE_COMPARISONS` in the issue is `ASSAY_ENABLE_BASELINE_COMPARISONS` today (the `CECE_` spelling still works as the legacy fallback).
3. **The drift the user names is real and three-fold.** The harness pins CECE to the fork branch `benkozi/CECE@fix/all-examples-pass` (`integration.yaml` `env`, both templates). On CECE `develop` (`dab7e54`, what the local checkout has): (a) the configs live at `examples/cece_config_*.yaml` — there is no `examples/config/` directory, so `CeceExamples.discover` finds nothing and the coverage guard's `assert configs` fails against this checkout; (b) there is no `examples/common.py`, `download-example-data.py`, or `run-example.py` — data tooling is `scripts/download_ex*.sh` and `scripts/download_hemco_data.py`; (c) `scripts/build-and-test-container.py` has `--no-test` but **not** `--target` or `--jobs`, which the integration workflow passes. `ex3` on `develop` reads `/work/data/MACCity_4x5.nc` (absolute) where the fork branch reads `data/MACCity_4x5.nc`. This change removes reasons (a) and (b) for the pin; (c) remains (see Risks).
4. **What the maccity suites need is one file.** `src/tests/config/cece/simple-maccity.yaml` (to become `cece/maccity/maccity.yaml`) reads `data/MACCity_4x5.nc` (cwd-relative; the driver runs in the checkout). CECE's own mapping (`examples/common.py` on the fork branch) sources it from the public `geos-chem` bucket (`https://geos-chem.s3.amazonaws.com/HEMCO/MACCITY/v2014-07/MACCity_4x5.nc`, i.e. the object `s3://geos-chem/HEMCO/MACCITY/v2014-07/MACCity_4x5.nc`). The local copy: 982,326 bytes, sha256 `ef70975af53499ab45f620a778043ff6fd29ec4e7e2777f3fd1f2db59a12aab9`. Both exhaustive suites share the same base config, so the same single `inputs` entry.
5. **Baselines (context only; unchanged by this change).** Three ULIDs under the local Dropbox store (`01KXNXCJ858EE6R4FPF39BC8V2`, `01KXNXCJ86E8Z2FKVAXRER5ND4`, `01KXNXCJ87190QGHQZV2913JAW`; 144 KB each), referenced by `simple-maccity-suite.yaml`; nothing in S3 yet. CI runs with `ASSAY_ENABLE_BASELINE_COMPARISONS=false` and the Ursa template has `baselines.enabled: false`. The private bucket `arn:aws:s3:::ufs-chem` and `src/s3_sync.py` (`S3SyncConfig`: `source`, `destination`, `dry_run`, `delete`, `only_show_errors`, `exclude`, `include`, `profile`, `timeout_s`; `sync()` = one `aws s3 sync`) exist since 2026-10-07; nothing calls the wrapper from the session or the CLI yet — `fetch` becomes its first caller (inputs); the session still never calls it. The wrapper has no `--no-sign-request` today: the S3 design (2026-10-06) read AQM-Eval's `data_sync` and deliberately left that flag out because the first use was the private bucket. It comes back here (fact 10).
6. **The integration job, measured** (run `37692138014`, 2026-10-07, `feature/basic-s3-auth`, green): job 4 min 03 s. CECE image build (gha buildx cache hit) 26 s; `actions/cache/restore` of `cece/build` 2 s — the log reads `Cache hit for: cece-build-b28ba69603c0964b1aedbad3994c86722bd58bb3` (the exact key, not a `restore-keys` fallback), `Cache Size: ~269 MB`; **"Build CECE in the container" 2 min 41 s on that exact hit** — the log shows the FetchContent dependencies as `Built target kokkoscore` / `yaml-cpp` but every CECE object from `[ 0%] Building CXX object extern/helm/libs/tick/...` through `[100%] Linking CXX executable cece_standalone_driver` recompiled: the fresh checkout's source mtimes are newer than the restored object files and CMake rebuilds on mtime, so the warm cache saves only the third-party deps. This is the "rebuilding every time" the user saw; it is not a cold cache. Save 2 s; maccity download 0 s (cached); `setup-uv` 3 s; the pytest step 24 s, of which `uv sync --frozen` was ~1 s (`Installed 81 packages in 65ms`); upload 1 s.
7. **The uv cache is already on.** `astral-sh/setup-uv@v10.2.0` logs `enable-cache: auto` (true on GitHub-hosted runners) and restores from `setup-uv-2-x86_64-…-3.14-<hash>` where the hash covers the default `cache-dependency-glob` (`**/pyproject.toml`, `**/uv.lock`, …). The user's uv requirement is therefore met in substance; the change below makes the key explicit (`uv.lock` only, so a comment edit in `pyproject.toml` does not invalidate it) and documents it.
8. **GitHub Actions cache limits**, for what "persistent" can mean here: 10 GB per repository with least-recently-used eviction, entries unused for 7 days deleted, and branch scoping — a PR run reads caches from its own branch and the base branch only (hence the workflow's push trigger "to create develop-scoped caches"). A GitHub Container Registry package would escape all three; the user declined that route (Resolved decisions), so the cache stays `actions/cache` and a cold rebuild after a week without runs is accepted.
9. **Repository state that constrains names.** `pyproject.toml` has `addopts = ["--strict-markers", "-m", "not data_integration"]`; `cli/main.py` has one subcommand (`run`) with `argparse` subparsers already in place; `Settings` and `CeceSettings` read the environment and `.env` exactly as pytest's sessionstart does, so a second entry point gets the same precedence for free.
10. **Public buckets through the CLI, verified on this Mac** (`aws-cli/2.37.9`, user-local): `aws s3 ls --no-sign-request s3://geos-chem/HEMCO/MACCITY/v2014-07/` lists the prefix anonymously (`MACCity_4x5.nc`, 982,326 bytes, plus the sibling `MACCity_anthro_*` files and an `.assets.md5`), and `aws s3 sync --no-sign-request --no-progress s3://geos-chem/HEMCO/MACCITY/v2014-07/ <tmp> --exclude '*' --include 'MACCity_4x5.nc'` transferred exactly that one object, whose sha256 matches the local copy (`ef70975a…`). So a single object is fetchable through the wrapper's one verb with the include/exclude filters it already has, and public data needs no credentials once the flag exists. CECE's two source buckets (`geos-chem`, `noaa-ufs-srw-pds`) are both public.

## Design

### 0. Decisions

- **Scope is the retirement of "example"** (user, 2026-10-07). The harness tests specific configurations of an application — a suite names a base configuration and how it is swept, asserted, analysed — and CECE is the application in use. Baseline sourcing is deferred wholesale (Goal, Deferred section); the suite model's `baseline_comparisons` entries, the comparison test, the settings, the run config's `baselines:` section, the templates, and the CI flag are untouched.
- **Reset, not migration** (user, 2026-10-07). The seven `ex*-suite.yaml` files, the coverage guard `test_every_runnable_example_has_a_suite_file` with its `UNCOVERED_EXAMPLES` set, and the `${CECE_ROOT_DIR}`-anchored-suite tests that exist only to serve them are deleted. The `${<PREFIX>ROOT_DIR}` config_path anchor itself **stays** (generic suite machinery, documented in design.md; a user may still point a suite at a config in the checkout); its three unit tests move into `test_suite_config.py` with a fabricated tree. The `CeceConfig` surface tests for `cadence`, `data_model`, `log_file`, `amio_worker_threads`, `grid_name` also stay (they test the model, not the examples) and move to `test_cece_config.py`. This is stronger than the issue's "keep the coverage guard as the one place the word survives": after this change the word "example" appears nowhere in `src/`, the templates, the runbook, or the README outside historical notes — **with one carve-out**: the CECE branch the harness pins is named `fix/all-examples-pass` (templates' `ref`, the workflow's `CECE_REF`, the `test_stages` assertions on the clone command). That is CECE's name, not the harness's vocabulary; it leaves when the pin leaves (Risks). The `test_user_docs` needle and the Verification grep exclude exactly that string.
- **One directory per application, one subdirectory per configuration, suites beside the configuration they sweep** (user question, 2026-10-07: CECE and CATChem configurations will both run here, and CATChem's configuration is several files). Today's split — base configs under `src/tests/config/<application>/`, every suite flat under `src/tests/config/suite/` — cannot hold a multi-file configuration and buries which suites belong to which configuration. The new layout is `src/tests/config/<application>/<configuration>/` holding the base configuration's file(s) and every `*-suite.yaml` that sweeps it (section 11). Three generic rules make it work for any application: (1) **suite discovery narrows to `*-suite.yaml`** (`select_suites` globs that instead of `*.yaml`, under the built-in root and every user search root alike), so configuration files in the same directory are never mistaken for suites — the "suite X lives in X-suite.yaml" convention, hitherto advisory, becomes the rule; (2) **`config_path` may name a directory** for an application whose configuration spans several files — `resolve_config_path` checks `exists()` instead of `is_file()` and the adapter's config model decides what shape it accepts (CECE: one YAML file; CATChem, in Phase C: a directory, loaded by its `CatchemConfig.from_path`); (3) **generated per-combination configs are written by the adapter** — today conftest calls `config.to_yaml(<combo_dir>/<combo_id>.yaml)` directly; that call moves behind an adapter method (`write_config(config, combo_dir, combo_id) -> PurePosixPath`, returning the driver's argument), which CECE implements as the same single file and CATChem will implement as a directory. Rules (2) and (3) are small, behaviour-preserving generalizations landed now so the layout decision does not paint Phase C into a corner; nothing CATChem-specific is written. This is the right moment: the reset already rewrites every suite file, and the CATChem plan (`design/spike/20260901-1229-rename-and-plan-for-catchem/`) sketches CATChem suites anchored on `${CATCHEM_ROOT_DIR}/configs/v1/<family>/`, which this layout mirrors for configurations the harness carries itself.
- **`inputs:` is generic suite configuration** (issue bullet 1): a list of `{url, dst, sha256?, public?}` on `SuiteConfig` in `models/suite_config.py`, `StrictModel` like everything else (unknown keys rejected). `dst` is relative to the application checkout's data directory, which the adapter names (`Application.data_dirname`, `"data"` for CECE) so the shared code stays application-agnostic. **`url` is an `s3://bucket/key` object URI, and the one transport is `s3_sync`** (user, 2026-10-07: the wrapper must pull from publicly accessible buckets). `public: true` marks a bucket that allows anonymous reads — the fetch then passes `--no-sign-request` and needs no credentials anywhere; `public: false` (the default, the safe convention) goes through the CLI's own credential chain, which is how a private input (data with no public source) would be declared. CECE's inputs are all public today, so every maccity entry says `public: true`. HTTPS was the earlier default and is dropped: one transport, one code path, one set of failure modes, and the AWS CLI is already a prerequisite in the toolchain image, on Ursa, and on GitHub runners (laptops install it, as the README's S3 section already says). A non-S3 web host is the only thing this gives up (Future work). Several suites of one configuration naturally declare the same input (the three maccity suites do): `fetch` deduplicates identical `(url, dst)` entries across the selected suites and refuses, before any transfer, two entries that name the same `dst` with different `url` or `sha256`.
- **A single object is fetched by syncing its parent prefix with filters, through the wrapper's one verb** (audit fact 10): `S3SyncConfig(source=<s3://bucket/parent/>, destination=<staging dir>, exclude=["*"], include=[<filename>], no_sign_request=public, only_show_errors=True)`. No `aws s3 cp` verb is added: "each call is one `aws s3 sync`" stays a tested invariant of the wrapper. The cost is one listing of the parent prefix per fetch (cheap for CECE's prefixes; noted under Risks for very large ones).
- **The wrapper gains `no_sign_request`** (`S3SyncConfig.no_sign_request: bool = False`, argv `--no-sign-request`), refused together with `profile` (anonymous and a named profile contradict). It reinstates the one AQM-Eval feature the S3 design left out, now with a user: public buckets. Nothing else in the wrapper changes; credentials remain the CLI's business.
- **Staging is the CLI's job, never pytest's.** A new subcommand, `ufs-chem-assay fetch`, resolves the selected suites exactly as a pytest session would (same `--application`/`--suite-config` semantics, same settings precedence) and stages their inputs; the run config's `data` stage calls it. The pytest session **fails fast at collection** when a declared input is missing (not under `--dry-run`, which needs no data), naming the file and the command — the same shape as the root-dir guard. pytest downloading on its own was considered and rejected: Ursa's compute nodes have no network, "pytest never orchestrates environment or data" is a standing design rule, and a test that silently fetches hides a provisioning step.
- **Skip by checksum, verify on download.** With `sha256` given, a present file whose digest matches is skipped; a mismatch is re-fetched. Without `sha256`, a present non-empty file is skipped (CECE's own `needs_fetch` rule). A download lands in a per-fetch staging directory under the data directory (`<data_dir>/.staging/<ulid>/`, the sync's destination), is verified there when a digest is given, and is moved into place only then — a killed or wrong fetch never leaves a half or foreign file under the real name; the staging directory is removed afterwards. All three maccity suites carry the digest from audit fact 4.
- **`run.yaml` records inputs as found at session start** (issue bullet 5, inputs half): per input `suite`, `url`, `path`, `sha256` (declared), `bytes` (present size, null when absent). The session does not hash files (a multi-gigabyte input would add minutes before the first container); hashing is `fetch`'s job, and `fetch` logs each digest result. The baselines half of the bullet is deferred with the rest of baseline sourcing.
- **Run-config surface shrinks, does not grow**: `applications.cece.examples` and `harness.run_examples` are removed; nothing replaces them — the selected suites say what to fetch. The `baselines:` section is unchanged.
- **CI cache: skip the build on an exact hit** (Part B; user, 2026-10-07: no registry-backed store). `actions/cache/restore` reports `cache-hit: true` only for the exact `cece-build-<sha>` key (a `restore-keys` fallback restores but reports false), and the restored tree already contains `build/cece_standalone_driver`; the build step gains `if: steps.restore-build.outputs.cache-hit != 'true'`. Same CECE SHA → no build at all (~2 min 40 s saved of a 4-minute job, audit fact 6); new SHA → the `cece-build-` prefix fallback seeds an incremental build exactly as today, and the save step stores the new key. The re-own step runs regardless (the driver runs as root in the container). The maccity data `actions/cache` step is removed — `fetch` skips a present file and the download is 1 MB. The `uv` step passes `enable-cache: true` and `cache-dependency-glob: uv.lock`. Accepted limits (audit fact 8): a CECE SHA unused for 7 days rebuilds cold once, and a PR's first run on a new SHA rebuilds unless `develop` already saved it; GHCR was considered for both and declined.
- **CECE stays pinned to `fix/all-examples-pass`** in this change. Of the three reasons for the pin (audit fact 3), the build-script flags remain; retargeting to `develop` is a CECE-side change (`--target`/`--jobs` upstream) and a separate PR here. The templates' `ref` comments stop mentioning examples.

### 1. Suite model — `models/suite_config.py`

```python
class InputFile(StrictModel):
    """One input the suite's base configuration reads, staged into the
    application checkout's data directory by `ufs-chem-assay fetch`."""

    url: str = Field(
        pattern=S3_OBJECT_URI_PATTERN,  # s3://bucket/key with a non-empty key not ending in '/'
        description="S3 object URI of the file (s3://bucket/key); fetched with one filtered `aws s3 sync` of the key's parent prefix",
    )
    public: bool = Field(
        False,
        description="True: the bucket allows anonymous reads and the fetch passes --no-sign-request (no credentials needed); False: the AWS CLI's own credential chain applies",
    )
    dst: PurePosixPath = Field(
        description="Destination relative to the checkout's data directory (the adapter's data_dirname); no absolute paths, no '..'",
    )
    sha256: str | None = Field(
        None,
        pattern=r"^[0-9a-f]{64}$",
        description="Expected SHA-256 (lowercase hex); a present file with this digest is skipped, a mismatch is re-fetched, a download is verified before it replaces anything; null skips present non-empty files unverified",
    )
    # validators: dst is relative, has no '..' or '.' parts, is not empty


class SuiteConfig(StrictModel):
    ...
    inputs: list[InputFile] = Field(
        default_factory=list,
        description="Input files the base configuration reads, staged by `ufs-chem-assay fetch` and required present at session start (dry runs excepted)",
    )
    # validator: dst values unique within the suite
```

`BaselineComparison` is unchanged. `RunManifest` gains one output-only list:

```python
class RecordedInput(StrictModel):
    suite: str; url: str; path: Path; sha256: str | None; bytes: int | None  # each with Field(description=...)

class RunManifest(StrictModel):
    ...
    inputs: list[RecordedInput] = Field(description="Every selected suite's declared inputs as found at session start")
```

The three maccity suites gain (comments abbreviated; `config_path` becomes `maccity.yaml`, suite-relative, per section 11):

```yaml
# Inputs the base config reads; `uv run ufs-chem-assay fetch --suite-config=simple-maccity-suite.yaml`
# stages them into <CECE_ROOT_DIR>/data (skipped when present with this digest).
inputs:
  - url: s3://geos-chem/HEMCO/MACCITY/v2014-07/MACCity_4x5.nc
    public: true  # anonymous reads (--no-sign-request); no AWS credentials needed
    dst: MACCity_4x5.nc
    sha256: ef70975af53499ab45f620a778043ff6fd29ec4e7e2777f3fd1f2db59a12aab9
```

### 2. Adapter surface — `applications/base.py`, `applications/cece/*`, `stubs.py`

- Remove `ExamplesSupport` and `Application.examples`; remove the `DownloadResult` type-only import.
- Add `data_dirname: ClassVar[str]` to `Application` (CECE: `"data"`; the inert stub adapter: `"data"` too), documented beside `checkout_dirname`; a helper `data_dir(root_dir) -> Path`.
- `applications/cece/cli.py`: `CeceRunSection.examples` removed; `_data` returns `[]` (the shared data stage does the work; the adapter hook stays for applications with extra data steps); the module docstring no longer mentions example data.
- `runner.docker_prefix` docstring: drop the examples sentence.

### 3. Staging — `src/staging.py` (new, generic)

```python
class StagedInput(StrictModel):          # frozen
    url: str; path: Path
    action: Literal["skipped", "downloaded", "failed"]
    detail: str                           # "sha256 verified" / "present, no digest declared" / the error
    sha256: str | None                    # digest of the file on disk when computed

def merge_inputs(suites: Sequence[SuiteConfig]) -> list[InputFile]   # deduplicate identical entries; ValueError on a dst declared two ways
def stage_inputs(inputs: Sequence[InputFile], data_dir: Path, *, dry_run: bool = False) -> list[StagedInput]
def missing_inputs(inputs: Sequence[InputFile], data_dir: Path) -> list[Path]      # existence only; the session guard
```

- Per input: present-and-verified → `skipped` with no network; otherwise one `s3_sync.sync` of the parent prefix filtered to the filename into `<data_dir>/.staging/<ulid>/`, then (digest given) `hashlib.sha256` over the staged file in 1 MiB chunks — a mismatch is `failed` with both digests and the staged file is removed — then `os.replace` into `<data_dir>/<dst>` (parents created) and the staging directory removed. A `CalledProcessError` from the wrapper (no credentials for a non-public input, no such key, network) or a `FileNotFoundError` (no `aws` on `PATH`, with the wrapper's install hint) becomes a `failed` action carrying the message. Every input is attempted even after a failure; `stage_inputs` never raises for a transfer error — the CLI turns any failure into a nonzero exit. A `dry_run` classifies (`skipped`/`would fetch`) and, for the ones it would fetch, passes `dry_run=True` through to the wrapper so the plan line (`(dryrun) download: s3://… to …`) is logged; nothing is written.
- A leftover `.staging/` from a killed run is removed at the start of the next `stage_inputs` (it never holds anything that cannot be re-fetched).
- Logging through `logs.get_logger("staging")`; no `print`.

### 4. Shared session resolution — `src/selection.py` (new)

The sequence conftest's `pytest_sessionstart` performs before any output root exists — `select_suites` → `_resolve_application` → `load_suite` per path with duplicate-name detection — moves into one pure function both entry points call:

```python
class ResolvedSuites(StrictModel):       # frozen; arbitrary types allowed for the adapter
    application: InstanceOf[Application]
    app_settings: InstanceOf[ApplicationSettings]
    suites: list[tuple[Path, SerializeAsAny[SuiteConfig]]]

def resolve_suites(settings: Settings, suite_option: str | None) -> ResolvedSuites   # raises ValueError
```

conftest wraps `ValueError` into `pytest.UsageError` as it does today and continues with enumeration and baseline-selector resolution (unchanged). Behaviour-preserving refactor; the existing selection tests keep passing against conftest's subprocess runs and gain direct unit tests.

### 5. CLI — `cli/main.py`, `cli/stages.py`, `cli/run_config.py`, templates

- **`ufs-chem-assay fetch [--application NAME] [--suite-config SELECTOR] [--dry-run]`**: loads `Settings()` (and `Settings(application=...)` when the flag is given, mirroring conftest), `resolve_suites`, requires the application root (`set CECE_ROOT_DIR`, the standard message) and, when anything needs fetching, `aws` on `PATH` (the wrapper's own hint; a fully-present data directory needs neither network nor the CLI), stages the merged inputs of the selected suites (`merge_inputs`; a conflict is a usage error before any transfer) into `<root>/<data_dirname>`. Exit 0 when every input ended `skipped`/`downloaded`; 1 otherwise, after logging each failure. A summary line per file at INFO. `--dry-run` reports and exits 0.
- **The `data` stage** (`cli/stages.py:_data`) becomes: `clean_python_env` + the same export block the harness stage renders (factored into `_exports(config, app, section)`, so `CECE_ROOT_DIR`, `ASSAY_SUITE_CONFIG_SEARCH_PATH`, `ASSAY_CONFIG_SEARCH_PATH`, `UV_CACHE_DIR` and the rest are identical in both scripts) + `cd <HARNESS_ROOT>` + `uv run --no-sync ufs-chem-assay fetch --application <app> --suite-config <harness.suite_config>` + the adapter's `stage_lines(DATA)` + the cartopy warm-up (first application only, as today).
- **Run config**: `HarnessSection.run_examples` removed (and its `--run-examples` pass-through in `_harness`). Templates: the `examples:` key under `applications.cece` and `run_examples:` under `harness:` deleted; `ref` comments stop mentioning examples. The `baselines:` section is untouched.
- Every file under `config/` and `docs/` is free of the word "example" (a `test_user_docs` needle enforces it).

### 6. pytest — `src/tests/conftest.py`

- `--run-examples` option, the `example_yaml` parametrization block, and the examples clause of the collection guard removed. The guard gains the **missing-inputs check**: after the root checks, for every suite context, `staging.missing_inputs(suite.inputs, app.data_dir(root_dir))`; any missing → `UsageError` listing the paths and `uv run ufs-chem-assay fetch --suite-config=<option>`. `--dry-run` returns before it, as today.
- `combo_roots` builds the `RecordedInput` list for `RunManifest` from the suite contexts; `generated_combos` calls `app.write_config(config, combo_dir, combo.combo_id)` instead of `config.to_yaml(...)` (section 11, rule 3).
- `_BUILTIN_SUITE_DIR` becomes `_BUILTIN_CONFIG_DIR = _TESTS_ROOT / "config"` (the whole tree is searched; discovery is by the `*-suite.yaml` glob).

### 7. Deletions

`src/examples.py`, `src/applications/cece/examples.py`, `src/tests/test_examples.py`, `src/tests/config/suite/ex1-suite.yaml` … `ex7-suite.yaml`, `src/tests/ufs_chem_assay/test_examples_gating.py`, `test_examples_module.py`, `applications/cece/test_cece_examples.py`, `applications/cece/test_cece_example_suites.py` (its surviving tests relocated per Decisions). `ExamplesSupport`, `Application.examples`, `CeceRunSection.examples`, `HarnessSection.run_examples`, the conftest option and clauses, the `--run-examples` and `data_download` passages of the README, and design.md's `--run-examples` option paragraph and examples-as-suites paragraph (replaced by a short "Inputs" subsection under Suite configuration and a pointer here — design.md is edited in the implementation phase only).

### 8. Documentation

- **README**: the opening paragraph says the harness tests specific configurations of an application and drops "examples" from the adapter's list; Prerequisites gains "the inputs of the suites you run, staged with `uv run ufs-chem-assay fetch`", and the AWS CLI v2 bullet now names `fetch` as a user (anonymous for public buckets — no account needed — credentials only for private inputs); the S3 data sync section drops "nothing in the harness calls it yet" and documents `no_sign_request`; Running loses the two example blocks and gains the `fetch` command (with `--dry-run`); the suite-YAML paragraph documents `inputs:` (`url`, `public`, `dst`, `sha256`); the `--suite-config` paragraph's multi-suite illustration uses the two exhaustive suites instead of `ex[0-9]-suite.yaml`; Results documents the `run.yaml` `inputs` block; the run-config section documents the `data` stage's new body; the CI section describes the skipped build on a cache hit and the `fetch` step. `test_readme_documents_the_new_surfaces` needles: `ufs-chem-assay fetch`, `inputs:`, `sha256`, `--no-sign-request`.
- **`docs/ursa-runbook.md`** step 5: the download command becomes `uv run --no-sync ufs-chem-assay fetch --suite-config=simple-maccity-suite.yaml` from the harness checkout, with one sentence on checksum skipping; the CECE-tooling Python note goes.
- **Suite file headers**: the three maccity suites document `inputs:`.
- **`design/design.md`** (implementation phase): Goal/README paragraphs (configurations, not examples), Applications (`data_dirname`, `examples` gone), Suite configuration ("Inputs"), Pytest integration (option removed, the inputs guard), Code layout (`staging.py`, `selection.py`, files removed), CI and releases (Part B), Non-goals (baseline sourcing: pointer to the Deferred section here; the S3 wrapper now has a caller), S3 data sync (`no_sign_request`), Resolved decisions.

### 9. CI — `.github/workflows/integration.yaml` (Part B)

Step order after the change: checkout both → buildx → CECE image via the gha buildx cache (unchanged) → resolve the CECE SHA → `actions/cache/restore` of `cece/build` with `id: restore-build` (key and `restore-keys` unchanged) → re-own the restored tree (unchanged) → **"Build CECE in the container" gated with `if: steps.restore-build.outputs.cache-hit != 'true'`** (`id: build`) → `actions/cache/save` gated on `if: always() && steps.restore-build.outputs.cache-hit != 'true' && steps.build.outcome == 'success'` (see Risks) → `setup-uv` with `enable-cache: true`, `cache-dependency-glob: uv.lock` → `uv sync --frozen` → **`uv run ufs-chem-assay fetch --suite-config=simple-maccity-suite.yaml`** (replaces the `download-example-data.py` step and the maccity `actions/cache` step) → pytest as today, still with `ASSAY_ENABLE_BASELINE_COMPARISONS: 'false'` → upload. The header comment is rewritten: the build is skipped on an exact hit and why a hit used to rebuild (mtimes), the fallback seeds an incremental build, the data comes from `fetch`, baselines remain off pending the deferred work. No new files in CI.

### 10. `src/s3_sync.py` — `no_sign_request`

```python
    no_sign_request: bool = Field(
        False,
        description=(
            "--no-sign-request: anonymous access for publicly readable buckets; "
            "no credentials are looked up. Refused together with profile"
        ),
    )
```

`argv` appends `--no-sign-request` after `--only-show-errors` and before `--profile`; `_check_sides` adds "`no_sign_request` and `profile` are contradictory". Module docstring: one sentence that public buckets are read anonymously with the flag. The wrapper stays import-free and single-verb; `S3_URI_PATTERN` is unchanged (an object URI is a valid prefix URI; staging derives the parent prefix and filename itself). Tests in `test_s3_sync.py`: the flag in argv, absent by default, the refusal with `profile`, and — under the existing `data_integration` marker, no credentials needed — `test_public_bucket_single_object`: the filtered unsigned sync of `MACCity_4x5.nc` from `s3://geos-chem/HEMCO/MACCITY/v2014-07/` into `tmp_path`, asserting exactly one file and the digest from audit fact 4.

### 11. Test configuration layout — `src/tests/config/<application>/<configuration>/`

```
src/tests/config/
  cece/
    maccity/
      maccity.yaml                               # the base driver config (was cece/simple-maccity.yaml)
      simple-maccity-suite.yaml                  # config_path: maccity.yaml (suite-relative)
      exhaustive-maccity-asserted-suite.yaml
      exhaustive-maccity-run-only-suite.yaml
  catchem/                                       # Phase C, not this change:
    <configuration>/                             #   several configuration files + the suites that sweep them;
      ...                                        #   config_path names the directory
```

- **Discovery** (`resolution.select_suites`): `root.rglob("*-suite.yaml")` replaces `rglob("*.yaml")`; the docstring, the README's `--suite-config` paragraph, and the `ASSAY_SUITE_CONFIG_SEARCH_PATH` descriptions say "every `*-suite.yaml` found recursively". The regex still fullmatches the file name or the root-relative posix path, so `simple-maccity-suite.yaml` (the default), `exhaustive-.*-suite.yaml`, and `cece/maccity/.*` all select as expected. `test_suite_selection.py` fixtures that name suites `x.yaml` are renamed `x-suite.yaml`; one new test proves a `maccity.yaml` beside a suite is not a candidate.
- **`config_path` may be a directory**: `SuiteConfig.resolve_config_path` requires `exists()` (message: "does not exist") and the adapter's `config_model` loader rejects the wrong shape (CECE's `from_yaml` on a directory is a plain `IsADirectoryError` turned into the usual `UsageError`). The field description says so.
- **`Application.write_config(config, combo_dir, combo_id) -> PurePosixPath`**: new adapter method; the base class provides the single-file default (`config.to_yaml(combo_dir / f"{combo_id}.yaml")`, returning that path's name), which CECE inherits; conftest's `generated_combos` calls it. A directory-configuration adapter overrides it. Covered by `test_cece_pipeline.py` (the generated file is where it was) and a stub-adapter test (an override is honoured).
- **Moves**: `git mv src/tests/config/cece/simple-maccity.yaml src/tests/config/cece/maccity/maccity.yaml`; the three suites into the same directory with `config_path: maccity.yaml`; `src/tests/config/suite/` ceases to exist. Fixtures in `src/tests/ufs_chem_assay/conftest.py` (`cece_config_path`, `suite_path`, `suite_dir`, `exhaustive_suite_path`) and the relative-resolution assertions in `test_suite_config.py` follow. The README (lines naming `src/tests/config/suite/`), design.md's Suite configuration, Pytest integration, Base configuration, and Code layout sections, and the exhaustive suite's header comment are updated.
- **Why suites live beside the configuration and not in a parallel tree**: a configuration and its suites change together (a new field in the base config, a new sweep over it), the relative `config_path` is one bare file name, and "what tests this configuration?" is answered by `ls`. The cost — the discovery rule — is what rule (1) pays.

## Implementation plan (TDD, red → green → refactor)

1. **Wrapper.** `test_s3_sync.py`: `no_sign_request` in argv, absent by default, refused with `profile`; the public-bucket live test under `data_integration`. Implement in `s3_sync.py`.
2. **Models.** Tests in `test_suite_config.py`: `inputs` parses, `dst` rejects absolute/`..`/empty, `sha256` pattern, duplicate `dst` rejected, `url` must be an `s3://bucket/key` object URI (bucket-only and trailing-slash values rejected), `public` defaults false; `RunManifest` round-trips `inputs`. Implement in `models/suite_config.py`.
3. **Adapter.** `test_registry.py`/stub: `data_dirname` present on every registered adapter; `ExamplesSupport` gone (import fails). Implement in `applications/base.py`, `cece/application.py`, `stubs.py`.
4. **Layout.** `test_suite_selection.py`: `*-suite.yaml` discovery, a sibling `maccity.yaml` is not a candidate, the default selector still resolves; `test_suite_config.py`: a directory `config_path` resolves, a missing one fails; `test_cece_pipeline.py` + a stub test: `write_config`. Then the `git mv` of the configuration and the three suites, the fixture paths, `_BUILTIN_CONFIG_DIR`. Implement in `resolution.py`, `models/suite_config.py`, `applications/base.py`, conftest.
5. **Staging.** `test_staging.py` (mocked `s3_sync.sync`, the fake writing the file into the destination it is given): `merge_inputs` dedupes identical entries and rejects a `dst` declared two ways; skip on matching digest with zero `sync` calls, re-fetch on mismatch, skip-unverified without digest, exactly one `sync` call per fetch with `source` = parent prefix, `exclude=["*"]`, `include=[filename]`, `no_sign_request` mirroring `public`; staged file verified then moved, staging directory gone afterwards; digest mismatch → `failed`, nothing under `dst`; `CalledProcessError`/`FileNotFoundError` → `failed` with the message; every file attempted after a failure; dry run passes `dry_run=True` through and writes nothing; stale `.staging/` removed; `missing_inputs`. Implement `src/staging.py`.
6. **Selection refactor.** `test_selection.py`: `resolve_suites` returns the same application/suites as the conftest path for the existing selection cases; duplicate-name error. Implement `src/selection.py`; conftest calls it; existing selection tests stay green.
7. **CLI `fetch`.** `test_cli.py`: argument parsing, exit codes (all skipped → 0; a failed download → 1; `--dry-run` → no transfer), the standard root-dir message. `test_stages.py`: the data stage renders the export block + the `fetch` line; `--run-examples` absent from the harness line. `test_run_config.py`: `examples`/`run_examples` keys rejected (`extra="forbid"`), templates load. Implement.
8. **pytest guard and manifest.** `test_dry_run.py`/`test_root_dir_guards.py`: a suite with a missing input is a `UsageError` naming `fetch`; `--dry-run` passes without the file; `run.yaml` carries `inputs` with `bytes: null` when absent and the size when present. Implement in conftest.
9. **Deletions and relocations** (section 7); `grep -ri example src config docs README.md | grep -v fix/all-examples-pass` returns nothing; `test_user_docs` needle added (same carve-out).
10. **Suites.** The three maccity suites, now under `cece/maccity/`, gain `inputs`; `--dry-run` on all three suites passes; `uv run ufs-chem-assay fetch --suite-config=simple-maccity-suite.yaml` against this Mac's checkout reports the maccity file `skipped` with `sha256 verified`, and against a checkout with `data/` moved aside fetches it anonymously and verifies it.
11. **Docs** (sections 8 and 11) and the design.md update.
12. **CI** (section 9): the build-skip `if:`, the gated save, the explicit uv cache key, the `fetch` step replacing the download and data-cache steps.
13. `uv run mypy`, `uv run pre-commit run --all-files`, the harness suite, `simple-maccity-suite.yaml` for real.

## Verification

```sh
# harness suite, type checks, hooks
uv run pytest src/tests/ufs_chem_assay
uv run mypy && uv run pre-commit run --all-files

# every suite dry-runs with no data present (fresh checkout: move data/ aside first)
for s in simple-maccity exhaustive-maccity-asserted exhaustive-maccity-run-only; do
  uv run pytest src/tests/test_driver_combos.py --dry-run --suite-config=$s-suite.yaml; done

# without the input the real run fails at collection, naming the fetch command
uv run pytest src/tests/test_driver_combos.py --suite-config=simple-maccity-suite.yaml   # UsageError

# stage, then run for real (baselines as today: local root from .env, or ASSAY_ENABLE_BASELINE_COMPARISONS=false);
# the fetch is anonymous (public: true) — no AWS credentials, and it must work with AWS_PROFILE unset
uv run ufs-chem-assay fetch --suite-config=simple-maccity-suite.yaml --dry-run   # logs "(dryrun) download: s3://geos-chem/..."
uv run ufs-chem-assay fetch --suite-config=simple-maccity-suite.yaml
# the wrapper's public-bucket live test (network, no credentials) beside the private one
uv run pytest -m data_integration src/tests/ufs_chem_assay/test_s3_sync.py -k public -v
uv run pytest src/tests/test_driver_combos.py --suite-config=simple-maccity-suite.yaml

# the CLI end to end on a laptop (docker runtime), data stage included
uv run ufs-chem-assay run --config-file=config/local.yaml --dry-run
uv run ufs-chem-assay run --config-file=config/local.yaml --stage data --stage harness

# Ursa (login node): runbook step 5 is now the fetch; then the harness stage as before
uv run ufs-chem-assay run --config-file=config/ursa.yaml --stage data

# nothing left (the CECE branch name is the one allowed occurrence)
grep -ri example src config docs README.md .github | grep -v 'fix/all-examples-pass' || echo clean
# the layout: suites beside their configuration, discovered by the -suite.yaml rule, default selector unchanged
ls src/tests/config/cece/maccity && test ! -e src/tests/config/suite
uv run pytest src/tests/test_driver_combos.py --dry-run                       # default suite still found
uv run pytest src/tests/test_driver_combos.py --dry-run --suite-config='cece/maccity/.*'   # all three, path-relative selector
```

CI acceptance: a PR run of `integration.yaml` shows "Build CECE in the container" **skipped** on a run whose restore step logs `Cache hit for: cece-build-<sha>` (the job then runs in roughly 1 min 20 s instead of 4 min); a run against a new CECE SHA restores the `cece-build-` fallback, builds incrementally, and saves the new key.

## Risks and notes

- **CECE stays on the fork branch.** `develop` lacks `--target`/`--jobs` on the container build script (audit fact 3c). Retargeting is a CECE PR plus a one-line `CECE_REF` change here; this design makes it the *only* remaining reason for the pin.
- **The maccity file's key is CECE's mapping, not a contract.** If the `geos-chem` bucket moves or removes the key, the filtered sync transfers nothing and `fetch` reports the input `failed` ("not fetched: no such object under the prefix" — the wrapper exits 0 for an empty match, so staging checks that the file arrived); the digest guards against a silently changed file.
- **One prefix listing per fetch.** The filtered sync lists the parent prefix before deciding; for CECE's prefixes (tens of objects) that is milliseconds, for a prefix with hundreds of thousands of objects it would be seconds. Only *missing* inputs pay it — a present, verified file never touches the network.
- **`aws` becomes a `fetch` prerequisite on laptops.** It already is one for anything S3 (README) and is present in the toolchain image, on Ursa, and on GitHub runners; a laptop without it gets the wrapper's install hint and nothing else breaks (`pytest` with the data present never needs it).
- **The skip trusts the cache.** A saved tree that is corrupt or was saved by a timed-out build would be restored and the build skipped, surfacing as a driver-not-found failure in the pytest step rather than a rebuild. Mitigation: the save step is gated on the build step's success, so a partial tree never lands under the exact key; the fallback seed is always the previous good tree. (Today's `if: always()` save was deliberate — "a timed-out build still persists its partial tree" — and is traded away here because a partial tree under the exact key would now *skip* the build forever for that SHA.)
- **Cold after idle.** A CECE SHA whose cache entry goes unused for 7 days is evicted and rebuilds cold once (the `cece-build-` fallback softens it when any sibling entry survives). Accepted with the decision not to use a registry-backed store.
- **The `*-suite.yaml` discovery rule is a (small) behaviour change for user search roots.** A user's suite named without the suffix is no longer discovered; the zero-match error lists the candidates it did find, and the README states the rule. The checked-in suites already follow it.
- **The `${CECE_ROOT_DIR}` anchor** keeps its tests but loses its only in-tree user; it stays because design.md documents it as the portable way to reference a config in the checkout and removing it would be a second, unrelated API change.

## Resolved decisions (user, 2026-10-07)

1. **Reset the CECE examples**: remove `ex1-suite.yaml` … `ex7-suite.yaml`, focus on maccity only. The alternative (carry the seven suites forward with `inputs` from CECE's `examples/common.py` mapping, as the issue's bullet 2 said) is dropped — the fork-branch tooling it depends on has drifted from CECE `develop`.
2. **No GHCR.** The cache stays `actions/cache`; the win is the build-skip `if:` on an exact hit. The user's "rebuilding every time" was a warm cache being recompiled on top of (audit fact 6), not a cold one; the registry-backed store would have bought only immunity to 7-day eviction and branch scoping. GHCR stays under Future work as the switch if eviction ever bites.
3. **Scope: retire "example"; baseline syncing is not implemented here.** The harness tests specific configurations of an application (CECE). Everything learned about baseline sourcing during this refinement is kept in the Deferred section as the input to its own design.
4. **Inputs come through `s3_sync`, from public buckets too.** `inputs[].url` is an `s3://` object URI; `public: true` passes `--no-sign-request`, which the wrapper gains. HTTPS is dropped as a transport. The trade-off that led to the earlier HTTPS default is kept below for the record.

5. **Directory-per-configuration layout, now** (user question, 2026-10-07; recommended and adopted here, switchable): `src/tests/config/<application>/<configuration>/` with suites beside the configuration, `*-suite.yaml` discovery, directory-capable `config_path`, adapter-owned `write_config`. Switch: keep the flat `config/suite/` and do the move with the CATChem adapter — at the cost of touching every suite file twice.

## Open questions

None. For the record, the trade-off behind the superseded HTTPS-only default:

1. ~~**`inputs[].url` HTTPS only.**~~ Default was: yes. *Why*: the only input in scope (the MACCity file) is public and served over HTTPS; the one case that wanted private data (CAMS-TEMPO for ex1/ex7) left with the reset; a `urllib` download is stdlib-only, needs no credentials and no AWS CLI, so `fetch` works on a laptop, a CI runner, and the Ursa login node with nothing but Python; the failure modes are HTTP statuses; and streaming through `urllib` lets the digest be computed as bytes arrive, so a file is verified before it is renamed into place. Adding `s3://` later is purely additive — the `url` pattern widens, staging dispatches on scheme, no suite changes. *What `s3://` would cost*: the wrapper only syncs prefixes, so one object means syncing the key's parent prefix with `exclude=['*']`/`include=[<filename>]` into a temporary directory, then verifying and renaming (an `aws s3 cp` verb would break the wrapper's single-verb invariant); the wrapper passes no `--no-sign-request`, so even a public bucket read over `s3://` fails with `Unable to locate credentials` on an unconfigured machine — either the wrapper gains that flag or public data stays on HTTPS and `s3://` is reserved for private objects; and `fetch` gains a second failure mode (CLI exit codes and output beside HTTP errors) and may need credentials for some inputs and not others. *Switch when*: a suite needs data with no public source (it then lives in the private `ufs-chem` bucket); the team mirrors public inputs into the private bucket for stability (the geos-chem URL is CECE's mapping, not a contract — see Risks); or a public dataset is requester-pays or otherwise signed-access only, where HTTPS cannot reach it.

## Future work

- **Baseline sourcing** — the whole of the next section.
- Inputs declared once per configuration directory (a `configuration.yaml` beside the base config) instead of repeated in each suite — only if the per-suite duplication grows beyond the one entry it is today; `merge_inputs` already makes the duplication harmless.
- Prefix inputs (`url` ending in `/`, `dst` a directory, no `sha256` or a manifest): the wrapper's native case, one sync, for multi-file datasets.
- An `https://` transport for a non-S3 web host, should one ever appear (stdlib `urllib`, digest on the fly); every known CECE source is an S3 bucket.
- Retarget `CECE_REF` to `develop` once its container build script accepts `--target`/`--jobs`.
- A `fetch --prune` that removes files under the data directory no selected suite declares (never by default); a `--verify` that hashes without downloading.
- A registry-backed build cache (a `FROM scratch` image of `cece/build` tagged by CECE SHA in `ghcr.io`) if the 7-day eviction or branch scoping of `actions/cache` turns out to cost real time; declined for now.

## Deferred: baseline sourcing — knowledge recorded for the follow-up

Not implemented in this change (user, 2026-10-07). Everything below was worked out during this refinement, confirmed or directed by the user where marked, and is kept so the follow-up design starts from it rather than from the issue text.

**Direction from the user (2026-10-07).** Baselines will always be stored as an S3 target when not already available locally; the harness should always evaluate against new baselines; the scan for new baselines must be optimized; a new baseline always has a new ULID; the question "what do we do with stale baselines" was asked and answered as below.

**Where things stand.** `BaselineComparison` has `ulid` and the comparison resolves `<ASSAY_BASELINE_ROOT_DIR>/<ulid>/`; a missing directory fails the test as "configured but missing"; `ASSAY_ENABLE_BASELINE_COMPARISONS=false` skips every comparison (CI and the Ursa template today). The three maccity baselines (audit fact 5) exist only in the user's Dropbox. `src/s3_sync.py` is the transport (one `aws s3 sync` per call, reentrant, credentials the CLI's own, `dry_run` passes `--dryrun` through); the private bucket is `arn:aws:s3:::ufs-chem`, used by the wrapper's live test under `ufs-chem-assay-tests/`.

**The design that was reached, in order of decisions.**

1. *One store, not per-entry URLs.* The issue's `baseline_comparisons[].url` is superseded: a suite pins ULIDs only; one harness-wide setting `ASSAY_BASELINE_STORE` (`Settings.baseline_store`, `s3://bucket/prefix`, default `s3://ufs-chem/baselines`; mirrored by `baselines.store` in the run config, `baseline_store` added to `SETTINGS_NOT_MIRRORED`) names the store, and a baseline's source is always `<store>/<ulid>/`. The `baselines/` prefix is a choice that keeps it apart from the live test's prefix. The local `baseline_root_dir` becomes a **cache** in front of the store.
2. *The scan.* ULIDs referenced by the selected suites are deduplicated; each is classified by one `stat` of a marker file `<root>/<ulid>/.synced` (one-line JSON `{"source": ..., "synced_at": ...}`). Marked → local, no network. Unmarked → exactly one `s3_sync.sync(S3SyncConfig(source=<store>/<ulid>/, destination=<root>/<ulid>/, only_show_errors=True))`; on exit 0 with at least one `.nc` present the marker is written. Zero network when everything is marked; one reentrant sync per missing ULID; a complete directory without a marker (the Dropbox copies) costs one no-op sync, once. The marker is what keeps the scan from degenerating into one S3 listing per entry per run. Sketch: `staging.ensure_baselines(ulids, store, baseline_root, *, dry_run) -> dict[str, BaselineState]` with `state ∈ {local, synced, failed}` and `detail`; never raises for a sync failure.
3. *Where the scan runs.* Both in `fetch` (pre-warm for the `data` stage: Ursa login node, CI) **and at pytest session start** — the one deliberate case of pytest doing network I/O, justified because baselines are the harness's own artifacts, small (144 KB each here), immutable, and consumed by a test, unlike inputs (gigabytes, the driver's). A newly pinned ULID is then compared against on the very next run with no manual step. `--dry-run` classifies without transferring. `enable_baseline_comparisons=false` → nothing scanned. `baseline_root_dir` unset with a sync needed → that comparison fails naming `ASSAY_BASELINE_ROOT_DIR` (unset means cwd for reading today; the harness must not write there).
4. *Failure semantics.* A failed sync (no credentials, no such prefix, network) fails **that comparison test** with the error text — the session, the driver runs, and the other comparisons proceed. This replaces "configured but missing" so a comparison is always evaluated or honestly red, never skipped. `run.yaml` records per baseline `suite`, `ulid`, `source`, `path`, `state`, `detail`. The comparison test reads the stashed state: `local`/`synced` → compare; `failed` → fail with the detail.
5. *The ULID invariant (user question, answered).* A baseline is a copy of one combination's output directory `<output-root>/<combo_ulid>/*.nc`; its name in the store is that combination's **runtime ULID**, minted fresh every run and never derived from content or configuration. So a new baseline always has a new ULID, the store is **append-only**, and a `.synced` marker never goes stale. Republishing after an intentional driver change = run → inspect → publish the new combination's output as a new `<store>/<ulid>/` → edit the suite's `ulid` (one line, commit trail) → the next session fetches it. `run.yaml` already records the ULIDs every run compared against, so any past run is re-evaluable. The publishing command (future) refuses to write into a prefix that already holds objects — detectable without a second verb by a `--dryrun` sync of the prefix into an empty directory.
6. *Stale baselines (user question, answered).* **Store**: never pruned by the harness; an unreferenced ULID stays (cheap; git history and old `run.yaml` files point at it); removal is a human decision outside the harness. **Cache**: disposable, re-syncable, and still never deleted silently — an opt-in `fetch --prune-baselines` lists and removes cached `<ulid>/` directories that (a) carry the harness's marker (the harness deletes only what it fetched) and (b) no suite among the **discoverable** candidates (every `*.yaml` under the suite search roots plus the built-in directory, not only the selected ones, so a narrow `--suite-config` cannot prune another suite's baselines) references; `--dry-run` lists only. A pruned ULID referenced again is simply re-synced. A `fetch --refresh-baselines` (drop markers, re-sync) is the escape hatch for a baseline placed by hand under an existing ULID.
7. *Turning comparisons back on.* Ursa template: `baselines.enabled: true` once the three baselines are uploaded (the AWS CLI is installed and configured there since 2026-10-07); `baselines.root_dir: null` could derive `<root_dir>/baselines` in the CLI so the template runs as shipped. CI: credentials via `aws-actions/configure-aws-credentials` with OIDC (`permissions: id-token: write`), gated on a repository variable `AWS_ROLE_TO_ASSUME`; `ASSAY_ENABLE_BASELINE_COMPARISONS` becomes `'true'` only when the variable is set and the event is not a fork's. AWS side: an OIDC provider for `token.actions.githubusercontent.com`, a role trusting `repo:benkozi/ufs-chem-assay:*`, with `s3:ListBucket` on the bucket and `s3:GetObject` under `baselines/`. Inside a `continue-on-error: true` job a red credentials step would hide the maccity result, so set the variable only after one successful manual run. Upload of the existing three, by hand: `AWS_PROFILE=<profile> aws s3 sync /path/to/cece-baselines s3://ufs-chem/baselines/ --exclude '*' --include '<ulid1>/*' --include '<ulid2>/*' --include '<ulid3>/*'`.
8. *An open reading of "new baselines".* The design above reads it as newly *referenced* ULIDs (a suite pins; publishing is a one-line edit with a commit trail). The other reading — the suite omits `ulid` and the harness compares against the newest ULID the store holds for that combination — needs a store layout keyed by combination (`<store>/<application>/<suite>/<combo>/<ulid>/`), one listing per combination at session start (the wrapper has no list verb: a second verb, or a `--dryrun` sync parsed for keys), and `run.yaml` recording the resolved ULID for reproducibility. The marker logic carries over unchanged once a ULID is resolved. To be decided in the follow-up.
9. *Tests sketched for the follow-up.* Marker present → zero `sync` calls; unmarked → exactly one call with the expected `source`/`destination`; marker written only after success with a `.nc`; duplicate ULIDs across suites synced once; failure recorded not raised; `baseline_root=None` → `failed` naming the setting, nothing written; dry run writes no marker; `prune_baselines` removes marked-and-unreferenced only; `test_comparison.py`: `failed` state fails with the detail, `synced` compares; `test_settings.py`: `baseline_store` default, env override, non-`s3://` rejected; `BaselineComparison` still rejects a `url` key.

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
- persist the agy session id and resume command in any design/refined files for resumability

## session

- **Session ID**: `74462254-d492-4e73-a00d-6924d18e3484`
- **Resume Command**: `agy --resume 74462254-d492-4e73-a00d-6924d18e3484`

### testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- refine from https://github.com/benkozi/ufs-chem-assay/issues/11
- we also need to see if the integration job can be cached. i recommend a globally persistent cache based on the CECE hash
  - no reason to rebuild cece
  - we should also cache the uv installation unless the lock file changed

Issue #11 ("Suites declare their own inputs and baseline sources; retire \"example\""), as read on 2026-10-07:

- Premise (already true today): an "example" is just a configuration. Every runnable shipped configuration has a checked-in suite — the coverage guard `test_every_runnable_example_has_a_suite_file` fails when a new one appears without one — and those suites run the configurations through the full pipeline (assertions, stats, plots, report rows). The older `--run-examples` path runs the same configurations verbatim through CECE's entrypoint, asserts exit code only, writes no report rows, and is unsupported under the slurm runtime; it is redundant once a configuration is a suite.
- Add `inputs:` to the suite config: a list of `{url, dst, sha256?}` entries, `dst` relative to the application checkout's `data/`; unknown keys rejected like everything else in the suite model.
- Give the seven CECE example suites their `inputs` from the mapping in CECE's `examples/common.py`, so an example is just a suite with downloadable inputs; drop `data.examples` from the run config.
- Make the CLI's `data` stage (and `--run-examples`' download pass) stage the inputs of the **selected** suites only, skipping files that already exist with a matching checksum.
- Add a per-entry `url` beside `ulid` in `baseline_comparisons`, fetched into `CECE_BASELINE_ROOT_DIR` when absent, and turn `CECE_ENABLE_BASELINE_COMPARISONS=false` back on in CI and the Ursa template once the store is reachable.
- Record staged inputs and fetched baselines (path, source, checksum) in `run.yaml`.
- Retire the example-specific machinery: `--run-examples`, `src/tests/test_examples.py`, `src/examples.py`, and the `examples-report.md`; keep the coverage guard (renamed to "every shipped configuration has a suite") as the one place the word "example" survives, since the configurations live in CECE's `examples/config/`. Suite ids stay as the configuration file names give them (`ex3-suite.yaml`).
- Vocabulary in README, design.md, and the run config: "configuration" and "suite", not "example"; `data.examples` becomes the suites' `inputs` per the first bullet.

## conversational updates

- 2026-10-07 (refinement, before user input): the issue's bullets were mapped onto the code as it stands (audit facts 1–2: the run-config key is already `applications.cece.examples`, the setting is `ASSAY_ENABLE_BASELINE_COMPARISONS`), CECE's `examples/common.py` on the fork branch was read for the seven examples' data mapping, and the local checksums of every file under the CECE checkout's `data/` were computed so that `inputs` entries could carry digests. The CI question was answered by measurement rather than assumption: the latest green integration run shows the build step recompiling all of CECE on a cache *hit* (2 min 41 s of a 4-minute job), the uv cache already active through `setup-uv`'s `auto` default, and the GitHub cache's eviction and branch scoping as the reason a GHCR package is the "globally persistent" store.
- 2026-10-07 (user direction: "reset with the cece examples — significant drift; remove the ex1-* suite files and focus on maccity only"): the design pivoted from migrating seven example suites to deleting them. The drift was verified against CECE `develop` (audit fact 3: layout, tooling, and build-script flags all differ from the pinned fork branch; the coverage guard cannot even find configs there). Consequences recorded in Decisions: the `inputs` model is unchanged but has exactly one entry across the three maccity suites (the MACCity file, digest from the local copy); the coverage guard and the `${CECE_ROOT_DIR}`-anchored example tests go while the anchor and the `CeceConfig` surface tests stay and relocate; `s3://` inputs lose their motivating case and move to Future work; the fork-branch pin keeps one reason (the build-script flags) and is noted as the follow-up. No files other than this document were created.
- 2026-10-07 (user: "let's not do the ghcr route; I guess it was a cold cache I was hitting? I just saw it rebuilding every time"): GHCR removed from Goal 4, Decisions, section 9, the Implementation plan, Verification, Risks, and the open questions (now Resolved decision 2; the registry is a Future-work switch). The log of the run in audit fact 6 answers the question: the restore step reported `Cache hit for: cece-build-b28ba696…` (exact key, 269 MB) and the build step still recompiled every CECE object — a warm cache rebuilt on top of, because the checkout's source mtimes postdate the restored objects. The fix is the `if:` on the build step, now the whole of Part B's CECE change, plus a save step gated on build success (new Risk) and the explicit `uv.lock` cache key.
- 2026-10-07 (user: "baselines will always be stored as an S3 target if not available locally already; is the scan for new baselines optimized? we just always want to evaluate against new baselines"): the per-entry `url` from the issue is dropped — a suite pins ULIDs only, and one setting, `ASSAY_BASELINE_STORE` (default `s3://ufs-chem/baselines`, mirrored by `baselines.store`), names the single store; the local root is a cache. The scan is now specified (Decisions, section 3): ULIDs deduplicated across suites, one `stat` of a `.synced` marker per ULID, zero network when all are marked, exactly one reentrant sync per unmarked ULID, the marker written only after a successful sync — so the Dropbox copies on this Mac cost three no-op syncs once and nothing after. The session itself runs the scan at session start (the one deliberate case of pytest doing network I/O, justified by the artifacts being small, immutable, and the harness's own), so a newly pinned ULID is compared against on the next run with no manual step; a failed sync fails that comparison test with the error instead of skipping, replacing "configured but missing". `fetch` pre-warms the same scan for the data stage. Two readings of "new baselines" are recorded; the pinned-ULID reading is the default and the newest-published reading is open question 3 with its costs. New Risks: pytest network I/O on credential-less laptops, and the marker never re-checking a republished ULID (a `--refresh-baselines` escape hatch under Future work).
- 2026-10-07 (user: "what do we do with stale baselines? new baselines will always have a new ULID?"): yes, by construction — a baseline is the copy of a combination's output directory and carries that combination's runtime ULID, minted fresh every run; recorded as the append-only-store invariant in Decisions, with the Future-work publishing command refusing to write into an occupied prefix. Stale handling split by place: the store keeps every ULID (history and old `run.yaml` files reference them; removal is a human decision, never the harness's), the local cache is disposable and gets an opt-in `fetch --prune-baselines` that removes only marked directories no *discoverable* suite references (section 3's `prune_baselines`, section 5's flag, Verification commands, tests in the plan). The marker Risk now rests on the invariant rather than on a README note.
- 2026-10-07 (user: "remember we're not trying to implement baseline syncing in this entirely — we just want 'examples' to no longer be a thing; we're testing specific configurations for an application, CECE so far"; then: "baseline syncing will be useful in the future for sure, so record any knowledge gained"): the document was rewritten around that scope. Goal, Decisions, sections 1–9, the plan, Verification, Risks, and the open questions now cover only the retirement of "example" (with `inputs:` + `fetch` as the minimal replacement for the one example path that did real work — staging the maccity file — and Part B's build-skip). Everything about baseline sourcing — the single S3 store and the cache, the marker-based scan and its cost model, where the scan runs and why pytest may do network I/O there, the failure semantics replacing "configured but missing", the new-ULID-per-baseline invariant and the append-only store, the stale policy for store versus cache and `--prune-baselines`, the CI OIDC gate and Ursa template flip, the upload command, the two readings of "new baselines", and the tests sketched — moved intact into "Deferred: baseline sourcing — knowledge recorded for the follow-up", so the next design starts from the user's directions rather than from the issue text. `baseline_comparisons`, the comparison test, the settings, the `baselines:` run-config section, the templates, and the CI flag are untouched by this change; Resolved decision 3 records the scope.
- 2026-10-07 (user: expand on the remaining open question): open question 1 (`inputs[].url` HTTPS-only) now states the reasons for the default, the concrete cost of accepting `s3://` (single-object fetch through a prefix-sync wrapper, the missing `--no-sign-request`, a second failure mode in `fetch`), and the three conditions under which to switch; the Future-work bullet carries the implementation sketch. No change to the design itself.
- 2026-10-07 (user: "yes, we need s3_sync to pull data from publicly accessible buckets — make any necessary changes"): inputs switch to the wrapper as their one transport. Verified first on this Mac (audit fact 10): `aws s3 ls --no-sign-request` lists the geos-chem prefix anonymously and a filtered unsigned sync fetches exactly `MACCity_4x5.nc` with the expected digest. Design changes: `inputs[].url` is an `s3://bucket/key` object URI with a `public` flag (default false; `true` → `--no-sign-request`), HTTPS is dropped (Resolved decision 4, the old trade-off kept struck through), a single object is fetched by syncing its parent prefix with `exclude=['*']`/`include=[<filename>]` into `<data_dir>/.staging/<ulid>/` and moved into place after verification, and `S3SyncConfig` gains `no_sign_request` (refused with `profile`) — new section 10 with its tests, including a credential-free public-bucket live test under the existing `data_integration` marker. The wrapper's single-verb invariant stands; `fetch` is its first caller. New Risks: an empty filtered match exits 0 (staging checks arrival), one prefix listing per missing input, `aws` on laptops. The maccity YAML, plan steps (renumbered, wrapper first), Verification, README/design.md notes updated; prefix inputs and an HTTPS transport for non-S3 hosts go to Future work.
- 2026-10-07 (user: review once more; CECE and CATChem configurations will both run here, CATChem's configuration is several files — move to a directory structure under `tests/config`?): yes, adopted now while the reset already rewrites every suite file — Decisions and new section 11: `src/tests/config/<application>/<configuration>/` with the base configuration's files and its `*-suite.yaml` files side by side (`cece/maccity/` holds `maccity.yaml` and the three suites; `config/suite/` goes), discovery narrowed to `*-suite.yaml`, `config_path` allowed to name a directory (`exists()`; the adapter's loader decides the shape), and generated per-combination configs written through a new adapter method `write_config` with the single-file default — the three generic rules that let CATChem's directory configuration land in Phase C without touching shared code; nothing CATChem-specific is written. Review findings fixed: the "example appears nowhere" claim now carves out the CECE branch name `fix/all-examples-pass` (templates, `CECE_REF`, `test_stages`), with the Verification grep and the `test_user_docs` needle excluding it; identical `inputs` across the three maccity suites are deduplicated by a new `merge_inputs`, which also refuses a `dst` declared two ways. Plan renumbered (layout as step 4), Verification gained the layout checks, Risks the discovery-rule change, Resolved decision 5 the layout with its switch, Future work the per-configuration inputs alternative.
