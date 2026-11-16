"""An application's own tests — what its developers run (ctest for a CMake
project), as opposed to the driver sweeps the pytest sessions run — as the
harness launches and judges them.

One run is one tests root (`<root_dir>/tests/<application>/`) and one test
ledger there (`tests.yaml`, jobs.py). Under the slurm runtime the run is a
job: `submit` guards against a job already queued, submits, and records it;
the job's output and junit report carry its id, so nothing is overwritten.
Under native and docker the stage script runs the tests in place and
`record_local` records that. Either way `evaluate` turns the finished run
into a verdict — Slurm's accounting where there is a job, the command's exit
status otherwise, the junit report's counts in both — and one benchmark row
appended to the root's `benchmarks.csv`. The ledger is the latest run; the
rows are every evaluated one. `ufs-chem-assay submit-tests`, `evaluate-tests`,
`watch`, `report` and `cancel` are the callers.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ulid import ULID

from benchmarks import (
    SlurmAccounting,
    TestBenchmarkRow,
    append_test_benchmarks,
    parse_tic_toc,
    slurm_accounting,
)
from jobs import (
    TestCounts,
    TestJob,
    TestLedger,
    TestProvenance,
    path_of_test_ledger,
    queue_states,
    read_test_ledger,
    scancel,
    submit_job,
    submitted_by,
    write_test_ledger,
)
from logs import get_logger
from models.conventions import CONVENTIONS

logger = get_logger("apptests")

Clock = Callable[[], datetime]


class TestsStillQueued(ValueError):
    """A tests root's job is still in the Slurm queue: nothing to evaluate
    yet, and nothing to submit beside it."""

    __test__ = False  # an exception, not a pytest class


def _clock(now: Clock | None) -> Clock:
    return (lambda: datetime.now(UTC)) if now is None else now


# ── the junit report ─────────────────────────────────────────────────────────


def parse_junit(path: Path) -> tuple[TestCounts, list[str]]:
    """A junit report (ctest --output-junit) as its counts and the names of
    the failed tests, in report order. A test with a <failure> or <error>
    (or status "fail") failed; one with <skipped> (or status "notrun" /
    "disabled") was skipped; any other passed. ValueError naming the path
    when the file is not a junit report — a job killed while writing it."""
    try:
        # The report is the test command's own output in the harness's tests
        # root, never a document from elsewhere.
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{path}: not a junit report ({exc})") from exc
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError(f"{path}: not a junit report (root element <{root.tag}>)")
    passed = skipped = 0
    seconds = 0.0
    failed: list[str] = []
    for case in root.iter("testcase"):
        status = case.get("status", "")
        if (
            case.find("failure") is not None
            or case.find("error") is not None
            or status == "fail"
        ):
            failed.append(case.get("name", "(unnamed)"))
        elif case.find("skipped") is not None or status in {"notrun", "disabled"}:
            skipped += 1
        else:
            passed += 1
        try:
            seconds += float(case.get("time", "0"))
        except ValueError:
            pass  # a report without a usable duration for this test
    counts = TestCounts(
        total=passed + skipped + len(failed),
        passed=passed,
        failed=len(failed),
        skipped=skipped,
        ctest_s=seconds,
    )
    return counts, failed


def _counts(junit: Path) -> TestCounts | None:
    """The report's counts; None without a report (a killed job, a test
    command that writes none) or with an unreadable one (logged)."""
    if not junit.is_file():
        return None
    try:
        return parse_junit(junit)[0]
    except ValueError as exc:
        logger.warning("%s; the run is judged without its counts", exc)
        return None


# ── launching ────────────────────────────────────────────────────────────────


def _settle_previous(tests_root: Path, *, resubmit: bool) -> None:
    """Before a new submission: a previous job still queued is refused
    (TestsStillQueued) or, with `resubmit`, cancelled; one that finished
    without being evaluated is evaluated first, so its row is not lost."""
    if not path_of_test_ledger(tests_root).is_file():
        return
    previous = read_test_ledger(tests_root)
    if previous.job is None or previous.evaluated_at is not None:
        return
    job_id = previous.job.job_id
    if job_id not in queue_states([job_id]):
        logger.info(
            "test job %s finished without being evaluated; evaluating it before "
            "the new submission",
            job_id,
        )
        evaluate(tests_root)
        return
    if not resubmit:
        raise TestsStillQueued(
            f"{tests_root}: test job {job_id} is still in the queue; "
            f"`ufs-chem-assay cancel --tests-root {tests_root}` cancels it, and "
            "--resubmit cancels it and submits again"
        )
    logger.info("cancelling queued test job %s (--resubmit)", job_id)
    scancel([job_id])


def submit(
    tests_root: Path,
    *,
    script: Path,
    application: str,
    toolchain: str | None,
    provenance: TestProvenance,
    timeout_s: int,
    queue_wait_s: int,
    resubmit: bool = False,
    now: Clock | None = None,
) -> TestLedger:
    """Submit an application's test job and record it: the tests root
    created (Slurm needs the job's --output directory), the previous run
    settled (_settle_previous), `sbatch --parsable <script>`, the ledger
    written whole. A refused sbatch raises SlurmSubmissionError and leaves
    the root as it was."""
    tests_root.mkdir(parents=True, exist_ok=True)
    _settle_previous(tests_root, resubmit=resubmit)
    job_id = submit_job(script)
    stem = CONVENTIONS.tests_stem
    ledger = TestLedger(
        run_id=str(ULID()),
        application=application,
        toolchain=toolchain,
        submitted_by=submitted_by(),
        provenance=provenance,
        job=TestJob(
            job_id=job_id,
            script=script,
            timeout_s=timeout_s,
            queue_wait_s=queue_wait_s,
            submitted_at=_clock(now)(),
        ),
        out_path=tests_root / f"{stem}-{job_id}.out",
        junit_path=tests_root / f"{stem}-{job_id}.xml",
    )
    write_test_ledger(ledger, tests_root)
    logger.info(
        "submitted test job %s for %s (sbatch --parsable %s); ledger: %s",
        job_id,
        application,
        script,
        path_of_test_ledger(tests_root),
    )
    return ledger


def record_local(
    tests_root: Path,
    *,
    application: str,
    toolchain: str | None,
    provenance: TestProvenance,
) -> TestLedger:
    """Record a test run made in place (native, docker): the stage script
    ran the tests into <tests root>/tests.out and tests.xml; this starts
    the run's ledger, which `evaluate` then completes with the command's
    exit status."""
    tests_root.mkdir(parents=True, exist_ok=True)
    stem = CONVENTIONS.tests_stem
    ledger = TestLedger(
        run_id=str(ULID()),
        application=application,
        toolchain=toolchain,
        submitted_by=submitted_by(),
        provenance=provenance,
        job=None,
        out_path=tests_root / f"{stem}.out",
        junit_path=tests_root / f"{stem}.xml",
    )
    write_test_ledger(ledger, tests_root)
    return ledger


# ── judging ──────────────────────────────────────────────────────────────────


def _job_verdict(accounting: SlurmAccounting | None, counts: TestCounts | None) -> int:
    """A job's exit status. With accounting: its exit code when nonzero;
    else 1 for a job that did not complete (a time limit, a cancel) or whose
    report shows failures; else 0. Without accounting — a machine whose
    sacct does not answer — the report is the evidence: 0 only for a report
    with no failure."""
    if accounting is None:
        return 0 if counts is not None and counts.failed == 0 else 1
    if accounting.exit_code:
        return accounting.exit_code
    if not (accounting.state or "").startswith("COMPLETED"):
        return 1
    return 1 if counts is not None and counts.failed else 0


def evaluate(
    tests_root: Path,
    *,
    exit_status: int | None = None,
    left_queue: bool = False,
    now: Clock | None = None,
) -> TestLedger:
    """Evaluate the tests root's run: the verdict, the junit counts, one
    benchmark row appended to the root's benchmarks.csv, the ledger
    rewritten with them. A job is judged by Slurm's accounting
    (_job_verdict) and must have left the queue (TestsStillQueued
    otherwise; `left_queue` says the caller's own poll just showed it gone,
    sparing a second squeue); a run in place by `exit_status`, the test
    command's own. An already evaluated run is returned as it is: one run,
    one row."""
    ledger = read_test_ledger(tests_root)
    if ledger.evaluated_at is not None:
        return ledger
    counts = _counts(ledger.junit_path)
    accounting: SlurmAccounting | None = None
    if ledger.job is not None:
        job_id = ledger.job.job_id
        if not left_queue and job_id in queue_states([job_id]):
            raise TestsStillQueued(
                f"{tests_root}: test job {job_id} is still in the queue; nothing "
                "to evaluate yet"
            )
        accounting = slurm_accounting(job_id)
        if accounting is None:
            logger.warning(
                "no accounting for job %s: judging the test run by its junit report",
                job_id,
            )
        status = _job_verdict(accounting, counts)
    elif exit_status is None:
        raise ValueError(
            f"{tests_root}: a test run in place needs the test command's exit "
            "status (--exit-status)"
        )
    else:
        status = exit_status
    recorded_at = _clock(now)()
    out_text = (
        ledger.out_path.read_text(errors="replace") if ledger.out_path.is_file() else ""
    )
    provenance = ledger.provenance
    row = TestBenchmarkRow(
        recorded_at=recorded_at,
        run_id=ledger.run_id,
        application=ledger.application,
        toolchain=ledger.toolchain,
        platform=provenance.platform,
        runtime=provenance.runtime,
        modulefile=provenance.modulefile,
        build_type=provenance.build_type,
        cmake_args=provenance.cmake_args,
        application_commit=provenance.application_commit,
        harness_version=provenance.harness_version,
        harness_commit=provenance.harness_commit,
        slurm_job_id=ledger.job.job_id if ledger.job else None,
        slurm_state=accounting.state if accounting else None,
        exit_code=status,
        wall_s=parse_tic_toc(out_text),
        queue_s=accounting.queue_s if accounting else None,
        elapsed_s=accounting.elapsed_s if accounting else None,
        max_rss_kb=accounting.max_rss_kb if accounting else None,
        tests_total=counts.total if counts else None,
        tests_passed=counts.passed if counts else None,
        tests_failed=counts.failed if counts else None,
        tests_skipped=counts.skipped if counts else None,
        ctest_s=counts.ctest_s if counts else None,
        tests_root=tests_root,
    )
    append_test_benchmarks(tests_root / CONVENTIONS.benchmarks, [row])
    evaluated = ledger.model_copy(
        update={"evaluated_at": recorded_at, "exit_status": status, "tests": counts}
    )
    write_test_ledger(evaluated, tests_root)
    return evaluated


# ── cancelling ───────────────────────────────────────────────────────────────


def _queued_job(ledger: TestLedger) -> str | None:
    """The ledger's job id when that job is unevaluated and still queued."""
    if ledger.job is None or ledger.evaluated_at is not None:
        return None
    job_id = ledger.job.job_id
    return job_id if job_id in queue_states([job_id]) else None


def cancel(tests_root: Path, *, dry_run: bool = False) -> list[str]:
    """scancel the tests root's job when it is still in the queue; returns
    the ids that were (or, dry run, would be) cancelled — none for a root
    without a ledger, a run in place, or a job already gone."""
    if not path_of_test_ledger(tests_root).is_file():
        return []
    job_id = _queued_job(read_test_ledger(tests_root))
    if job_id is None:
        return []
    if not dry_run:
        scancel([job_id])
    return [job_id]


def cancel_overdue(ledger: TestLedger, now: datetime) -> bool:
    """Give up on a job still queued past its deadline (submission + time
    limit + queue allowance): scancel it and return True, so the caller
    evaluates what is left (Slurm's accounting says CANCELLED)."""
    if ledger.job is None or now < ledger.job.deadline:
        return False
    job_id = _queued_job(ledger)
    if job_id is None:
        return False
    logger.error(
        "test job %s still queued past its deadline %s; cancelling",
        job_id,
        ledger.job.deadline.isoformat(),
    )
    scancel([job_id])
    return True
