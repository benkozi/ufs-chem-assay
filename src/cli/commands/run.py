"""`ufs-chem-assay run`: render the stage scripts of a run config and execute
them in order with bash on this node."""

from __future__ import annotations

import argparse
from pathlib import Path

from applications.base import Stage
from applications.registry import get_application
from cli.commands import Subparsers
from cli.run_config import RunConfig
from cli.selectors import selected_applications, selected_sessions, selected_stages
from cli.shell import run_bash, write_script
from cli.stages import PER_SESSION, render_stage
from logs import get_logger
from models.conventions import CONVENTIONS
from platforms import Platform

logger = get_logger("cli")

# Script file numbering: fixed slots in the canonical order, so the harness
# stage is always 05 (slot 04 is held for the application's own tests, issue #9)
# and its resume half 06.
_INDEX = {
    Stage.SOURCE: 1,
    Stage.BUILD: 2,
    Stage.DATA: 3,
    Stage.HARNESS: 5,
    Stage.RESUME: 6,
}
_RESUME_NEEDS_SUBMIT_ONLY = "resume is rendered only with harness.submit_only: true"


def register(subparsers: Subparsers, name: str) -> None:
    run = subparsers.add_parser(name, help="render the stage scripts and execute them")
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
        "--session",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "run only this configured session (repeatable); default: every "
            "entry of the config's sessions: map"
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
            f"one of {', '.join(stage.value for stage in Stage)}. resume (the "
            "evaluation of a submit-only session, which `watch` runs) is "
            "rendered by default but executed only when named"
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


def handler(args: argparse.Namespace) -> int:
    try:
        config = RunConfig.from_yaml(
            args.config_file,
            platform=args.platform,
            root_dir=args.root_dir,
            overrides=args.override,
            resolve_application=get_application,
        )
        applications = selected_applications(config, args.application)
        sessions = selected_sessions(config, args.session)
        stages = selected_stages(args.stage)
        if Stage.RESUME in stages and not all(
            session.harness.submit_only for session in sessions
        ):
            raise ValueError(_RESUME_NEEDS_SUBMIT_ONLY)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    scripts_dir = config.root_dir / "scripts"
    logs_dir = config.root_dir / "logs"
    logger.info(
        "platform %s / runtime %s; root %s; applications: %s; sessions: %s; stages: %s",
        config.platform.value,
        config.runtime.value,
        config.root_dir,
        ", ".join(app.name for app in applications),
        ", ".join(session.name or "(nameless)" for session in sessions),
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
    # no override list, and `watch` reads it for the sessions' roots.
    effective = scripts_dir / CONVENTIONS.effective_config
    config.to_yaml(effective)
    logger.info("wrote %s", effective)

    # Render and write everything first so a bad config fails before any
    # stage runs. Stage-major order: every application's source, then every
    # build, ... so build failures surface before any pytest session; the
    # per-session stages render once per application per session. A
    # submit-only session's resume script is rendered beside its harness
    # script whenever the harness stage is — `watch` runs it — but executed
    # only when --stage resume asks.
    render = list(stages)
    if Stage.HARNESS in render and Stage.RESUME not in render:
        render.append(Stage.RESUME)
    to_run: list[Path] = []
    for stage in render:
        for position, app in enumerate(applications):
            if stage in PER_SESSION:
                for session in sessions:
                    if stage is Stage.RESUME and not session.harness.submit_only:
                        continue
                    path = write_script(
                        render_stage(stage, config, app, session),
                        scripts_dir,
                        _INDEX[stage],
                    )
                    logger.info("wrote %s", path)
                    if stage in stages:
                        to_run.append(path)
            else:
                path = write_script(
                    render_stage(
                        stage,
                        config,
                        app,
                        shared_data=position == 0,
                        sessions=sessions,
                    ),
                    scripts_dir,
                    _INDEX[stage],
                )
                logger.info("wrote %s", path)
                to_run.append(path)
    if args.dry_run:
        logger.info("dry run: nothing executed")
        return 0
    for path in to_run:
        code = run_bash(path, logs_dir)
        if code != 0:
            logger.error("stage %s failed with exit %s", path.stem, code)
            return code
    return 0
