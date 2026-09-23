"""`ufs-chem-assay run --config-file=X [--override K:P=V ...]`: render the
stage scripts for every configured application and run them in order with
bash, on this node. Under the slurm runtime the harness itself submits one
Slurm job per driver call."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from applications.base import Application
from applications.registry import get_application
from cli.run_config import RunConfig
from cli.shell import run_bash, write_script
from cli.stages import Stage, render_stage
from logs import configure_logging, get_logger
from platforms import Platform
from settings import ENV_PREFIX, LEGACY_ENV_PREFIX

logger = get_logger("cli")

# Script file numbering: fixed slots in the canonical order, so the harness
# stage is always 05 (slot 04 is held for the application's own tests, issue #9).
_INDEX = {Stage.SOURCE: 1, Stage.BUILD: 2, Stage.DATA: 3, Stage.HARNESS: 5}
EFFECTIVE_CONFIG_NAME = "run-config.yaml"


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
        "--root-dir",
        type=Path,
        default=None,
        help=(
            "override the run root (default: the config file's root_dir, else the "
            "harness checkout's parent directory)"
        ),
    )
    run.add_argument(
        "--application",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "run only this configured application (repeatable); default: every "
            "application in the config's applications: map"
        ),
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
        "-o",
        "--override",
        action="extend",
        nargs="+",
        default=[],
        metavar="KEY:PATH=VALUE",
        help=(
            "override a run-config value before validation (repeatable; several "
            "per flag), e.g. harness:suite_config=ex3-suite.yaml "
            "applications:cece:ref=develop slurm:qos=batch; values are YAML "
            "scalars (true/false, null, [a, b])"
        ),
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="write every script under <root_dir>/scripts/ and stop: nothing executes",
    )
    return parser


def _selected_stages(requested: list[Stage] | None) -> list[Stage]:
    """The requested stages in canonical order; all of them when none given."""
    if requested is None:
        return list(Stage)
    return [stage for stage in Stage if stage in requested]


def _selected_applications(
    config: RunConfig, requested: list[str] | None
) -> list[Application]:
    """The configured applications, in the config's order, narrowed to the
    requested ones; naming an unconfigured application is an error."""
    if requested is not None:
        unknown = sorted(set(requested) - set(config.applications))
        if unknown:
            raise ValueError(
                f"--application {unknown} not in the run config's applications "
                f"({config.application_names})"
            )
    return [
        get_application(name)
        for name in config.application_names
        if requested is None or name in requested
    ]


def _run(args: argparse.Namespace) -> int:
    try:
        config = RunConfig.from_yaml(
            args.config_file,
            platform=args.platform,
            root_dir=args.root_dir,
            overrides=args.override,
        )
        applications = _selected_applications(config, args.application)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    scripts_dir = config.root_dir / "scripts"
    logs_dir = config.root_dir / "logs"
    stages = _selected_stages(args.stage)
    logger.info(
        "platform %s / runtime %s; root %s; applications: %s; stages: %s",
        config.platform.value,
        config.runtime.value,
        config.root_dir,
        ", ".join(app.name for app in applications),
        ", ".join(stage.value for stage in stages),
    )
    if args.override:
        logger.info("overrides: %s", " ".join(args.override))

    # A bad root_dir: say so instead of tracing back from mkdir.
    try:
        scripts_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            "cannot create root_dir %s: %s (edit root_dir in the run config or pass --root-dir)",
            config.root_dir,
            exc.strerror,
        )
        return 1

    # The effective configuration — the file plus every override, as
    # validated — beside the scripts it produced: re-runs the same run with
    # no override list.
    effective = scripts_dir / EFFECTIVE_CONFIG_NAME
    config.to_yaml(effective)
    logger.info("wrote %s", effective)

    # Render and write everything first so a bad config fails before any
    # stage runs. Stage-major order: every application's source, then every
    # build, ... so build failures surface before any pytest session.
    paths = [
        write_script(
            render_stage(stage, config, app, shared_data=position == 0),
            scripts_dir,
            _INDEX[stage],
        )
        for stage in stages
        for position, app in enumerate(applications)
    ]
    for path in paths:
        logger.info("wrote %s", path)
    if args.dry_run:
        logger.info("dry run: nothing executed")
        return 0
    for path in paths:
        code = run_bash(path, logs_dir)
        if code != 0:
            logger.error("stage %s failed with exit %s", path.stem, code)
            return code
    return 0


def main(argv: list[str] | None = None) -> int:
    # The harness's namespace logger, level from the same variable pytest
    # honours (settings.log_level, with the legacy spelling as fallback); the
    # CLI never loads Settings itself.
    level = os.environ.get(f"{ENV_PREFIX}LOG_LEVEL") or os.environ.get(
        f"{LEGACY_ENV_PREFIX}LOG_LEVEL", "INFO"
    )
    configure_logging(level)
    return _run(_parser().parse_args(argv))  # `run` is the only subcommand
