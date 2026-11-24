"""`ufs-chem-assay submit-tests`: submit an application's own test job and
record it in the tests root's ledger — what the rendered test stage calls
under the slurm runtime."""

from __future__ import annotations

import argparse
from pathlib import Path

import apptests
from cli.commands import Subparsers
from cli.run_config import HARNESS_ROOT
from cli.selectors import one_build_config, one_toolchain_config, selected_applications
from cli.sessions import job_script_for_tests, load_effective, provenance_for_tests
from cli.stages import tests_enabled
from jobs import SlurmSubmissionError
from logs import get_logger
from platforms import Runtime

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    submit = subparsers.add_parser(
        name,
        help=(
            "submit an application's own test job (the 04-test-<application>.sbatch "
            "the last `run` rendered) and record it under <root_dir>/tests/"
            "<application>/; `watch` evaluates it, `report` summarizes it. The "
            "rendered test stage calls this (slurm runtime)"
        ),
    )
    submit.add_argument(
        "--root-dir",
        type=Path,
        default=None,
        help="the run root `run` used (default: the harness checkout's parent directory)",
    )
    submit.add_argument(
        "--application",
        required=True,
        metavar="NAME",
        help="the configured application whose tests to submit",
    )
    submit.add_argument(
        "--toolchain",
        default=None,
        metavar="NAME",
        help=(
            "the toolchain whose build to test (the rendered stage passes it); "
            "needed when the run selected several"
        ),
    )
    submit.add_argument(
        "--build",
        default=None,
        metavar="NAME",
        help=(
            "the build variant whose test run to submit (the rendered stage passes "
            "it); needed when the application has several builds:"
        ),
    )
    submit.add_argument(
        "--resubmit",
        action="store_true",
        help=(
            "cancel the application's test job still in the queue and submit "
            "again (without it, a queued job is an error)"
        ),
    )


def handler(args: argparse.Namespace) -> int:
    """Everything from the effective config under --root-dir, so the job
    cannot drift from what `run` rendered: the job script, the time limit
    (tests.minutes), the queue allowance (slurm.queue_wait_s), what the run
    is recorded as running against. Every failure is one ERROR line and
    exit 1; nothing is submitted then."""
    root_dir = args.root_dir or HARNESS_ROOT.parent
    try:
        config = one_toolchain_config(load_effective(root_dir), args.toolchain)
        (app,) = selected_applications(config, [args.application])
        config = one_build_config(config, app, args.build)
        if not tests_enabled(config, app):
            raise ValueError(
                f"applications.{app.name}.tests.enabled is false: no tests to submit"
            )
        if config.runtime is not Runtime.SLURM or config.slurm is None:
            raise ValueError(
                f"submit-tests is the slurm runtime's; {config.platform.value} runs "
                f"{config.runtime.value}, where the test stage runs the tests in "
                "place and calls evaluate-tests"
            )
        section = config.applications[app.name]
        script = job_script_for_tests(
            root_dir / "scripts", app, config.toolchain, section.build_name
        )
        if not script.is_file():
            raise ValueError(
                f"{script} does not exist: `ufs-chem-assay run --config-file=... "
                "--dry-run` (same config, same --root-dir) renders it"
            )
        plan = app.test_plan(section, "junit")
        apptests.submit(
            config.tests_root(app),
            script=script,
            application=app.name,
            toolchain=config.toolchain,
            build=section.build_name,
            provenance=provenance_for_tests(config, app),
            timeout_s=section.tests.minutes * 60,
            queue_wait_s=config.slurm.queue_wait_s,
            passed_marker=plan.passed_marker if plan else None,
            resubmit=args.resubmit,
        )
    except SlurmSubmissionError as exc:
        logger.error(
            "sbatch refused the test job (exit %s): %s",
            exc.returncode,
            (exc.stderr or exc.output or "no message").strip(),
        )
        return 1
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    return 0
