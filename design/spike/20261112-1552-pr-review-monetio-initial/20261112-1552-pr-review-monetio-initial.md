# Pull Request Review: [benkozi/ufs-chem-assay#45] - feat: add monetio application adapter and aeronet suite

## 1. Executive Summary & Verdict
- **Verdict**: REQUEST CHANGES
- **Target Repo & PR**: [benkozi/ufs-chem-assay#45](https://github.com/benkozi/ufs-chem-assay/pull/45)
- **Pinned Revision**: `55d71df..4241e20` (58 changed files across 6 logical groups)
- **Lines Removable (Ponytail)**: ~39 lines
- **Findings Summary**: 1 Blocker (P0), 2 Majors (P1), 5 Over-Engineering / Edge Cases (P2), 2 Nits (P3)
- **Change Groups Reviewed**:
  1. New `monetio` application adapter (`src/applications/monetio/*`, 8 files)
  2. Application base, registry, `cece` refactor & global settings (`src/applications/base.py`, `src/applications/cece/*`, `src/applications/registry.py`, `src/settings.py`, `src/models/conventions.py`, 6 files)
  3. Core harness, runner, selection, analysis & comparison (`src/runner.py`, `src/selection.py`, `src/analysis.py`, `src/comparison.py`, 4 files)
  4. CLI & run config (`src/cli/*`, 8 files)
  5. AERONET suite config, local config, project config & docs (`config/local.yaml`, `pyproject.toml`, `README.md`, `design/*`, `src/tests/config/monetio/aeronet/*`, 8 files)
  6. Tests & fixtures (`src/tests/conftest.py`, `src/tests/ufs_chem_assay/*`, 24 files)
- **Strengths / What Went Well**:
  - Clean separation of concerns across `Application` (`driver_arguments` hook), `MonetioConfig` / `MonetioSuiteConfig` (`StrictModel` + `StrictBoolLoader`), and `analysis.spatial_variables` / `comparison._compare_variable` generalization for UGRID `(time, node)` point datasets and non-numeric variables.
  - Thorough unit and dry-run test coverage in `src/tests/ufs_chem_assay/applications/test_monetio.py` (543 lines) and updated CLI/selector/settings tests, plus full retirement of the legacy `CECE_` harness fallback.
  - All 8 prior review comments from `@benkozi` were addressed cleanly in subsequent commits.
- **High-Level Takeaway**:
  The PR cleanly introduces `MonetioApplication` as the second application adapter alongside `CeceApplication`, retires the transitional `CECE_` harness prefix, and wires `aeronet-suite.yaml` through the shared enumeration, comparison, and analysis pipeline. However, `MonetioApplication.driver_arguments` currently attempts to read the container-scoped `driver_yaml` path on the host filesystem, which will raise `FileNotFoundError` under `Runtime.DOCKER` (the default for `config/local.yaml`), and `integration.yaml` does not yet exercise `monetio` end-to-end.

## 2. Existing PR Review Discussions, Validity Assessment & Status

| Author | File / Context | Summary of Prior Comment | Validity Verdict | Technical Validity Assessment & Status |
| :--- | :--- | :--- | :--- | :--- |
| `@benkozi` | `config/local.yaml:23` | Uncomment `monetio` and make `--application` mandatory with `all` and multi-selector support (`"monetio cece"`). | `[VALID]` | `[RESOLVED]` — Uncommented `monetio:` in `config/local.yaml:16-24`, set `required=True` and `nargs="+"` on `--application` in `src/cli/commands/run.py:59-70`, and added `all` / whitespace-split token handling in `src/cli/selectors.py:43-72`. |
| `@benkozi` | `src/applications/monetio/assertions.py:35` | Replace module-level globals with a Pydantic model. | `[VALID]` | `[RESOLVED]` — Replaced with `MonetioAssertionSpec(StrictModel)` in `src/applications/monetio/assertions.py:15-40`. |
| `@benkozi` | `src/applications/monetio/combos.py:15` | Pull `_TARGET` global into settings or explicit parameter. | `[VALID]` | `[RESOLVED]` — Removed `_TARGET`; `dimensions()` and `effective_parameters()` now accept `target: str` from `MonetioApplication.name`. |
| `@benkozi` | `src/applications/monetio/settings.py:11` | Move `env_prefix` and `container_workdir` onto settings. | `[VALID]` | `[RESOLVED]` — Moved onto `ApplicationSettings`, `MonetioSettings`, and `CeceSettings` as `ClassVar` attributes. |
| `@benkozi` | `src/applications/registry.py:24` | Remove `DEFAULT_APPLICATION` following `--application` change. | `[VALID]` | `[RESOLVED]` — Removed `DEFAULT_APPLICATION` from `registry.py` and `selection.py`; `resolve_suites()` now raises `ValueError` if neither `--application` nor `--suite-config` is given. |
| `@benkozi` | `src/cli/run_config/sessions.py:16` | Move `effective_runtime` to setting/section level. | `[VALID]` | `[RESOLVED]` — Moved to `HarnessSection.effective_runtime()` in `src/cli/run_config/sections.py:190-192`. |
| `@benkozi` | `src/cli/main.py:115` | Read `log_level` through Pydantic `Settings`. | `[VALID]` | `[RESOLVED]` — Updated `src/cli/main.py:112` to `configure_logging(Settings().log_level)`. |
| `@benkozi` | `src/tests/config/monetio/aeronet/aeronet.yaml:1` | Rename `aeronet_base.yaml` to `aeronet.yaml`. | `[VALID]` | `[RESOLVED]` — Renamed to `aeronet.yaml` and updated `aeronet-suite.yaml:6`, tests, and `design/design.md`. |

## 3. GitHub Actions CI Checks & Test Coverage/Gating Analysis

| Workflow / Check Name | Result | Elapsed Time | Analysis & Gating Status |
| :--- | :--- | :--- | :--- |
| `ci/tests (pull_request)` | `[PASSED]` | 3m 42s | Runs `uv run pytest src/tests/ufs_chem_assay` inside the runner container with no `ASSAY_*`/`CECE_*`/`MONETIO_*` env vars; driver execution is mocked/stubbed. |
| `ci/pre-commit (pull_request)` | `[PASSED]` | 1m 10s | Runs `ruff`, `mypy`, `codespell`, `vulture`, `yamlfix`, `yamllint`, and `conventional-pre-commit` across the repo (`design/` excluded from yaml/whitespace/codespell). |
| `integration/simple-macci...` | `[PASSED]` | 5m 25s | Stages `simple-maccity-suite.yaml` and runs `uv run pytest src/tests/test_driver_combos.py --suite-config=simple-maccity-suite.yaml` for `cece` only. |
| `ci/build (pull_request)` | `[PASSED]` | 20s | Builds the harness runner image. |
| `ci/actionlint (pull_request)` | `[PASSED]` | 7s | Lints `.github/workflows/*.yaml`. |
| `CodeQL` / `codeql/analyze` | `[PASSED]` | 4s–1m 13s | Static analysis for Python and Actions. |
| `dependency-review` | `[PASSED]` | 8s | Checks dependency diffs. |
| `ci/release-preview` | `[PASSED]` | 11s–1m 34s | Dry-runs semantic-release. |
| `ci/release-psr` | `[SKIPPED]` | — | Push-only release job; expected skip on PRs. |

- **False Confidence / Gating Explanation**:
  Although all 11 active checks passed, neither `ci/tests` nor `integration/simple-macci` executes `MonetioApplication` against a real `monetio` CLI or `aeronet-suite.yaml`. Furthermore, `test_runner.py:167` monkeypatches `driver_arguments` on a stubbed `CeceApplication` rather than testing `MonetioApplication.driver_arguments` through `build_command(..., Runtime.DOCKER)`, masking the `[P0]` `FileNotFoundError` when `driver_yaml` is a container path (`/work/...` or `/assay-output/...`).

## 4. Prioritized Action Items
- [ ] **[P0]** `MonetioApplication.driver_arguments` fails with `FileNotFoundError` under `Runtime.DOCKER` on container-scoped `driver_yaml` paths — `src/applications/monetio/application.py:49-55`
- [ ] **[P1]** CI integration workflow does not exercise `monetio` or `aeronet-suite.yaml` — `.github/workflows/integration.yaml:183-186`
- [ ] **[P1]** Committed `benchmarks.csv` records `baseline_state=local` from a `-dirty` harness commit — `src/tests/config/monetio/aeronet/benchmarks.csv:2-3`
- [ ] **[P2]** Non-numeric comparison treats `NaT != NaT` as mismatched on `datetime64` coordinates — `src/comparison.py:257-266`
- [ ] **[P2]** Ponytail: Duplicate `_module_block` and `_source` shell-stage builders across `monetio/cli.py` and `cece/cli.py` — `src/applications/monetio/cli.py:28-59`
- [ ] **[P2]** Ponytail: Hardcoded `"monetio/monetio-dev"` default duplicates `MonetioSettings.docker_image` — `src/applications/monetio/cli.py:66`
- [ ] **[P2]** Ponytail: Redundant `output_variable_names()` wrapper re-instantiates `MonetioAssertionSpec` on every call — `src/applications/monetio/assertions.py:53-55`
- [ ] **[P2]** Ponytail: Duplicated `unknown` application validation in `selected_applications` — `src/cli/selectors.py:56-69`
- [ ] **[P3]** Stale `README.md` prose claims CECE is the default in every run config — `README.md:14-15`
- [ ] **[P3]** `aeronet.yaml` header comment mentions `-v` flags that `driver_arguments` does not emit — `src/tests/config/monetio/aeronet/aeronet.yaml:3`

## 5. Detailed Findings & Machine-Applicable Suggestions

### [P0] `MonetioApplication.driver_arguments` fails under `Runtime.DOCKER`
- **Location**: `src/applications/monetio/application.py:49-55`
- **Recommended Post Location**: Inline on `src/applications/monetio/application.py` (lines `49-55`)
- **Tag**: `[defect / runtime]` | Lines affected: 7 lines

**Runtime: `driver_arguments` cannot resolve container-scoped `driver_yaml` paths under Docker**

In `Runtime.DOCKER` (the default for `platform: local` in `config/local.yaml`), `runner.build_command` calls `driver_command(app_settings, driver_yaml, app=app)` with a container path such as `/work/.../combo.yaml` or `/assay-output/.../combo.yaml`. `_resolve_yaml_path` checks `Path(driver_yaml).is_file()` on the host (which is `False`) and then computes `Path(root) / Path(driver_yaml)`, which in `pathlib` discards `Path(root)` whenever `driver_yaml` is absolute, causing `MonetioConfig.from_yaml(...)` to raise `FileNotFoundError` before `docker run` starts. Additionally, reading `os.environ.get("MONETIO_ROOT_DIR")` directly misses `MONETIO_ROOT_DIR` when it is configured only in `.env`.

**Suggested fix:** Pass the host `Path` (or the already-loaded `DriverConfig` from `GeneratedCombo`) into `Application.driver_arguments` from `runner.driver_command` and `runner.build_command` so `MonetioApplication.driver_arguments` never has to map a container path back to the host.

**Test:** Add a test in `src/tests/ufs_chem_assay/applications/test_monetio.py` that calls `build_command` with `Runtime.DOCKER`, `MonetioApplication()`, and a container-scoped `PurePosixPath("/work/ufs-chem-assay-output/01JB/01JB.yaml")` (plus `/assay-output/...`) while the generated YAML exists only under `tmp_path` on the host.

### [P1] CI integration workflow does not exercise `monetio` or `aeronet-suite.yaml`
- **Location**: `.github/workflows/integration.yaml:183-186`
- **Recommended Post Location**: Top-level PR conversation comment
- **Tag**: `[testing / ci-gating]` | Lines affected: 4 lines

**Testing: `monetio` and `aeronet-suite.yaml` have no end-to-end CI execution**

`ci/tests` only runs `src/tests/ufs_chem_assay` with mocked driver subprocesses, and `integration/simple-macci` only stages and runs `simple-maccity-suite.yaml` against `cece`. Consequently, the new `MonetioApplication` adapter, `aeronet-suite.yaml` S3 input staging, and real `monetio load aeronet` execution are unverified in CI.

**Suggested fix:** Add an integration job (or matrix entry) in `.github/workflows/integration.yaml` that installs `monetio` natively and runs `aeronet-suite.yaml` (or document the follow-up issue if private S3 inputs block public PR runners).

**Test:** Trigger the integration workflow on a PR and verify `pytest src/tests/test_driver_combos.py --application=monetio --suite-config=aeronet-suite.yaml` completes with exit code 0.

### [P1] Committed `benchmarks.csv` records `baseline_state=local` from a dirty commit
- **Location**: `src/tests/config/monetio/aeronet/benchmarks.csv:2-3`
- **Recommended Post Location**: Inline on `src/tests/config/monetio/aeronet/benchmarks.csv` (lines `2-3`)
- **Tag**: `[testing / provenance]` | Lines affected: 2 lines

**Provenance: `benchmarks.csv` was recorded from a `-dirty` checkout with `baseline_state=local`**

Both rows in `src/tests/config/monetio/aeronet/benchmarks.csv` record `harness_commit=7037213d8c25f62e34a105f884571ab3badb3707-dirty` and `baseline_state=local` rather than published S3 baselines. Committed benchmark histories are meant to anchor reproducible runs against clean commits and published baselines.

**Suggested fix:** Re-record `src/tests/config/monetio/aeronet/benchmarks.csv` from a clean commit against the published S3 baselines (`01M78KZ51F3PJC51N5GQCSYTTA` and `01M78KZ51F3PJC51N5GQCSYTTB`), or remove `benchmarks.csv` from the PR until baselines are published.

**Test:** Verify `harness_commit` in `src/tests/config/monetio/aeronet/benchmarks.csv` has no `-dirty` suffix and `baseline_state` is `published` (or `git ls-files src/tests/config/monetio/aeronet/benchmarks.csv` is empty if deferred).

### [P2] Non-numeric comparison treats `NaT != NaT` as mismatched
- **Location**: `src/comparison.py:257-266`
- **Recommended Post Location**: Inline on `src/comparison.py` (lines `257-266`)
- **Tag**: `[defect / edge-case]` | Lines affected: 10 lines

**Correctness: `real_arr != base_arr` flags identical `NaT` timestamps as mismatches**

In `_compare_variable`, `datetime64` coordinate arrays (such as `time`) are not `np.number` dtypes and therefore enter the new non-numeric branch. Because NumPy evaluates `NaT != NaT` as `True`, any identical missing timestamp in a `datetime64` array will be reported as a data mismatch.

**Suggested fix:** Mask out positions where both arrays are `NaT` when both dtypes are `np.datetime64` before counting mismatches.

**Test:** Add a case to `src/tests/ufs_chem_assay/test_comparison.py` comparing two `datetime64[ns]` `DataArray`s containing `np.datetime64("NaT")` and assert `data_match is True` and `n_mismatched == 0`.

````suggestion
    if not (
        np.issubdtype(realization.dtype, np.number)
        and np.issubdtype(baseline.dtype, np.number)
    ):
        real_arr = np.asarray(realization.values)
        base_arr = np.asarray(baseline.values)
        both_na = (
            np.isnat(real_arr) & np.isnat(base_arr)
            if np.issubdtype(real_arr.dtype, np.datetime64)
            and np.issubdtype(base_arr.dtype, np.datetime64)
            else False
        )
        mismatches = int(np.sum((real_arr != base_arr) & ~both_na))
````

### [P2] Ponytail: Duplicate `_module_block` and `_source` across `monetio/cli.py` and `cece/cli.py`
- **Location**: `src/applications/monetio/cli.py:28-59`
- **Recommended Post Location**: Inline on `src/applications/monetio/cli.py` (lines `28-59`)
- **Tag**: `[deduplicate / ponytail]` | Lines removable: ~28 lines

**Simplification: `_module_block` and `_source` duplicate `src/applications/cece/cli.py`**

`_module_block` (lines 28–36) is identical to `src/applications/cece/cli.py:38-50`, and `_source` (lines 39–59) duplicates `src/applications/cece/cli.py:53-79` except for `--recurse-submodules` and the `extern/helm/libs` guard. Keeping two copies of git clone/fetch/checkout and Lmod module setup invites drift between adapters.

**Suggested fix:** Extract shared `module_block(checkout, section)` and `source_lines(checkout, section, *, recurse_submodules=False)` helpers into `src/cli/stages.py` and call them from both `cece/cli.py` and `monetio/cli.py`.

**Test:** Run `uv run pytest src/tests/ufs_chem_assay/applications/test_monetio.py src/tests/ufs_chem_assay/test_stages.py` to confirm rendered `SOURCE` and `BUILD` scripts are unchanged.

### [P2] Ponytail: Hardcoded `"monetio/monetio-dev"` default duplicates `MonetioSettings.docker_image`
- **Location**: `src/applications/monetio/cli.py:66`
- **Recommended Post Location**: Inline on `src/applications/monetio/cli.py` (line `66`)
- **Tag**: `[deduplicate / ponytail]` | Lines removable: ~1 line

**Simplification: `docker_image` fallback duplicates `MonetioSettings.docker_image`**

`_build` falls back to the hardcoded literal `"monetio/monetio-dev"` when `section.docker_image` is `None`, duplicating the default already declared on `MonetioSettings.docker_image` in `src/applications/monetio/settings.py:18-21`. If the default image changes or `MONETIO_DOCKER_IMAGE` is configured in `.env`, `Stage.BUILD` and `runner.docker_prefix` can diverge.

**Suggested fix:** Read the default from `MonetioSettings().docker_image` instead of repeating the string literal.

**Test:** Run `uv run pytest src/tests/ufs_chem_assay/applications/test_monetio.py -k stage_lines` with `MONETIO_DOCKER_IMAGE` set and unset.

````suggestion
        image = section.docker_image or MonetioSettings().docker_image
````

### [P2] Ponytail: Redundant `output_variable_names()` wrapper re-instantiates `MonetioAssertionSpec`
- **Location**: `src/applications/monetio/assertions.py:53-55`
- **Recommended Post Location**: Inline on `src/applications/monetio/assertions.py` (lines `53-55`)
- **Tag**: `[delete / ponytail]` | Lines removable: ~4 lines

**Simplification: `assertions.output_variable_names()` wraps `_ASSERTION_SPEC.output_variable_names()` unnecessarily**

`MonetioApplication` already instantiates `_ASSERTION_SPEC = assertions.MonetioAssertionSpec()` at module scope (`src/applications/monetio/application.py:57`), yet `MonetioApplication.output_variable_names` calls `assertions.output_variable_names()`, which constructs a fresh `MonetioAssertionSpec()` on every invocation.

**Suggested fix:** Call `_ASSERTION_SPEC.output_variable_names(_config(config))` directly in `src/applications/monetio/application.py:97` and delete `assertions.output_variable_names`.

**Test:** Run `uv run pytest src/tests/ufs_chem_assay/applications/test_monetio.py -k assertions`.

### [P2] Ponytail: Duplicated `unknown` application validation in `selected_applications`
- **Location**: `src/cli/selectors.py:56-69`
- **Recommended Post Location**: Inline on `src/cli/selectors.py` (lines `56-69`)
- **Tag**: `[deduplicate / ponytail]` | Lines removable: ~6 lines

**Simplification: `unknown` application check is duplicated across both branches**

The `unknown = sorted(...)` check and `ValueError` raise appear identically in both the `"all" in tokens` branch and the normal branch. Validating `unknown = sorted((set(tokens) - {"all"}) - set(config.applications))` once before the `if "all" in tokens:` branch removes 6 lines and keeps one error path.

**Suggested fix:** Consolidate the `unknown` check before checking `"all" in tokens`.

**Test:** Run `uv run pytest src/tests/ufs_chem_assay/test_cli.py -k application`.

````suggestion
    unknown = sorted((set(tokens) - {"all"}) - set(config.applications))
    if unknown:
        raise ValueError(
            f"--application {unknown} not in the run config's applications "
            f"({config.application_names})"
        )
    if "all" in tokens:
        return [get_application(name) for name in config.application_names]
````

### [P3] Stale `README.md` prose claims CECE is the default in every run config
- **Location**: `README.md:14-15`
- **Recommended Post Location**: Inline on `README.md` (lines `14-15`)
- **Tag**: `[docs / accuracy]` | Lines affected: 2 lines

**Docs: `README.md` still states `cece` is the default in every suite and run config**

This PR removed `DEFAULT_APPLICATION` from `registry.py`/`selection.py` and made `--application` required on `ufs-chem-assay run`, so `cece` is no longer defaulted in run configs or CLI suite selection.

**Suggested fix:** Clarify that `cece` is only the default `application:` field in `SuiteConfig` YAML files, while CLI commands require `--application` (or `--suite-config`).

**Test:** Verify `uv run pytest src/tests/ufs_chem_assay/test_user_docs.py` passes.

````suggestion
checkout, data directory — lives in an application adapter; CECE (`application: cece`,
the default `SuiteConfig` application) and MONETIO (`application: monetio`)
````

### [P3] `aeronet.yaml` header comment mentions `-v` flags not emitted by `driver_arguments`
- **Location**: `src/tests/config/monetio/aeronet/aeronet.yaml:3`
- **Recommended Post Location**: Inline on `src/tests/config/monetio/aeronet/aeronet.yaml` (line `3`)
- **Tag**: `[docs / accuracy]` | Lines affected: 1 line

**Docs: Header comment lists `-v ...` flag that `MonetioApplication.driver_arguments` does not pass**

`MonetioApplication.driver_arguments` emits `monetio load <source> -f ... -o ... -k ...` and uses `variables:` only for harness output assertions, not `-v` CLI flags.

**Suggested fix:** Remove `-v ...` from the header comment in `src/tests/config/monetio/aeronet/aeronet.yaml`.

**Test:** Compare `src/tests/config/monetio/aeronet/aeronet.yaml:3` against `MonetioApplication.driver_arguments` in `src/applications/monetio/application.py:99-107`.

````suggestion
# into `monetio load aeronet -f ... -o ... -k ...`.
````

## 6. Machine-Applicable Unified Diff Patch
```diff
--- a/src/applications/base.py
+++ b/src/applications/base.py
@@ -237,11 +237,13 @@ class Application(ABC):
         the same convention read back from a published baseline directory."""
         return combo_dir / f"{combo_id}.yaml"
 
-    def driver_arguments(self, driver_yaml: PurePosixPath) -> list[str]:
+    def driver_arguments(
+        self, driver_yaml: PurePosixPath, *, host_yaml: Path | None = None
+    ) -> list[str]:
         """The CLI arguments following `driver_path` for one combination.
         The default passes the generated configuration path as a single
         positional argument; an application whose CLI takes subcommands
-        or flags overrides it."""
+        or flags overrides it (reading `host_yaml` when provided)."""
         return [str(driver_yaml)]
 
     def config_fingerprint(self, path: Path) -> str:
--- a/src/applications/monetio/application.py
+++ b/src/applications/monetio/application.py
@@ -3,7 +3,6 @@
 
 from __future__ import annotations
 
-import os
 from enum import StrEnum
 from pathlib import Path, PurePosixPath
 
@@ -46,12 +45,10 @@ def _kwarg_flags(key: str, value: KwargValue) -> list[str]:
     return ["-k", f"{key}={_format_scalar(value)}"]
 
 
-def _resolve_yaml_path(driver_yaml: PurePosixPath) -> Path:
-    path = Path(driver_yaml)
-    if path.is_file():
-        return path
-    root = os.environ.get(f"{MonetioSettings.env_prefix}ROOT_DIR")
-    return Path(root) / path if root is not None else path
+def _resolve_yaml_path(
+    driver_yaml: PurePosixPath, host_yaml: Path | None = None
+) -> Path:
+    return host_yaml if host_yaml is not None else Path(driver_yaml)
 
 
 _ASSERTION_SPEC = assertions.MonetioAssertionSpec()
@@ -94,10 +91,12 @@ class MonetioApplication(Application):
         return assertions.expected_output_filenames(_config(config))
 
     def output_variable_names(self, config: DriverConfig) -> list[str]:
-        return assertions.output_variable_names(_config(config))
+        return _ASSERTION_SPEC.output_variable_names(_config(config))
 
-    def driver_arguments(self, driver_yaml: PurePosixPath) -> list[str]:
-        config = MonetioConfig.from_yaml(_resolve_yaml_path(driver_yaml))
+    def driver_arguments(
+        self, driver_yaml: PurePosixPath, *, host_yaml: Path | None = None
+    ) -> list[str]:
+        config = MonetioConfig.from_yaml(_resolve_yaml_path(driver_yaml, host_yaml))
         args = ["load", config.source]
         for file_path in config.files:
             args += ["-f", file_path]
--- a/src/applications/monetio/assertions.py
+++ b/src/applications/monetio/assertions.py
@@ -48,8 +48,3 @@ def expected_output_filenames(config: MonetioConfig) -> set[str]:
     """The basename of `config.output` inside the combo output directory."""
     return {PurePosixPath(config.output).name}
 
-
-def output_variable_names(config: MonetioConfig) -> list[str]:
-    """Expected observation output variables configured to check against `standard_dimensions`."""
-    return MonetioAssertionSpec().output_variable_names(config)
-
--- a/src/comparison.py
+++ b/src/comparison.py
@@ -260,7 +260,13 @@ def _compare_variable(
     ):
         real_arr = np.asarray(realization.values)
         base_arr = np.asarray(baseline.values)
-        mismatches = int(np.sum(real_arr != base_arr))
+        both_na = (
+            np.isnat(real_arr) & np.isnat(base_arr)
+            if np.issubdtype(real_arr.dtype, np.datetime64)
+            and np.issubdtype(base_arr.dtype, np.datetime64)
+            else False
+        )
+        mismatches = int(np.sum((real_arr != base_arr) & ~both_na))
         equal = mismatches == 0
         if not equal:
             detail_parts.append(f"data differs ({mismatches} element(s))")
--- a/src/cli/selectors.py
+++ b/src/cli/selectors.py
@@ -53,19 +53,14 @@ def selected_applications(
         raise ValueError(
             "--application requires at least one application name or 'all'"
         )
-    if "all" in tokens:
-        unknown = sorted(set(tokens) - {"all"} - set(config.applications))
-        if unknown:
-            raise ValueError(
-                f"--application {unknown} not in the run config's applications "
-                f"({config.application_names})"
-            )
-        return [get_application(name) for name in config.application_names]
-    unknown = sorted(set(tokens) - set(config.applications))
+    unknown = sorted((set(tokens) - {"all"}) - set(config.applications))
     if unknown:
         raise ValueError(
             f"--application {unknown} not in the run config's applications "
             f"({config.application_names})"
         )
+    if "all" in tokens:
+        return [get_application(name) for name in config.application_names]
     return [
         get_application(name) for name in config.application_names if name in tokens
     ]
```
