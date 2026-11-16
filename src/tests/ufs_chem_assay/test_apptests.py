"""An application's own test runs (apptests.py): the junit report read into
counts, the submission of the test job with its guard, and the evaluation
that turns a finished run into a verdict and one benchmark row. Slurm
(sbatch, squeue, sacct, scancel) is mocked; the ledgers and reports are real
files."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

import apptests
from apptests import TestsStillQueued, evaluate, parse_junit, record_local, submit
from benchmarks import SlurmAccounting, read_test_benchmarks_csv
from jobs import (
    SlurmSubmissionError,
    TestCounts,
    TestJob,
    TestLedger,
    TestProvenance,
    path_of_test_ledger,
    read_test_ledger,
    write_test_ledger,
)
from logs import LOGGER_NAME
from models.conventions import CONVENTIONS
from platforms import Platform, Runtime

_T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
_PROVENANCE = TestProvenance(
    platform=Platform.URSA,
    runtime=Runtime.SLURM,
    modulefile="ufs_ursa.oneapi",
    build_type="Debug",
    cmake_args="-DCMAKE_BUILD_TYPE=Debug -DENABLE_ESMF=ON",
    application_commit="36da92e0f02ec9aa05d4261596927527350509b8",
    harness_version="0.1.0-rc.11",
    harness_commit="abc123",
)
_JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="Linux-c++" BuildName="Linux-c++" tests="4" failures="1"
           disabled="0" skipped="1" hostname="u01" time="4" timestamp="2026-11-16T12:01:00">
  <testcase name="CeceCore.Init" classname="CeceCore.Init" time="1.25" status="run">
    <system-out>ok</system-out>
  </testcase>
  <testcase name="CeceAxis.Decode" classname="CeceAxis.Decode" time="0.5" status="fail">
    <failure message=""/>
    <system-out>expected 1.0, got 0.99</system-out>
  </testcase>
  <testcase name="CeceMpi.Four" classname="CeceMpi.Four" time="0" status="notrun">
    <skipped message="SKIP_RETURN_CODE=77"/>
  </testcase>
  <testcase name="CeceIo.Write" classname="CeceIo.Write" time="2" status="run"/>
</testsuite>
"""
_PASSING = _JUNIT.replace(
    '<failure message=""/>', ""
).replace('status="fail"', 'status="run"')


def _accounting(
    job_id: str = "4242", state: str = "COMPLETED", exit_code: int = 0
) -> SlurmAccounting:
    return SlurmAccounting(
        job_id=job_id,
        submit=datetime(2026, 11, 16, 12, 0),
        start=datetime(2026, 11, 16, 12, 0, 40),
        end=datetime(2026, 11, 16, 12, 6),
        elapsed_s=320.0,
        queue_s=40.0,
        max_rss_kb=2_000_000,
        state=state,
        exit_code=exit_code,
    )


@pytest.fixture
def slurm(mocker: MockerFixture) -> MockerFixture:
    """Slurm as apptests sees it: sbatch returns 4242, nothing is queued,
    sacct says COMPLETED 0, scancel is recorded."""
    mocker.patch("apptests.submit_job", return_value="4242")
    mocker.patch("apptests.queue_states", return_value={})
    mocker.patch("apptests.slurm_accounting", return_value=_accounting())
    mocker.patch("apptests.scancel")
    return mocker


def _submit(root: Path, **overrides: object) -> TestLedger:
    values: dict[str, object] = {
        "script": root.parent / "04-test-cece.sbatch",
        "application": "cece",
        "toolchain": None,
        "provenance": _PROVENANCE,
        "timeout_s": 300,
        "queue_wait_s": 3600,
        "now": lambda: _T0,
    }
    values.update(overrides)
    return submit(root, **values)  # type: ignore[arg-type]


def _finish(root: Path, junit: str | None = _PASSING, job_id: str = "4242") -> None:
    """What a finished job leaves in the tests root."""
    (root / f"tests-{job_id}.out").write_text(
        "tic: 2026-11-16T12:00:40Z\n100% tests passed\ntoc: 2026-11-16T12:05:52Z\n"
    )
    if junit is not None:
        (root / f"tests-{job_id}.xml").write_text(junit)


