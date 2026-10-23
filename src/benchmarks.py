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
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import ConfigDict, Field

from logs import get_logger
from models.base import StrictModel
from models.suite_config import RunManifest
from platforms import Platform, Runtime
from report import TestResult

logger = get_logger("benchmarks")

BENCHMARK_FILENAME = "benchmarks.csv"
DriverResult = Literal["passed", "failed", "timeout"]

_TIC = re.compile(r"^tic: (\S+)$", re.MULTILINE)
_TOC = re.compile(r"^toc: (\S+)$", re.MULTILINE)
_CLOCK_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_SACCT_FIELDS = [
    "JobID",
    "Submit",
    "Start",
    "End",
    "Elapsed",
    "MaxRSS",
    "State",
    "ExitCode",
]
_SACCT_RETRY_DELAY_S = 2.0
_RSS_UNITS = {"K": 1, "M": 1024, "G": 1024**2, "T": 1024**3}


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
    return int(float(value) * _RSS_UNITS.get(unit, 1))


def parse_sacct(output: str, job_id: str) -> SlurmAccounting | None:
    """`sacct -P -n -o <_SACCT_FIELDS>` output for one job: the row whose
    JobID is the job supplies the times, state and exit code; MaxRSS is the
    maximum over every row that reports one (the steps). None when the
    job's row is absent (accounting not yet written, or a wrong id)."""
    job_row: list[str] | None = None
    rss: list[int] = []
    for line in output.splitlines():
        parts = line.split("|")
        if len(parts) != len(_SACCT_FIELDS):
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


def slurm_accounting(job_id: str) -> SlurmAccounting | None:
    """Ask `sacct` for a finished job, retrying once after a short pause
    (accounting can trail completion by seconds). None, logged once, when
    sacct is missing, fails, or has no row for the job — never an error."""
    argv = ["sacct", "-j", job_id, "-P", "-n", "-o", ",".join(_SACCT_FIELDS)]
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
            time.sleep(_SACCT_RETRY_DELAY_S)
    logger.warning(
        "sacct has no record of job %s yet; accounting columns left empty "
        "(sacct -j %s by hand)",
        job_id,
        job_id,
    )
    return None


# ── the job's own clocks ─────────────────────────────────────────────────────


def parse_tic_toc(out_text: str) -> float | None:
    """toc - tic in seconds from the two lines the launch writes around the
    driver (`tic: <ISO UTC>` / `toc: <ISO UTC>`); None when either is absent
    or unreadable — a job killed by its time limit has tic and no toc."""
    tic, toc = _TIC.search(out_text), _TOC.search(out_text)
    if tic is None or toc is None:
        return None
    try:
        start = datetime.strptime(tic.group(1), _CLOCK_FORMAT).replace(tzinfo=UTC)
        end = datetime.strptime(toc.group(1), _CLOCK_FORMAT).replace(tzinfo=UTC)
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


def _cells(row: BenchmarkRow) -> list[str]:
    dumped = row.model_dump(mode="json")
    return [
        "" if dumped[column] is None else str(dumped[column])
        for column in BENCHMARK_COLUMNS
    ]


def write_benchmarks_csv(rows: Sequence[BenchmarkRow], path: Path) -> pd.DataFrame:
    """Write rows as a CSV with the model's columns (an empty list writes the
    header alone); None cells are empty. Returns the frame written."""
    frame = pd.DataFrame([_cells(row) for row in rows], columns=BENCHMARK_COLUMNS)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(BENCHMARK_COLUMNS)
        for row in rows:
            writer.writerow(_cells(row))
    logger.info("wrote %s benchmark row(s) to %s", len(rows), path)
    return frame


def _validate_header(path: Path, header: Sequence[str]) -> None:
    if list(header) != BENCHMARK_COLUMNS:
        raise ValueError(
            f"{path}: header {list(header)} is not the benchmark columns "
            f"{BENCHMARK_COLUMNS}; a schema change edits the file deliberately"
        )


def read_benchmarks_csv(path: Path) -> list[BenchmarkRow]:
    """Every row validated through the model; empty cells are None. A header
    that is not the model's columns is a ValueError naming both."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        _validate_header(path, header)
        return [
            BenchmarkRow.model_validate(
                {
                    column: (cell if cell != "" else None)
                    for column, cell in zip(BENCHMARK_COLUMNS, cells, strict=True)
                }
            )
            for cells in reader
            if cells
        ]


class AppendResult(StrictModel):
    """What append_benchmarks did."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    appended: int = Field(description="Rows added")
    skipped: int = Field(
        description="Rows already present (same run_id and combo_id), left alone"
    )


def append_benchmarks(repo_csv: Path, rows: Sequence[BenchmarkRow]) -> AppendResult:
    """Grow the committed per-configuration file: created with the header
    when absent; a present (run_id, combo_id) is skipped; the rest are
    appended as lines. Existing lines are never touched, re-sorted, or
    re-formatted. The file is reloaded through the model afterwards and
    restored from the original text if that fails."""
    if not repo_csv.is_file():
        repo_csv.parent.mkdir(parents=True, exist_ok=True)
        write_benchmarks_csv(rows, repo_csv)
        return AppendResult(appended=len(rows), skipped=0)
    original = repo_csv.read_text()
    existing = read_benchmarks_csv(repo_csv)  # validates the header
    keys = {(row.run_id, row.combo_id) for row in existing}
    appended = skipped = 0
    to_append: list[BenchmarkRow] = []
    for row in rows:
        if (row.run_id, row.combo_id) in keys:
            skipped += 1
            continue
        to_append.append(row)
        appended += 1
    text = original
    if text and not text.endswith("\n"):
        text += "\n"
    with open(repo_csv, "w", newline="") as f:
        f.write(text)
        writer = csv.writer(f)
        for row in to_append:
            writer.writerow(_cells(row))
    try:
        read_benchmarks_csv(repo_csv)
    except (ValueError, OSError) as exc:
        repo_csv.write_text(original)
        raise ValueError(
            f"{repo_csv}: reload after the append failed ({exc}); restored"
        ) from exc
    logger.info(
        "%s: %s row(s) appended, %s skipped (already recorded)",
        repo_csv,
        appended,
        skipped,
    )
    return AppendResult(appended=appended, skipped=skipped)


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
