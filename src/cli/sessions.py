"""The sessions of a run as `watch` and `report` see them: the effective
config `run` wrote (`<root_dir>/scripts/run-config.yaml` — never a config
file and overrides of their own, so the resume session's exports cannot
drift from the submit session's), each (application, session) with its
output root and its rendered resume script, one squeue tick over their
ledgers, and the evaluation of one session through its resume script. No
command logic: cli/commands/watch.py is the loop, cli/commands/report.py
the rollup."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum, unique
from pathlib import Path

from pydantic import Field

import apptests
from applications.base import Application, Stage
from applications.registry import get_application
from cli.run_config import ResolvedSession, RunConfig
from cli.shell import run_bash
from cli.stages import SCRIPT_INDEX, tests_enabled
from identity import harness_commit, harness_version
from jobs import (
    QUEUED_UNKNOWN,
    JobLedger,
    TestLedger,
    TestProvenance,
    path_of_test_ledger,
    queue_states,
    read_ledger,
    read_test_ledger,
)
from logs import get_logger
from models.base import StrictModel
from models.conventions import CONVENTIONS
from resolution import resolve_output_roots

logger = get_logger("cli")

_RESUME_INDEX = SCRIPT_INDEX[Stage.RESUME]


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
        session.harness.effective_runtime(config.runtime),
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


def poll(
    ledgers: Iterable[JobLedger | None], test_ledgers: Iterable[TestLedger | None] = ()
) -> dict[str, str]:
    """One look at the queue: one squeue for the job ids of every
    unevaluated session and test run together (none asked when there is
    nothing to ask about)."""
    ids = [
        job.job_id
        for ledger in ledgers
        if ledger is not None and ledger.evaluated_at is None
        for job in ledger.jobs
    ]
    ids += [
        ledger.job.job_id
        for ledger in test_ledgers
        if ledger is not None and ledger.evaluated_at is None and ledger.job is not None
    ]
    return queue_states(ids) if ids else {}


def tick(
    sessions: list[WatchedSession],
    ledgers: dict[Path, JobLedger | None],
    states: dict[str, str] | None = None,
) -> dict[Path, SessionReport]:
    """A report per session from one look at the queue: `states` when the
    caller polled (the sessions and the test runs together), else one
    squeue for these sessions' ids."""
    if states is None:
        states = poll(ledgers.values())
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


# ── the applications' own tests ──────────────────────────────────────────────


@unique
class TestsState(StrEnum):
    """What one tick knows about one application's test run."""

    __test__ = False  # an enum, not a pytest class

    NOT_SUBMITTED = "not submitted"  # no test ledger yet
    WAITING = "waiting"  # the job is still queued or running
    READY = "ready"  # the job is gone, not evaluated
    EVALUATED = "evaluated"  # the ledger says so
    FAILED = "failed"  # this watch tried to evaluate it and could not


class WatchedTests(StrictModel):
    """One application's own tests as `watch` and `report` see them."""

    application: str = Field(description="The application's registry name")
    toolchain: str | None = Field(
        default=None, description="The build's toolchain; None without one"
    )
    root: Path = Field(description="The tests root (<root_dir>/tests/<application>)")

    @property
    def label(self) -> str:
        suffix = f"/{self.toolchain}" if self.toolchain else ""
        return f"tests {self.application}{suffix}"


class TestsReport(StrictModel):
    """What one tick found out about one test run."""

    __test__ = False

    state: TestsState = Field(description="The run's state as of this tick")
    job_id: str | None = Field(default=None, description="The run's Slurm job id")
    queue_state: str | None = Field(
        default=None, description="squeue's state of the job while it is queued"
    )
    exit_status: int | None = Field(
        default=None, description="The run's verdict, when evaluated"
    )
    detail: str | None = Field(
        default=None,
        description="The evaluated run's summary, or why an evaluation failed",
    )

    def line(self) -> str:
        if self.state is TestsState.NOT_SUBMITTED:
            return "not submitted"
        if self.state is TestsState.EVALUATED:
            return self.detail or f"evaluated (exit {self.exit_status})"
        if self.state is TestsState.FAILED:
            return f"evaluation failed ({self.detail})"
        if self.state is TestsState.WAITING:
            how = "queued" if self.queue_state in PendingState else "running"
            return f"job {self.job_id} {how} ({self.queue_state}); not evaluated"
        done = "done" if self.job_id is not None else "run in place"
        return f"job {self.job_id} {done}; not evaluated"


