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
import socket
import subprocess
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from pydantic import Field

from benchmarks import SlurmAccounting, slurm_accounting
from logs import get_logger
from models.base import StrictModel
from models.conventions import CONVENTIONS

logger = get_logger("jobs")

_SQUEUE_TIMEOUT_S = 60

# The states squeue reports while a job still occupies the queue; anything a
# poll cannot classify is treated as queued so the next poll decides.
QUEUED_UNKNOWN = "UNKNOWN"


class SubmittedJob(StrictModel):
    """One combination's Slurm job as the session submitted it: enough to
    wait for it, judge it, and cancel it from any later process."""

    suite: str = Field(description="The suite the combination belongs to")
    combo: str = Field(description="The combination's canonical name")
    combo_id: str = Field(description="The combination's runtime ULID")
    job_id: str = Field(description="The Slurm job id sbatch returned")
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


def write_ledger(ledger: JobLedger, output_root: Path) -> Path:
    """Write the ledger whole, keys in model order, through a temporary file
    and an atomic rename so a reader (`watch`) never sees a partial file."""
    path = ledger_path(output_root)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w") as f:
        yaml.safe_dump(
            ledger.model_dump(mode="json"), f, default_flow_style=False, sort_keys=False
        )
    os.replace(tmp, path)
    return path


# ── slurm ────────────────────────────────────────────────────────────────────


def submission_command(script: Path) -> list[str]:
    """`sbatch --parsable <script>`: the job id on stdout, no waiting."""
    return ["sbatch", "--parsable", str(script)]


def submit_job(script: Path) -> str:
    """Submit a job script; returns the job id (the first `;`-separated
    field of --parsable's line). A nonzero exit or an empty id raises
    SlurmSubmissionError carrying what sbatch said."""
    command = submission_command(script)
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    job_id = completed.stdout.strip().split(";")[0].strip() if completed.stdout else ""
    if completed.returncode != 0 or not job_id:
        raise SlurmSubmissionError(
            completed.returncode or 1,
            command,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return job_id


def queue_states(job_ids: Iterable[str]) -> dict[str, str]:
    """The queue state (`%T`: PENDING, RUNNING, COMPLETING, ...) of every
    given job still in the queue, from one `squeue` call; ids absent from
    the result have left the queue. squeue exits nonzero when every id is
    unknown to it — that is "nothing queued", not an error. When squeue
    itself cannot run (missing, timed out), every id is reported as
    queued with state UNKNOWN so the caller polls again rather than
    judging a job it knows nothing about."""
    ids = list(job_ids)
    if not ids:
        return {}
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
        if len(parts) >= 2 and parts[0] in ids:
            states[parts[0]] = parts[1]
    if completed.returncode != 0 and not states:
        logger.debug(
            "squeue exited %s for %s (every id gone): %s",
            completed.returncode,
            ids,
            completed.stderr.strip(),
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
    while job.job_id in queue_states([job.job_id]):
        if now() >= job.deadline:
            logger.error(
                "job %s (%s) still queued past its deadline %s; cancelling",
                job.job_id,
                job.combo,
                job.deadline.isoformat(),
            )
            subprocess.run(["scancel", job.job_id], check=False)
            raise SlurmJobTimeout(command, job.timeout_s + job.queue_wait_s, job.job_id)
        sleep(poll_s)
    accounting = slurm_accounting(job.job_id)
    if accounting is None:
        raise SlurmAccountingMissing(job.job_id)
    return accounting


def cancel_jobs(ledger: JobLedger, *, dry_run: bool = False) -> list[str]:
    """scancel every ledger job still in the queue (one call); returns the
    ids that were (or, dry run, would be) cancelled."""
    queued = queue_states(job.job_id for job in ledger.jobs)
    live = [job.job_id for job in ledger.jobs if job.job_id in queued]
    if live and not dry_run:
        subprocess.run(["scancel", *live], check=False)
    return live
