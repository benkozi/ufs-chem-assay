"""`ufs-chem-assay evaluate-tests`: turn a finished run of an application's
own tests into a verdict and a benchmark row — what the rendered test stage
calls under docker and native, and the by-hand path under slurm when
`watch` is not used."""

from __future__ import annotations

import argparse
from pathlib import Path

import apptests
from cli.commands import Subparsers
from cli.run_config import HARNESS_ROOT
from cli.selectors import selected_applications
from cli.sessions import load_effective, test_provenance, tests_line
from cli.stages import tests_enabled
from logs import get_logger
from platforms import Runtime

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    evaluate = subparsers.add_parser(
        name,
        help=(
            "record the verdict of an application's own test run under "
            "<root_dir>/tests/<application>/: the junit counts, one benchmark row. "
            "slurm: the submitted job, once it has left the queue (`watch` does "
            "this by itself); docker and native: the run the test stage just "
            "made, with --exit-status"
        ),
    )
    evaluate.add_argument(
        "--root-dir",
        type=Path,
        default=None,
        help="the run root `run` used (default: the harness checkout's parent directory)",
    )
    evaluate.add_argument(
        "--application",
        required=True,
        metavar="NAME",
        help="the configured application whose test run to evaluate",
    )
    evaluate.add_argument(
        "--exit-status",
        type=int,
        default=None,
        metavar="N",
        help=(
            "docker and native: the test command's exit status (the rendered "
            "stage passes it); ignored under slurm, where the job's accounting decides"
        ),
    )


def handler(args: argparse.Namespace) -> int:
    """Exit 0 when the run was evaluated and recorded — whatever its
    verdict, which the log line and the ledger carry (the stage script
    exits with the tests' own status). Every failure to evaluate is one
    ERROR line and exit 1."""
    root_dir = args.root_dir or HARNESS_ROOT.parent
    try:
        config = load_effective(root_dir)
        (app,) = selected_applications(config, [args.application])
        if not tests_enabled(config, app):
            raise ValueError(
                f"applications.{app.name}.tests.enabled is false: no tests to evaluate"
            )
        tests_root = config.tests_root(app)
        if config.runtime is Runtime.SLURM:
            ledger = apptests.evaluate(tests_root)
        else:
            if args.exit_status is None:
                raise ValueError(
                    f"evaluate-tests needs --exit-status under the "
                    f"{config.runtime.value} runtime: the test command's exit "
                    "status (the rendered test stage passes it)"
                )
            plan = app.test_plan(config.applications[app.name], "junit")
            apptests.record_local(
                tests_root,
                application=app.name,
                toolchain=None,
                provenance=test_provenance(config, app),
                passed_marker=plan.passed_marker if plan else None,
            )
            ledger = apptests.evaluate(tests_root, exit_status=args.exit_status)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    logger.info("tests %s: %s", app.name, tests_line(ledger))
    return 0
