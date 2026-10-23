"""benchmarks.py: the per-combination benchmark row, the job-output clocks,
Slurm accounting (sacct mocked), and the CSV files — per run, per
combination, and the append-only committed copy."""

from __future__ import annotations

import csv
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest_mock import MockerFixture

from benchmarks import (
    BENCHMARK_COLUMNS,
    BENCHMARK_FILENAME,
    AppendResult,
    BenchmarkRow,
    SlurmAccounting,
    append_benchmarks,
    collect_row,
    parse_sacct,
    parse_tic_toc,
    read_benchmarks_csv,
    slurm_accounting,
    write_benchmarks_csv,
)
from models.suite_config import LaunchRecord, RunManifest
from platforms import Platform, Runtime

_RECORDED = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
_RUN = RunManifest(
    run_id="01JRUNRUNRUNRUNRUNRUNRUNRU",
    application="cece",
    application_commit="36da92e0f02ec9aa05d4261596927527350509b8",
    harness_version="0.1.0-rc.8",
    harness_commit="abc123-dirty",
    platform=Platform.URSA,
    runtime=Runtime.SLURM,
    modulefile="cece_ursa.intelllvm",
    launch=LaunchRecord(
        sbatch_args="-A epic -q debug -p u1-compute -N 1 -n 1 -c 1 --mem=0",
        srun_args="--ntasks=1 --cpu-bind=cores",
        job_env={"OMP_NUM_THREADS": "1", "I_MPI_FABRICS": "shm"},
    ),
    suites=[],
)
_SACCT = (
    "123|2026-10-23T14:00:00|2026-10-23T14:05:30|2026-10-23T14:35:30|00:30:00||TIMEOUT|0:0\n"
    "123.batch|2026-10-23T14:05:30|2026-10-23T14:05:30|2026-10-23T14:35:30|00:30:00|2048K|CANCELLED|0:15\n"
    "123.extern|2026-10-23T14:05:30|2026-10-23T14:05:30|2026-10-23T14:35:30|00:30:00|1024K|COMPLETED|0:0\n"
    "123.0|2026-10-23T14:05:31|2026-10-23T14:05:31|2026-10-23T14:35:30|00:29:59|3.5G|CANCELLED|0:15\n"
)


def _row(**overrides: object) -> BenchmarkRow:
    values: dict[str, object] = {
        "recorded_at": _RECORDED,
        "run_id": _RUN.run_id,
        "application": "cece",
        "suite": "gcafs-anthro",
        "combo": "base",
        "combo_id": "01JCOMBOCOMBOCOMBOCOMBOCOM",
        "config_sha256": "a" * 64,
        "application_commit": _RUN.application_commit,
        "harness_version": _RUN.harness_version,
        "harness_commit": _RUN.harness_commit,
        "platform": Platform.URSA,
        "runtime": Runtime.SLURM,
        "modulefile": "cece_ursa.intelllvm",
        "sbatch_args": "-A epic -N 1 -n 1 -c 1 --mem=0",
        "srun_args": "--ntasks=1 --cpu-bind=cores",
        "job_env": "OMP_NUM_THREADS=1 I_MPI_FABRICS=shm",
        "timeout_s": 1800,
        "driver_result": "timeout",
        "exit_code": None,
        "duration_s": 2130.5,
        "wall_s": None,
        "queue_s": 330.0,
        "elapsed_s": 1800.0,
        "max_rss_kb": 3670016,
        "slurm_job_id": "123",
        "slurm_state": "TIMEOUT",
        "nc_files": 12,
        "output_bytes": 300_000_000,
        "baseline_ulid": None,
        "baseline_state": "unpublished",
        "comparison_result": "skipped",
        "published": False,
    }
    values.update(overrides)
    return BenchmarkRow.model_validate(values)


# ── the row and its columns ──────────────────────────────────────────────────


def test_columns_are_the_model_fields_in_order() -> None:
    assert list(BenchmarkRow.model_fields) == BENCHMARK_COLUMNS
    assert BENCHMARK_COLUMNS[:6] == [
        "recorded_at",
        "run_id",
        "application",
        "suite",
        "combo",
        "combo_id",
    ]
    assert BENCHMARK_COLUMNS[-3:] == ["baseline_state", "comparison_result", "published"]
    assert BENCHMARK_FILENAME == "benchmarks.csv"
    for field in BenchmarkRow.model_fields.values():
        assert field.description, field


def test_row_rejects_unknown_results_and_extra_keys() -> None:
    with pytest.raises(ValidationError, match="driver_result"):
        _row(driver_result="crashed")
    with pytest.raises(ValidationError, match="extra"):
        BenchmarkRow.model_validate({**_row().model_dump(), "extra": 1})


