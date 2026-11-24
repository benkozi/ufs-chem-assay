"""`ufs-chem-assay report`: the rollup of what a run's sessions left behind,
from the files alone (cli/run_report.py does the reading and the lines)."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import yaml

from cli.commands import Subparsers
from cli.run_config import HARNESS_ROOT
from cli.run_report import (
    DEFAULT_TAIL,
    RunReport,
    SessionSummary,
    StageSummary,
    TestsSummary,
    render,
    summarize_session,
    summarize_tests,
)
from cli.selectors import selected_applications, selected_sessions, toolchain_configs
from cli.sessions import (
    configured_lanes,
    configured_sessions,
    configured_tests,
    lane_outcomes,
    load_effective,
    session_started,
    stage_summaries,
    tests_started,
)
from cli.stage_outcomes import read_stage_record
from logs import as_command, get_logger, log_to_file
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
        "--toolchain",
        action="extend",
        nargs="+",
        default=None,
        metavar="NAME",
        help=(
            "with --root-dir: report only these toolchains' sessions and test "
            "runs, or 'all' (default: the ones the last `run` selected)"
        ),
    )
    report_parser.add_argument(
        "--build",
        action="extend",
        nargs="+",
        default=None,
        metavar="NAME",
        help=(
            "with --root-dir: report only these build variants' test runs, or 'all' "
            "(default: the ones the last `run` selected, else every variant)"
        ),
    )
    report_parser.add_argument(
        "--only",
        choices=("sessions", "tests"),
        default=None,
        help=(
            "with --root-dir: report only the sessions, or only the applications' "
            "own test runs (default: both)"
        ),
    )
    report_parser.add_argument(
        "--tail",
        type=int,
        default=DEFAULT_TAIL,
        metavar="N",
        help=(
            f"lines of a failed combination's .out to show, and failed tests of a "
            f"test run to name (default {DEFAULT_TAIL}; 0: none)"
        ),
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
    tests: list[TestsSummary] = []
    summaries: list[SessionSummary] = []
    stages: list[StageSummary] = []
    if args.output_root is not None:
        if args.application or args.session or args.only or args.toolchain:
            logger.error(
                "--output-root takes one root; --application/--session/--only/"
                "--toolchain select from the effective config (use --root-dir)"
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
            builds = toolchain_configs(config, args.toolchain)
            record = read_stage_record(root_dir / "scripts")
            stages = stage_summaries(
                record,
                [
                    lane
                    for build in builds
                    for lane in configured_lanes(build, applications, args.build)
                ],
                tail=args.tail,
            )
            # Only what a run started: not a session or test run whose stage
            # no run selected or recorded (cli/sessions.*_started).
            watched_tests = (
                []
                if args.only == "sessions"
                else [
                    t
                    for build in builds
                    for t in configured_tests(build, applications, args.build)
                    if tests_started(config, record, t)
                ]
            )
        except ValueError as exc:
            logger.error("%s", exc)
            return 1
        if args.only != "tests":
            summaries = [
                summarize_session(
                    w.root,
                    application=w.application,
                    session=w.session,
                    toolchain=w.toolchain,
                    logs_dir=root_dir / "logs",
                    tail=args.tail,
                )
                for build in builds
                for w in configured_sessions(
                    build, applications, sessions, root_dir / "scripts"
                )
                if session_started(config, record, w)
            ]
        tests = [
            summarize_tests(
                t.root,
                application=t.application,
                toolchain=t.toolchain,
                build=t.build,
                tail=args.tail,
                lane=lane_outcomes(record, t.toolchain, t.application, t.build),
            )
            for t in watched_tests
        ]
    report = RunReport(
        root_dir=root_dir,
        generated_at=datetime.now(UTC),
        sessions=summaries,
        tests=tests,
        stages=stages,
    )
    for line in render(report, quiet=args.quiet):
        logger.info("%s", line)
    if args.write is not None:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        with open(args.write, "w") as f:
            yaml.safe_dump(report.model_dump(mode="json"), f, sort_keys=False)
        logger.info("wrote %s", args.write)
    return 0 if report.ok else 1


def report_to(root_dir: Path, path: Path) -> int:
    """`report` with its defaults over one root, under its own command tag,
    its lines also written to `path`: what `transfer` writes as report.txt.
    Returns report's exit status."""
    parser = argparse.ArgumentParser(prog="report")
    register(parser.add_subparsers(), "report")
    args = parser.parse_args(["report", f"--root-dir={root_dir}"])
    with as_command("report"), log_to_file(path):
        return handler(args)
