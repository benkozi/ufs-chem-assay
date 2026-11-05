"""The job ledger (jobs.yaml) and the asynchronous Slurm wait: submission
parsing, the one-call queue poll, the deadline, the accounting verdict, and
cancellation. Every Slurm command is mocked at subprocess.run."""

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from benchmarks import SlurmAccounting
from jobs import (
    QUEUED_UNKNOWN,
    JobLedger,
    SlurmAccountingMissing,
    SlurmJobTimeout,
    SlurmSubmissionError,
    SubmittedJob,
    cancel_jobs,
    ledger_path,
    queue_states,
    read_ledger,
    submit_job,
    submitted_by,
    wait_for_job,
    write_ledger,
)
from models.conventions import CONVENTIONS

_T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def _job(job_id: str = "123", **overrides: object) -> SubmittedJob:
    values: dict[str, object] = {
        "suite": "simple-maccity",
        "combo": "mapalgo-conserve",
        "combo_id": "01JC0000000000000000000000",
        "job_id": job_id,
        "script": Path("/o/x/x.sbatch"),
        "out_path": Path("/o/x/x.out"),
        "timeout_s": 300,
        "queue_wait_s": 60,
        "submitted_at": _T0,
    }
    values.update(overrides)
    return SubmittedJob.model_validate(values)


def _accounting(state: str = "COMPLETED", exit_code: int | None = 0) -> SlurmAccounting:
    return SlurmAccounting(
        job_id="123",
        submit=_T0,
        start=_T0,
        end=_T0 + timedelta(seconds=10),
        elapsed_s=10.0,
        queue_s=0.0,
        max_rss_kb=None,
        state=state,
        exit_code=exit_code,
    )


def _completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["x"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# ── the ledger ───────────────────────────────────────────────────────────────


def test_ledger_round_trips_through_yaml_in_model_order(tmp_path: Path) -> None:
    ledger = JobLedger(
        run_id="01JRUN", application="cece", submitted_by="me@login1", jobs=[_job()]
    )
    path = write_ledger(ledger, tmp_path)
    assert path == ledger_path(tmp_path) == tmp_path / CONVENTIONS.job_ledger
    assert CONVENTIONS.job_ledger == "jobs.yaml"
    raw = yaml.safe_load(path.read_text())
    assert list(raw) == [
        "run_id",
        "application",
        "submitted_by",
        "jobs",
        "evaluated_at",
        "exit_status",
    ]
    assert raw["evaluated_at"] is None
    assert raw["jobs"][0]["submitted_at"] == "2026-11-04T12:00:00Z"
    assert read_ledger(tmp_path) == ledger
    assert not list(tmp_path.glob(".*.tmp"))  # the temporary file was renamed

    done = ledger.model_copy(update={"evaluated_at": _T0, "exit_status": 1})
    write_ledger(done, tmp_path)
    assert read_ledger(tmp_path).exit_status == 1
    assert read_ledger(tmp_path).evaluated_at == _T0


def test_read_ledger_errors_name_the_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=f"{tmp_path} has no jobs.yaml"):
        read_ledger(tmp_path)
    ledger_path(tmp_path).write_text("run_id: x\nbogus: 1\n")
    with pytest.raises(ValueError, match="not a job ledger"):
        read_ledger(tmp_path)
    ledger_path(tmp_path).write_text("::: not yaml")
    with pytest.raises(ValueError, match="not a job ledger"):
        read_ledger(tmp_path)


def test_job_deadline_and_submitted_by() -> None:
    assert _job().deadline == _T0 + timedelta(seconds=360)
    assert "@" in submitted_by()
    with pytest.raises(ValueError, match="greater than 0"):
        _job(timeout_s=0)


# ── sbatch ───────────────────────────────────────────────────────────────────


def test_submit_job_parses_the_parsable_id(mocker: MockerFixture) -> None:
    run = mocker.patch("jobs.subprocess.run", return_value=_completed("123;cluster\n"))
    assert submit_job(Path("/o/x.sbatch")) == "123"
    assert run.call_args.args[0] == ["sbatch", "--parsable", "/o/x.sbatch"]
    run.return_value = _completed("456\n")
    assert submit_job(Path("/o/x.sbatch")) == "456"


def test_submit_job_failure_carries_sbatch_output(mocker: MockerFixture) -> None:
    mocker.patch(
        "jobs.subprocess.run",
        return_value=_completed(
            "", returncode=1, stderr="sbatch: error: invalid account"
        ),
    )
    with pytest.raises(SlurmSubmissionError) as excinfo:
        submit_job(Path("/o/x.sbatch"))
    assert excinfo.value.returncode == 1
    assert "invalid account" in excinfo.value.stderr
    assert isinstance(excinfo.value, subprocess.CalledProcessError)
    # Exit 0 but no id is just as unusable.
    mocker.patch("jobs.subprocess.run", return_value=_completed("\n"))
    with pytest.raises(SlurmSubmissionError) as excinfo:
        submit_job(Path("/o/x.sbatch"))
    assert excinfo.value.returncode == 1


# ── squeue ───────────────────────────────────────────────────────────────────