# ── tic/toc ──────────────────────────────────────────────────────────────────


def test_parse_tic_toc_reads_the_two_lines_in_seconds() -> None:
    text = "noise\ntic: 2026-10-23T14:05:31Z\n...driver output...\ntoc: 2026-10-23T14:35:01Z\n"
    assert parse_tic_toc(text) == 1770.0


@pytest.mark.parametrize(
    "text",
    ["", "tic: 2026-10-23T14:05:31Z\nkilled", "toc: 2026-10-23T14:35:01Z\n", "tic: garbage\ntoc: 2026-10-23T14:35:01Z\n"],
)
def test_parse_tic_toc_is_none_without_both_lines(text: str) -> None:
    assert parse_tic_toc(text) is None


# ── sacct ────────────────────────────────────────────────────────────────────


def test_parse_sacct_takes_the_job_row_and_the_largest_step_rss() -> None:
    accounting = parse_sacct(_SACCT, "123")
    assert accounting is not None
    assert accounting.job_id == "123"
    assert accounting.state == "TIMEOUT"
    assert accounting.exit_code == 0
    assert accounting.elapsed_s == 1800.0
    assert accounting.queue_s == 330.0  # 14:05:30 - 14:00:00
    assert accounting.max_rss_kb == int(3.5 * 1024 * 1024)  # the 3.5G step


def test_parse_sacct_handles_days_unknown_starts_and_missing_rows() -> None:
    text = "7|2026-10-23T14:00:00|Unknown|Unknown|1-02:03:04||CANCELLED by 1000|0:0\n"
    accounting = parse_sacct(text, "7")
    assert accounting is not None
    assert accounting.elapsed_s == 1 * 86400 + 2 * 3600 + 3 * 60 + 4
    assert accounting.queue_s is None
    assert accounting.max_rss_kb is None
    assert accounting.state == "CANCELLED by 1000"
    assert parse_sacct("", "7") is None
    assert parse_sacct(_SACCT, "999") is None


def test_parse_sacct_skips_malformed_lines_and_unreadable_fields() -> None:
    text = (
        "garbage line without pipes\n"
        "9|notatime|notatime||weird|lots|RUNNING|x\n"
    )
    accounting = parse_sacct(text, "9")
    assert accounting is not None
    assert (accounting.submit, accounting.start, accounting.elapsed_s, accounting.max_rss_kb) == (
        None,
        None,
        None,
        None,
    )
    assert accounting.exit_code is None


