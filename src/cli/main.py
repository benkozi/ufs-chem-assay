"""The harness CLI.

`ufs-chem-assay run --config-file=X [--session NAME ...] [--override
K:P=V ...] [--watch]`: render the stage scripts for the selected
applications (the flags, else the file's selected_* keys, else every one) —
per session for the harness and resume stages — and run them in order
with bash, on this node; with --watch, then watch the root. A run config
may `extends:` another. Under the slurm runtime the harness stage's
pytest session submits every driver job at once and, with
harness.submit_only, stops; the resume stage (rendered beside it,
executed under --stage resume) evaluates the finished jobs.

`ufs-chem-assay watch [--root-dir DIR] [--session NAME ...] [--interval S]
[--once]`: the loop over the last `run`'s submit-only sessions — poll
Slurm, run each session's resume script when its jobs are done, exit with
the sessions' statuses. `ufs-chem-assay cancel --output-root PATH`:
scancel a session's jobs still queued (cli/watch.py). `ufs-chem-assay
report [--root-dir DIR | --output-root PATH] [--session NAME ...] [--tail N]
[--write PATH] [--quiet]`: the rollup of what the sessions left behind —
states, test counts, one block per issue with the paths to open — from the
files alone, exit 1 when anything is wrong (cli/run_report.py).

`ufs-chem-assay submit-tests --application NAME [--root-dir DIR]
[--resubmit]` and `evaluate-tests --application NAME [--root-dir DIR]
[--exit-status N]`: the two halves of an application's own test run that
the rendered test stage calls — submit the test job and record it (slurm);
turn a finished run into a verdict and a benchmark row (apptests.py).

`ufs-chem-assay fetch [--application NAME] [--suite-config SELECTOR]
[--dry-run]`: stage the inputs the selected suites declare into the
application checkout's data directory — the same selection and settings a
pytest session would use; the run config's data stage calls it.

`ufs-chem-assay publish-baselines --output-root PATH [--application NAME]
[--suite-config SELECTOR] [--store s3://bucket/prefix] [--dry-run]
[--no-suite-update]`: publish a finished run's compared combinations as
new baselines in the store and repoint the suites at them (baselines.py;
the pytest session's --publish-baselines does the same at session end).

`ufs-chem-assay transfer --config-file=X [--name NAME] [--dry-run]`: copy
the root's evidence and report to the config's transfer_dir, then git
pull, add, commit and push there (cli/commands/transfer.py)."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from enum import StrEnum, unique
from typing import Protocol

from cli.commands import (
    Subparsers,
    cancel,
    download_output,
    evaluate_tests,
    fetch,
    publish_baselines,
    record_benchmarks,
    report,
    run,
    submit_tests,
    transfer,
    upload_output,
    watch,
)
from logs import configure_logging, set_command
from settings import Settings


@unique
class Command(StrEnum):
    """The subcommands, each the name `argparse` sees and the key of its handler."""

    RUN = "run"
    FETCH = "fetch"
    PUBLISH_BASELINES = "publish-baselines"
    RECORD_BENCHMARKS = "record-benchmarks"
    UPLOAD_OUTPUT = "upload-output"
    DOWNLOAD_OUTPUT = "download-output"
    WATCH = "watch"
    CANCEL = "cancel"
    REPORT = "report"
    SUBMIT_TESTS = "submit-tests"
    EVALUATE_TESTS = "evaluate-tests"
    TRANSFER = "transfer"


class CommandModule(Protocol):
    """What every module under cli/commands provides."""

    def register(self, subparsers: Subparsers, name: str) -> None:
        """Add the subcommand's parser under `name`."""

    def handler(self, args: argparse.Namespace) -> int:
        """Run the subcommand; returns its exit code."""


# One module per subcommand, in help order; `argparse` makes an unknown name
# unreachable and the test suite makes a Command without a module a failure.
COMMANDS: dict[Command, CommandModule] = {
    Command.RUN: run,
    Command.FETCH: fetch,
    Command.PUBLISH_BASELINES: publish_baselines,
    Command.RECORD_BENCHMARKS: record_benchmarks,
    Command.UPLOAD_OUTPUT: upload_output,
    Command.DOWNLOAD_OUTPUT: download_output,
    Command.WATCH: watch,
    Command.CANCEL: cancel,
    Command.REPORT: report,
    Command.SUBMIT_TESTS: submit_tests,
    Command.EVALUATE_TESTS: evaluate_tests,
    Command.TRANSFER: transfer,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ufs-chem-assay",
        description=(
            "Assemble and execute a harness run from one YAML run config: "
            "target-driver source and build, input data, the pytest session — "
            "on this node; under the slurm runtime each driver run is a Slurm job. "
            "Command-line arguments are overrides on the file."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, module in COMMANDS.items():
        module.register(subparsers, command.value)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging(Settings().log_level)
    args = _parser().parse_args(argv)
    set_command(args.command)  # every line from here on says which command
    return HANDLERS[Command(args.command)](args)


# The dispatch table `main` reads at call time (the tests swap entries).
HANDLERS: dict[Command, Callable[[argparse.Namespace], int]] = {
    command: module.handler for command, module in COMMANDS.items()
}
