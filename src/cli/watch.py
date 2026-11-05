"""`ufs-chem-assay watch` and `ufs-chem-assay cancel`: the monitor loop
over a run's submit-only sessions, and the scancel of one session's jobs.

`watch` is dumb on purpose. It reads the effective config `run` wrote
(`<root_dir>/scripts/run-config.yaml` — never a config file and overrides
of its own, so the resume session's exports cannot drift from the submit
session's), the sessions' job ledgers and `squeue`; when a session's jobs
have all left the queue and its ledger says "not evaluated", it runs the
resume script `run` rendered beside the harness script — one session at a
time, so the committed files (benchmarks.csv, the suite files) are written
serially — and it ends when every selected session is evaluated, with the
sessions' own pytest statuses as its exit. It owns no test logic and no
Slurm semantics beyond "is this job still queued"; it is safe to kill and
restart at any point (a session already evaluated is skipped by its
ledger's evaluated_at; a resume that died is tried again by the next
watch). It ends with the run report (cli/run_report.py) over the watched
roots — the same summary `ufs-chem-assay report` prints."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from applications.base import Application, Stage
from applications.registry import get_application
from cli.run_config import HARNESS_ROOT, ResolvedSession, RunConfig
from cli.run_report import RunReport, render, summarize_session
from cli.shell import run_bash
from jobs import JobLedger, cancel_jobs, queue_states, read_ledger
from logs import get_logger
from models.base import StrictModel
from models.conventions import CONVENTIONS
from resolution import resolve_output_roots

logger = get_logger("cli")

# The script numbering is cli/main's; the resume slot is named here so the
# two modules cannot import each other.
_RESUME_INDEX = 6

# squeue's %T values while a job has not started yet; anything else in the
# queue is counted as running (RUNNING, COMPLETING, ...).
_PENDING_STATES = frozenset(
    {"PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_HOLD", "SUSPENDED", "UNKNOWN"}
)

SessionState = Literal["not submitted", "waiting", "ready", "evaluated", "failed"]

# The selection helpers are cli/main's (they serve `run` too); injected so
# this module never imports main.
SelectApplications = Callable[[RunConfig, list[str] | None], list[Application]]
SelectSessions = Callable[[RunConfig, list[str] | None], list[ResolvedSession]]


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

    state: SessionState = Field(
        description=(
            "not submitted: no ledger yet; waiting: a job is still queued; ready: "
            "every job gone, not evaluated; evaluated: the ledger says so; failed: "
            "this watch ran the resume and the ledger still says not evaluated"
        )
    )
    queued: int = Field(default=0, description="Jobs not started yet")
    running: int = Field(default=0, description="Jobs in the queue and started")
    done: int = Field(default=0, description="Jobs that have left the queue")
    exit_status: int | None = Field(
        default=None, description="The evaluating session's exit status, when evaluated"
    )

    def line(self) -> str:
        if self.state == "not submitted":
            return "not submitted"
        if self.state == "evaluated":
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
        config.runtime,
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


def _read(watched: WatchedSession) -> JobLedger | None:
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
            reports[watched.root] = SessionReport(state="not submitted")
            continue
        if ledger.evaluated_at is not None:
            reports[watched.root] = SessionReport(
                state="evaluated", exit_status=ledger.exit_status
            )
            continue
        queued = running = done = 0
        for job in ledger.jobs:
            state = states.get(job.job_id)
            if state is None:
                done += 1
            elif state in _PENDING_STATES:
                queued += 1
            else:
                running += 1
        reports[watched.root] = SessionReport(
            state="ready" if queued == running == 0 else "waiting",
            queued=queued,
            running=running,
            done=done,
        )
    return reports


def _evaluate(watched: WatchedSession, logs_dir: Path) -> SessionReport:
    """Run the session's resume script and read the ledger back: evaluated,
    or failed (the session died; its log says why)."""
    code = run_bash(watched.script, logs_dir)
    ledger = _read(watched)
    if ledger is not None and ledger.evaluated_at is not None:
        return SessionReport(state="evaluated", exit_status=ledger.exit_status)
    logger.error(
        "%s: evaluation failed (exit %s); see the log of %s, then run "
        "`ufs-chem-assay watch` again or the script by hand",
        watched.label,
        code,
        watched.script.name,
    )
    return SessionReport(state="failed", exit_status=code)


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


def watch(
    args: argparse.Namespace,
    select_applications: SelectApplications,
    select_sessions: SelectSessions,
) -> int:
    """The loop: a tick, the ready sessions evaluated one after another,
    sleep, until every selected session is evaluated or has had its one
    attempt this watch; then the run report over the watched roots. Exit 0
    when every session's pytest status is 0, 1 otherwise (or a failed
    evaluation), 130 on an interrupt; --once reports one tick and exits 0."""
    root_dir = args.root_dir or HARNESS_ROOT.parent
    try:
        config = load_effective(root_dir)
        applications = select_applications(config, args.application)
        sessions = select_sessions(config, args.session)
        not_submit_only = [
            s.name or "(nameless)" for s in sessions if not s.harness.submit_only
        ]
        if not_submit_only:
            raise ValueError(
                f"nothing to watch: session(s) {not_submit_only} do not set "
                "harness.submit_only (their harness stage evaluates in place)"
            )
        watched = configured_sessions(
            config, applications, sessions, root_dir / "scripts"
        )
        missing = [w.script for w in watched if not w.script.is_file()]
        if missing:
            raise ValueError(
                f"resume script(s) missing: {', '.join(str(p) for p in missing)}; "
                "`ufs-chem-assay run --dry-run` (same config, same --root-dir) renders them"
            )
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    interval = args.interval
    if interval is None:
        interval = config.slurm.poll_s if config.slurm is not None else 30
    logs_dir = root_dir / "logs"
    logger.info(
        "watching %s session(s) under %s every %ss: %s",
        len(watched),
        root_dir,
        interval,
        ", ".join(w.label for w in watched),
    )
    final: dict[Path, SessionReport] = {}  # evaluated or failed this watch
    try:
        while True:
            ledgers = {w.root: _read(w) for w in watched}
            reports = tick(watched, ledgers)
            for w in watched:
                report = final.get(w.root, reports[w.root])
                logger.info("%s: %s", w.label, report.line())
                if report.state == "evaluated":
                    final[w.root] = report
            if args.once:
                return 0
            for w in watched:
                if w.root in final or reports[w.root].state != "ready":
                    continue
                logger.info("%s: jobs done; evaluating with %s", w.label, w.script.name)
                final[w.root] = _evaluate(w, logs_dir)
                if final[w.root].state == "evaluated":
                    logger.info("%s: %s", w.label, final[w.root].line())
            if len(final) == len(watched):
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("interrupted; run `ufs-chem-assay watch` again to continue")
        return 130
    failed = [w.label for w in watched if final[w.root].state == "failed"]
    nonzero = [
        w.label
        for w in watched
        if final[w.root].state == "evaluated" and final[w.root].exit_status != 0
    ]
    if failed:
        logger.error("evaluation failed: %s", ", ".join(failed))
    run_report = RunReport(
        root_dir=root_dir,
        generated_at=datetime.now(UTC),
        sessions=[
            summarize_session(
                w.root, application=w.application, session=w.session, logs_dir=logs_dir
            )
            for w in watched
        ],
    )
    for line in render(run_report):
        logger.info("%s", line)
    return 1 if failed or nonzero else 0


def cancel(args: argparse.Namespace) -> int:
    """scancel a session's jobs still in the queue (one call); a root with
    no ledger or with nothing queued is a no-op."""
    output_root = args.output_root.resolve()
    try:
        ledger = read_ledger(output_root)
    except ValueError as exc:
        logger.info("%s; nothing to cancel", exc)
        return 0
    live = cancel_jobs(ledger, dry_run=args.dry_run)
    if not live:
        logger.info("%s: no job in the queue; nothing to cancel", output_root)
    elif args.dry_run:
        logger.info("dry run: would cancel %s job(s): %s", len(live), ", ".join(live))
    else:
        logger.info("cancelled %s job(s): %s", len(live), ", ".join(live))
    return 0