def test_queue_states_is_one_call_and_parses_states(mocker: MockerFixture) -> None:
    run = mocker.patch(
        "jobs.subprocess.run", return_value=_completed("123 PENDING\n456 RUNNING\n")
    )
    assert queue_states(["123", "456", "789"]) == {"123": "PENDING", "456": "RUNNING"}
    assert run.call_count == 1
    assert run.call_args.args[0] == ["squeue", "-h", "-j", "123,456,789", "-o", "%i %T"]


def test_queue_states_empty_input_runs_nothing(mocker: MockerFixture) -> None:
    run = mocker.patch("jobs.subprocess.run")
    assert queue_states([]) == {}
    run.assert_not_called()


def test_queue_states_all_gone_is_not_an_error(mocker: MockerFixture) -> None:
    mocker.patch(
        "jobs.subprocess.run",
        return_value=_completed(
            "", returncode=1, stderr="slurm_load_jobs error: Invalid job id"
        ),
    )
    assert queue_states(["123"]) == {}


def test_queue_states_unavailable_keeps_every_job_queued(mocker: MockerFixture) -> None:
    mocker.patch("jobs.subprocess.run", side_effect=OSError("no squeue"))
    assert queue_states(["1", "2"]) == {"1": "UNKNOWN", "2": "UNKNOWN"}
    mocker.patch(
        "jobs.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="squeue", timeout=60),
    )
    assert queue_states(["1"]) == {"1": QUEUED_UNKNOWN}


# ── the wait ─────────────────────────────────────────────────────────────────


def test_wait_for_job_polls_until_the_job_leaves_the_queue(
    mocker: MockerFixture,
) -> None:
    states = mocker.patch(
        "jobs.queue_states",
        side_effect=[{"123": "PENDING"}, {"123": "RUNNING"}, {}],
    )
    accounting = _accounting()
    sacct = mocker.patch("jobs.slurm_accounting", return_value=accounting)
    sleeps: list[float] = []
    clock = iter([_T0, _T0 + timedelta(seconds=30)])
    result = wait_for_job(
        _job(), poll_s=30, now=lambda: next(clock), sleep=sleeps.append
    )
    assert result is accounting
    assert sleeps == [30, 30]
    assert states.call_count == 3
    sacct.assert_called_once_with("123")


def test_wait_for_job_past_the_deadline_cancels_and_times_out(
    mocker: MockerFixture,
) -> None:
    mocker.patch("jobs.queue_states", return_value={"123": "PENDING"})
    run = mocker.patch("jobs.subprocess.run")
    sacct = mocker.patch("jobs.slurm_accounting")
    late = _T0 + timedelta(seconds=360)  # exactly timeout + queue allowance
    with pytest.raises(SlurmJobTimeout) as excinfo:
        wait_for_job(_job(), poll_s=5, now=lambda: late, sleep=lambda _s: None)
    assert run.call_args.args[0] == ["scancel", "123"]
    assert excinfo.value.job_id == "123"
    assert excinfo.value.timeout == 360
    assert excinfo.value.cmd == ["sbatch", "--parsable", "/o/x/x.sbatch"]
    assert isinstance(excinfo.value, subprocess.TimeoutExpired)
    sacct.assert_not_called()


def test_wait_for_job_without_accounting_is_an_error(mocker: MockerFixture) -> None:
    mocker.patch("jobs.queue_states", return_value={})
    mocker.patch("jobs.slurm_accounting", return_value=None)
    with pytest.raises(SlurmAccountingMissing, match="sacct -j 123") as excinfo:
        wait_for_job(_job(), poll_s=1, sleep=lambda _s: None)
    assert excinfo.value.job_id == "123"


def test_wait_for_job_default_clock_and_sleep(mocker: MockerFixture) -> None:
    # The defaults are the real clock and time.sleep; one queued poll then
    # gone, with a job submitted far in the future so the deadline never hits.
    mocker.patch("jobs.queue_states", side_effect=[{"123": "RUNNING"}, {}])
    mocker.patch("jobs.slurm_accounting", return_value=_accounting())
    sleep = mocker.patch("jobs.time.sleep")
    wait_for_job(_job(submitted_at=datetime.now(UTC)), poll_s=7)
    sleep.assert_called_once_with(7)


# ── scancel ──────────────────────────────────────────────────────────────────


def test_cancel_jobs_cancels_only_the_queued_ones(mocker: MockerFixture) -> None:
    ledger = JobLedger(
        run_id="r",
        application="cece",
        submitted_by="me@h",
        jobs=[_job("1"), _job("2"), _job("3")],
    )
    states = mocker.patch(
        "jobs.queue_states", return_value={"1": "PENDING", "3": "RUNNING"}
    )
    run = mocker.patch("jobs.subprocess.run")
    assert cancel_jobs(ledger) == ["1", "3"]
    assert list(states.call_args.args[0]) == ["1", "2", "3"]
    run.assert_called_once()
    assert run.call_args.args[0] == ["scancel", "1", "3"]
    run.reset_mock()
    assert cancel_jobs(ledger, dry_run=True) == ["1", "3"]
    run.assert_not_called()
    states.return_value = {}
    assert cancel_jobs(ledger) == []
    run.assert_not_called()
