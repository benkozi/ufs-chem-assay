# Pull Request Review: [benkozi/ufs-chem-assay#40] - feat: async HPC runs with multiple suites

## 1. Executive Summary & Verdict
- **Verdict**: APPROVE
- **Target Repo & PR**: [benkozi/ufs-chem-assay#40](https://github.com/benkozi/ufs-chem-assay/pull/40)
- **Pinned Revision**: `bef691a..15d6cb4` (42 changed files across 5 logical groups)
- **Lines Removable (Ponytail)**: ~25 lines (short-circuiting redundant benchmark rewrites, defensive `mkdir` encapsulation, and $O(1)$ set conversions)
- **Findings Summary**: 0 Blockers (P0), 1 Major (P1), 4 Moderate / Over-Engineering / Robustness (P2), 2 Minor / Nits (P3)
- **Change Groups Reviewed**:
  1. *Core CLI & Driver Orchestration*: `src/cli/main.py`, `src/cli/run_config.py`, `src/cli/run_report.py`, `src/cli/stages.py`, `src/cli/watch.py`, `src/runner.py`, `src/staging.py`, `src/settings.py`
  2. *Asynchronous HPC / Slurm & Concurrency Architecture*: `src/fslock.py`, `src/jobs.py`, `src/applications/base.py`, `src/models/conventions.py`
  3. *Benchmark & Baseline Management*: `src/baselines/suite.py`, `src/benchmarks.py`, `src/tests/config/cece/gcafs-anthro/benchmarks.csv`, `src/tests/config/cece/maccity/benchmarks.csv`, `src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml`
  4. *Configuration & Runbooks*: `config/local.yaml`, `config/ursa-gcafs-anthro.yaml`, `config/ursa.yaml`, `docs/gcafs-anthro-runbook.md`, `docs/ursa-runbook.md`, `README.md`, `design/design.md`, design spike and feature docs
  5. *Testing Framework & Test Suites*: `src/tests/conftest.py`, `src/tests/ufs_chem_assay/report_roots.py`, `src/tests/ufs_chem_assay/run_configs.py`, `src/tests/ufs_chem_assay/test_*.py`
- **Strengths / What Went Well**:
  - **Decoupled Asynchronous Lifecycle**: Replacing blocking `sbatch --wait` with a clean two-phase submission (`--submit-only`) and resume (`watch` / `--resume`) orchestrated via atomic file-backed ledgers (`jobs.yaml`) enables full-cluster concurrency without long-running interactive shells or risk of dropped SSH connections.
  - **Advisory File Concurrency Locking**: Incorporating `fcntl.flock` (`src/fslock.py`) into committed repository artifacts (`benchmarks.csv` and suite manifests) prevents race conditions and corrupted rows during concurrent session completions on login nodes.
  - **Exemplary Integration Testing Without Cluster Dependencies**: Synthetic Slurm command stubs (`sbatch`, `squeue`, `sacct`, `scancel`) injected via PATH simulate cluster edge cases, submission failures, queue polling, and deadlines deterministically in local pytest subprocesses.
  - **Zero-Drift Documentation Guard**: `src/tests/ufs_chem_assay/test_user_docs.py` enforces that all runbooks (`docs/ursa-runbook.md`, `docs/gcafs-anthro-runbook.md`) and `README.md` strictly match real CLI commands and template configurations.
- **High-Level Takeaway**:
  This pull request is an outstanding architectural enhancement that elevates `ufs-chem-assay` into a production-grade asynchronous test harness for NOAA RDHPCS HPC environments (Ursa). By decoupling driver job submission from queue polling and post-processing evaluation, users can submit dozens of parameter combinations simultaneously, disconnect, and monitor progress via `ufs-chem-assay watch` or generate post-mortem rollups with `ufs-chem-assay report`. All historical CodeQL security alerts raised on the PR have been systematically addressed and resolved. The findings below address edge-case resilience against transient Slurm communication hiccups, defensive file handling, and Ponytail simplifications.

---

## 2. Existing PR Review Discussions, Validity Assessment & Status

| Author | File / Context | Summary of Prior Comment | Validity Verdict | Technical Validity Assessment & Status |
| :--- | :--- | :--- | :--- | :--- |
| `@github-advanced-security[bot]` | `src/fslock.py:38` | Overly permissive file permissions in `os.open` (mode `0o644`). | `[VALID - RESOLVED]` | Valid security flag regarding hardcoded file modes. Author resolved in commit `89d6f76` by removing `os.open` explicit mode and using `open(path, "a")`, letting process umask govern permissions consistently with the rest of the harness. |
| `@github-advanced-security[bot]` | `src/tests/ufs_chem_assay/test_fslock.py:48` | Unreachable statement detected after an unconditional `raise`. | `[VALID - RESOLVED]` | Valid static analysis detection. Author resolved in commit `89d6f76` by encapsulating the exception raise inside a nested `hold_and_fail()` helper invoked via `pytest.raises`, ensuring subsequent lock release assertions execute properly. |
| `@github-advanced-security[bot]` | `src/tests/ufs_chem_assay/test_jobs.py:13` | Module imported with both `import jobs` and `from jobs import ...`. | `[VALID - RESOLVED]` | Valid linting alert. Author resolved in commit `89d6f76` by importing `QUEUED_UNKNOWN` directly from `jobs` and removing the redundant bare module import. |
| `@github-advanced-security[bot]` | `src/tests/ufs_chem_assay/report_roots.py:59` | Unused global variables `B`, `C`, and `P` in shared fixture helper. | `[VALID - RESOLVED]` | Valid dead-variable alert. Author resolved in commit `89d6f76` by relocating combination alias definitions directly into `test_run_report.py` where they are consumed. |

---

## 3. GitHub Actions CI Checks & Test Coverage/Gating Analysis

| Workflow / Check Name | Result | Elapsed Time | Analysis & Gating Status |
| :--- | :--- | :--- | :--- |
| `ci/tests (pull_request)` | `[PASSED]` | 3m 18s | Executed 902 tests across unit and harness suites with pytest and coverage enforcement. |
| `integration/simple-maccity` | `[PASSED]` | 1m 16s | Full end-to-end integration run of CECE `simple-maccity` suite inside container environment, followed by `report --output-root` verification (3/3 clean). |
| `ci/pre-commit (pull_request)` | `[PASSED]` | 51s | Ruff linting, formatting, YAML schema checks, and trailing whitespace hygiene passed. |
| `codeql/analyze (python)` | `[PASSED]` | 1m 02s | CodeQL security scanning passed; all 4 previous alert threads marked resolved. |
| `codeql/analyze (actions)` | `[PASSED]` | 47s | Security scanning for GitHub Actions workflow definitions passed. |
| `ci/actionlint (pull_request)` | `[PASSED]` | 8s | Workflow syntax validation passed across all `.github/workflows/*.yaml`. |
| `ci/build (pull_request)` | `[PASSED]` | 20s | Python wheel and sdist package build passed. |
| `dependency-review/dependency-review` | `[PASSED]` | 5s | Audited dependency updates against vulnerability advisories. |
| `ci/release-preview` | `[PASSED]` | 1m 33s | Semantic release preview generated next release version. |
| `ci/release-psr (pull_request)` | `[SKIPPED]` | 0s | Intentionally skipped on PR branches (executes only on merge to `main`). |

- **False Confidence / Gating Explanation**:
  All 11 active CI checks passed cleanly. However, GitHub Actions runners do not host a live Slurm scheduler daemon (`slurmd`/`slurmctld`). Consequently, live cluster interactions (such as Slurm controller socket timeouts, SlurmDBD accounting synchronization delays, or multi-node filesystem latency) cannot be tested in GitHub Actions. The test suite compensates for this effectively by employing high-fidelity PATH stubs (`sbatch`, `squeue`, `sacct`, `scancel`) in `src/tests/ufs_chem_assay/test_session_jobs.py`. Users should note that real-cluster testing on NOAA Ursa is still necessary to validate native Slurm behavior under actual queue load.

---

## 4. Prioritized Action Items
- [ ] **[P1]** Prevent false-positive queue drain on transient `squeue` communication errors — `src/jobs.py:220-227`
- [ ] **[P2]** Ponytail: Short-circuit `append_benchmarks` when all candidate rows are already recorded — `src/benchmarks.py:408-418`
- [ ] **[P2]** Defensive path creation: Ensure parent directories exist within `locked()` context manager — `src/fslock.py:37-41`
- [ ] **[P2]** Guard against corrupted or 0-byte `combos.csv` in `_combinations` report parser — `src/cli/run_report.py:240-248`
- [ ] **[P2]** Validate `--interval` CLI flag to reject non-positive polling intervals — `src/cli/watch.py:253-256`
- [ ] **[P3]** Optimize job ID membership testing in `queue_states` to $O(1)$ set lookup — `src/jobs.py:202-219`
- [ ] **[P3]** Tighten parameter ID boundary matching in log error message extractor — `src/cli/run_report.py:321-330`

---

## 5. Detailed Findings & Machine-Applicable Suggestions

### [P1] Prevent false-positive queue drain on transient `squeue` communication errors
- **Location**: `src/jobs.py:220-227`
- **Recommended Post Location**: Inline on `src/jobs.py` (lines 220–227)
- **Tag**: `[defect / hpc]` | Lines affected: 8 lines

**Reliability: Treat transient `squeue` communication failures as `QUEUED_UNKNOWN` instead of an empty queue.**

When `squeue` fails with a nonzero return code due to an infrastructure or communication failure (such as `Unable to contact slurm controller (connect failure)` or RPC timeout), `completed.stdout` is empty and `states` evaluates to `{}`. Under the current logic, `completed.returncode != 0 and not states` logs a debug message and returns the empty dictionary. Callers (`wait_for_job` and `watch`) interpret an empty dictionary as "every job has left the queue," triggering premature evaluation via `06-resume-*` while the batch jobs are still actively executing on compute nodes.

Only specific error messages indicating that job IDs are no longer in the queue (e.g. `Invalid job id` or `slurm_load_jobs`) should be treated as a drained queue; any other unexpected controller failure should return `QUEUED_UNKNOWN` so the caller continues to wait.

**Suggested fix:** Check `completed.stderr` on nonzero exit; return `dict.fromkeys(ids, QUEUED_UNKNOWN)` unless the error indicates invalid/unknown job IDs.

**Test:** Add a unit test in `test_jobs.py` mocking `squeue` exit code 1 with `stderr="slurm_load_jobs error: Unable to contact slurm controller"`, asserting `queue_states` returns `{"123": QUEUED_UNKNOWN}`.

````suggestion
    if completed.returncode != 0 and not states:
        stderr = completed.stderr.strip()
        if "Invalid job id" not in stderr and "slurm_load_jobs" not in stderr:
            logger.warning(
                "squeue failed (%s: %s); treating %s job(s) as queued",
                completed.returncode,
                stderr,
                len(ids),
            )
            return dict.fromkeys(ids, QUEUED_UNKNOWN)
        logger.debug(
            "squeue exited %s for %s (every id gone): %s",
            completed.returncode,
            ids,
            stderr,
        )
    return states
````

---

### [P2] Ponytail: Short-circuit `append_benchmarks` when all candidate rows are already recorded
- **Location**: `src/benchmarks.py:404-418`
- **Recommended Post Location**: Inline on `src/benchmarks.py` (lines 404–418)
- **Tag**: `[delete / deduplicate]` | Lines removable: ~12 lines

**Simplification: Avoid rewriting and re-parsing benchmark CSV files when no new rows are appended.**

In `_append_benchmarks_locked`, if all incoming rows already exist in the benchmark file (`(row.run_id, row.combo_id) in keys`), `to_append` remains empty. However, the function still opens the file for writing, rewrites the original content, re-reads and validates the CSV through pandas, and touches the file timestamp.

Short-circuiting when `not to_append` avoids unnecessary disk I/O, prevents modifying file timestamps when no changes occurred, and eliminates redundant re-parsing.

**Suggested fix:** Return immediately with `AppendResult(appended=0, skipped=skipped)` when `to_append` is empty.

**Test:** Run `pytest src/tests/ufs_chem_assay/test_benchmarks.py` to confirm that appending duplicate rows returns `AppendResult(appended=0, skipped=N)` without modifying the target CSV file.

````suggestion
    if not to_append:
        logger.info(
            "%s: 0 row(s) appended, %s skipped (already recorded)",
            repo_csv,
            skipped,
        )
        return AppendResult(appended=0, skipped=skipped)
    text = original
    if text and not text.endswith("\n"):
        text += "\n"
````

---

### [P2] Defensive path creation: Ensure parent directories exist within `locked()` context manager
- **Location**: `src/fslock.py:37-41`
- **Recommended Post Location**: Inline on `src/fslock.py` (lines 37–41)
- **Tag**: `[architecture / robustness]` | Lines affected: 5 lines

**Robustness: Automatically create parent directories when acquiring an advisory file lock.**

`locked(path)` opens `path` in `"a"` mode with the expectation that absent files will be created automatically. However, if the target path resides in a nested directory that does not yet exist, `open(path, "a")` raises `FileNotFoundError`. Callers currently have to remember to invoke `path.parent.mkdir(parents=True, exist_ok=True)` prior to calling `with locked(path):`.

Moving directory creation inside `locked(path)` encapsulates filesystem path preparation and prevents unexpected `FileNotFoundError` exceptions across all current and future call sites.

**Suggested fix:** Call `path.parent.mkdir(parents=True, exist_ok=True)` inside `locked()` before calling `open()`.

**Test:** Add a unit test verifying `with locked(tmp_path / "deep" / "nested" / "file.lock"):` succeeds without prior manual directory creation.

````suggestion
    # Append mode: created when absent (the process umask decides the
    # permissions, as for every other file the harness writes), never
    # truncated, and nothing is written through this handle.
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
````

---

### [P2] Guard against corrupted or 0-byte `combos.csv` in `_combinations` report parser
- **Location**: `src/cli/run_report.py:240-248`
- **Recommended Post Location**: Inline on `src/cli/run_report.py` (lines 240–248)
- **Tag**: `[robustness / error-handling]` | Lines affected: 9 lines

**Error Handling: Catch `EmptyDataError` and `ParserError` when parsing combinations in `run_report`.**

`_combinations(root)` checks `path.is_file()` and immediately passes it to `pd.read_csv(path)`. If a prior run was interrupted while initializing `combos.csv`, or if the file was truncated to 0 bytes, `pd.read_csv` raises `pandas.errors.EmptyDataError: No columns to parse from file`, causing `ufs-chem-assay report` and `watch` to crash with an unhandled traceback.

Wrapping the read in a `try...except` block ensures that empty or malformed files degrade gracefully to an empty list, allowing `summarize_session` to report a clean `not-submitted` or `submission-incomplete` issue.

**Suggested fix:** Catch `(pd.errors.EmptyDataError, pd.errors.ParserError)` and return `[]`.

**Test:** In `test_run_report.py`, create an empty `combos.csv` file at the session root and assert `summarize_session` returns without raising `EmptyDataError`.

````suggestion
    path = root / CONVENTIONS.combos_csv
    if not path.is_file():
        return []
    try:
        frame = pd.read_csv(path, dtype=str).drop_duplicates("combo_id")
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return []
    return [
````

---

### [P2] Validate `--interval` CLI flag to reject non-positive polling intervals
- **Location**: `src/cli/watch.py:253-256`
- **Recommended Post Location**: Inline on `src/cli/watch.py` (lines 253–256)
- **Tag**: `[cli / validation]` | Lines affected: 4 lines

**CLI Validation: Reject non-positive polling intervals passed to `watch`.**

In `watch(args, ...)`, `args.interval` defaults to `config.slurm.poll_s` (or 30) if omitted. However, if a user explicitly passes `--interval 0` or `--interval -5`, `time.sleep(interval)` inside the watch loop will either trigger a busy-spin or raise an unhandled `ValueError: sleep length must be non-negative`.

Adding an explicit check ensures users receive a helpful CLI error message and a nonzero exit code.

**Suggested fix:** Validate `if interval <= 0:` and log a descriptive error message before entering the polling loop.

**Test:** Test `ufs-chem-assay watch --interval 0` in `test_watch.py` and verify it exits with code 1 and logs an error message.

````suggestion
    interval = args.interval
    if interval is None:
        interval = config.slurm.poll_s if config.slurm is not None else 30
    elif interval <= 0:
        logger.error("--interval must be greater than 0; got %s", interval)
        return 1
````

---

### [P3] Optimize job ID membership testing in `queue_states` to $O(1)$ set lookup
- **Location**: `src/jobs.py:202-219`
- **Recommended Post Location**: Inline on `src/jobs.py` (lines 202–219)
- **Tag**: `[performance / style]` | Lines affected: 18 lines

**Optimization: Convert `ids` to a set for $O(1)$ line-by-line membership checks in `queue_states`.**

In `queue_states(job_ids)`, `ids = list(job_ids)` maintains an ordered list. Within the parsing loop, `if len(parts) >= 2 and parts[0] in ids:` performs an $O(N)$ linear scan for every line of `squeue` output. While typical session job counts are modest, instantiating `id_set = set(ids)` yields $O(1)$ membership checks and adheres to Python best practices.

**Suggested fix:** Define `id_set = set(ids)` and check `parts[0] in id_set`.

**Test:** Run `pytest src/tests/ufs_chem_assay/test_jobs.py` to confirm that all `queue_states` tests pass with identical outputs.

````suggestion
    ids = list(job_ids)
    if not ids:
        return {}
    id_set = set(ids)
    argv = ["squeue", "-h", "-j", ",".join(ids), "-o", "%i %T"]
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=_SQUEUE_TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "squeue unavailable (%s); treating %s job(s) as queued", exc, len(ids)
        )
        return dict.fromkeys(ids, QUEUED_UNKNOWN)
    states: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in id_set:
            states[parts[0]] = parts[1]
````

---

### [P3] Tighten parameter ID boundary matching in log error message extractor
- **Location**: `src/cli/run_report.py:321-330`
- **Recommended Post Location**: Inline on `src/cli/run_report.py` (lines 321–330)
- **Tag**: `[robustness / regex]` | Lines affected: 10 lines

**Correctness: Prevent prefix collisions when matching combination failure messages from logs.**

In `_messages(log, test, suite, combo)`, if exact matches are not found, the fallback search checks `any(i.startswith(f"{part}-") for part in parts)`. If a suite defines combinations where one name is a prefix of another (e.g. `map` and `map-passthrough`), a failure in `map-passthrough` could match `map` if `map` failed without an exact parameter ID match.

Matching against the prefix prior to the trailing species identifier (e.g. via `i.rsplit("-", 1)[0] == part`) prevents cross-combination message leakage.

**Suggested fix:** Check that the prefix before the final hyphen delimiter matches `part` exactly.

**Test:** Run `pytest src/tests/ufs_chem_assay/test_run_report.py` to confirm that all test report message extraction tests pass.

---

## 6. Machine-Applicable Unified Diff Patch
```diff
--- a/src/jobs.py
+++ b/src/jobs.py
@@ -202,6 +202,7 @@ def queue_states(job_ids: Iterable[str]) -> dict[str, str]:
     ids = list(job_ids)
     if not ids:
         return {}
+    id_set = set(ids)
     argv = ["squeue", "-h", "-j", ",".join(ids), "-o", "%i %T"]
     try:
         completed = subprocess.run(
@@ -216,14 +217,23 @@ def queue_states(job_ids: Iterable[str]) -> dict[str, str]:
     states: dict[str, str] = {}
     for line in completed.stdout.splitlines():
         parts = line.split()
-        if len(parts) >= 2 and parts[0] in ids:
+        if len(parts) >= 2 and parts[0] in id_set:
             states[parts[0]] = parts[1]
     if completed.returncode != 0 and not states:
+        stderr = completed.stderr.strip()
+        if "Invalid job id" not in stderr and "slurm_load_jobs" not in stderr:
+            logger.warning(
+                "squeue failed (%s: %s); treating %s job(s) as queued",
+                completed.returncode,
+                stderr,
+                len(ids),
+            )
+            return dict.fromkeys(ids, QUEUED_UNKNOWN)
         logger.debug(
             "squeue exited %s for %s (every id gone): %s",
             completed.returncode,
             ids,
-            completed.stderr.strip(),
+            stderr,
         )
     return states
 
--- a/src/benchmarks.py
+++ b/src/benchmarks.py
@@ -401,6 +401,13 @@ def _append_benchmarks_locked(
             continue
         to_append.append(row)
         appended += 1
+    if not to_append:
+        logger.info(
+            "%s: 0 row(s) appended, %s skipped (already recorded)",
+            repo_csv,
+            skipped,
+        )
+        return AppendResult(appended=0, skipped=skipped)
     text = original
     if text and not text.endswith("\n"):
         text += "\n"
--- a/src/fslock.py
+++ b/src/fslock.py
@@ -37,6 +37,7 @@ def locked(path: Path) -> Iterator[None]:
     # Append mode: created when absent (the process umask decides the
     # permissions, as for every other file the harness writes), never
     # truncated, and nothing is written through this handle.
+    path.parent.mkdir(parents=True, exist_ok=True)
     with open(path, "a") as handle:
         try:
             logger.debug("acquiring lock on %s", path)
--- a/src/cli/watch.py
+++ b/src/cli/watch.py
@@ -253,6 +253,9 @@ def watch(
     interval = args.interval
     if interval is None:
         interval = config.slurm.poll_s if config.slurm is not None else 30
+    elif interval <= 0:
+        logger.error("--interval must be greater than 0; got %s", interval)
+        return 1
     logs_dir = root_dir / "logs"
     logger.info(
         "watching %s session(s) under %s every %ss: %s",
--- a/src/cli/run_report.py
+++ b/src/cli/run_report.py
@@ -241,7 +241,10 @@ def _combinations(root: Path) -> list[tuple[str, str, str]]:
     path = root / CONVENTIONS.combos_csv
     if not path.is_file():
         return []
-    frame = pd.read_csv(path, dtype=str).drop_duplicates("combo_id")
+    try:
+        frame = pd.read_csv(path, dtype=str).drop_duplicates("combo_id")
+    except (pd.errors.EmptyDataError, pd.errors.ParserError):
+        return []
     return [
         (str(row["combo_id"]), str(row["suite"]), str(row["name"]))
         for row in frame.to_dict("records")
```

