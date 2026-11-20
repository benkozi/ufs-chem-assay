"""The job ledger and the asynchronous Slurm wait.

Under the slurm runtime a session submits every combination's job up front
and records them in `<output root>/jobs.yaml` (the ledger); each
combination then waits for *its* job by polling `squeue` and takes the
verdict from `sacct`. One mechanism serves a session that submitted the
jobs itself and a `--resume` session that only found the ledger: nothing
here needs the submitting process, only the job ids. The ledger is a
recorded artifact like `run.yaml` — rewritten whole after every
submission (so an interrupted submit leaves a truthful file) and once more
at session end with the verdict's time and status.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
import subprocess
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import yaml
from pydantic import Field

from benchmarks import SlurmAccounting, slurm_accounting
from logs import get_logger
from models.base import StrictModel
from models.conventions import CONVENTIONS
from platforms import Platform, Runtime

logger = get_logger("jobs")

_SQUEUE_TIMEOUT_S = 60

# The states squeue reports while a job still occupies the queue; anything a
# poll cannot classify is treated as queued so the next poll decides.
QUEUED_UNKNOWN = "UNKNOWN"

# What squeue says on stderr when every asked-for id has left the queue
# (`slurm_load_jobs error: Invalid job id specified`). Any other nonzero exit
# — the controller unreachable, an RPC timeout — says nothing about the jobs.
_IDS_GONE = re.compile(r"Invalid job id")


class JobRef(NamedTuple):
    """What `sbatch --parsable` says of an accepted job: its id and, when
    it went to a named cluster of a multi-cluster Slurm (Gaea's c6, c7), the
    cluster. On such a machine every later command about the job — squeue,
    sacct, scancel — must name that cluster (`-M`), or it asks the wrong
    controller and the job looks gone."""

    job_id: str
    cluster: str | None


def _on(clusters: str | None) -> list[str]:
    """`-M <clusters>` for a Slurm client command; nothing without one."""
    return [] if clusters is None else ["-M", clusters]


class SubmittedJob(StrictModel):
    """One combination's Slurm job as the session submitted it: enough to
    wait for it, judge it, and cancel it from any later process."""

    suite: str = Field(description="The suite the combination belongs to")
    combo: str = Field(description="The combination's canonical name")
    combo_id: str = Field(description="The combination's runtime ULID")
    job_id: str = Field(description="The Slurm job id sbatch returned")
    cluster: str | None = Field(
        default=None,
        description=(
            "The cluster sbatch said the job went to (multi-cluster Slurm), "
            "named in every later squeue, sacct and scancel about it; null on "
            "a single-cluster machine and in older ledgers"
        ),
    )
    script: Path = Field(description="The submitted job script (<combo_id>.sbatch)")
    out_path: Path = Field(
        description="Where Slurm writes the job's output (<combo_id>.out)"
    )
    timeout_s: int = Field(
        gt=0,
        description="The job's own time limit in seconds (the suite timeout, capped)",
    )
    queue_wait_s: int = Field(
        gt=0,
        description="Seconds the job may wait in the queue before the harness cancels it",
    )
    submitted_at: datetime = Field(
        description="When sbatch accepted the job (UTC); the deadline counts from here"
    )

    @property
    def deadline(self) -> datetime:
        """When the harness gives up on the job: submission plus its time
        limit plus its queue allowance."""
        return self.submitted_at + timedelta(seconds=self.timeout_s + self.queue_wait_s)


class JobLedger(StrictModel):
    """`jobs.yaml`: every job a session submitted for one run, and whether a
    session has evaluated them yet."""

    run_id: str = Field(description="The run ULID of run.yaml beside this file")
    application: str = Field(description="The run's application (registry name)")
    submitted_by: str = Field(description="user@host of the submitting session")
    jobs: list[SubmittedJob] = Field(
        default_factory=list, description="The submitted jobs, in enumeration order"
    )
    evaluated_at: datetime | None = Field(
        default=None,
        description="When a session finished evaluating the jobs (UTC); null until then",
    )
    exit_status: int | None = Field(
        default=None,
        description="The evaluating pytest session's exit status; null until evaluated",
    )


class TestCounts(StrictModel):
    """What a junit report says of one run of an application's own tests."""

    __test__ = False  # a model, not a pytest class

    total: int = Field(ge=0, description="Tests in the report")
    passed: int = Field(ge=0, description="Tests that ran and passed")
    failed: int = Field(ge=0, description="Tests that failed or errored")
    skipped: int = Field(ge=0, description="Tests skipped, disabled, or not run")
    ctest_s: float | None = Field(
        ge=0,
        description=(
            "The tests' own durations, summed, in seconds; null when the report "
            "is a pass marker, which times nothing"
        ),
    )


class TestProvenance(StrictModel):
    """What a test run ran against, as known when it was launched: the
    benchmark row's identity columns, recorded then so an evaluation hours
    later describes the build the tests actually ran."""

    __test__ = False

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


class TestJob(StrictModel):
    """The Slurm job of one test run: enough to wait for it, judge it and
    cancel it from any later process."""

    __test__ = False

    job_id: str = Field(description="The Slurm job id sbatch returned")
    cluster: str | None = Field(
        default=None,
        description=(
            "The cluster sbatch said the job went to (multi-cluster Slurm), "
            "named in every later squeue, sacct and scancel about it; null on "
            "a single-cluster machine"
        ),
    )
    script: Path = Field(
        description="The submitted job script (04-test-<application>.sbatch)"
    )
    timeout_s: int = Field(
        gt=0, description="The job's own time limit in seconds (tests.minutes)"
    )
    queue_wait_s: int = Field(
        gt=0,
        description="Seconds the job may wait in the queue before the harness cancels it",
    )
    submitted_at: datetime = Field(
        description="When sbatch accepted the job (UTC); the deadline counts from here"
    )

    @property
    def deadline(self) -> datetime:
        """When the harness gives up on the job: submission plus its time
        limit plus its queue allowance."""
        return self.submitted_at + timedelta(seconds=self.timeout_s + self.queue_wait_s)


class TestLedger(StrictModel):
    """`tests.yaml`: the latest run of an application's own tests at a
    tests root — submitted (slurm) or run in place (native, docker), and
    whether it has been evaluated. Earlier runs live on in the root's
    benchmark rows and in their own output and junit files."""

    __test__ = False

    run_id: str = Field(description="The test run's ULID, minted when it was launched")
    application: str = Field(description="The application (registry name)")
    toolchain: str | None = Field(
        default=None,
        description="The run config's toolchain the build used; null without one",
    )
    build: str | None = Field(
        default=None,
        description="The build variant the tests ran against; null for the unnamed build",
    )
    submitted_by: str = Field(
        description="user@host of the process that launched the run"
    )
    provenance: TestProvenance = Field(description="What the run ran against")
    job: TestJob | None = Field(
        description="The run's Slurm job; null for a run in place (native, docker)"
    )
    out_path: Path = Field(
        description="The run's output (tic/toc lines around the tests)"
    )
    junit_path: Path = Field(description="The junit report the test command writes")
    passed_marker: str | None = Field(
        default=None,
        description=(
            "For a test command that writes no junit report: the text in its "
            "output that means it passed (the run counts as one test)"
        ),
    )
    evaluated_at: datetime | None = Field(
        default=None, description="When the run was evaluated (UTC); null until then"
    )
    exit_status: int | None = Field(
        default=None,
        description="The run's verdict (0: every test passed); null until evaluated",
    )
    tests: TestCounts | None = Field(
        default=None,
        description="The junit report's counts; null until evaluated, or without a report",
    )


class SlurmSubmissionError(subprocess.CalledProcessError):
    """`sbatch` refused the job script or printed no job id."""


class SlurmAccountingMissing(RuntimeError):
    """A job left the queue but `sacct` has no row for it: the asynchronous
    wait cannot judge the job without accounting."""

    def __init__(self, job_id: str) -> None:
        super().__init__(
            f"job {job_id} left the Slurm queue but sacct has no record of it; "
            f"check by hand: sacct -j {job_id}"
        )
        self.job_id = job_id


class SlurmJobError(subprocess.CalledProcessError):
    """A Slurm driver job ended nonzero; carries the job id for accounting."""

    def __init__(
        self, returncode: int, cmd: list[str], output: bytes, job_id: str
    ) -> None:
        super().__init__(returncode, cmd, output=output)
        self.job_id = job_id


class SlurmJobTimeout(subprocess.TimeoutExpired):
    """The harness's outer bound (timeout + queue allowance) passed and the
    job was cancelled, or Slurm itself ended the job by its limit; carries
    the job id for accounting."""

    def __init__(self, cmd: list[str], timeout: float, job_id: str) -> None:
        super().__init__(cmd, timeout)
        self.job_id = job_id


# ── the ledger on disk ───────────────────────────────────────────────────────


def ledger_path(output_root: Path) -> Path:
    return output_root / CONVENTIONS.job_ledger


def submitted_by() -> str:
    """user@host of this process, for the ledger."""
    return f"{getpass.getuser()}@{socket.gethostname()}"


def read_ledger(output_root: Path) -> JobLedger:
    """The ledger at an output root; ValueError naming the path when it is
    missing or does not validate."""
    path = ledger_path(output_root)
    if not path.is_file():
        raise ValueError(
            f"{output_root} has no {CONVENTIONS.job_ledger}: nothing submitted"
        )
    try:
        with open(path) as f:
            return JobLedger.model_validate(yaml.safe_load(f))
    except (ValueError, yaml.YAMLError) as exc:  # ValidationError is a ValueError
        raise ValueError(f"{path}: not a job ledger ({exc})") from exc


def _write_whole(ledger: StrictModel, path: Path) -> Path:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w") as f:
        yaml.safe_dump(
            ledger.model_dump(mode="json"), f, default_flow_style=False, sort_keys=False
        )
    os.replace(tmp, path)
    return path


def write_ledger(ledger: JobLedger, output_root: Path) -> Path:
    """Write the ledger whole, keys in model order, through a temporary file
    and an atomic rename so a reader (`watch`) never sees a partial file."""
    return _write_whole(ledger, ledger_path(output_root))


def path_of_test_ledger(tests_root: Path) -> Path:
    return tests_root / CONVENTIONS.test_ledger


def read_test_ledger(tests_root: Path) -> TestLedger:
    """The test ledger at a tests root; ValueError naming the path when it
    is missing or does not validate."""
    path = path_of_test_ledger(tests_root)
    if not path.is_file():
        raise ValueError(
            f"{tests_root} has no {CONVENTIONS.test_ledger}: no test run recorded"
        )
    try:
        with open(path) as f:
            return TestLedger.model_validate(yaml.safe_load(f))
    except (ValueError, yaml.YAMLError) as exc:  # ValidationError is a ValueError
        raise ValueError(f"{path}: not a test ledger ({exc})") from exc


def write_test_ledger(ledger: TestLedger, tests_root: Path) -> Path:
    """Write the test ledger whole, atomically, as write_ledger does."""
    return _write_whole(ledger, path_of_test_ledger(tests_root))


# ── slurm ────────────────────────────────────────────────────────────────────


def submission_command(script: Path) -> list[str]:
    """`sbatch --parsable <script>`: the job id on stdout, no waiting."""
    return ["sbatch", "--parsable", str(script)]


def submit_job(script: Path) -> JobRef:
    """Submit a job script; returns the job's id and cluster — the two
    `;`-separated fields of --parsable's line, the second present only when
    the job went to a named cluster. A nonzero exit or an empty id raises
    SlurmSubmissionError carrying what sbatch said."""
    command = submission_command(script)
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    job_id, _, cluster = (completed.stdout or "").strip().partition(";")
    job_id, cluster = job_id.strip(), cluster.strip()
    if completed.returncode != 0 or not job_id:
        raise SlurmSubmissionError(
            completed.returncode or 1,
            command,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return JobRef(job_id=job_id, cluster=cluster or None)


def queue_states(job_ids: Iterable[str], clusters: str | None = None) -> dict[str, str]:
    """The queue state (`%T`: PENDING, RUNNING, COMPLETING, ...) of every
    given job still in the queue, from one `squeue` call — on `clusters`
    (`-M`) when the jobs were submitted to a named cluster; ids absent from
    the result have left the queue (lines that are not `<id> <state>`, such
    as the `CLUSTER:` heading -M adds, are skipped). squeue exits nonzero
    when every id is unknown to it (`Invalid job id`) — that is "nothing
    queued", not an error. When squeue itself cannot run (missing, timed out) or fails for
    any other reason (the controller unreachable), every id is reported as
    queued with state UNKNOWN so the caller polls again rather than
    judging a job it knows nothing about."""
    ids = list(job_ids)
    if not ids:
        return {}
    wanted = set(ids)
    argv = ["squeue", "-h", *_on(clusters), "-j", ",".join(ids), "-o", "%i %T"]
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
        if len(parts) >= 2 and parts[0] in wanted:
            states[parts[0]] = parts[1]
    if completed.returncode != 0 and not states:
        stderr = completed.stderr.strip()
        if not _IDS_GONE.search(stderr):
            logger.warning(
                "squeue failed (exit %s: %s); treating %s job(s) as queued",
                completed.returncode,
                stderr or "no message",
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


def wait_for_job(
    job: SubmittedJob,
    *,
    poll_s: int,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> SlurmAccounting:
    """Poll until the job has left the queue, then return its accounting.
    Past the deadline (submission + timeout + queue allowance) the job is
    cancelled and SlurmJobTimeout raised, as the synchronous wait did; a
    job gone from the queue without an sacct row raises
    SlurmAccountingMissing — the harness never guesses a verdict. `now`
    and `sleep` default to the real clock and time.sleep (injectable for
    tests)."""
    now = (lambda: datetime.now(UTC)) if now is None else now
    sleep = time.sleep if sleep is None else sleep
    command = submission_command(job.script)
    while job.job_id in queue_states([job.job_id], clusters=job.cluster):
        if now() >= job.deadline:
            logger.error(
                "job %s (%s) still queued past its deadline %s; cancelling",
                job.job_id,
                job.combo,
                job.deadline.isoformat(),
            )
            scancel([job.job_id], clusters=job.cluster)
            raise SlurmJobTimeout(command, job.timeout_s + job.queue_wait_s, job.job_id)
        sleep(poll_s)
    accounting = slurm_accounting(job.job_id, clusters=job.cluster)
    if accounting is None:
        raise SlurmAccountingMissing(job.job_id)
    return accounting


def scancel(job_ids: list[str], clusters: str | None = None) -> None:
    """`scancel [-M <clusters>] <ids>` in one call; a job already gone is
    not an error."""
    subprocess.run(["scancel", *_on(clusters), *job_ids], check=False)


def by_cluster(jobs: Iterable[tuple[str, str | None]]) -> dict[str | None, list[str]]:
    """(job id, cluster) pairs as the job ids of each cluster, clusters in
    first-seen order: one squeue or scancel per cluster covers them all."""
    grouped: dict[str | None, list[str]] = {}
    for job_id, cluster in jobs:
        grouped.setdefault(cluster, []).append(job_id)
    return grouped


def queued_jobs(ledger: JobLedger) -> dict[str | None, list[str]]:
    """The ledger's jobs still in the queue, by the cluster they are on
    (one squeue per cluster); clusters with none queued are left out."""
    grouped = by_cluster((job.job_id, job.cluster) for job in ledger.jobs)
    live: dict[str | None, list[str]] = {}
    for cluster, ids in grouped.items():
        queued = queue_states(ids, clusters=cluster)
        mine = [job_id for job_id in ids if job_id in queued]
        if mine:
            live[cluster] = mine
    return live


def cancel_jobs(ledger: JobLedger, *, dry_run: bool = False) -> list[str]:
    """scancel every ledger job still in the queue (one squeue and one
    scancel per cluster the jobs are on); returns the ids that were (or,
    dry run, would be) cancelled, in ledger order."""
    live = queued_jobs(ledger)
    if not dry_run:
        for cluster, ids in live.items():
            scancel(ids, clusters=cluster)
    cancelled = {job_id for ids in live.values() for job_id in ids}
    return [job.job_id for job in ledger.jobs if job.job_id in cancelled]