# ── the junit report ─────────────────────────────────────────────────────────


def test_parse_junit_counts_and_names_the_failures(tmp_path: Path) -> None:
    path = tmp_path / "tests.xml"
    path.write_text(_JUNIT)
    counts, failed = parse_junit(path)
    assert counts == TestCounts(total=4, passed=2, failed=1, skipped=1, ctest_s=3.75)
    assert failed == ["CeceAxis.Decode"]


def test_parse_junit_refuses_what_is_not_a_report(tmp_path: Path) -> None:
    path = tmp_path / "tests.xml"
    path.write_text("<testsuite><testcase name='a'")  # a job killed mid-write
    with pytest.raises(ValueError, match=r"tests\.xml: not a junit report"):
        parse_junit(path)
    path.write_text("<html/>")
    with pytest.raises(ValueError, match="not a junit report"):
        parse_junit(path)


def test_parse_junit_reads_errors_and_disabled_tests(tmp_path: Path) -> None:
    path = tmp_path / "tests.xml"
    path.write_text(
        "<testsuites><testsuite>"
        '<testcase name="a" time="1"><error message="segv"/></testcase>'
        '<testcase name="b" status="disabled"/>'
        '<testcase name="c" time="not-a-number"/>'
        "</testsuite></testsuites>"
    )
    counts, failed = parse_junit(path)
    assert counts == TestCounts(total=3, passed=1, failed=1, skipped=1, ctest_s=1.0)
    assert failed == ["a"]


# ── the ledger ───────────────────────────────────────────────────────────────


def test_test_ledger_round_trips_and_names_its_file(tmp_path: Path) -> None:
    assert CONVENTIONS.test_ledger == "tests.yaml"
    assert path_of_test_ledger(tmp_path) == tmp_path / "tests.yaml"
    with pytest.raises(ValueError, match="no tests.yaml: no test run recorded"):
        read_test_ledger(tmp_path)
    ledger = TestLedger(
        run_id="01JTESTRUNTESTRUNTESTRUNTE",
        application="cece",
        submitted_by="me@ufe01",
        provenance=_PROVENANCE,
        job=TestJob(
            job_id="4242",
            script=tmp_path / "x.sbatch",
            timeout_s=300,
            queue_wait_s=60,
            submitted_at=_T0,
        ),
        out_path=tmp_path / "tests-4242.out",
        junit_path=tmp_path / "tests-4242.xml",
    )
    assert ledger.job is not None
    assert ledger.job.deadline == _T0 + timedelta(seconds=360)
    write_test_ledger(ledger, tmp_path)
    assert read_test_ledger(tmp_path) == ledger
    assert not list(tmp_path.glob(".*.tmp"))  # written through a rename
    path_of_test_ledger(tmp_path).write_text("jobs: []\n")
    with pytest.raises(ValueError, match="not a test ledger"):
        read_test_ledger(tmp_path)
    for model in (TestLedger, TestJob, TestProvenance, TestCounts):
        for field in model.model_fields.values():
            assert field.description, field


# ── submission ───────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("slurm")
def test_submit_creates_the_root_submits_and_records_the_job(tmp_path: Path) -> None:
    root = tmp_path / "tests" / "cece"  # Slurm needs the --output directory
    ledger = _submit(root)
    assert root.is_dir()
    apptests.submit_job.assert_called_once_with(tmp_path / "tests/04-test-cece.sbatch")  # type: ignore[attr-defined]
    assert read_test_ledger(root) == ledger
    assert ledger.job == TestJob(
        job_id="4242",
        script=tmp_path / "tests/04-test-cece.sbatch",
        timeout_s=300,
        queue_wait_s=3600,
        submitted_at=_T0,
    )
    # The job's files carry its id: a resubmission overwrites nothing.
    assert ledger.out_path == root / "tests-4242.out"
    assert ledger.junit_path == root / "tests-4242.xml"
    assert len(ledger.run_id) == 26
    assert "@" in ledger.submitted_by
    assert ledger.provenance == _PROVENANCE
    assert (ledger.evaluated_at, ledger.exit_status, ledger.tests) == (None, None, None)


