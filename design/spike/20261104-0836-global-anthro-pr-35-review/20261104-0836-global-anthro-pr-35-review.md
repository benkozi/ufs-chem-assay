# Pull Request Review: [benkozi/ufs-chem-assay#35] - feat: global anthropogenic CECE use case integration

## 1. Executive Summary & Verdict
- **Verdict**: COMMENT (or APPROVE WITH SUGGESTIONS)
- **Target Repo & PR**: [benkozi/ufs-chem-assay#35](https://github.com/benkozi/ufs-chem-assay/pull/35)
- **Pinned Revision**: `7edd5bd..c80954c` (55 changed files across 5 logical groups)
- **Lines Removable (Ponytail)**: ~3 lines
- **Findings Summary**: 0 Blockers (P0), 2 Majors (P1), 1 Over-Engineering / Cleanup (P2), 1 Nit (P3)
- **Change Groups Reviewed**:
  1. Design & Architecture Documentation (`README.md`, `design/design.md`, `design/feat/20261019-1447-global-anthro-use-case/*`, `docs/*runbook.md`)
  2. Configuration & Suite Specifications (`config/ursa-gcafs-anthro.yaml`, `config/ursa.yaml`, `config/local.yaml`, `src/tests/config/cece/gcafs-anthro/*`)
  3. Core Engine & Data Pipeline (`src/applications/cece/config.py`, `src/models/suite_config.py`, `src/outputs.py`, `src/benchmarks.py`, `src/staging.py`, `src/plotting.py`, `src/runner.py`, `src/analysis.py`, `src/settings.py`, `src/platforms.py`, `src/baselines/*`)
  4. CLI & Runtime Orchestration (`src/cli/main.py`, `src/cli/run_config.py`, `src/cli/stages.py`)
  5. Test Harness & Fixtures (`src/tests/conftest.py`, `src/tests/test_driver_combos.py`, `src/tests/applications/cece/*`, `src/tests/ufs_chem_assay/*`)
- **Strengths / What Went Well**:
  - Exemplary architectural hygiene: Strict typing with Pydantic models (`StrictModel`, `ConfigDict(extra="forbid", frozen=True)`) guarantees schema validation across complex atmospheric emission streams and Slurm accounting logs.
  - Robust fail-fast staging & atomic baseline lifecycle: Clean integration of source-less input linking (`url: null`), SHA-256 digest recording, and non-destructive output store transfers.
  - Deep test coverage: The 55 changed files include comprehensive pytest fixtures with 800 passing tests locally covering dry-run orchestration, symlink resolution, NetCDF statistics, plotting transforms, and Slurm accounting parsing.
- **High-Level Takeaway**:
  This pull request delivers full end-to-end support for CECE's complex global anthropogenic emissions use case (`gcafs-anthro`: 1440x720x1 global grid, 25 hourly steps, 32 streams across 7 inputs, 10 species) and introduces production-grade persistent benchmarking (`benchmarks.py`) and resumable run output management (`outputs.py`). The design is thoroughly documented in architectural spikes and runbooks.
  
  The review identified two P1 robustness/safety items: an unsafe staging directory wipe in `download_output` that could affect concurrent downloads into a shared destination, and potential false-positive ambiguity errors in `_find_source` when search paths contain overlapping or symlinked directories. Additionally, Section 3 clarifies that while GitHub Actions CI is all-green, the heavy `gcafs-anthro` driver run is intentionally gated to Ursa HPC and only dry-run tested in CI.

## 2. Existing PR Review Discussions, Validity Assessment & Status

| Author | File / Context | Summary of Prior Comment | Validity Verdict | Technical Validity Assessment & Status |
| :--- | :--- | :--- | :--- | :--- |
| *None* | N/A | No existing inline or top-level comments submitted | N/A | Clean discussion thread; first review pass. |

## 3. GitHub Actions CI Checks & Test Coverage/Gating Analysis

| Workflow / Check Name | Result | Elapsed Time | Analysis & Gating Status |
| :--- | :--- | :--- | :--- |
| `ci/actionlint (pull_request)` | `[PASSED]` | 7s | Actionlint passed with custom flags for App token v3 compatibility. |
| `codeql/analyze (actions)` | `[PASSED]` | 36s | No workflow AST security vulnerabilities detected. |
| `codeql/analyze (python)` | `[PASSED]` | 1m 9s | Semantic code analysis passed with zero security alerts. |
| `ci/build (pull_request)` | `[PASSED]` | 1m 11s | CI Docker image built and warmed in GitHub Actions cache. |
| `ci/pre-commit (pull_request)` | `[PASSED]` | 1m 28s | Ruff, codespell, vulture, yamllint, and conventional commits passed. |
| `ci/release-preview` | `[PASSED]` | 1m 9s | Python-semantic-release dry run preview evaluated cleanly. |
| `ci/tests (pull_request)` | `[PASSED]` | 3m 37s | Harness test suite passed with 100% test coverage gate (`fail_under = 100`). |
| `integration/simple-maccity` | `[PASSED]` | 7m 35s | End-to-end integration test executed successfully against CECE develop container. |

- **False Confidence / Gating Explanation**:
  All 11 checks in GitHub Actions passed, but reviewers should understand the intentional test gating boundary:
  1. `integration/simple-maccity` only executes the small `simple-maccity-suite.yaml` (4x5 degree regional grid, 1 input file, ~4-minute driver run).
  2. The new `gcafs-anthro-suite.yaml` (0.25° global grid, 1440x720x1, 7 multi-gigabyte datasets, whole-node memory requirement) is **NOT** executed in GitHub Actions CI because the large input data files reside on Ursa HPC scratch storage and have no public S3 mirror.
  3. In CI, `src/tests/ufs_chem_assay/applications/cece/test_gcafs_anthro.py` validates the configuration schema and tests `--dry-run` execution only. Full physics execution, Slurm job submission, and NetCDF output generation are verified on Ursa via `docs/gcafs-anthro-runbook.md`.

## 4. Prioritized Action Items
- [ ] **[P1]** Scope staging cleanup in `download_output` to specific run directory — `src/outputs.py:182`
- [ ] **[P1]** Canonicalize resolved paths in `_find_source` to prevent false ambiguity — `src/staging.py:194-198`
- [ ] **[P2]** Ponytail: Update outdated placeholder ULID comment in gcafs-anthro suite — `src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml:71-75`
- [ ] **[P3]** Clarify `download_output` docstring regarding directory vs S3 store target handling — `src/outputs.py:138-142`

## 5. Detailed Findings & Machine-Applicable Suggestions

### [P1] Scope staging cleanup in `download_output` to specific run directory
- **Location**: `src/outputs.py:182`
- **Recommended Post Location**: Inline on `src/outputs.py` (lines 181–183)
- **Tag**: `[defect / safety]` | Lines affected: 2 lines

**Resource Management: Staging directory rmtree deletes shared staging root across concurrent downloads**

In `download_output`, the `finally` block unconditionally calls `shutil.rmtree(dest / _STAGING_DIRNAME, ignore_errors=True)`. If multiple downloads run concurrently into the same destination directory with different run ULIDs, one download finishing or failing will remove the entire shared `.staging/` directory, corrupting or aborting active transfers of concurrent runs.

Cleaning up only the run-specific staging directory (`staging = dest / _STAGING_DIRNAME / run_id`) isolates cleanup to the active transfer and prevents cross-process data races.

**Suggested fix:** Remove `staging` in the `finally` block instead of the whole `dest / _STAGING_DIRNAME` directory, and attempt non-blocking `rmdir()` on the parent.

**Test:** Run concurrent downloads of two distinct run ULIDs into the same `dest` directory with `_fake_sync` and verify neither download fails with missing staging paths.

````suggestion
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            try:
                (dest / _STAGING_DIRNAME).rmdir()
            except OSError:
                pass
````

---

### [P1] Canonicalize resolved paths in `_find_source` to prevent false ambiguity
- **Location**: `src/staging.py:194-198`
- **Recommended Post Location**: Inline on `src/staging.py` (lines 194–198)
- **Tag**: `[defect / robustness]` | Lines affected: 5 lines

**Input Resolution: Un-deduplicated search path results report false ambiguity for duplicate or nested directories**

`_find_source` iterates over `search_path` and appends every matching `Path` to `found`. If `ASSAY_INPUT_SEARCH_PATH` contains redundant directories, nested paths (e.g. `/scratch/data` and `/scratch/data/inputs`), or symlinked directory trees, `found` contains multiple paths that resolve to the exact same physical file on disk. `_link_source_less` checks `len(found) > 1` and aborts with an "ambiguous source under the input search path" error even when all matches point to the same file.

Deduplicating candidates by `p.resolve()` ensures that distinct paths pointing to the same file are treated as a single match.

**Suggested fix:** Deduplicate discovered paths by resolved canonical path using a dictionary or set before returning.

**Test:** In `test_staging.py`, invoke `_link_source_less` with `search_path=[tmp_dir, tmp_dir / "nested"]` where `nested` symlinks to `tmp_dir` or is a subdirectory containing the file, and assert it links successfully without raising ambiguity errors.

````suggestion
    found: dict[Path, Path] = {}
    for directory in search_path:
        if directory.is_dir():
            for p in sorted(directory.rglob(name)):
                if p.is_file():
                    found.setdefault(p.resolve(), p)
    return list(found.values())
````

---

### [P2] Ponytail: Update outdated placeholder ULID comment in gcafs-anthro suite
- **Location**: `src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml:71-75`
- **Recommended Post Location**: Inline on `src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml` (lines 71–75)
- **Tag**: `[delete / clarify]` | Lines removable: ~3 lines

**Simplification: Outdated template comment explains all-zero ULID placeholder on a suite that already pins a published baseline**

Lines 71–75 explain that `"The all-zero ULID means 'not published yet' ... and publish-baselines replaces it (keeping the quotes, which YAML needs for 26 zeros)"`. However, line 78 already pins the published baseline `01M5JV8MBW0XE9TPE0BDCGSFJZ` without quotes. The comment is stale template boilerplate that no longer matches the file contents.

Replacing the comment with a concise 2-line explanation of the single sweep-less combination and published baseline eliminates confusion and saves lines.

**Suggested fix:** Replace the placeholder explanation with a brief comment documenting the published baseline and tolerance.

**Test:** Run `uv run pytest src/tests/ufs_chem_assay/applications/cece/test_gcafs_anthro.py` to verify YAML suite validation passes.

````suggestion
# One entry: empty selector matches the single combination of the sweep-less suite.
# Published baseline on Ursa; rtol set to 1.0e-15 (a few float64 ULPs).
````

---

### [P3] Clarify `download_output` docstring regarding directory vs S3 store target handling
- **Location**: `src/outputs.py:138-142`
- **Recommended Post Location**: Inline on `src/outputs.py` (lines 138–142)
- **Tag**: `[docs / nit]` | Lines affected: 5 lines

**Documentation: Docstring states target is synced into never removed but omits S3 atomic staging move mechanism**

The docstring notes that `<store>/<run_id>/` is downloaded into `<dest>/<run_id>/` and that "an existing target is synced into, never removed." In the implementation, S3 downloads sync into an isolated `.staging/<run_id>` directory and atomically replace existing files using `os.replace`, while directory stores invoke `shutil.copytree(dirs_exist_ok=True)`. Clarifying that S3 replaces files individually via staging provides clearer technical guidance for callers inspecting partial runs.

**Suggested fix:** Update docstring summary to mention atomic file replacement via staging for S3 transfers.

**Test:** Run `pytest src/tests/ufs_chem_assay/test_outputs.py` to confirm docstring change passes tests.

## 6. Machine-Applicable Unified Diff Patch

```diff
--- a/src/outputs.py
+++ b/src/outputs.py
@@ -179,7 +179,12 @@ def download_output(
                     final.parent.mkdir(parents=True, exist_ok=True)
                     os.replace(path, final)
         finally:
-            shutil.rmtree(dest / _STAGING_DIRNAME, ignore_errors=True)
+            if staging.exists():
+                shutil.rmtree(staging, ignore_errors=True)
+            try:
+                (dest / _STAGING_DIRNAME).rmdir()
+            except OSError:
+                pass
     record_path = target / UPLOAD_RECORD
     if record_path.is_file():
         with open(record_path) as f:
--- a/src/staging.py
+++ b/src/staging.py
@@ -191,11 +191,13 @@ def _fetch(entry: InputFile, target: Path, staging_root: Path) -> StagedInput:
 def _find_source(name: str, search_path: Sequence[Path]) -> list[Path]:
     """Every file called `name` under the search-path directories (recursive,
     sorted): the one link target, or the ambiguity to report."""
-    found: list[Path] = []
+    found: dict[Path, Path] = {}
     for directory in search_path:
         if directory.is_dir():
-            found += sorted(p for p in directory.rglob(name) if p.is_file())
-    return found
+            for p in sorted(directory.rglob(name)):
+                if p.is_file():
+                    found.setdefault(p.resolve(), p)
+    return list(found.values())
 
 
 def _link_source_less(
--- a/src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml
+++ b/src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml
@@ -68,11 +68,8 @@ plotting:
   scale: log  # the default: SO2/OC/BC span many decades; zeros (ocean) grey
   decades: 5  # the floor five decades below the suite-wide maximum
   bias_scale: linear  # the default
-# One entry: the empty selector matches the single combination of a sweep-less
-# suite. The all-zero ULID means "not published yet": the harness never looks
-# for it, the comparison test skips, and publish-baselines replaces it (keeping
-# the quotes, which YAML needs for 26 zeros). rtol as for maccity: a few
-# float64 ULPs at any magnitude.
+# One entry: empty selector matches the single combination of the sweep-less suite.
+# Published baseline on Ursa; rtol set to 1.0e-15 (a few float64 ULPs).
 baseline_comparisons:
   - sweep_selector: {}
     ulid: 01M5JV8MBW0XE9TPE0BDCGSFJZ
```