def configured_tests(
    config: RunConfig, applications: list[Application]
) -> list[WatchedTests]:
    """The test run of every selected application whose own tests are
    enabled, in config order."""
    return [
        WatchedTests(application=app.name, root=config.tests_root(app))
        for app in applications
        if tests_enabled(config, app)
    ]


def read_tests_ledger(watched: WatchedTests) -> TestLedger | None:
    if not path_of_test_ledger(watched.root).is_file():
        return None
    return read_test_ledger(watched.root)


def _evaluated(ledger: TestLedger) -> TestsReport:
    return TestsReport(
        state=TestsState.EVALUATED,
        job_id=ledger.job.job_id if ledger.job else None,
        exit_status=ledger.exit_status,
        detail=tests_line(ledger),
    )


def tick_tests(
    tests: list[WatchedTests],
    ledgers: dict[Path, TestLedger | None],
    states: dict[str, str],
) -> dict[Path, TestsReport]:
    """A report per test run from the tick's one look at the queue."""
    reports: dict[Path, TestsReport] = {}
    for watched in tests:
        ledger = ledgers[watched.root]
        if ledger is None:
            reports[watched.root] = TestsReport(state=TestsState.NOT_SUBMITTED)
        elif ledger.evaluated_at is not None:
            reports[watched.root] = _evaluated(ledger)
        else:
            job_id = ledger.job.job_id if ledger.job else None
            queue_state = states.get(job_id) if job_id is not None else None
            reports[watched.root] = TestsReport(
                state=TestsState.READY if queue_state is None else TestsState.WAITING,
                job_id=job_id,
                queue_state=queue_state,
            )
    return reports


def evaluate_tests(watched: WatchedTests) -> TestsReport:
    """Evaluate a test run whose job the tick just showed gone — in this
    process: there is no resume script, only the accounting, the junit
    report and one benchmark row (apptests.evaluate). A run that cannot be
    evaluated is reported failed with the reason, never raised."""
    try:
        return _evaluated(apptests.evaluate(watched.root, left_queue=True))
    except ValueError as exc:
        return TestsReport(state=TestsState.FAILED, exit_status=1, detail=str(exc))


def tests_job_script(scripts_dir: Path, app: Application) -> Path:
    """The test job script `run` rendered beside the application's test
    stage script: <scripts_dir>/04-test-<application>.sbatch."""
    stage = Stage.TEST
    return scripts_dir / f"{SCRIPT_INDEX[stage]:02d}-{stage.value}-{app.name}.sbatch"


def test_provenance(config: RunConfig, app: Application) -> TestProvenance:
    """What a test run of `app` runs against, read now: the config's
    platform, runtime, modulefile and configure flags; the checkout's commit
    and CMake build type; the harness's own identity. A checkout whose
    commit cannot be read (not there yet, not a git checkout) records null
    with a warning — the stage's guard, not this record, is what requires a
    build."""
    section = config.applications[app.name]
    settings = app.settings_model(root_dir=config.checkout_dir(app))
    try:
        commit = settings.commit_sha()
    except ValueError as exc:
        logger.warning("%s; the test run records application_commit: null", exc)
        commit = None
    return TestProvenance(
        platform=config.platform,
        runtime=config.runtime,
        modulefile=section.modulefile,
        build_type=settings.build_type(),
        cmake_args=" ".join(section.build_flags()) or None,
        application_commit=commit,
        harness_version=harness_version(),
        harness_commit=harness_commit(),
    )


def tests_line(ledger: TestLedger) -> str:
    """An evaluated test run's ledger as the half-line `watch` and
    `evaluate-tests` print after its label."""
    line = f"evaluated (exit {ledger.exit_status})"
    counts = ledger.tests
    if counts is None:
        return f"{line}, no junit report"
    return (
        f"{line}, {counts.passed} passed / {counts.failed} failed / "
        f"{counts.skipped} skipped"
    )


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