def test_submit_refuses_while_a_job_is_queued(
    tmp_path: Path, slurm: MockerFixture
) -> None:
    first = _submit(tmp_path)
    slurm.patch("apptests.queue_states", return_value={"4242": "PENDING"})
    apptests.submit_job.reset_mock()  # type: ignore[attr-defined]
    with pytest.raises(TestsStillQueued) as refused:
        _submit(tmp_path)
    message = str(refused.value)
    assert "job 4242" in message
    assert f"ufs-chem-assay cancel --tests-root {tmp_path}" in message
    assert "--resubmit" in message
    apptests.submit_job.assert_not_called()  # type: ignore[attr-defined]
    assert read_test_ledger(tmp_path) == first


def test_resubmit_cancels_the_queued_job_first(
    tmp_path: Path, slurm: MockerFixture
) -> None:
    first = _submit(tmp_path)
    slurm.patch("apptests.queue_states", return_value={"4242": "RUNNING"})
    slurm.patch("apptests.submit_job", return_value="4300")
    second = _submit(tmp_path, resubmit=True)
    apptests.scancel.assert_called_once_with(["4242"])  # type: ignore[attr-defined]
    assert second.job is not None
    assert second.job.job_id == "4300"
    assert second.run_id != first.run_id
    assert second.out_path == tmp_path / "tests-4300.out"
    # The cancelled run was never evaluated: no row for it.
    assert not (tmp_path / CONVENTIONS.benchmarks).exists()


def test_submit_evaluates_a_finished_unevaluated_run_first(
    tmp_path: Path, slurm: MockerFixture
) -> None:
    # The job left the queue and nobody ran `watch`: its row is not lost.
    first = _submit(tmp_path)
    _finish(tmp_path)
    slurm.patch("apptests.submit_job", return_value="4300")
    second = _submit(tmp_path)
    (row,) = read_test_benchmarks_csv(tmp_path / CONVENTIONS.benchmarks)
    assert row.run_id == first.run_id
    assert row.slurm_job_id == "4242"
    assert second.job is not None
    assert second.job.job_id == "4300"
    assert second.evaluated_at is None


@pytest.mark.usefixtures("slurm")
def test_submit_replaces_an_evaluated_run(tmp_path: Path) -> None:
    _submit(tmp_path)
    _finish(tmp_path)
    evaluate(tmp_path, now=lambda: _T0)
    second = _submit(tmp_path)
    assert second.evaluated_at is None
    assert len(read_test_benchmarks_csv(tmp_path / CONVENTIONS.benchmarks)) == 1


def test_a_refused_sbatch_leaves_no_ledger(tmp_path: Path, slurm: MockerFixture) -> None:
    slurm.patch(
        "apptests.submit_job",
        side_effect=SlurmSubmissionError(1, ["sbatch"], output="", stderr="bad QOS"),
    )
    with pytest.raises(SlurmSubmissionError):
        _submit(tmp_path / "root")
    assert not path_of_test_ledger(tmp_path / "root").exists()


# ── evaluation ───────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("slurm")
def test_evaluate_records_the_verdict_and_one_benchmark_row(tmp_path: Path) -> None:
    submitted = _submit(tmp_path)
    _finish(tmp_path)
    later = _T0 + timedelta(minutes=7)
    ledger = evaluate(tmp_path, now=lambda: later)
    assert ledger == read_test_ledger(tmp_path)
    assert ledger.evaluated_at == later
    assert ledger.exit_status == 0
    assert ledger.tests == TestCounts(total=4, passed=3, failed=0, skipped=1, ctest_s=3.75)
    (row,) = read_test_benchmarks_csv(tmp_path / CONVENTIONS.benchmarks)
    assert row.model_dump() == {
        "recorded_at": later,
        "run_id": submitted.run_id,
        "application": "cece",
        "toolchain": None,
        "platform": Platform.URSA,
        "runtime": Runtime.SLURM,
        "modulefile": "ufs_ursa.oneapi",
        "build_type": "Debug",
        "cmake_args": "-DCMAKE_BUILD_TYPE=Debug -DENABLE_ESMF=ON",
        "application_commit": "36da92e0f02ec9aa05d4261596927527350509b8",
        "harness_version": "0.1.0-rc.11",
        "harness_commit": "abc123",
        "slurm_job_id": "4242",
        "slurm_state": "COMPLETED",
        "exit_code": 0,
        "wall_s": 312.0,  # toc - tic, from the job's own output
        "queue_s": 40.0,
        "elapsed_s": 320.0,
        "max_rss_kb": 2_000_000,
        "tests_total": 4,
        "tests_passed": 3,
        "tests_failed": 0,
        "tests_skipped": 1,
        "ctest_s": 3.75,
        "tests_root": tmp_path,
    }
    # Evaluating again changes nothing: one run, one row.
    assert evaluate(tmp_path, now=lambda: later + timedelta(hours=1)) == ledger
    assert len(read_test_benchmarks_csv(tmp_path / CONVENTIONS.benchmarks)) == 1


