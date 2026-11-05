"""`ufs-chem-assay watch`: the monitor loop over the last `run`'s submit-only
sessions — one squeue per tick, each session's resume script run when its
jobs are done (one at a time), the run report at the end, exit = the
sessions' pytest statuses. The machinery is cli/sessions.py."""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

from cli.commands import Subparsers
from cli.run_config import HARNESS_ROOT
from cli.run_report import RunReport, render, summarize_session
from cli.selectors import selected_applications, selected_sessions
from cli.sessions import (
    SessionReport,
    SessionState,
    configured_sessions,
    evaluate_session,
    load_effective,
    read_session_ledger,
    tick,
)
from logs import get_logger

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    watch_parser = subparsers.add_parser(
        name,
        help=(
            "poll Slurm for the submit-only sessions of the last `run` (its "
            "effective config under <root_dir>/scripts/) and run each session's "
            "resume script when its jobs are done, one session at a time; ends "
            "when every session is evaluated, with the sessions' pytest statuses"
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
        "--interval",
        type=int,
        default=None,
        metavar="S",
        help="seconds between polls (default: the config's slurm.poll_s)",
    )
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="report every session's state once and exit 0; evaluate nothing",
    )


def handler(args: argparse.Namespace) -> int:
    """The loop: a tick, the ready sessions evaluated one after another,
    sleep, until every selected session is evaluated or has had its one
    attempt this watch; then the run report over the watched roots. Exit 0
    when every session's pytest status is 0, 1 otherwise (or a failed
    evaluation), 130 on an interrupt; --once reports one tick and exits 0."""
    root_dir = args.root_dir or HARNESS_ROOT.parent
    try:
        config = load_effective(root_dir)
        applications = selected_applications(config, args.application)
        sessions = selected_sessions(config, args.session)
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
    elif interval <= 0:
        logger.error(
            "--interval must be a positive number of seconds; got %s", interval
        )
        return 1
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
            ledgers = {w.root: read_session_ledger(w) for w in watched}
            reports = tick(watched, ledgers)
            for w in watched:
                report = final.get(w.root, reports[w.root])
                logger.info("%s: %s", w.label, report.line())
                if report.state is SessionState.EVALUATED:
                    final[w.root] = report
            if args.once:
                return 0
            for w in watched:
                if w.root in final or reports[w.root].state is not SessionState.READY:
                    continue
                logger.info("%s: jobs done; evaluating with %s", w.label, w.script.name)
                final[w.root] = evaluate_session(w, logs_dir)
                if final[w.root].state is SessionState.EVALUATED:
                    logger.info("%s: %s", w.label, final[w.root].line())
            if len(final) == len(watched):
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("interrupted; run `ufs-chem-assay watch` again to continue")
        return 130
    failed = [w.label for w in watched if final[w.root].state is SessionState.FAILED]
    nonzero = [
        w.label
        for w in watched
        if final[w.root].state is SessionState.EVALUATED
        and final[w.root].exit_status != 0
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
