"""Persistent benchmarking: one row per executed combination of a run —
identity, provenance, launch, timings, memory, outcome, baseline links —
as a strict model whose fields are the CSV columns. Three files share the
writer: `<output root>/benchmarks.csv` (every row of the run),
`<combo_dir>/benchmarks.csv` (that combination's row, uploaded with a
published baseline), and the committed per-configuration
`src/tests/config/<application>/<configuration>/benchmarks.csv`, which
`append_benchmarks` grows by rows (duplicates skipped, nothing rewritten)
and the user commits. Slurm's numbers come from `sacct`, once
per job, where the platform has accounting; the job's own `tic`/`toc`
lines in the `.out` give a runtime-independent wall time.
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar

import pandas as pd
from pydantic import ConfigDict, Field

from fslock import locked
from logs import get_logger
from models.base import StrictModel
from models.conventions import CONVENTIONS
from models.suite_config import RunManifest
from platforms import Platform, Runtime
from report import TestResult

logger = get_logger("benchmarks")

DriverResult = Literal["passed", "failed", "timeout"]


class BenchmarkConventions(StrictModel):
    """How the timings and memory are read: the clock lines in a job's
    output and the `sacct` query. The file name and the clock format are the
    harness-wide `CONVENTIONS`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tic_pattern: str = Field(
        r"^tic: (\S+)$",
        description="Regex (multiline) of the clock line written before the driver starts",
    )
    toc_pattern: str = Field(
        r"^toc: (\S+)$",
        description="Regex (multiline) of the clock line written after the driver exits",
    )
    sacct_fields: tuple[str, ...] = Field(
        ("JobID", "Submit", "Start", "End", "Elapsed", "MaxRSS", "State", "ExitCode"),
        description="The `sacct -o` columns, in order; a row with another count is not ours",
    )
    sacct_retry_delay_s: float = Field(
        2.0,
        ge=0,
        description="Pause between `sacct` attempts while the job row is incomplete",
    )
    rss_units: Mapping[str, int] = Field(
        {"K": 1, "M": 1024, "G": 1024**2, "T": 1024**3},
        description="Multipliers to KiB of the suffix `sacct` puts on MaxRSS",
    )


BENCHMARK_CONVENTIONS = BenchmarkConventions()


# ── Slurm accounting ─────────────────────────────────────────────────────────


class SlurmAccounting(StrictModel):
    """What `sacct` says about one finished job: the job row's times, state
    and exit code, and the largest step's peak memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str = Field(description="The Slurm job id")
    submit: datetime | None = Field(
        description="Submit time (the cluster's local clock)"
    )
    start: datetime | None = Field(
        description="Start time; None when the job never started"
    )
    end: datetime | None = Field(description="End time; None when unknown")
    elapsed_s: float | None = Field(description="Elapsed, in seconds")
    queue_s: float | None = Field(
        description="Start - Submit, in seconds; None without both"
    )
    max_rss_kb: int | None = Field(
        description="The largest MaxRSS over the job's steps, in KiB; None when none reported"
    )
    state: str | None = Field(
        description="State as printed (COMPLETED, TIMEOUT, CANCELLED by …)"
    )
    exit_code: int | None = Field(description="The first number of ExitCode (N:M)")

    @property
    def completed(self) -> bool:
        """Whether Slurm says the job ran to its end (COMPLETED). ExitCode's
        first number alone does not: a job Slurm ended itself (out of
        memory, a lost node, a signal) can carry 0 there."""
        return (self.state or "").startswith("COMPLETED")


def _parse_clock(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_elapsed(text: str) -> float | None:
    """`[D-]HH:MM:SS` (sacct) to seconds."""
    match = re.fullmatch(r"(?:(\d+)-)?(\d+):(\d\d):(\d\d)", text.strip())
    if match is None:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def _parse_rss(text: str) -> int | None:
    """`2048K`, `1.5G`, `12M`, or a bare number of KiB, to KiB."""
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)([KMGT]?)", text.strip())
    if match is None:
        return None
    value, unit = match.groups()
    return int(float(value) * BENCHMARK_CONVENTIONS.rss_units.get(unit, 1))


def parse_sacct(output: str, job_id: str) -> SlurmAccounting | None:
    """`sacct -P -n -o <sacct_fields>` output for one job: the row whose
    JobID is the job supplies the times, state and exit code; MaxRSS is the
    maximum over every row that reports one (the steps). None when the
    job's row is absent (accounting not yet written, or a wrong id)."""
    job_row: list[str] | None = None
    rss: list[int] = []
    for line in output.splitlines():
        parts = line.split("|")
        if len(parts) != len(BENCHMARK_CONVENTIONS.sacct_fields):
            continue
        if parts[0] == job_id:
            job_row = parts
        kb = _parse_rss(parts[5])
        if kb is not None:
            rss.append(kb)
    if job_row is None:
        return None
    submit, start, end = (_parse_clock(job_row[i]) for i in (1, 2, 3))
    exit_match = re.match(r"(\d+)", job_row[7])
    return SlurmAccounting(
        job_id=job_id,
        submit=submit,
        start=start,
        end=end,
        elapsed_s=_parse_elapsed(job_row[4]),
        queue_s=(start - submit).total_seconds() if submit and start else None,
        max_rss_kb=max(rss) if rss else None,
        state=job_row[6].strip() or None,
        exit_code=int(exit_match.group(1)) if exit_match else None,
    )


