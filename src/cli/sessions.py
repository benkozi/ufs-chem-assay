"""The sessions of a run as `watch` and `report` see them: the effective
config `run` wrote (`<root_dir>/scripts/run-config.yaml` — never a config
file and overrides of their own, so the resume session's exports cannot
drift from the submit session's), each (application, session) with its
output root and its rendered resume script, one squeue tick over their
ledgers, and the evaluation of one session through its resume script. No
command logic: cli/commands/watch.py is the loop, cli/commands/report.py
the rollup."""

from __future__ import annotations

from enum import StrEnum, unique
from pathlib import Path

from pydantic import Field

from applications.base import Application, Stage
from applications.registry import get_application
from cli.run_config import ResolvedSession, RunConfig
from cli.shell import run_bash
from jobs import QUEUED_UNKNOWN, JobLedger, queue_states, read_ledger
from logs import get_logger
from models.base import StrictModel
from models.conventions import CONVENTIONS
from resolution import resolve_output_roots

logger = get_logger("cli")

# The script numbering is cli/commands/run's; the resume slot is named here
# so the machinery never imports a command.
_RESUME_INDEX = 6


@unique
class PendingState(StrEnum):
    """squeue's %T values while a job has not started yet (plus the poll's
    own UNKNOWN); anything else in the queue counts as running (RUNNING,
    COMPLETING, ...)."""

    PENDING = "PENDING"
    CONFIGURING = "CONFIGURING"
    REQUEUED = "REQUEUED"
    REQUEUE_HOLD = "REQUEUE_HOLD"
    SUSPENDED = "SUSPENDED"
    UNKNOWN = QUEUED_UNKNOWN


@unique
class SessionState(StrEnum):
    """What one tick knows about one session."""

    NOT_SUBMITTED = "not submitted"  # no ledger yet
    WAITING = "waiting"  # a job is still queued or running
    READY = "ready"  # every job gone, not evaluated
    EVALUATED = "evaluated"  # the ledger says so
    FAILED = "failed"  # this watch ran the resume; the ledger still says not evaluated


class WatchedSession(StrictModel):
    """One (application, session) of the effective config as `watch` sees it."""

    application: str = Field(description="The application's registry name")
    session: str | None = Field(description="The sessions: key; None when nameless")
    root: Path = Field(description="The session's output root (host path)")
    script: Path = Field(description="The rendered resume script `watch` runs")

    @property
    def label(self) -> str:
        return self.session or self.application


class SessionReport(StrictModel):
    """What one tick found out about one session."""

    state: SessionState = Field(description="The session's state as of this tick")
    queued: int = Field(default=0, description="Jobs not started yet")
    running: int = Field(default=0, description="Jobs in the queue and started")
    done: int = Field(default=0, description="Jobs that have left the queue")
    exit_status: int | None = Field(
        default=None, description="The evaluating session's exit status, when evaluated"
    )

    def line(self) -> str:
        if self.state is SessionState.NOT_SUBMITTED:
            return "not submitted"
        if self.state is SessionState.EVALUATED:
            return f"evaluated (exit {self.exit_status})"
        total = self.queued + self.running + self.done
        return (
            f"{total} job(s): {self.queued} queued, {self.running} running, "
            f"{self.done} done; not evaluated"
        )


def session_root(config: RunConfig, app: Application, session: ResolvedSession) -> Path:
    """The session's output root as the pytest session resolves its
    --combo-output-root: relative paths under the application checkout."""
    host, _driver = resolve_output_roots(
        config.session_output_root(app, session),
        config.checkout_dir(app),
        session.effective_runtime(config.runtime),
        container_workdir=app.container_workdir,
    )
    return host


def configured_sessions(
    config: RunConfig,
    applications: list[Application],
    sessions: list[ResolvedSession],
    scripts_dir: Path,
) -> list[WatchedSession]:
    """Every (application, session) pair in config order, with its root and
    its resume script's path (rendered or not: `report` summarizes a session
    evaluated in place too; `watch` checks submit_only on top)."""
    watched: list[WatchedSession] = []
    for app in applications:
        for session in sessions:
            name = f"{Stage.RESUME.value}-{app.name}"
            if session.name is not None:
                name = f"{name}-{session.name}"
            watched.append(
                WatchedSession(
                    application=app.name,
                    session=session.name,
                    root=session_root(config, app, session),
                    script=scripts_dir / f"{_RESUME_INDEX:02d}-{name}.sh",
                )
            )
    return watched


def read_session_ledger(watched: WatchedSession) -> JobLedger | None:
    try:
        return read_ledger(watched.root)
    except ValueError:
        return None


def tick(
    sessions: list[WatchedSession], ledgers: dict[Path, JobLedger | None]
) -> dict[Path, SessionReport]:
    """One look at the queue: one squeue for every unevaluated session's
    ids together, then a report per session."""
    ids = [
        job.job_id
        for ledger in ledgers.values()
        if ledger is not None and ledger.evaluated_at is None
        for job in ledger.jobs
    ]
    states = queue_states(ids) if ids else {}
    reports: dict[Path, SessionReport] = {}
    for watched in sessions:
        ledger = ledgers[watched.root]
        if ledger is None:
            reports[watched.root] = SessionReport(state=SessionState.NOT_SUBMITTED)
            continue
        if ledger.evaluated_at is not None:
            reports[watched.root] = SessionReport(
                state=SessionState.EVALUATED, exit_status=ledger.exit_status
            )
            continue
        queued = running = done = 0
        for job in ledger.jobs:
            state = states.get(job.job_id)
            if state is None:
                done += 1
            elif state in PendingState:
                queued += 1
            else:
                running += 1
        reports[watched.root] = SessionReport(
            state=SessionState.READY
            if queued == running == 0
            else SessionState.WAITING,
            queued=queued,
            running=running,
            done=done,
        )
    return reports


def evaluate_session(watched: WatchedSession, logs_dir: Path) -> SessionReport:
    """Run the session's resume script and read the ledger back: evaluated,
    or failed (the session died; its log says why)."""
    code = run_bash(watched.script, logs_dir)
    ledger = read_session_ledger(watched)
    if ledger is not None and ledger.evaluated_at is not None:
        return SessionReport(
            state=SessionState.EVALUATED, exit_status=ledger.exit_status
        )
    logger.error(
        "%s: evaluation failed (exit %s); see the log of %s, then run "
        "`ufs-chem-assay watch` again or the script by hand",
        watched.label,
        code,
        watched.script.name,
    )
    return SessionReport(state=SessionState.FAILED, exit_status=code)


def load_effective(root_dir: Path) -> RunConfig:
    """The effective config `run` wrote under root_dir; ValueError naming
    the command that writes it when absent."""
    effective = root_dir / "scripts" / CONVENTIONS.effective_config
    if not effective.is_file():
        raise ValueError(
            f"{effective} does not exist: `ufs-chem-assay run --config-file=... "
            f"[--dry-run]` writes it (pass the same --root-dir)"
        )
    return RunConfig.from_yaml(effective, resolve_application=get_application)
