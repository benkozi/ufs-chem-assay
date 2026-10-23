# Feature: persistent benchmarking — every executed combination leaves one row (timings, memory, identity, baseline links) in a per-configuration `benchmarks.csv` committed to the repository

Refined 2026-10-23 from https://github.com/benkozi/ufs-chem-assay/issues/7 ("Add persistent benchmarking to suite runs", label `enhancement`, open since 2026-09-04) and the user's direction for this refinement: a CSV committed to the repository to begin with (JSON considered and set aside), wrapped in a pydantic model as everything else is, with links by ULID. Built on what the harness already records per run (`run.yaml`, `combos.csv`, `test-report.csv`, the stats CSVs, `baseline.yaml`) and on the job-output instrumentation the global-anthropogenic work added (`tic`/`toc`, `report-mem`, the `launch` record — `20261019-1447-global-anthro-use-case.md`, sections 5 and 6a). Written while that feature's first Ursa runs are in progress; its deliberately short 30-minute timeout is the first benchmark datum this design wants to keep.

# session

- **Session ID**: `c30fb022-0fff-43a5-9908-1cfbe2cb2d88`
- **Resume Command**: `agy --resume c30fb022-0fff-43a5-9908-1cfbe2cb2d88`

## Goal

1. **A row per executed combination, per run** — `BenchmarkRow`, a strict pydantic model whose fields are the CSV columns: what ran (run, suite, combination, its ULIDs), against which code (application commit, harness version and commit), where and how (platform, runtime, modulefile, the launch geometry), how long and how much (the harness's wall clock, the job's own `tic`→`toc`, Slurm's elapsed, queue wait and peak memory), what came out (driver verdict, files and bytes written, the baseline it was compared against and the verdict, whether it became a baseline itself). Dry runs produce no rows: nothing executed.
2. **Three homes for the rows.** Every session writes **`<output root>/benchmarks.csv`** (all rows — the per-run record, always) and **`<combination directory>/benchmarks.csv`** (that combination's one row — so a published baseline carries its own benchmark, since the whole directory is uploaded and the manifest lists it) at session end. The **committed** file of each configuration, `src/tests/config/<application>/<configuration>/benchmarks.csv`, beside the suites that sweep it, is the concatenation across runs: appended **at the end of every run on a known platform** (`harness.record_benchmarks: true` in the Ursa run configs; the pytest flag `--record-benchmarks`) and by **`ufs-chem-assay record-benchmarks --output-root <root>`** for a run that was not recorded or needs re-recording — the user reviews the diff and commits, as with baseline ULIDs and input digests.
3. **Reproducibility is the row.** Every identity a reader needs is in it: `run_id` and `combo_id` (the output root and combination directory, and the baseline's name if it was published), `baseline_ulid` (what it was compared against), `application_commit`, `harness_commit`, the launch, and a fingerprint of the generated configuration — so two rows with the same fingerprint are the same test of the application, whatever the run.
4. **Slurm's numbers, not a parse of `report-mem`, on known platforms.** Accounting is a per-platform capability (`platforms.py`): on Ursa the harness asks `sacct` once per job after `sbatch --wait` returns (the login node has it; the runbook already uses it) for `Submit`, `Start`, `End`, `Elapsed`, `MaxRSS`, `State`, `ExitCode`; the job id, already known to the runner, is surfaced for that. Platforms without an accounting entry (local today; Gaea C6/C7 when they are added) get the harness's own clock and null accounting columns — memory is recorded only where the harness knows how to ask for it.
5. **Append-only, validated, no analysis yet.** The committed CSV grows by rows; a `(run_id, combo_id)` already present is refused, as is a header that differs from the model's columns (schema changes are a deliberate edit, not a side effect). Plots and trend tables over the committed rows are Future work: the data comes first.

## Pre-design audit (fresh facts, 2026-10-23)

Read from the harness checkout at `6cd252f` ("change to 30 min walltime"; the global-anthropogenic feature's branch).

1. **What a run already records.** `run.yaml` (`RunManifest`): `run_id`, `application`, `application_commit`, `harness_version`, `harness_commit`, `platform`, `runtime`, `modulefile`, `launch` (`sbatch_args`, `srun_args`, `job_env`; slurm only, since 2026-10-19), the resolved suites, inputs as found (url, path, sha256, bytes), pinned baselines as found (state, detail). `combos.csv`: one row per (combination, target, field) with the effective value and `swept`. `test-report.csv` (`TestReportRow`): one row per combo-parameterized test — `pytest_name`, `application`, `suite`, `combo_id`, `combo`, `result` (passed/failed/skipped). Per combination: the generated config, `.out`, `cece.log`, the `.sbatch` (slurm), NetCDF, `<combo_id>-stats.csv`, `<combo_id>-stats-comparison.csv`, plots; `baseline.yaml` once published (the manifest: ULID, store, application and commit, suite, combo, run_id, harness version/commit, platform, runtime, launch, published_at/by, `superseded_ulid`, parameters, files with digests). The session-level `stats.csv` and `stats-comparison.csv` concatenate the per-combo ones.
2. **What is measured and thrown away.** The `driver_run` fixture (`src/tests/conftest.py`) times `run_driver` with `time.monotonic()` and **logs** `combo X completed in 12.3s` / `FAILED after …` — the duration is not kept in any artifact, and under slurm it includes queue time. `DriverRunResult` carries `combo`, `combo_dir`, `out_path`, `config`, `error` (the `CalledProcessError`/`TimeoutExpired`/`OSError`, or None); `run_driver` returns None.
3. **The job's own clocks (2026-10-22).** The rendered job script brackets the launch with `echo "tic: $(date -u)"` / `echo "toc: $(date -u)"` and ends with a guarded `report-mem` (an RDHPCS site command whose output format the harness does not know). A job killed by Slurm's time limit has `tic` and no `toc`. `_run_slurm_job` reads the job id from `sbatch --parsable`'s first line (used for `scancel` on the outer timeout) and does not return it.
4. **Slurm accounting.** `sacct -j <id> -o JobID,Submit,Start,End,Elapsed,MaxRSS,State,ExitCode -P -n` answers from the login node once the job has finished (`sbatch --wait` returns after completion, so the record exists; `MaxRSS` is on the `.batch`/step rows, so the query takes `-j <id>` and picks the step with a value). `Elapsed` is `[D-]HH:MM:SS`, `MaxRSS` a number with a `K`/`M`/`G` suffix, `State` one of `COMPLETED`, `FAILED`, `TIMEOUT`, `CANCELLED …`, `OUT_OF_MEMORY`, `ExitCode` `N:M`. The Ursa runbooks already send users to `sacct -j <id> -o Elapsed,MaxRSS` by hand.
5. **Docker and native** have no accounting path today: no job id, no `sacct`; the harness clock is the only duration, and peak memory is unavailable (native could read `resource.getrusage(RUSAGE_CHILDREN)` after the subprocess; docker would need `--memory` cgroup inspection during the run — neither is cheap enough to be a first step).
6. **Where committed, hand-reviewed artifacts live.** The suites' `baseline_comparisons[].ulid` lines and `inputs[].sha256` lines are rewritten by the harness and committed by the user; both edits are textual with a reload check (`baselines/suite.py`, `staging.rewrite_suite_digests`). A CSV appended by the harness and committed by the user is the same pattern with a simpler file.
7. **The 2026-10-22 run shape** on Ursa: `config/ursa-gcafs-anthro.yaml`, one combination, `timeout_s: 1800` chosen to see the job time out before a long window is requested — a measurement the current artifacts record only as a `failed` test and a `.out` without `toc`.
8. **The CECE and harness identity helpers** exist (`ApplicationSettings.commit_sha`, `identity.harness_version`/`harness_commit`), and `Application.config_fingerprint(path)` already produces the normalised generated-configuration string the baseline comparison uses for `config_match`; its SHA-256 is a stable per-configuration key.
9. **Conventions this design inherits.** Strict models with described fields (`models/base.StrictModel`); CSVs written with pandas from model dumps, columns fixed by the module (`report.py`, `combos.py`); session-end artifacts in `pytest_sessionfinish` in a fixed order (report → publish → stats → plots), never raising into pytest; CLI subcommands over a finished output root (`publish-baselines`); the harness logger over print; 100 % coverage, vulture, ruff `ARG`, mypy strict in CI.

## Design

### 0. Decisions

1. **CSV, per configuration, beside its suites.** `src/tests/config/<application>/<configuration>/benchmarks.csv` (`cece/maccity/benchmarks.csv`, `cece/gcafs-anthro/benchmarks.csv`): the rows are facts about that configuration's combinations, and the file sits with the other facts about it (the suites, the pinned ULIDs, the input digests). One repo-wide file was set aside (every change to any configuration would touch one file; per-configuration files diff cleanly), as was JSON (the user's call: CSV first; a row-per-line CSV diffs and greps well, pandas reads it, and the model is the schema either way). A later aggregate across configurations is a `pandas.concat` away.
2. **The model is the schema.** `BenchmarkRow(StrictModel)` in a new `src/benchmarks.py`; the CSV's header is `BenchmarkRow.model_fields` in order; reading validates every row through the model (empty cell ↔ `None`); writing dumps it. A header that differs from the model refuses the append (a schema change edits the committed files in the same commit as the model).
3. **One row per executed combination** — passed, failed, or timed out: a timeout is a benchmark result (the 30-minute experiment is the motivating case). Skipped combinations (dry run) produce nothing. Per-combination, not per-test: the tests' verdicts are columns.
4. **Three files, one writer; recording is the default where runs are expensive** (user, 2026-10-23: record every run — a long run forgotten is a shame). The session always writes `<output root>/benchmarks.csv` (all rows) and `<combo_dir>/benchmarks.csv` (one row; it travels with a published baseline); the committed per-configuration file is appended at session end when `--record-benchmarks` is set — **true in `config/ursa.yaml` and `config/ursa-gcafs-anthro.yaml`, false in `config/local.yaml`** (a laptop's maccity runs would otherwise dirty the tracked file on every run; a developer who wants them passes the flag) — and by `record-benchmarks` after the fact. A gitignored local ledger was considered and declined: the tracked file *is* the ledger, and its diff in `git status` is the reminder; a gitignored file is the one place git never mentions. The per-run file covers scraping a run that was not recorded.
5. **Accounting is a platform capability, Ursa first** (user, 2026-10-23: record memory only on known platforms; Gaea C6/C7 later). `platforms.py` gains `ACCOUNTING: dict[Platform, Accounting]` with `Platform.URSA: Accounting.SACCT` and nothing for `LOCAL`; `benchmarks.slurm_accounting` runs only when the session's platform has `SACCT`, once per job, from the harness process (the login node) right after `sbatch --wait` returns; a missing or failing `sacct` leaves the accounting columns null and logs once — never fails the test. A new platform is one table entry (and a hostname pattern) once its `sacct` is confirmed to answer the same columns. `report-mem`'s output stays in the `.out` for humans; the harness does not parse it.
6. **The job's `tic`/`toc` are parsed from the `.out`** (two fixed lines the template writes; `toc` absent → `wall_s` null — the signature of a time-limit kill) because they are runtime-independent: docker and native get them too once their launch is bracketed the same way (plan step 3 brackets the docker/native command in the runner so all three runtimes write them).
7. **Baseline links as columns, not joins.** `baseline_ulid` is the ULID the suite entry pinned when the run started (null when no entry; the placeholder is recorded as null with `baseline_state: unpublished`), `comparison_result` the test's verdict. Whether the combination itself became a baseline is not a column (dropped 2026-10-23, see section 1): the suite's `ulid:` line names the `combo_id` and the store's manifest names the run. `superseded` chains are the manifest's business, not the row's.
8. **Configuration identity.** `config_sha256` = SHA-256 of `Application.config_fingerprint(generated config)`: equal across runs for the same configuration, different when any field changes — the key for "same test over time". `suite` + `combo` stay as the human handles.
9. **Append refuses duplicates and never rewrites.** `(run_id, combo_id)` present → that row is skipped with a log line (a re-run of `record-benchmarks` is idempotent); existing lines are never touched, re-sorted or re-formatted (the diff is the appended lines), and a missing committed file is created with the header.
10. **No analysis in this change.** Trend plots and tables over the committed rows (per configuration, by `recorded_at` or `application_commit`) are Future work with the data in hand.

### 1. The row — `src/benchmarks.py`

```python
class BenchmarkRow(StrictModel):
    """One executed combination of one run: identity, provenance, launch, timings, memory, outcome, baseline links. The CSV columns, in this order."""
    recorded_at: datetime        # when the session wrote the row (UTC, aware)
    run_id: str                  # session ULID (the output root)
    application: str
    suite: str
    combo: str                   # canonical combination name
    combo_id: str                # the combination's runtime ULID (directory; baseline name if published)
    config_sha256: str           # SHA-256 of the application's config_fingerprint of the generated config
    application_commit: str | None
    harness_version: str
    harness_commit: str | None
    platform: Platform
    runtime: Runtime
    modulefile: str | None
    sbatch_args: str | None      # launch (slurm): as run.yaml's launch
    srun_args: str | None
    job_env: str | None          # NAME=VALUE pairs, space-joined (the ASSAY_JOB_ENV spelling)
    timeout_s: int               # the effective per-combination timeout (suite value capped by run_timeout_s)
    driver_result: Literal["passed", "failed", "timeout"]   # test_driver_execution's verdict; timeout when the error was TimeoutExpired (harness) or State TIMEOUT (slurm)
    exit_code: int | None        # the driver's (docker/native: CalledProcessError.returncode; slurm: sacct ExitCode's first number)
    duration_s: float            # the harness's clock around run_driver (slurm: includes the queue)
    wall_s: float | None         # toc - tic from the .out; null when toc is missing (killed) or the lines are absent
    queue_s: float | None        # slurm: Start - Submit
    elapsed_s: float | None      # slurm: Elapsed
    max_rss_kb: int | None       # slurm: MaxRSS, in KiB
    slurm_job_id: str | None
    slurm_state: str | None      # COMPLETED, FAILED, TIMEOUT, OUT_OF_MEMORY, CANCELLED …
    nc_files: int                # NetCDF files found in the combination directory
    output_bytes: int            # their total size
    baseline_ulid: str | None    # the ULID compared against (null: no entry, or the placeholder)
    baseline_state: str | None   # run.yaml's state for it: local, synced, unpublished, failed …
    comparison_result: Literal["passed", "failed", "skipped"] | None
```

(`published: bool` — whether the combination directory carried `baseline.yaml` at record time — was in the first refinement and is **dropped** (user, 2026-10-23): it lagged a post-session publish and needed a re-record to flip, the one in-place edit the file allowed. Whether a combination became a baseline is the suite's `ulid:` line and the store's manifest; a row never changes once written.)

Every field carries a `Field(description=…)`; `datetime` round-trips as ISO-8601; enums as their values; `None` as an empty cell. `BENCHMARK_COLUMNS = list(BenchmarkRow.model_fields)`.

Functions:

- `write_benchmarks_csv(rows, path) -> DataFrame` — the per-run file (all rows) and the per-combination file (one row), columns fixed, like `write_test_report_csv`.
- `read_benchmarks_csv(path) -> list[BenchmarkRow]` — validates every row; a header ≠ `BENCHMARK_COLUMNS` is a `ValueError` naming the file and the two headers.
- `append_benchmarks(repo_csv, rows) -> AppendResult` — creates the file with the header when absent; validates the existing header; skips rows whose `(run_id, combo_id)` is present (logged); appends the rest as lines, never touching existing ones (pandas `to_csv(mode="a", header=False)`); returns counts and the rows written; reloads the file through `read_benchmarks_csv` and restores the original text if the reload fails (the repoint pattern).
- `collect_row(...)` — builds a `BenchmarkRow` from the session's pieces: the run manifest, the `DriverRunResult` plus the execution record, the report rows of the combination, the baseline states, the combination directory (files, bytes, `baseline.yaml`, the `.out`'s `tic`/`toc`), the Slurm accounting.
- `parse_tic_toc(out_text) -> float | None`, `slurm_accounting(job_id) -> SlurmAccounting | None` (a small frozen model: `submit`, `start`, `end`, `elapsed_s`, `max_rss_kb`, `state`, `exit_code`; `sacct … -P -n` parsed, `MaxRSS` suffixes converted, the step row with a value chosen; `None` and one WARNING when `sacct` is missing or fails).

### 2. The runner surfaces what it knows — `src/runner.py`, `src/tests/conftest.py`

- `run_driver` returns a frozen `DriverExecution(job_id: str | None, submitted_at: datetime | None)` (slurm: the id `sbatch --parsable` printed; others: `None`), instead of `None`. `collect_row` asks for accounting only when `ACCOUNTING.get(settings.platform)` says how (Ursa: `sacct`); elsewhere the accounting columns are null without a query. `_run_slurm_job` returns the id it already parses. `DriverRunResult` gains `execution: DriverExecution`, `duration_s: float`, and `timeout_s: int` (the effective value) — the fixture measures the clock already; it now keeps it.
- Docker and native launches are bracketed like the slurm job: the runner prefixes the captured output with `tic: <utc>` and suffixes `toc: <utc>` lines it writes itself around the subprocess call (the `.out` is the harness's file; the lines are the same two the template writes), so `parse_tic_toc` is runtime-independent. The slurm template is unchanged.
- The tests' verdicts per combination come from the existing `_REPORT_ROWS` (keyed by nodeid; `TestReportRow.combo_id` + `pytest_name` prefix → the driver and comparison verdicts).
- **Session end** (`pytest_sessionfinish`, after the test report and before publishing, so a publish failure never costs the rows): `rows = [collect_row(...) for each DriverRunResult the session produced]` (the fixture results are kept in a stash dict by `combo_id`, as the report rows are); `write_benchmarks_csv(rows, roots.host / "benchmarks.csv")` and, per combination, `write_benchmarks_csv([row], combo_dir / "benchmarks.csv")` — **before** the publish step, so the uploaded baseline directory carries the file and the manifest digests it; then, with `--record-benchmarks`, `append_benchmarks` into each selected suite's configuration file (`suite_path.parent / "benchmarks.csv"`) for its combinations. One pass: report → benchmarks (per-run, per-combination, committed) → publish → stats → plots. Never raises into pytest; failures are logged with the post-hoc command. (The first refinement had a second pass after publishing to refresh `published`; gone with the column.)
- A dry run writes an empty `benchmarks.csv` (header only) to the output root — the artifact exists and says nothing ran — and never touches the committed files.

### 3. The CLI — `cli/main.py`, `cli/run_config.py`, `cli/stages.py`

- **`ufs-chem-assay record-benchmarks --output-root <root> [--application NAME] [--suite-config SELECTOR] [--dry-run]`**: reads `<root>/run.yaml` and `<root>/benchmarks.csv` (the per-run file is the source of truth: the rows were computed when the data was fresh; `published` is re-evaluated from the combination directories, so a `publish-baselines` run between the session and this command is reflected), reselects the run's suites exactly as `publish-baselines` does (`baselines/plan.py`'s `load_run` resolution is reused), appends each suite's rows to its configuration's committed CSV, logs `N row(s) appended to <path>; review the diff and commit`, `--dry-run` prints the rows it would append. Exit 1 on a header mismatch or a missing per-run file (an older run without one: "re-run the session, or record by hand").
- **`pytest --record-benchmarks`** (the session flag), **`harness.record_benchmarks`** in the run config (rendered as the flag by the harness stage): `true` in `config/ursa.yaml` and `config/ursa-gcafs-anthro.yaml` (every Ursa run is kept; the comment says to commit the appended rows), `false` in `config/local.yaml` (opt in with `--override harness:record_benchmarks=true`). **`ASSAY_*`**: nothing new — the flag is per session like `--publish-baselines`.
- `fetch` is untouched.

### 4. The committed files — `src/tests/config/cece/{maccity,gcafs-anthro}/benchmarks.csv`

Created by the first recording run (header + rows) — the Ursa run configs record by default — or by `record-benchmarks` for a run that predates this change, committed by the user; the design ships no rows. The gcafs-anthro file's first rows are the 30-minute timeout runs (`driver_result: timeout`, `slurm_state: TIMEOUT`, `wall_s` null, `elapsed_s ≈ 1800`), then the runs with the raised timeout — the comparison the user wants to see is those rows side by side.

### 5. Documentation

- README: a "Benchmarks" section (the row, the two files, the two entry points, the Slurm columns and their absence elsewhere, "review the diff and commit"), the `--record-benchmarks` flag beside `--publish-baselines`, `harness.record_benchmarks` in the run-config description, the output-root layout gaining `benchmarks.csv`.
- `docs/ursa-runbook.md` and `docs/gcafs-anthro-runbook.md`: one paragraph each after the publish step — `record-benchmarks --output-root …`, review, commit; the gcafs runbook names the timeout experiment as the first rows.
- `design/design.md` (implementation phase): the output-root layout, the session-end order, a "Benchmarks" section summarising this document, the test configuration layout listing `benchmarks.csv`.
- The run-config templates: `harness.record_benchmarks: true` in the two Ursa files, `false` in `local.yaml`, each with a comment; `test_user_docs` pins the values.

### 6. Edge cases and guards (consistency scan)

- **A combination that never started** (submission rejected, `OSError` on docker): `duration_s` measured, `driver_result: failed`, `exit_code` null, no `tic` → `wall_s` null, `nc_files: 0`; Slurm columns from `sacct` when there is a job id (a rejected submission has none).
- **The outer queue timeout** (`TimeoutExpired` from the harness after `scancel`): `driver_result: timeout`, `slurm_state: CANCELLED by …`, `elapsed_s` whatever ran — distinguishable from the job's own `TIMEOUT` by `slurm_state`.
- **`sacct` lag**: accounting records can trail job completion by seconds on some sites; the query runs after `sbatch --wait` returned and retries once after a short pause when it returns no rows, then gives up with null columns and a WARNING naming the job id (`sacct -j <id>` by hand fills the gap; `record-benchmarks` does not re-query — the per-run file is the record).
- **Several suites in one session**: rows carry `suite`; `record-benchmarks` splits them by suite into each suite's configuration file (two suites of one configuration append to the same file, which is fine: distinct `combo_id`s).
- **The committed file edited by hand** (a row deleted, a column added): the header check and the model validation refuse the append naming the problem; the user fixes the file. Nothing is ever rewritten in place.
- **The placeholder baseline** (`unpublished`): `baseline_ulid` null, `baseline_state: unpublished`, `comparison_result: skipped`.
- **CSV cells**: `job_env` is the `ASSAY_JOB_ENV` spelling (space-separated `NAME=VALUE`), so no nested structure; strings with commas (`sbatch_args` never has them; `slurm_state` "CANCELLED by 1234" has none) are quoted by pandas anyway.
- **Timezones**: `recorded_at` and the Slurm timestamps are aware UTC (`sacct` prints local time without a zone; the harness converts with the login node's zone, `datetime.astimezone`, before taking differences — differences are what the row keeps).
- **The per-combination file and the baseline**: a baseline is the whole combination directory, so `benchmarks.csv` rides along and appears in `baseline.yaml`'s `files` with its digest; the comparison reads `*.nc` only, so the file never affects a verdict. A baseline published before this change has none; nothing looks for it.
- **A recording run on a platform without accounting** (a laptop with `--override harness:record_benchmarks=true`): rows with null accounting columns are appended like any other — the columns say what was and was not measured.
- **Coverage**: `sacct` is subprocess-mocked in the harness tests like `aws` and `sbatch`; the `.out` parser and the CSV append are pure functions; the platform table is unit-tested (Ursa → sacct, local → none).

## Implementation plan (TDD, red → green → refactor)

1. **`src/benchmarks.py`** — `BenchmarkRow`, `SlurmAccounting`, `parse_tic_toc`, `slurm_accounting` (mocked `sacct`: a complete record, a `K`/`M`/`G` `MaxRSS`, no rows then rows on retry, missing binary), `write_benchmarks_csv`/`read_benchmarks_csv` (round trip, header mismatch, empty cells ↔ None), `append_benchmarks` (create, append, skip duplicates, refuse a bad header, restore on a failed reload). Tests in `src/tests/ufs_chem_assay/test_benchmarks.py`.
2. **Runner** — `DriverExecution`, `run_driver` returning it, `_run_slurm_job` returning the job id, the `tic`/`toc` bracketing of docker/native output; `DriverRunResult` fields. Tests in `test_runner.py`, `test_slurm_runtime.py`, `test_native_runtime.py` updated for the return value and the two lines.
3. **Session** — `collect_row` over a fabricated `DriverRunResult` + report rows + states + combo dir (unit-tested), the per-combination file, the session-end order, the empty dry-run file, `--record-benchmarks`; `platforms.ACCOUNTING` and its test; `test_dry_run.py` asserts the header-only file; the subprocess dry-run tests gain nothing else (nothing executes).
4. **CLI** — `record-benchmarks` (`test_cli.py`: a fabricated run with a per-run `benchmarks.csv` → the committed file created/appended, duplicates skipped, dry run, header mismatch exit 1, missing per-run file exit 1); `harness.record_benchmarks` in `HarnessSection` and the stage render (`test_run_config.py`, `test_stages.py`); the templates — true on Ursa, false locally (`test_user_docs.py`).
5. **Docs** — README, the two runbooks, the templates' comments; `design.md` at the end.
6. **First rows** (the user's step, on Ursa): `record-benchmarks --output-root=$ROOT/CECE/ufs-chem-assay-output` after the 30-minute run and again after the raised-timeout run; commit `src/tests/config/cece/gcafs-anthro/benchmarks.csv`; a maccity run on Ursa and locally seeds `cece/maccity/benchmarks.csv` with one slurm and one docker row.

## Verification

```bash
uv run pytest src/tests/ufs_chem_assay -q --cov          # 100 %
uv run pre-commit run --all-files
uv run pytest src/tests/test_driver_combos.py --dry-run --suite-config='.*-suite.yaml'   # header-only benchmarks.csv per run, no committed file touched
uv run pytest src/tests/test_driver_combos.py --suite-config=simple-maccity-suite.yaml   # docker: 3 rows (per-run and one per combo dir), tic/toc wall_s, accounting columns empty
uv run ufs-chem-assay record-benchmarks --output-root=<that root> --dry-run             # the 3 rows it would append
uv run ufs-chem-assay record-benchmarks --output-root=<that root>                       # cece/maccity/benchmarks.csv created; a second call appends nothing
```

On Ursa (recording on by default): the gcafs-anthro timeout run's row appears in `cece/gcafs-anthro/benchmarks.csv` at session end (`timeout`, `TIMEOUT`, `wall_s` empty, `elapsed_s` ≈ 1800, `max_rss_kb` set, `queue_s` set), the raised-timeout run's row beside it; after `publish-baselines` the uploaded baseline directory holds the combination's `benchmarks.csv`, and a `record-benchmarks` flips `published` to true for that row in the committed file (decision 9's one in-place update).

## Risks and notes

- **`sacct` availability and format** vary by site; the parser pins the `-P -n` column list and treats anything unexpected as null columns with a warning, never a failure. Ursa's `sacct` is the first and only target now.
- **`MaxRSS` is per step**, not per job: the query reads all rows of the job and takes the maximum with a value (the `.batch` step for a single `srun`). A multi-task job reports the largest task.
- **Clock skew** between the login node and compute nodes affects `wall_s` by at most the skew; `elapsed_s` is Slurm's and authoritative.
- **Row growth**: one row per combination per recorded run; an exhaustive maccity run is 240 rows. Recording is opt-in per run (`--record-benchmarks`), so the committed files hold the runs someone chose to keep. A per-configuration size that becomes a nuisance is the cue for the aggregate/analysis follow-up, not for pruning.
- **The per-run file is the truth**; `record-benchmarks` copies its rows and computes nothing anew. Re-running a session overwrites its output root and its per-run file — the committed rows are what persists.

## Open questions — resolved (user, 2026-10-23)

1. **Per-configuration files** — yes, to keep conflicts small (decision 1).
2. **Record every run** — yes: `harness.record_benchmarks: true` in the Ursa run configs, the per-run and per-combination files always; a gitignored ledger was offered as an alternative and declined (decision 4).
3. **Memory only on known platforms** — yes: accounting is a per-platform table, Ursa via `sacct` now, Gaea C6/C7 when added; nothing for local (decision 5).
4. **The row travels with the baseline** — yes: each combination directory gets its own `benchmarks.csv` (one row), uploaded with the baseline; the repository keeps the concatenated per-configuration copy (decisions 4 and Goal 2). (`job_env` stays a column, as proposed; the user's answer addressed the baseline copy instead.)

## Future work

- Analysis over the committed rows: per-configuration trend tables and plots (`elapsed_s`, `max_rss_kb` by `recorded_at`/`application_commit`/`ntasks`), a `benchmarks` subcommand or a notebook under `docs/`.
- Per-rank timings from the driver's own profiler output (CECE's kernel profiler), once the driver writes a machine-readable summary.
- Docker and native peak memory; GPU partitions.
- Gaea C6/C7 as platforms with accounting (hostname patterns, the `ACCOUNTING` entries, a run config each).

## Implementation notes (2026-10-23)

Implemented in the design's order, red → green per module; the user's addition at implementation time was that the runbooks must let the feature be tested on Ursa.

**Files.** `src/benchmarks.py` (new): `BenchmarkRow` (32 described fields = `BENCHMARK_COLUMNS`), `SlurmAccounting`, `parse_tic_toc`, `parse_sacct` (the job's row for times/state/exit code, the largest step `MaxRSS`, `[D-]HH:MM:SS` and `K/M/G/T` suffixes, `Unknown` times as None, malformed lines skipped), `slurm_accounting` (`sacct -j <id> -P -n -o …`, one retry after 2 s, None with a warning when missing/failing/empty), `write_benchmarks_csv`/`read_benchmarks_csv` (the `csv` module; empty cell ↔ None; a foreign header refused), `append_benchmarks` (create / append / skip duplicates / restore on a failed reload; a file without a final newline is handled; existing lines are never touched since the `published` column was dropped later the same day), `collect_row`. `src/platforms.py`: `Accounting` and `ACCOUNTING = {URSA: SACCT}`. `src/runner.py`: `DriverExecution` returned by `run_driver`; `SlurmJobError`/`SlurmJobTimeout` (subclasses of the standard exceptions) carry the job id; docker/native output bracketed by `tic:`/`toc:` lines the runner writes (ISO-8601 UTC); the job template's clocks switched from `date -u` to `date -u +%Y-%m-%dT%H:%M:%SZ` so both are parseable; `DriverRunResult` + `execution`, `duration_s`, `timeout_s`. `src/tests/conftest.py`: `--record-benchmarks`; `_DRIVER_RESULTS`/`_ACCOUNTING` stashes filled by the fixture (accounting queried right after the job, only when `ACCOUNTING` says so); `_benchmark_rows` and `_record_benchmarks` in the session-end order report → benchmarks (per-run, per-combination, and the committed file under the flag) → publish → stats → plots, wrapped so it never fails the session. `src/cli/main.py`: `record-benchmarks` (`--output-root`, `--application`, `--suite-config`, `--dry-run`); `cli/run_config.py`: `harness.record_benchmarks`; `cli/stages.py`: the flag; the three templates (`true` on Ursa, `false` locally). Docs: README "Benchmarks" section, `docs/ursa-runbook.md` (a Benchmarks paragraph after publishing), `docs/gcafs-anthro-runbook.md` (step 5a: what the timeout run's row looks like, the re-record after publishing, `git diff`, commit), `design.md` (a Benchmarks section, the layout entries, `benchmarks.py` in the code layout).

**Tests** (coverage 100 %, hooks green, every suite dry-runs — 2907 skipped —, `mypy` clean): `test_benchmarks.py` (new, 22 tests: columns and descriptions, verdict validation, tic/toc, sacct parsing and the mocked query with retry/absence, round trip with empty cells, header-only write, foreign header, append create/append/skip/no-trailing-newline/restore, `collect_row` for passed/timeout/failed/no-launch), `test_platforms.py` (the table), `test_runner.py`/`test_native_runtime.py`/`test_slurm_runtime.py` (the clock lines, the job id on success), `test_cece_pipeline.py` (output between the clocks), `test_dry_run.py` (header-only per-run file, no per-combination files), `test_cli.py` (append, idempotent re-record, dry run, clean errors, empty run, unselected suite, foreign application, bad selector, foreign header), `test_run_config.py`/`test_stages.py`/`test_user_docs.py` (the key, the flag, the templates, the docs needles).

**Real run.** `simple-maccity` under docker into `ufs-chem-assay-output-bench/`: three rows in the per-run file and one per combination directory; `wall_s` 1.0/0.0/0.0 s (the clocks are second-resolution; the tiny maccity driver runs under a second), `duration_s` ≈ 0.35 s, `config_sha256` distinct per `mapalgo`, `baseline_ulid`/`baseline_state`/`comparison_result` = the pinned ULID / `local` / `passed`, accounting columns empty (local). The committed `cece/maccity/benchmarks.csv` was not created: `local.yaml` records nothing and the test run passed no flag — seeding it is the user's `record-benchmarks` call.

**Details worth knowing.** (1) The tic/toc parser requires both lines in the ISO form the runner and template now write; a `.out` from before this change yields `wall_s` null. (2) `collect_row`'s `driver_result` is `timeout` on either signal (the harness's `TimeoutExpired`, or Slurm's `TIMEOUT` state even when the harness saw a nonzero exit), which is what the 30-minute gcafs-anthro run will produce. (3) `exit_code` prefers `sacct`'s `ExitCode` over the submission's exit status under slurm. (4) A committed row never changes once written; `record-benchmarks` exists for runs that were not recorded at session end.

**Remaining (Ursa).** The gcafs-anthro run with `config/ursa-gcafs-anthro.yaml` records by default; `git diff src/tests/config/cece/gcafs-anthro/benchmarks.csv` after the run, commit. A maccity run with `config/ursa.yaml` seeds `cece/maccity/benchmarks.csv` with a slurm row; a local `record-benchmarks --output-root=<the bench root>` adds the docker rows.

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

- **Session ID**: `c30fb022-0fff-43a5-9908-1cfbe2cb2d88`
- **Resume Command**: `agy --resume c30fb022-0fff-43a5-9908-1cfbe2cb2d88`

### testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- refine from https://github.com/benkozi/ufs-chem-assay/issues/7

Issue #7 ("Add persistent benchmarking to suite runs", label `enhancement`, open, no comments, created 2026-09-04), as read on 2026-10-23:

- Add option to persist benchmarking for suite runs.
- Run configurations, CECE hash, harness hash, etc. should all be captured to ensure runs are reproducible.

The user's direction for this refinement (2026-10-23): a CSV or JSON file that can be committed to the repository initially — CSV is probably best; wrap it in a pydantic model as usual; links via ULID are required.

## conversational updates

- 2026-10-23 (refinement, no further user input): the issue's two bullets and the user's three directions became the Goal and Design sections 0–6. Findings that shaped it: (1) the harness already measures each driver run's wall clock and logs it, and already records every identity the issue asks for in `run.yaml` — the missing piece is a per-combination row that joins them with the measurements and the baseline links, so the model (`BenchmarkRow`) is mostly a projection of existing records plus the timings; (2) the 2026-10-22 job instrumentation (`tic`/`toc`, `report-mem`) is human-readable but `report-mem`'s format is unknown, while `sacct` on the login node gives elapsed, queue wait, peak memory, state, and exit code in a documented format — so Slurm's numbers come from `sacct`, the `.out`'s `tic`/`toc` give a runtime-independent wall time, and the runner surfaces the job id it already parses; (3) the repoint-and-commit pattern for ULIDs and digests fits an append-only CSV beside the suites, per configuration. Decisions taken without the user, with their alternatives under Open questions: per-configuration files, recording on request (the per-run file always), no memory for docker/native yet, `job_env` as a column. The motivating first rows are the gcafs-anthro 30-minute timeout runs. No files other than this document were created or changed.
- 2026-10-23 (user: answers to the four open questions, and "what do you think?" about a gitignored benchmark file scraped after the fact): (1) per configuration; (2) record every run — the design now records at session end by default in the Ursa run configs (`harness.record_benchmarks: true`), keeps it off in `local.yaml`, and declines the gitignored ledger: the tracked file is the ledger and its diff the reminder, while the per-run file at the output root already serves scraping; (3) memory only on known platforms — accounting becomes a per-platform table (`platforms.ACCOUNTING`, Ursa → `sacct`, Gaea C6/C7 later, nothing for local); (4) read as a design change rather than an answer about `job_env`: each combination directory gets a one-row `benchmarks.csv` so a published baseline carries its benchmark, and the repository keeps the concatenated per-configuration file; `job_env` stays a column. Goal 2 and 4, decisions 4–5, sections 1–6, the plan, verification and Future work updated in place.
- 2026-10-23 (user: implement, and make sure the runbook lets the feature be tested on Ursa): implemented as designed (Implementation notes); the gcafs runbook's new step 5a and the Ursa runbook's Benchmarks paragraph are the Ursa test — the run records by default, the diff shows the row, `record-benchmarks` re-records after publishing. Every gate green locally; the Ursa rows are the user's.
- 2026-10-23 (user: "I don't understand: after the publish the row's published cell is stale until it is re-recorded" — explained: the column lagged a post-session publish by design and `record-benchmarks` was the catch-up; offered to drop it; user: drop it): `published` removed from `BenchmarkRow`; `append_benchmarks` now never touches an existing line (`AppendResult` is `appended`/`skipped`); the session's second benchmark pass and `record-benchmarks`' refresh are gone; README, `design.md`, both runbooks (no re-record step; `record-benchmarks` is for a run that was not recorded) and this document's sections 1–2 updated. Whether a combination became a baseline is the suite's `ulid:` line.
