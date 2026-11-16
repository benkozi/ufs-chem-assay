"""`ufs-chem-assay run`: render the stage scripts of a run config and execute
them in order with bash on this node."""

from __future__ import annotations

import argparse
from pathlib import Path

from applications.base import Application, Stage
from applications.registry import get_application
from cli.commands import Subparsers
from cli.run_config import ResolvedSession, RunConfig
from cli.selectors import (
    selected_applications,
    selected_sessions,
    selected_stages,
    toolchain_configs,
)
from cli.shell import run_bash, write_script
from cli.stages import PER_SESSION, SCRIPT_INDEX, render_stage, tests_enabled
from logs import get_logger
from models.conventions import CONVENTIONS
from platforms import Platform

logger = get_logger("cli")

_INDEX = SCRIPT_INDEX  # <NN>-<stage>-...: the fixed slots (cli/stages.py)
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
        action="extend",
        nargs="+",
        required=True,
        metavar="NAME",
        help=(
            "configured application(s) to run, or 'all' for every application in "
            "the config's applications: map (repeatable or space-separated, e.g. "
            "--application cece, --application 'monetio cece', or --application all)"
        ),
    )
    run.add_argument(
        "--toolchain",
        action="extend",
        nargs="+",
        default=None,
        metavar="NAME",
        help=(
            "toolchain(s) of the config's toolchains: map to build and run with, "
            "or 'all' (repeatable or space-separated); each is its own set of "
            "scripts, checkouts and results under <root_dir>/<NAME>, run one "
            "toolchain after another. Required when the config names several; "
            "an error when it names none"
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
            f"one of {', '.join(stage.value for stage in Stage)}. test runs an "
            "application's own tests against its build, where its tests are "
            "enabled; resume (the evaluation of a submit-only session, which "
            "`watch` runs) is rendered by default but executed only when named"
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


def _render(
    config: RunConfig,
    applications: list[Application],
    sessions: list[ResolvedSession],
    stages: list[Stage],
    render: list[Stage],
    testing: dict[str, bool],
    scripts_dir: Path,
) -> list[Path]:
    """Render and write one build's scripts — the config as loaded, or as
    one toolchain builds it — and return the ones to execute, in order.
    Stage-major: every application's source, then every build, ... so build
    failures surface before any pytest session; the per-session stages
    render once per application per session. An application is rendered
    only the stages it takes part in. A submit-only session's resume script
    is rendered beside its harness script whenever the harness stage is —
    `watch` runs it — but executed only when --stage resume asks."""
    # The harness-wide data lines render once, under the first application
    # that has a data stage.
    shared_data_app = next(
        (app.name for app in applications if Stage.DATA in app.stages), None
    )
    to_run: list[Path] = []
    for stage in render:
        for app in applications:
            if stage not in app.stages:
                continue
            if stage is Stage.TEST and not testing[app.name]:
                logger.info(
                    "tests disabled for %s (applications.%s.tests.enabled): "
                    "no test stage",
                    app.name,
                    app.name,
                )
                continue
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
                        shared_data=app.name == shared_data_app,
                        sessions=sessions,
                    ),
                    scripts_dir,
                    _INDEX[stage],
                )
                logger.info("wrote %s", path)
                to_run.append(path)
    return to_run


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
        # One build per selected toolchain; the config itself without any.
        builds = toolchain_configs(config, args.toolchain)
        if Stage.RESUME in stages and not all(
            session.harness.submit_only for session in sessions
        ):
            raise ValueError(_RESUME_NEEDS_SUBMIT_ONLY)
        # The test stage exists for the applications whose own tests are
        # enabled (and is an error for one with no test suite to enable).
        testing = [
            {
                app.name: Stage.TEST in stages and tests_enabled(build, app)
                for app in applications
            }
            for build in builds
        ]
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    toolchains = [build.toolchain for build in builds if build.toolchain is not None]
    scripts_dir = config.root_dir / "scripts"
    logs_dir = config.root_dir / "logs"
    logger.info(
        "platform %s / runtime %s; root %s; applications: %s; sessions: %s; stages: %s%s",
        config.platform.value,
        config.runtime.value,
        config.root_dir,
        ", ".join(app.name for app in applications),
        ", ".join(session.name or "(nameless)" for session in sessions),
        ", ".join(stage.value for stage in stages),
        f"; toolchains: {', '.join(toolchains)}" if toolchains else "",
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
    # validated, and the toolchains this run selected — beside the scripts
    # it produced: re-runs the same run with no override list, and `watch`
    # and `report` read it for the sessions' and the tests' roots.
    effective = scripts_dir / CONVENTIONS.effective_config
    config.model_copy(update={"selected_toolchains": toolchains}).to_yaml(effective)
    logger.info("wrote %s", effective)

    # Render and write everything first — every toolchain's scripts — so a
    # bad config fails before any stage runs.
    render = list(stages)
    if Stage.HARNESS in render and Stage.RESUME not in render:
        render.append(Stage.RESUME)
    # An application is rendered only the stages it takes part in (one that
    # is built and tested has no data or harness stage; one shipped with
    # the harness no source stage either), and the run says what it skipped.
    for app in applications:
        skipped = [stage.value for stage in render if stage not in app.stages]
        if skipped:
            logger.info(
                "%s takes no part in the %s stage(s)", app.name, ", ".join(skipped)
            )
    plans = [
        (
            build.toolchain,
            _render(
                build, applications, sessions, stages, render, enabled, scripts_dir
            ),
        )
        for build, enabled in zip(builds, testing, strict=True)
    ]
    if args.dry_run:
        logger.info("dry run: nothing executed")
        return 0
    # Toolchain-major: the whole stage list for one toolchain, then for the
    # next. A failed stage ends its own toolchain's sequence only — the
    # others still run whole — and the run's status is the first failure's.
    status = 0
    outcomes: list[tuple[str, Path | None, int]] = []
    for toolchain, to_run in plans:
        failed, code = None, 0
        for path in to_run:
            code = run_bash(path, logs_dir)
            if code != 0:
                logger.error("stage %s failed with exit %s", path.stem, code)
                failed = path
                break
        if toolchain is None:
            return code
        outcomes.append((toolchain, failed, code))
        status = status or code
    for toolchain, failed, code in outcomes:
        if failed is None:
            logger.info("%s: ok", toolchain)
        else:
            logger.error("%s: failed at %s (exit %s)", toolchain, failed.name, code)
    return status