def slurm_accounting(
    job_id: str, clusters: str | None = None
) -> SlurmAccounting | None:
    """Ask `sacct` for a finished job, retrying once after a short pause
    (accounting can trail completion by seconds) — on the job's own
    cluster (`-M`) when it was submitted to a named one. None, logged once,
    when sacct is missing, fails, or has no row for the job — never an
    error."""
    on = [] if clusters is None else ["-M", clusters]
    argv = [
        "sacct",
        *on,
        "-j",
        job_id,
        "-P",
        "-n",
        "-o",
        ",".join(BENCHMARK_CONVENTIONS.sacct_fields),
    ]
    for attempt in (1, 2):
        try:
            completed = subprocess.run(
                argv, check=False, capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("sacct unavailable for job %s: %s", job_id, exc)
            return None
        if completed.returncode != 0:
            logger.warning(
                "sacct exited %s for job %s: %s",
                completed.returncode,
                job_id,
                completed.stderr.strip(),
            )
            return None
        accounting = parse_sacct(completed.stdout, job_id)
        if accounting is not None:
            return accounting
        if attempt == 1:
            time.sleep(BENCHMARK_CONVENTIONS.sacct_retry_delay_s)
    logger.warning(
        "sacct has no record of job %s yet; accounting columns left empty (%s by hand)",
        job_id,
        " ".join(["sacct", *on, "-j", job_id]),
    )
    return None


# ── the job's own clocks ─────────────────────────────────────────────────────


def parse_tic_toc(out_text: str) -> float | None:
    """toc - tic in seconds from the two lines the launch writes around the
    driver (`tic: <ISO UTC>` / `toc: <ISO UTC>`); None when either is absent
    or unreadable — a job killed by its time limit has tic and no toc."""
    tic = re.search(BENCHMARK_CONVENTIONS.tic_pattern, out_text, re.MULTILINE)
    toc = re.search(BENCHMARK_CONVENTIONS.toc_pattern, out_text, re.MULTILINE)
    if tic is None or toc is None:
        return None
    try:
        start = datetime.strptime(tic.group(1), CONVENTIONS.clock_format).replace(
            tzinfo=UTC
        )
        end = datetime.strptime(toc.group(1), CONVENTIONS.clock_format).replace(
            tzinfo=UTC
        )
    except ValueError:
        return None
    return (end - start).total_seconds()


# ── the row ──────────────────────────────────────────────────────────────────


class BenchmarkRow(StrictModel):
    """One executed combination of one run — identity, provenance, launch,
    timings, memory, outcome, baseline links. The CSV columns, in this order."""

    recorded_at: datetime = Field(
        description="When the session wrote the row (UTC, aware)"
    )
    run_id: str = Field(description="Session ULID (the output root)")
    application: str = Field(description="Registry name of the application")
    suite: str = Field(description="Unique suite name")
    combo: str = Field(description="Canonical combination name")
    combo_id: str = Field(
        description="The combination's runtime ULID: its directory, and its baseline name if published"
    )
    config_sha256: str = Field(
        description="SHA-256 of the application's config_fingerprint of the generated config: equal across runs of the same configuration"
    )
    application_commit: str | None = Field(
        description="HEAD of the application checkout; null when none"
    )
    build_type: str | None = Field(
        description=(
            "CMAKE_BUILD_TYPE of the checkout's build directory (run.yaml's): a "
            "Debug-build row is never read beside a Release one by mistake; null "
            "without a CMake cache, and in rows older than the column"
        )
    )
    harness_version: str = Field(description="Harness package version")
    harness_commit: str | None = Field(
        description="Harness checkout HEAD (-dirty when edited); null outside a checkout"
    )
    platform: Platform = Field(description="Machine the run happened on")
    runtime: Runtime = Field(description="How the driver was spawned")
    modulefile: str | None = Field(
        description="Modulefile the driver jobs loaded; null outside slurm"
    )
    sbatch_args: str | None = Field(
        description="run.yaml's launch.sbatch_args; null outside slurm"
    )
    srun_args: str | None = Field(
        description="run.yaml's launch.srun_args; null outside slurm"
    )
    job_env: str | None = Field(
        description="run.yaml's launch.job_env as NAME=VALUE pairs, space-joined (the ASSAY_JOB_ENV spelling); null outside slurm"
    )
    timeout_s: int = Field(
        description="The effective per-combination timeout (the suite's, capped by run_timeout_s)"
    )
    driver_result: DriverResult = Field(
        description="test_driver_execution's verdict; timeout when the harness's TimeoutExpired fired or Slurm's State is TIMEOUT"
    )
    exit_code: int | None = Field(
        description="The driver's exit code (docker/native: the process's; slurm: sacct's ExitCode); null when unknown"
    )
    duration_s: float = Field(
        description="The harness's clock around the driver call (slurm: includes the queue)"
    )
    wall_s: float | None = Field(
        description="toc - tic from the .out; null without toc (killed) or without the lines"
    )
    queue_s: float | None = Field(
        description="Slurm Start - Submit; null without accounting"
    )
    elapsed_s: float | None = Field(
        description="Slurm Elapsed; null without accounting"
    )
    max_rss_kb: int | None = Field(
        description="Slurm MaxRSS over the job's steps, KiB; null without accounting"
    )
    slurm_job_id: str | None = Field(description="The Slurm job id; null outside slurm")
    slurm_state: str | None = Field(
        description="Slurm State as printed; null without accounting"
    )
    nc_files: int = Field(
        description="NetCDF files in the combination directory (top level)"
    )
    output_bytes: int = Field(description="Their total size in bytes")
    baseline_ulid: str | None = Field(
        description="The ULID the suite entry pinned and the run compared against; null without an entry or for the placeholder"
    )
    baseline_state: str | None = Field(
        description="run.yaml's state for that baseline (local, synced, unpublished, failed …); null without an entry"
    )
    comparison_result: TestResult | None = Field(
        description="test_baseline_comparison's verdict; null when the test did not run"
    )


BENCHMARK_COLUMNS = list(BenchmarkRow.model_fields)

# Columns added after files were already written, per kind of file: a file
# without them still reads (the cells are null). A driver rows' file — an old
# output root, a published baseline's row — is never appended to (the
# committed files were migrated); a test rows' file — a tests root, evaluated
# once per run — is rewritten with the current header on its next append
# (_MIGRATED_ON_APPEND), since a tests root outlives harness updates.
_ADDED_COLUMNS: dict[str, frozenset[str]] = {
    "BenchmarkRow": frozenset({"build_type"}),
    "TestBenchmarkRow": frozenset({"build"}),
}
_MIGRATED_ON_APPEND = frozenset({"TestBenchmarkRow"})


class TestBenchmarkRow(StrictModel):
    """One run of an application's own test suite — the whole run is the
    benchmarked unit (the junit report under the tests root has the
    individual tests). The CSV columns, in this order."""

    __test__ = False  # a model, not a pytest class

    recorded_at: datetime = Field(description="When the run was evaluated (UTC, aware)")
    run_id: str = Field(description="The test run's ULID (the test ledger's)")
    application: str = Field(description="Registry name of the application")
    toolchain: str | None = Field(
        description="The run config's toolchain the build used; null without one"
    )
    build: str | None = Field(
        description="The build variant the tests ran against; null for the unnamed build"
    )
    platform: Platform = Field(description="Machine the tests ran on")
    runtime: Runtime = Field(description="How the tests were launched")
    modulefile: str | None = Field(
        description="Modulefile the build and the tests loaded; null without one"
    )
    build_type: str | None = Field(
        description="CMAKE_BUILD_TYPE of the build directory; null when unknown"
    )
    cmake_args: str | None = Field(
        description="The run config's configure flags, space-joined; null when none"
    )
    application_commit: str | None = Field(
        description="HEAD of the application checkout; null when unknown"
    )
    harness_version: str = Field(description="Harness package version")
    harness_commit: str | None = Field(
        description="Harness checkout HEAD (-dirty when edited); null outside a checkout"
    )
    slurm_job_id: str | None = Field(description="The Slurm job id; null outside slurm")
    slurm_state: str | None = Field(
        description="Slurm State as printed; null without accounting"
    )
    exit_code: int = Field(
        description="The run's verdict: the test command's exit status (0: all passed)"
    )
    wall_s: float | None = Field(
        description="toc - tic around the test command; null without both lines"
    )
    queue_s: float | None = Field(
        description="Slurm Start - Submit; null without accounting"
    )
    elapsed_s: float | None = Field(
        description="Slurm Elapsed; null without accounting"
    )
    max_rss_kb: int | None = Field(
        description="Slurm MaxRSS over the job's steps, KiB; null without accounting"
    )
    tests_total: int | None = Field(
        description="Tests in the junit report; null without a report"
    )
    tests_passed: int | None = Field(
        description="Tests that passed; null without a report"
    )
    tests_failed: int | None = Field(
        description="Tests that failed; null without a report"
    )
    tests_skipped: int | None = Field(
        description="Tests skipped or not run; null without a report"
    )
    ctest_s: float | None = Field(
        description="The tests' own durations, summed (the junit's); null without a report"
    )
    tests_root: Path = Field(
        description="The tests root the run's output and junit report are under"
    )


TEST_BENCHMARK_COLUMNS = list(TestBenchmarkRow.model_fields)

RowT = TypeVar("RowT", BenchmarkRow, TestBenchmarkRow)

# What a header error calls each model's columns.
_KINDS: dict[type[StrictModel], str] = {
    BenchmarkRow: "benchmark",
    TestBenchmarkRow: "test benchmark",
}


def _cells(row: StrictModel) -> list[str]:
    dumped = row.model_dump(mode="json")
    return ["" if dumped[name] is None else str(dumped[name]) for name in dumped]


def _write_csv(
    model: type[StrictModel], rows: Sequence[StrictModel], path: Path
) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(list(model.model_fields))
        for row in rows:
            writer.writerow(_cells(row))


def write_benchmarks_csv(rows: Sequence[BenchmarkRow], path: Path) -> pd.DataFrame:
    """Write rows as a CSV with the model's columns (an empty list writes the
    header alone); None cells are empty. Returns the frame written."""
    frame = pd.DataFrame([_cells(row) for row in rows], columns=BENCHMARK_COLUMNS)
    _write_csv(BenchmarkRow, rows, path)
    logger.info("wrote %s benchmark row(s) to %s", len(rows), path)
    return frame


def _older_columns(model: type[StrictModel]) -> list[str]:
    """The model's columns less the ones added since files were first written."""
    added = _ADDED_COLUMNS.get(model.__name__, frozenset())
    return [name for name in model.model_fields if name not in added]


def _read_csv(model: type[RowT], path: Path, *, legacy: bool = False) -> list[RowT]:
    """Every row validated through `model`; empty cells are None. The header
    must be the model's columns — or, with `legacy`, those columns less the
    ones added since (read as None); anything else is a ValueError naming
    both."""
    columns = list(model.model_fields)
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        older = _older_columns(model)
        if header != columns and not (legacy and header == older):
            raise ValueError(
                f"{path}: header {header} is not the {_KINDS[model]} columns "
                f"{columns}; a schema change edits the file deliberately"
            )
        return [
            model.model_validate(
                {
                    **dict.fromkeys(columns),
                    **{
                        name: (cell if cell != "" else None)
                        for name, cell in zip(header, cells, strict=True)
                    },
                }
            )
            for cells in reader
            if cells
        ]


def read_benchmarks_csv(path: Path) -> list[BenchmarkRow]:
    """Every row validated through the model; empty cells are None. A header
    that is not the model's columns is a ValueError naming both — except a
    file older than a later-added column (build_type), which reads with that
    column None."""
    return _read_csv(BenchmarkRow, path, legacy=True)


def read_test_benchmarks_csv(path: Path) -> list[TestBenchmarkRow]:
    """A tests root's (or the committed per-application) rows, validated
    through the model; a header that is not its columns is a ValueError —
    except a file older than a later-added column (build), which reads with
    that column None."""
    return _read_csv(TestBenchmarkRow, path, legacy=True)


class AppendResult(StrictModel):
    """What an append did."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    appended: int = Field(description="Rows added")
    skipped: int = Field(description="Rows already present (same key), left alone")


def _append_locked(
    model: type[RowT],
    key: Callable[[RowT], tuple[str, ...]],
    repo_csv: Path,
    rows: Sequence[RowT],
) -> AppendResult:
    """The append, under the file's lock. New rows are written after what
    is there (append mode: the lock is on this file, so it is never
    replaced, and existing lines are never rewritten); nothing is written
    when nothing is new. A batch may repeat a key: the first row of each is
    the one kept."""
    offered = len(rows)
    first_of: dict[tuple[str, ...], RowT] = {}
    for row in rows:
        first_of.setdefault(key(row), row)
    rows = list(first_of.values())
    if not repo_csv.is_file() or repo_csv.stat().st_size == 0:
        _write_csv(model, rows, repo_csv)
        logger.info("wrote %s %s row(s) to %s", len(rows), _KINDS[model], repo_csv)
        return AppendResult(appended=len(rows), skipped=offered - len(rows))
    if model.__name__ in _MIGRATED_ON_APPEND and _header(repo_csv) == _older_columns(
        model
    ):
        rows_before = _read_csv(model, repo_csv, legacy=True)
        _write_csv(model, rows_before, repo_csv)
        added = sorted(set(model.model_fields) - set(_older_columns(model)))
        logger.warning(
            "%s: added the %s column%s to its %s row(s) (empty in each)",
            repo_csv,
            ", ".join(added),
            "" if len(added) == 1 else "s",
            len(rows_before),
        )
    keys = {key(row) for row in _read_csv(model, repo_csv)}  # validates the header
    to_append = [row for row in rows if key(row) not in keys]
    result = AppendResult(appended=len(to_append), skipped=offered - len(to_append))
    if to_append:
        original = repo_csv.read_bytes()
        with open(repo_csv, "a", newline="") as f:
            if not original.endswith(b"\n"):
                f.write("\n")
            writer = csv.writer(f, lineterminator="\n")
            for row in to_append:
                writer.writerow(_cells(row))
        try:
            _read_csv(model, repo_csv)
        except (ValueError, OSError) as exc:
            os.truncate(repo_csv, len(original))
            raise ValueError(
                f"{repo_csv}: reload after the append failed ({exc}); restored"
            ) from exc
    logger.info(
        "%s: %s row(s) appended, %s skipped (already recorded)",
        repo_csv,
        result.appended,
        result.skipped,
    )
    return result


def _header(path: Path) -> list[str]:
    with open(path, newline="") as f:
        return next(csv.reader(f), [])


def _append(
    model: type[RowT],
    key: Callable[[RowT], tuple[str, ...]],
    repo_csv: Path,
    rows: Sequence[RowT],
) -> AppendResult:
    # The whole read-modify-write holds the file's exclusive lock (fslock),
    # so two writers ending at once append one after the other.
    repo_csv.parent.mkdir(parents=True, exist_ok=True)
    with locked(repo_csv):
        return _append_locked(model, key, repo_csv, rows)


def append_benchmarks(repo_csv: Path, rows: Sequence[BenchmarkRow]) -> AppendResult:
    """Grow the committed per-configuration file: created with the header
    when absent (or empty), a present (run_id, combo_id) is skipped; the
    rest are appended as lines. Existing lines are never touched,
    re-sorted, or re-formatted. The file is reloaded through the model
    afterwards and restored from the original text if that fails."""
    return _append(BenchmarkRow, lambda row: (row.run_id, row.combo_id), repo_csv, rows)


def append_test_benchmarks(
    csv_path: Path, rows: Sequence[TestBenchmarkRow]
) -> AppendResult:
    """Grow a test-benchmarks file — a tests root's (one row per evaluated
    run) or the committed per-application one — by the rows whose run_id is
    not there yet; the same append-only, reload-and-restore discipline as
    append_benchmarks."""
    return _append(TestBenchmarkRow, lambda row: (row.run_id,), csv_path, rows)


# ── building a row ───────────────────────────────────────────────────────────


def _driver_result(
    error: Exception | None,
    test_result: TestResult | None,
    accounting: SlurmAccounting | None,
) -> DriverResult:
    if isinstance(error, subprocess.TimeoutExpired):
        return "timeout"
    if accounting is not None and (accounting.state or "").startswith("TIMEOUT"):
        return "timeout"
    if error is None and test_result == "passed":
        return "passed"
    return "failed"


def _exit_code(
    error: Exception | None, accounting: SlurmAccounting | None
) -> int | None:
    if accounting is not None and accounting.exit_code is not None:
        return accounting.exit_code
    if isinstance(error, subprocess.CalledProcessError):
        return int(error.returncode)
    return None if error is not None else 0


def collect_row(
    *,
    recorded_at: datetime,
    run: RunManifest,
    suite: str,
    combo: str,
    combo_id: str,
    config_sha256: str,
    timeout_s: int,
    duration_s: float,
    error: Exception | None,
    combo_dir: Path,
    accounting: SlurmAccounting | None,
    test_results: Mapping[str, TestResult],
    baseline: tuple[str | None, str | None] | None,
) -> BenchmarkRow:
    """One combination's row from the session's pieces: the run manifest,
    the driver call's outcome and clock, the combination directory (the
    .out's tic/toc, the NetCDF written), the accounting when
    the platform has it, the tests' verdicts (by test function name), and
    the pinned baseline as (ulid or None for the placeholder, state)."""
    out_text = ""
    out_path = combo_dir / f"{combo_id}.out"
    if out_path.is_file():
        out_text = out_path.read_text(errors="replace")
    nc_paths = [path for path in combo_dir.glob("*.nc") if path.is_file()]
    launch = run.launch
    baseline_ulid, baseline_state = baseline if baseline is not None else (None, None)
    return BenchmarkRow(
        recorded_at=recorded_at,
        run_id=run.run_id,
        application=run.application,
        suite=suite,
        combo=combo,
        combo_id=combo_id,
        config_sha256=config_sha256,
        application_commit=run.application_commit,
        build_type=run.build_type,
        harness_version=run.harness_version,
        harness_commit=run.harness_commit,
        platform=run.platform,
        runtime=run.runtime,
        modulefile=run.modulefile,
        sbatch_args=launch.sbatch_args if launch else None,
        srun_args=launch.srun_args if launch else None,
        job_env=" ".join(f"{k}={v}" for k, v in launch.job_env.items())
        if launch
        else None,
        timeout_s=timeout_s,
        driver_result=_driver_result(
            error, test_results.get("test_driver_execution"), accounting
        ),
        exit_code=_exit_code(error, accounting),
        duration_s=duration_s,
        wall_s=parse_tic_toc(out_text),
        queue_s=accounting.queue_s if accounting else None,
        elapsed_s=accounting.elapsed_s if accounting else None,
        max_rss_kb=accounting.max_rss_kb if accounting else None,
        slurm_job_id=accounting.job_id if accounting else None,
        slurm_state=accounting.state if accounting else None,
        nc_files=len(nc_paths),
        output_bytes=sum(path.stat().st_size for path in nc_paths),
        baseline_ulid=baseline_ulid,
        baseline_state=baseline_state,
        comparison_result=test_results.get("test_baseline_comparison"),
    )