@pytest.mark.parametrize(
    ("state", "code", "junit", "expected", "total"),
    [
        ("COMPLETED", 0, _JUNIT, 1, 4),  # a failure the exit code did not carry
        ("FAILED", 8, _JUNIT, 8, 4),  # ctest's own exit: 8 = tests failed
        ("TIMEOUT", 0, None, 1, None),  # killed by the limit: no report
        ("CANCELLED by 1234", 0, None, 1, None),
        ("COMPLETED", 0, None, 0, None),  # no report to read: the exit decides
    ],
)
def test_evaluate_judges_by_the_accounting_and_the_report(
    tmp_path: Path,
    slurm: MockerFixture,
    state: str,
    code: int,
    junit: str | None,
    expected: int,
    total: int | None,
) -> None:
    _submit(tmp_path)
    _finish(tmp_path, junit)
    slurm.patch(
        "apptests.slurm_accounting", return_value=_accounting(state=state, exit_code=code)
    )
    ledger = evaluate(tmp_path)
    assert ledger.exit_status == expected
    (row,) = read_test_benchmarks_csv(tmp_path / CONVENTIONS.benchmarks)
    assert row.exit_code == expected
    assert row.slurm_state == state
    assert row.tests_total == total


@pytest.mark.parametrize(("junit", "expected"), [(_PASSING, 0), (_JUNIT, 1), (None, 1)])
def test_evaluate_without_accounting_judges_by_the_report(
    tmp_path: Path,
    slurm: MockerFixture,
    caplog: pytest.LogCaptureFixture,
    junit: str | None,
    expected: int,
) -> None:
    # A machine whose sacct does not answer: the report is the evidence.
    _submit(tmp_path)
    _finish(tmp_path, junit)
    slurm.patch("apptests.slurm_accounting", return_value=None)
    with caplog.at_level(logging.WARNING, logger=f"{LOGGER_NAME}.apptests"):
        ledger = evaluate(tmp_path)
    assert ledger.exit_status == expected
    assert "no accounting for job 4242" in caplog.text
    (row,) = read_test_benchmarks_csv(tmp_path / CONVENTIONS.benchmarks)
    assert (row.slurm_state, row.elapsed_s, row.max_rss_kb) == (None, None, None)
    assert row.slurm_job_id == "4242"


def test_evaluate_refuses_a_job_still_in_the_queue(
    tmp_path: Path, slurm: MockerFixture
) -> None:
    _submit(tmp_path)
    slurm.patch("apptests.queue_states", return_value={"4242": "RUNNING"})
    with pytest.raises(TestsStillQueued, match="job 4242 is still in the queue"):
        evaluate(tmp_path)
    assert read_test_ledger(tmp_path).evaluated_at is None


