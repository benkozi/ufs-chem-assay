"""`ufs-chem-assay report`: the rollup of what a run's sessions left behind,
from the files alone (cli/run_report.py does the reading and the lines)."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import yaml

from cli.commands import Subparsers
from cli.run_config import HARNESS_ROOT
from cli.run_report import DEFAULT_TAIL, RunReport, render, summarize_session
from cli.selectors import selected_applications, selected_sessions
from cli.sessions import configured_sessions, load_effective
from logs import get_logger
from models.conventions import CONVENTIONS

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    report_parser = subparsers.add_parser(
        name,
        help=(
            "summarize what the last `run`'s sessions left behind (or one output "
            "root): one line per session, one block per issue with the reasons and "
            "the paths to open; files only, no Slurm; exit 1 when anything is wrong "
            "or not finished"
        ),
    )
    roots = report_parser.add_mutually_exclusive_group()
    roots.add_argument(
        "--root-dir",
        type=Path,
        default=None,
        help=(
            "the run root `run` used: its effective config names the sessions, its "
            "logs/ supplies the details (default: the harness checkout's parent)"
        ),
    )
    roots.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help=(
            "one session's output root instead (a downloaded one works): no "
            "effective config, no logs; excludes --application and --session"
        ),
    )
    report_parser.add_argument(
        "--application",
        action="append",
        default=None,
        metavar="NAME",
        help="report only this configured application (repeatable)",
    )
    report_parser.add_argument(
        "--session",
        action="append",
        default=None,
        metavar="NAME",
        help="report only this configured session (repeatable)",
    )
    report_parser.add_argument(
        "--tail",
        type=int,
        default=DEFAULT_TAIL,
        metavar="N",
        help=f"lines of a failed combination's .out to show (default {DEFAULT_TAIL}; 0: none)",
    )
    report_parser.add_argument(
        "--write",
        type=Path,
        default=None,
        metavar="PATH",
        help="also write the report as YAML to this path (nothing is written into a root)",
    )
    report_parser.add_argument(
        "--quiet",
        action="store_true",
        help="the session lines and the verdict only; no issue blocks",
    )


def handler(args: argparse.Namespace) -> int:
    """The run report: the sessions of the effective config under --root-dir
    (every configured one, submit-only or not; logs from <root_dir>/logs),
    or the one root --output-root names (application from its run.yaml, the
    directory name as the session, no logs). Exit 0 when every session is
    evaluated, exited 0 and has no issue; 1 otherwise."""
    if args.output_root is not None:
        if args.application or args.session:
            logger.error(
                "--output-root takes one root; --application/--session select "
                "sessions of the effective config (use --root-dir)"
            )
            return 1
        root = args.output_root.resolve()
        manifest = root / CONVENTIONS.run_manifest
        application = "unknown"
        if manifest.is_file():
            with open(manifest) as f:
                application = str(yaml.safe_load(f).get("application", application))
        logger.info("logs: none (output root given directly)")
        summaries = [
            summarize_session(
                root, application=application, session=root.name, tail=args.tail
            )
        ]
        root_dir = None
    else:
        root_dir = args.root_dir or HARNESS_ROOT.parent
        try:
            config = load_effective(root_dir)
            applications = selected_applications(config, args.application)
            sessions = selected_sessions(config, args.session)
        except ValueError as exc:
            logger.error("%s", exc)
            return 1
        configured = configured_sessions(
            config, applications, sessions, root_dir / "scripts"
        )
        summaries = [
            summarize_session(
                w.root,
                application=w.application,
                session=w.session,
                logs_dir=root_dir / "logs",
                tail=args.tail,
            )
            for w in configured
        ]
    report = RunReport(
        root_dir=root_dir, generated_at=datetime.now(UTC), sessions=summaries
    )
    for line in render(report, quiet=args.quiet):
        logger.info("%s", line)
    if args.write is not None:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        with open(args.write, "w") as f:
            yaml.safe_dump(report.model_dump(mode="json"), f, sort_keys=False)
        logger.info("wrote %s", args.write)
    return 0 if report.ok else 1
