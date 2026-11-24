"""`ufs-chem-assay watch`: the monitor loop over the last `run`'s submit-only
sessions and its applications' own test jobs — one squeue per tick for all
of them, each session's resume script run when its jobs are done (one at a
time), each test run evaluated in-process when its job is done, the run
report at the end, exit = the sessions' pytest statuses and the test runs'
verdicts. The machinery is cli/sessions.py."""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

import apptests
from cli.commands import Subparsers
from cli.run_config import HARNESS_ROOT
from cli.run_report import RunReport, render, summarize_session, summarize_tests
from cli.selectors import (
    check_builds,
    selected_applications,
    selected_sessions,
    toolchain_configs,
)
from cli.sessions import (
    SessionReport,
    SessionState,
    TestsReport,
    TestsState,
    WatchedSession,
    configured_lanes,
    configured_sessions,
    configured_tests,
    evaluate_session,
    evaluate_tests,
    lane_outcomes,
    load_effective,
    poll,
    read_session_ledger,
    read_tests_ledgers,
    session_cause,
    session_started,
    stage_summaries,
    tests_cause,
    tests_started,
    tick,
    tick_tests,
)
from cli.stage_outcomes import read_stage_record
from logs import as_command, get_logger

logger = get_logger("cli")

# How many checks a session or test run may stay not submitted, with nothing
# in the stage record to say why, before watch stops waiting for it: about
# five minutes at the default 30 s poll, time for a `run` started alongside
# to submit what it renders.
_NOT_SUBMITTED_TICKS = 10


def register(subparsers: Subparsers, name: str) -> None:
    watch_parser = subparsers.add_parser(
        name,
        help=(
            "poll Slurm for the submit-only sessions of the last `run` (its "
            "effective config under <root_dir>/scripts/) and for its applications' "
            "own test jobs; run each session's resume script when its jobs are "
            "done, one session at a time, and evaluate each test run when its job "
            "is done; ends when everything is evaluated, with the sessions' pytest "
            "statuses and the test runs' verdicts"
        ),
    )
    watch_parser.add_argument(
        "--root-dir",
        type=Path,
        default=None,
        help="the run root `run` used (default: the harness checkout's parent directory)",
    )
    watch_parser.add_argument(
        "--application",
        action="append",
        default=None,
        metavar="NAME",
        help="watch only this configured application (repeatable)",
    )
    watch_parser.add_argument(
        "--session",
        action="append",
        default=None,
        metavar="NAME",
        help="watch only this configured session (repeatable)",
    )
    watch_parser.add_argument(
        "--toolchain",
        action="extend",
        nargs="+",
        default=None,
        metavar="NAME",
        help=(
            "watch only these toolchains' sessions and test jobs, or 'all' "
            "(default: the ones the last `run` selected)"
        ),
    )
    watch_parser.add_argument(
        "--build",
        action="extend",
        nargs="+",
        default=None,
        metavar="NAME",
        help=(
            "watch only these build variants' test jobs, or 'all' "
            "(default: the ones the last `run` selected, else every variant)"
        ),
    )
    watch_parser.add_argument(
        "--only",
        choices=("sessions", "tests"),
        default=None,
        help=(
            "watch only the sessions, or only the applications' own test jobs "
            "(default: both; `tests` is the choice after a run of the source, "
            "build and test stages alone)"
        ),
    )
    watch_parser.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="S",
        help="seconds between polls (default: the config's slurm.poll_s)",
    )
    watch_parser.add_argument(
        "--not-submitted-ticks",
        type=int,
        default=_NOT_SUBMITTED_TICKS,
        metavar="N",
        help=(
            "stop waiting for a session or test run that is still not submitted "
            f"after N checks (default {_NOT_SUBMITTED_TICKS}); one the stage record "
            "says will not be submitted (its build failed, say) is settled at the "
            "first check. Either way it is reported as not submitted, exit 1"
        ),
    )
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="report every session's state once and exit 0; evaluate nothing",
    )


def watch_root(root_dir: Path) -> int:
    """`watch` with its defaults over one root, under its own command tag:
    what `run --watch` calls after its stages."""
    parser = argparse.ArgumentParser(prog="watch")
    register(parser.add_subparsers(), "watch")
    args = parser.parse_args(["watch", f"--root-dir={root_dir}"])
    with as_command("watch"):
        return handler(args)