def test_slurm_accounting_queries_sacct_and_retries_once(mocker: MockerFixture) -> None:
    run = mocker.patch(
        "benchmarks.subprocess.run",
        side_effect=[
            subprocess.CompletedProcess(["sacct"], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(["sacct"], 0, stdout=_SACCT, stderr=""),
        ],
    )
    sleep = mocker.patch("benchmarks.time.sleep")
    accounting = slurm_accounting("123")
    assert accounting is not None
    assert accounting.state == "TIMEOUT"
    assert run.call_count == 2
    argv = run.call_args.args[0]
    assert argv[:3] == ["sacct", "-j", "123"]
    assert "-P" in argv
    assert "-n" in argv
    sleep.assert_called_once()


def test_slurm_accounting_is_none_when_sacct_is_missing_or_fails(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("benchmarks.time.sleep")
    mocker.patch("benchmarks.subprocess.run", side_effect=FileNotFoundError("sacct"))
    assert slurm_accounting("1") is None
    mocker.patch(
        "benchmarks.subprocess.run",
        return_value=subprocess.CompletedProcess(["sacct"], 1, stdout="", stderr="boom"),
    )
    assert slurm_accounting("1") is None
    mocker.patch(
        "benchmarks.subprocess.run",
        return_value=subprocess.CompletedProcess(["sacct"], 0, stdout="", stderr=""),
    )
    assert slurm_accounting("1") is None  # no rows, even after the retry
    assert any("sacct" in r.getMessage() for r in caplog.records)


# ── the files ────────────────────────────────────────────────────────────────


def test_write_and_read_round_trip_with_empty_cells_as_none(tmp_path: Path) -> None:
    rows = [_row(), _row(combo_id="01JOTHEROTHEROTHEROTHEROTH", wall_s=12.5, published=True)]
    path = tmp_path / BENCHMARK_FILENAME
    frame = write_benchmarks_csv(rows, path)
    assert list(frame.columns) == BENCHMARK_COLUMNS
    with open(path, newline="") as f:
        header = next(csv.reader(f))
    assert header == BENCHMARK_COLUMNS
    assert read_benchmarks_csv(path) == rows
    text = path.read_text()
    assert ",," in text  # None cells are empty (exit_code), never "None" or "nan"
    assert "None" not in text
    assert "nan" not in text


def test_write_an_empty_file_is_the_header_only(tmp_path: Path) -> None:
    path = tmp_path / BENCHMARK_FILENAME
    write_benchmarks_csv([], path)
    assert path.read_text().splitlines() == [",".join(BENCHMARK_COLUMNS)]
    assert read_benchmarks_csv(path) == []


def test_read_refuses_a_foreign_header(tmp_path: Path) -> None:
    path = tmp_path / BENCHMARK_FILENAME
    path.write_text("run_id,combo_id\nx,y\n")
    with pytest.raises(ValueError, match="header"):
        read_benchmarks_csv(path)


def test_append_creates_appends_skips_duplicates_and_flips_published(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = tmp_path / "cece" / "gcafs-anthro" / BENCHMARK_FILENAME
    first = _row()
    result = append_benchmarks(repo, [first])
    assert result == AppendResult(appended=1, skipped=0, updated=0)
    assert read_benchmarks_csv(repo) == [first]
    before = repo.read_text()
    # The same row again: skipped; a new run's row: appended as one line.
    second = _row(run_id="01JRUN2RUN2RUN2RUN2RUN2RUN")
    result = append_benchmarks(repo, [first, second])
    assert result == AppendResult(appended=1, skipped=1, updated=0)
    after = repo.read_text()
    assert after.startswith(before)
    assert len(after.splitlines()) == 3
    # Published later: the one in-place edit — that row's published cell.
    result = append_benchmarks(repo, [first.model_copy(update={"published": True})])
    assert result == AppendResult(appended=0, skipped=0, updated=1)
    rows = read_benchmarks_csv(repo)
    assert [r.published for r in rows] == [True, False]
    assert len(repo.read_text().splitlines()) == 3
    assert any("published" in r.getMessage() for r in caplog.records)


def test_append_to_a_file_without_a_trailing_newline(tmp_path: Path) -> None:
    repo = tmp_path / BENCHMARK_FILENAME
    write_benchmarks_csv([_row()], repo)
    repo.write_text(repo.read_text().rstrip("\n"))  # hand-edited: no final newline
    result = append_benchmarks(repo, [_row(run_id="01JRUN2RUN2RUN2RUN2RUN2RUN")])
    assert result.appended == 1
    assert len(read_benchmarks_csv(repo)) == 2


def test_append_refuses_a_foreign_header_and_restores_on_a_bad_reload(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    repo = tmp_path / BENCHMARK_FILENAME
    repo.write_text("run_id,combo_id\nx,y\n")
    with pytest.raises(ValueError, match="header"):
        append_benchmarks(repo, [_row()])
    assert repo.read_text() == "run_id,combo_id\nx,y\n"
    write_benchmarks_csv([_row()], repo)
    original = repo.read_text()
    mocker.patch("benchmarks.read_benchmarks_csv", side_effect=[[_row()], ValueError("bad")])
    with pytest.raises(ValueError, match="restored"):
        append_benchmarks(repo, [_row(run_id="01JRUN2RUN2RUN2RUN2RUN2RUN")])
    assert repo.read_text() == original


# ── collect_row ──────────────────────────────────────────────────────────────


def _combo_dir(tmp_path: Path, *, published: bool, toc: bool = True) -> Path:
    combo_dir = tmp_path / "01JCOMBOCOMBOCOMBOCOMBOCOM"
    combo_dir.mkdir()
    for hour in (12, 13):
        (combo_dir / f"cece_20211220_{hour}00.nc").write_bytes(b"x" * 10)
    (combo_dir / "sub").mkdir()
    (combo_dir / "sub" / "not-counted.nc").write_bytes(b"y")
    out = "tic: 2026-10-23T14:05:31Z\nrunning\n" + ("toc: 2026-10-23T14:35:01Z\n" if toc else "")
    (combo_dir / "01JCOMBOCOMBOCOMBOCOMBOCOM.out").write_text(out)
    if published:
        (combo_dir / "baseline.yaml").write_text("ulid: x\n")
    return combo_dir


def test_collect_row_joins_run_outcome_files_accounting_and_baseline(tmp_path: Path) -> None:
    combo_dir = _combo_dir(tmp_path, published=True)
    row = collect_row(
        recorded_at=_RECORDED,
        run=_RUN,
        suite="gcafs-anthro",
        combo="base",
        combo_id="01JCOMBOCOMBOCOMBOCOMBOCOM",
        config_sha256="a" * 64,
        timeout_s=1800,
        duration_s=2130.5,
        error=None,
        combo_dir=combo_dir,
        accounting=parse_sacct(_SACCT.replace("TIMEOUT", "COMPLETED"), "123"),
        test_results={"test_driver_execution": "passed", "test_baseline_comparison": "passed"},
        baseline=("01JBASEBASEBASEBASEBASEBAS", "synced"),
    )
    assert row.driver_result == "passed"
    assert row.exit_code == 0
    assert row.wall_s == 1770.0
    assert (row.queue_s, row.elapsed_s, row.max_rss_kb) == (330.0, 1800.0, int(3.5 * 1024 * 1024))
    assert (row.slurm_job_id, row.slurm_state) == ("123", "COMPLETED")
    assert (row.nc_files, row.output_bytes) == (2, 20)
    assert (row.baseline_ulid, row.baseline_state, row.comparison_result) == (
        "01JBASEBASEBASEBASEBASEBAS",
        "synced",
        "passed",
    )
    assert row.published is True
    assert row.job_env == "OMP_NUM_THREADS=1 I_MPI_FABRICS=shm"
    assert row.sbatch_args == _RUN.launch.sbatch_args if _RUN.launch else False


def test_collect_row_timeout_and_failure_verdicts(tmp_path: Path) -> None:
    combo_dir = _combo_dir(tmp_path, published=False, toc=False)
    common = {
        "recorded_at": _RECORDED,
        "run": _RUN,
        "suite": "gcafs-anthro",
        "combo": "base",
        "combo_id": "01JCOMBOCOMBOCOMBOCOMBOCOM",
        "config_sha256": "a" * 64,
        "timeout_s": 1800,
        "duration_s": 10.0,
        "combo_dir": combo_dir,
        "test_results": {"test_driver_execution": "failed"},
        "baseline": None,
    }
    # Slurm killed it: the state says TIMEOUT even though the harness saw a nonzero exit.
    row = collect_row(
        error=subprocess.CalledProcessError(1, ["sbatch"]),
        accounting=parse_sacct(_SACCT, "123"),
        **common,  # type: ignore[arg-type]
    )
    assert row.driver_result == "timeout"
    assert row.wall_s is None  # no toc
    assert row.exit_code == 0  # sacct's ExitCode for the job
    assert (row.baseline_ulid, row.baseline_state, row.comparison_result) == (None, None, None)
    assert row.published is False
    # The harness's own outer timeout, no accounting (docker/native).
    row = collect_row(
        error=subprocess.TimeoutExpired(["docker"], 10),
        accounting=None,
        **common,  # type: ignore[arg-type]
    )
    assert row.driver_result == "timeout"
    assert (row.queue_s, row.elapsed_s, row.max_rss_kb, row.slurm_job_id) == (None, None, None, None)
    # A plain failure with an exit code.
    row = collect_row(
        error=subprocess.CalledProcessError(3, ["docker"]),
        accounting=None,
        **common,  # type: ignore[arg-type]
    )
    assert (row.driver_result, row.exit_code) == ("failed", 3)


def test_collect_row_without_a_launch_record(tmp_path: Path) -> None:
    combo_dir = _combo_dir(tmp_path, published=False)
    run = _RUN.model_copy(update={"launch": None, "runtime": Runtime.DOCKER, "platform": Platform.LOCAL})
    row = collect_row(
        recorded_at=_RECORDED,
        run=run,
        suite="simple-maccity",
        combo="MACCITY.map-consd",
        combo_id="01JCOMBOCOMBOCOMBOCOMBOCOM",
        config_sha256="b" * 64,
        timeout_s=10,
        duration_s=3.2,
        error=None,
        combo_dir=combo_dir,
        accounting=None,
        test_results={"test_driver_execution": "passed", "test_baseline_comparison": "skipped"},
        baseline=(None, "unpublished"),
    )
    assert (row.sbatch_args, row.srun_args, row.job_env) == (None, None, None)
    assert row.runtime is Runtime.DOCKER
    assert (row.baseline_ulid, row.baseline_state, row.comparison_result) == (None, "unpublished", "skipped")


def test_accounting_model_is_frozen() -> None:
    accounting = SlurmAccounting(job_id="1", submit=None, start=None, end=None, elapsed_s=None, max_rss_kb=None, state=None, exit_code=None, queue_s=None)
    with pytest.raises(ValidationError):
        accounting.state = "x"  # type: ignore[misc]