@pytest.mark.usefixtures("slurm")
def test_evaluate_survives_an_unreadable_report(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _submit(tmp_path)
    _finish(tmp_path, "<testsuite><testcase")
    with caplog.at_level(logging.WARNING, logger=f"{LOGGER_NAME}.apptests"):
        ledger = evaluate(tmp_path)
    assert ledger.tests is None
    assert ledger.exit_status == 0  # sacct: COMPLETED 0
    assert "not a junit report" in caplog.text


def test_evaluate_needs_a_ledger(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no test run recorded"):
        evaluate(tmp_path)


# ── runs in place (native, docker) ───────────────────────────────────────────


def test_a_run_in_place_is_recorded_with_its_exit_status(tmp_path: Path) -> None:
    root = tmp_path / "tests" / "cece"
    root.mkdir(parents=True)
    (root / "tests.out").write_text(
        "tic: 2026-11-16T12:00:00Z\n1 test failed\ntoc: 2026-11-16T12:00:30Z\n"
    )
    (root / "tests.xml").write_text(_JUNIT)
    local = _PROVENANCE.model_copy(
        update={"platform": Platform.LOCAL, "runtime": Runtime.DOCKER, "modulefile": None}
    )
    started = record_local(root, application="cece", toolchain=None, provenance=local)
    assert started.job is None
    assert (started.out_path, started.junit_path) == (root / "tests.out", root / "tests.xml")
    with pytest.raises(ValueError, match="needs the test command's exit status"):
        evaluate(root)
    ledger = evaluate(root, exit_status=8, now=lambda: _T0)
    assert ledger.exit_status == 8
    assert ledger.tests is not None
    assert ledger.tests.failed == 1
    (row,) = read_test_benchmarks_csv(root / CONVENTIONS.benchmarks)
    assert row.exit_code == 8
    assert row.wall_s == 30.0
    assert (row.tests_passed, row.tests_skipped) == (2, 1)
    assert row.runtime is Runtime.DOCKER
    assert (row.slurm_job_id, row.slurm_state, row.queue_s) == (None, None, None)
    # The next run in place starts a new record; the rows accumulate.
    again = record_local(root, application="cece", toolchain=None, provenance=local)
    assert again.run_id != started.run_id
    evaluate(root, exit_status=0)
    assert len(read_test_benchmarks_csv(root / CONVENTIONS.benchmarks)) == 2


# ── cancelling ───────────────────────────────────────────────────────────────


def test_cancel_scancels_a_queued_test_job(tmp_path: Path, slurm: MockerFixture) -> None:
    assert apptests.cancel(tmp_path) == []  # no ledger: nothing to cancel
    _submit(tmp_path)
    assert apptests.cancel(tmp_path) == []  # nothing queued
    slurm.patch("apptests.queue_states", return_value={"4242": "PENDING"})
    assert apptests.cancel(tmp_path, dry_run=True) == ["4242"]
    apptests.scancel.assert_not_called()  # type: ignore[attr-defined]
    assert apptests.cancel(tmp_path) == ["4242"]
    apptests.scancel.assert_called_once_with(["4242"])  # type: ignore[attr-defined]
    # An evaluated run has nothing left to cancel, whatever squeue says.
    slurm.patch("apptests.queue_states", return_value={})
    _finish(tmp_path)
    evaluate(tmp_path)
    slurm.patch("apptests.queue_states", return_value={"4242": "PENDING"})
    assert apptests.cancel(tmp_path) == []


def test_cancel_overdue_gives_up_on_a_job_past_its_deadline(
    tmp_path: Path, slurm: MockerFixture
) -> None:
    ledger = _submit(tmp_path, timeout_s=300, queue_wait_s=60)
    slurm.patch("apptests.queue_states", return_value={"4242": "PENDING"})
    assert apptests.cancel_overdue(ledger, _T0 + timedelta(seconds=359)) is False
    apptests.scancel.assert_not_called()  # type: ignore[attr-defined]
    assert apptests.cancel_overdue(ledger, _T0 + timedelta(seconds=360)) is True
    apptests.scancel.assert_called_once_with(["4242"])  # type: ignore[attr-defined]
    local = ledger.model_copy(update={"job": None})
    assert apptests.cancel_overdue(local, _T0 + timedelta(days=1)) is False
    # Past the deadline but already gone: nothing to cancel, evaluate instead.
    slurm.patch("apptests.queue_states", return_value={})
    assert apptests.cancel_overdue(ledger, _T0 + timedelta(days=1)) is False
    apptests.scancel.assert_called_once()  # type: ignore[attr-defined]


def test_nothing_here_shells_out_for_real() -> None:
    # The fixture's patch targets exist: a renamed import would silently
    # let a test reach Slurm.
    for name in ("submit_job", "queue_states", "slurm_accounting", "scancel"):
        assert callable(getattr(apptests, name))
