"""`ufs-chem-assay run --config-file=X`: render the stages, run the
login-node ones with bash, and hand the compute stages to Slurm (or bash
when no slurm section is configured)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from cli.run_config import RunConfig
from cli.shell import run_bash, submit_sbatch, write_script
from cli.stages import COMPUTE_STAGES, Stage, render_batch, render_stage
from logs import configure_logging, get_logger
from platforms import Platform

logger = get_logger("cli")

# Script file numbering: a stage's position in the canonical order; the
# batch script takes the slot of the first compute stage it replaces.
_INDEX = {stage: position + 1 for position, stage in enumerate(Stage)}
_BATCH_INDEX = _INDEX[COMPUTE_STAGES[0]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ufs-chem-assay",
        description=(
            "Assemble and execute a harness run from one YAML run config: "
            "CECE source, driver build, data, CECE unit tests, the pytest "
            "session — directly, or as one Slurm batch job."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="render the stage scripts and execute them")
    run.add_argument(
        "--config-file",
        required=True,
        type=Path,
        help="run config YAML (templates in config/)",
    )
    run.add_argument(
        "--platform",
        type=Platform,
        choices=list(Platform),
        default=None,
        help="override the config file's platform (and hostname detection)",
    )
    run.add_argument(
        "--stage",
        action="append",
        type=Stage,
        choices=list(Stage),
        default=None,
        metavar="STAGE",
        help=(
            "run only this stage (repeatable, executed in canonical order); "
            f"one of {', '.join(stage.value for stage in Stage)}"
        ),
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="write every script under <root_dir>/scripts/ and stop: nothing executes",
    )
    run.add_argument(
        "--no-submit",
        action="store_true",
        help="write the batch script and log the sbatch command instead of submitting",
    )
    return parser


def _selected_stages(config: RunConfig, requested: list[Stage] | None) -> list[Stage]:
    if requested is not None:
        return [stage for stage in Stage if stage in requested]
    return [
        stage
        for stage in Stage
        if stage is not Stage.CECE_TESTS or config.cece.run_tests
    ]


def _run(args: argparse.Namespace) -> int:
    config = RunConfig.from_yaml(args.config_file, platform=args.platform)
    scripts_dir = config.root_dir / "scripts"
    logs_dir = config.root_dir / "logs"
    stages = _selected_stages(config, args.stage)
    logger.info(
        "platform %s / runtime %s; root %s; stages: %s",
        config.platform.value,
        config.runtime.value,
        config.root_dir,
        ", ".join(stage.value for stage in stages),
    )

    # An unedited template carries a placeholder root_dir: say so instead
    # of tracing back from mkdir.
    try:
        scripts_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            "cannot create root_dir %s: %s (edit root_dir in the run config)",
            config.root_dir,
            exc.strerror,
        )
        return 1

    # Render and write everything first so a bad config fails before any
    # stage runs. Compute stages fold into one batch script under Slurm.
    compute = [stage for stage in stages if stage in COMPUTE_STAGES]
    bash_paths = [
        write_script(render_stage(stage, config), scripts_dir, _INDEX[stage])
        for stage in stages
        if stage not in COMPUTE_STAGES or config.slurm is None
    ]
    batch_path: Path | None = None
    if compute and config.slurm is not None:
        batch_path = write_script(
            render_batch(config, compute), scripts_dir, _BATCH_INDEX
        )
    for path in [*bash_paths, *([batch_path] if batch_path else [])]:
        logger.info("wrote %s", path)

    if args.dry_run:
        logger.info("dry run: nothing executed")
        if batch_path is not None:
            logger.info("to submit: sbatch %s", batch_path)
        return 0

    for path in bash_paths:
        code = run_bash(path, logs_dir)
        if code != 0:
            logger.error("stage %s failed with exit %s", path.stem, code)
            return code
    if batch_path is not None and config.slurm is not None:
        if args.no_submit or not config.slurm.submit:
            logger.info("batch script written; submit with: sbatch %s", batch_path)
            return 0
        logs_dir.mkdir(parents=True, exist_ok=True)  # the sbatch -o target must exist
        code = submit_sbatch(batch_path)
        if code != 0:
            logger.error("sbatch failed with exit %s", code)
            return code
        logger.info("watch: squeue -u $USER; logs: %s/slurm-<jobid>.out", logs_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    # The harness's namespace logger, level from the same variable pytest
    # honours (settings.log_level); the CLI never loads Settings itself.
    configure_logging(os.environ.get("CECE_LOG_LEVEL", "INFO"))
    return _run(_parser().parse_args(argv))  # `run` is the only subcommand