def _give_up(
    cause: str | None, root: Path, counts: dict[Path, int], limit: int
) -> str | None:
    """Why watch stops waiting for a root that is not submitted, or None to
    keep waiting: the stage record's reason at once, else after `limit`
    checks in a row."""
    if cause is not None:
        return cause
    counts[root] = counts.get(root, 0) + 1
    if counts[root] >= limit:
        return f"still not submitted after {counts[root]} checks"
    return None


def handler(args: argparse.Namespace) -> int:
    """The loop: a tick (one squeue for every session's and test run's
    jobs), the ready sessions evaluated one after another and the ready test
    runs evaluated in-process, sleep, until everything selected is
    evaluated or has had its one attempt this watch; then the run report
    over the watched roots. Exit 0 when every session's pytest status and
    every test run's verdict is 0, 1 otherwise (or a failed evaluation),
    130 on an interrupt; --once reports one tick and exits 0."""
    root_dir = args.root_dir or HARNESS_ROOT.parent
    try:
        config = load_effective(root_dir)
        applications = selected_applications(config, args.application)
        # One build per selected toolchain (the config itself without any):
        # each has its own session roots, resume scripts and tests roots.
        builds = toolchain_configs(config, args.toolchain)
        check_builds(config, applications, args.build)
        # Only what a run started (cli/sessions.*_started): a session or
        # test run whose stage no run selected or recorded is not waited for.
        started = read_stage_record(root_dir / "scripts")
        configured = (
            []
            if args.only == "sessions"
            else [
                t
                for build in builds
                for t in configured_tests(build, applications, args.build)
            ]
        )
        tests = [t for t in configured if tests_started(config, started, t)]
        lanes = [
            lane
            for build in builds
            for lane in configured_lanes(build, applications, args.build)
        ]
        tests_alone = (
            "; `--only tests` watches the applications' own test jobs alone"
            if tests
            else ""
        )
        watched: list[WatchedSession] = []
        if args.only != "tests":
            sessions = selected_sessions(config, args.session)
            watched = [
                w
                for build in builds
                for w in configured_sessions(
                    build, applications, sessions, root_dir / "scripts"
                )
                if session_started(config, started, w)
            ]
            names = {w.session for w in watched}
            not_submit_only = [
                s.name or "(nameless)"
                for s in sessions
                if s.name in names and not s.harness.submit_only
            ]
            if not_submit_only:
                raise ValueError(
                    f"nothing to watch: session(s) {not_submit_only} do not set "
                    "harness.submit_only (their harness stage evaluates in place)"
                    + tests_alone
                )
            missing = [w.script for w in watched if not w.script.is_file()]
            if missing:
                raise ValueError(
                    f"resume script(s) missing: {', '.join(str(p) for p in missing)}; "
                    "`ufs-chem-assay run --dry-run` (same config, same --root-dir) "
                    "renders them" + tests_alone
                )
        elif not configured:
            raise ValueError(
                "nothing to watch: no application has tests enabled "
                "(applications.<name>.tests.enabled)"
            )
        if not watched and not tests:
            selected = ", ".join(config.selected_stages) or "every stage"
            raise ValueError(
                f"nothing to watch: the last run (stages {selected}) started no "
                "session or test run, and the stage record holds none"
            )
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    interval = args.interval
    if interval is None:
        interval = config.slurm.poll_s if config.slurm is not None else 30
    elif interval <= 0:
        logger.error(
            "--interval must be a positive number of seconds; got %s", interval
        )
        return 1
    if args.not_submitted_ticks <= 0:
        logger.error(
            "--not-submitted-ticks must be a positive number of checks; got %s",
            args.not_submitted_ticks,
        )
        return 1
    logs_dir = root_dir / "logs"
    logger.info(
        "watching %s session(s) and %s test run(s) under %s every %ss: %s",
        len(watched),
        len(tests),
        root_dir,
        interval,
        ", ".join([*(w.label for w in watched), *(t.label for t in tests)]),
    )
    final: dict[Path, SessionReport] = {}  # evaluated, failed or given up this watch
    final_tests: dict[Path, TestsReport] = {}
    unsubmitted: dict[Path, int] = {}  # consecutive not-submitted checks per root
    try:
        while True:
            ledgers = {w.root: read_session_ledger(w) for w in watched}
            test_ledgers, unreadable = read_tests_ledgers(tests)
            for t in tests:
                if t.root in unreadable and t.root not in final_tests:
                    final_tests[t.root] = unreadable[t.root]
            states = poll(ledgers.values(), test_ledgers.values())
            reports = tick(watched, ledgers, states)
            test_reports = tick_tests(tests, test_ledgers, states)
            # Re-read each check: a run started alongside records as it goes.
            record = read_stage_record(root_dir / "scripts")
            limit = args.not_submitted_ticks
            for w in watched:
                waiting = reports[w.root].state is SessionState.NOT_SUBMITTED
                if w.root not in final and waiting:
                    detail = _give_up(
                        session_cause(record, w), w.root, unsubmitted, limit
                    )
                    if detail is not None:
                        final[w.root] = SessionReport(
                            state=SessionState.NOT_SUBMITTED, detail=detail
                        )
            for t in tests:
                waiting = test_reports[t.root].state is TestsState.NOT_SUBMITTED
                if t.root not in final_tests and waiting:
                    detail = _give_up(
                        tests_cause(record, t), t.root, unsubmitted, limit
                    )
                    if detail is not None:
                        final_tests[t.root] = TestsReport(
                            state=TestsState.NOT_SUBMITTED, detail=detail
                        )
            for w in watched:
                report = final.get(w.root, reports[w.root])
                logger.info("%s: %s", w.label, report.line())
                if report.state is SessionState.EVALUATED:
                    final[w.root] = report
            for t in tests:
                test_report = final_tests.get(t.root, test_reports[t.root])
                logger.info("%s: %s", t.label, test_report.line())
                if test_report.state is TestsState.EVALUATED:
                    final_tests[t.root] = test_report
            if args.once:
                return 0
            for w in watched:
                if w.root in final or reports[w.root].state is not SessionState.READY:
                    continue
                logger.info("%s: jobs done; evaluating with %s", w.label, w.script.name)
                final[w.root] = evaluate_session(w, logs_dir)
                if final[w.root].state is SessionState.EVALUATED:
                    logger.info("%s: %s", w.label, final[w.root].line())
            for t in tests:
                if t.root in final_tests:
                    continue
                test_report = test_reports[t.root]
                ledger = test_ledgers[t.root]
                if test_report.state is TestsState.READY:
                    logger.info(
                        "%s: job %s done; evaluating", t.label, test_report.job_id
                    )
                    final_tests[t.root] = evaluate_tests(t)
                    if final_tests[t.root].state is TestsState.EVALUATED:
                        logger.info("%s: %s", t.label, final_tests[t.root].line())
                    else:
                        logger.error("%s: %s", t.label, final_tests[t.root].line())
                elif test_report.state is TestsState.WAITING and ledger is not None:
                    # Past its deadline it is cancelled; the next tick sees
                    # it gone and evaluates what Slurm recorded.
                    apptests.cancel_overdue(ledger, datetime.now(UTC))
            if len(final) == len(watched) and len(final_tests) == len(tests):
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("interrupted; run `ufs-chem-assay watch` again to continue")
        return 130
    failed = [w.label for w in watched if final[w.root].state is SessionState.FAILED]
    failed += [t.label for t in tests if final_tests[t.root].state is TestsState.FAILED]
    never = [
        w.label for w in watched if final[w.root].state is SessionState.NOT_SUBMITTED
    ]
    never += [
        t.label for t in tests if final_tests[t.root].state is TestsState.NOT_SUBMITTED
    ]
    nonzero = [
        w.label
        for w in watched
        if final[w.root].state is SessionState.EVALUATED
        and final[w.root].exit_status != 0
    ]
    nonzero += [
        t.label
        for t in tests
        if final_tests[t.root].state is TestsState.EVALUATED
        and final_tests[t.root].exit_status != 0
    ]
    if failed:
        logger.error("evaluation failed: %s", ", ".join(failed))
    if never:
        logger.error("not submitted: %s", ", ".join(never))
    # The stage record as it is now: a run may have finished since the watch began.
    record = read_stage_record(root_dir / "scripts")
    run_report = RunReport(
        root_dir=root_dir,
        generated_at=datetime.now(UTC),
        sessions=[
            summarize_session(
                w.root,
                application=w.application,
                session=w.session,
                toolchain=w.toolchain,
                logs_dir=logs_dir,
            )
            for w in watched
        ],
        tests=[
            summarize_tests(
                t.root,
                application=t.application,
                toolchain=t.toolchain,
                build=t.build,
                lane=lane_outcomes(record, t.toolchain, t.application, t.build),
            )
            for t in tests
        ],
        stages=stage_summaries(record, lanes),
    )
    for line in render(run_report):
        logger.info("%s", line)
    return 1 if failed or nonzero or never else 0
