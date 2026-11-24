"""`ufs-chem-assay run`: render the stage scripts of a run config and execute
them in order with bash on this node."""

from __future__ import annotations

import argparse
import os
import socket
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from ulid import ULID

from applications.base import Application, Stage
from applications.registry import get_application
from cli.commands import Subparsers
from cli.commands.watch import watch_root
from cli.run_config import ResolvedSession, RunConfig
from cli.run_config.config import load_extended
from cli.selectors import (
    build_configs,
    check_builds,
    selected_applications,
    selected_sessions,
    selected_stages,
    toolchain_configs,
)
from cli.shell import run_bash, write_script
from cli.stage_outcomes import (
    StageOutcome,
    StageState,
    newest_stage_log,
    record_outcome,
)
from cli.stages import PER_SESSION, SCRIPT_INDEX, render_stage, tests_enabled
from identity import harness_commit, harness_version
from logs import get_logger, log_to_file
from models.conventions import CONVENTIONS
from platforms import Platform

logger = get_logger("cli")

_INDEX = SCRIPT_INDEX  # <NN>-<stage>-...: the fixed slots (cli/stages.py)
# The stages that leave something for `run --watch` to wait for.
_WAITED_FOR = frozenset({Stage.TEST, Stage.HARNESS, Stage.RESUME})
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
        default=None,
        metavar="NAME",
        help=(
            "configured application(s) to run, or 'all' for every application in "
            "the config's applications: map (repeatable or space-separated, e.g. "
            "--application cece, --application 'monetio cece', or --application all); "
            "default: the config's selected_applications, else every application"
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
        "--build",
        action="extend",
        nargs="+",
        default=None,
        metavar="NAME",
        help=(
            "build variant(s) of the applications' builds: maps to build and "
            "test, or 'all' (repeatable or space-separated); default every "
            "variant. An application without builds: has its one build either way"
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
            "`watch` runs) is rendered by default but executed only when named. "
            "Default: the config's selected_stages, else every stage but resume"
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
    run.add_argument(
        "--watch",
        action="store_true",
        help=(
            "after the stages — whatever they returned — watch this root as "
            "`ufs-chem-assay watch` does with its defaults; the exit status is "
            "the stages' when they failed, else watch's. Skipped under --dry-run "
            "and when no test, harness or resume stage is selected"
        ),
    )


# The stages a build variant has its own script of; the others render once
# per application from the section as written.
_PER_BUILD = frozenset({Stage.BUILD, Stage.TEST})

# One script to execute: its path, then its lane — the application, the build
# variant (None for the unnamed build and for the stages a variant has no
# script of) and the stage — and the session of a per-session stage. A
# failure stops only its lane (_execute).
_Step = tuple[Path, str, str | None, Stage, str | None]
_Failure = tuple[Path, int]  # a failed script and its exit code


def _render(
    config: RunConfig,
    applications: list[Application],
    sessions: list[ResolvedSession],
    stages: list[Stage],
    render: list[Stage],
    builds: list[str] | None,
    scripts_dir: Path,
) -> list[_Step]:
    """Render and write one build's scripts — the config as loaded, or as
    one toolchain builds it — and return the ones to execute, in order.
    Stage-major: every application's source, then every build, ... so build
    failures surface before any pytest session; the per-session stages
    render once per application per session. An application is rendered
    only the stages it takes part in. A submit-only session's resume script
    is rendered beside its harness script whenever the harness stage is —
    `watch` runs it — but executed only when --stage resume asks. The build
    and test stages render once per selected variant of an application with
    builds:, in the map's order."""
    # The harness-wide data lines render once, under the first application
    # that has a data stage.
    shared_data_app = next(
        (app.name for app in applications if Stage.DATA in app.stages), None
    )
    to_run: list[_Step] = []
    for stage in render:
        for app in applications:
            if stage not in app.stages:
                continue
            if stage in _PER_BUILD:
                for variant in build_configs(config, app, builds):
                    if stage is Stage.TEST and not tests_enabled(variant, app):
                        logger.info(
                            "tests disabled for %s (applications.%s.tests.enabled): "
                            "no test stage",
                            app.name,
                            app.name,
                        )
                        continue
                    path = write_script(
                        render_stage(stage, variant, app), scripts_dir, _INDEX[stage]
                    )
                    logger.info("wrote %s", path)
                    build = variant.applications[app.name].build_name
                    to_run.append((path, app.name, build, stage, None))
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
                        to_run.append((path, app.name, None, stage, session.name))
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
                to_run.append((path, app.name, None, stage, None))
    return to_run


def _outcome(
    step: _Step,
    *,
    toolchain: str | None,
    run_id: str,
    state: StageState,
    code: int | None,
    started: datetime,
    log: Path | None,
    cause: str | None,
) -> StageOutcome:
    """One step's entry for the stage record, ending now."""
    path, app, build, stage, session = step
    return StageOutcome(
        script=path.name,
        stage=stage,
        application=app,
        toolchain=toolchain,
        build=build,
        session=session,
        state=state,
        exit_status=code,
        started_at=started,
        finished_at=datetime.now(UTC),
        log=log,
        cause=cause,
        run_id=run_id,
    )


def _execute(
    to_run: list[_Step],
    logs_dir: Path,
    *,
    toolchain: str | None,
    scripts_dir: Path,
    run_id: str,
) -> list[_Failure]:
    """Run one toolchain's scripts in order and return its failures (script,
    exit code), in order. A failure stops only what depends on it, within
    its application (the dependencies `watch` reports by: cli/sessions'
    tests_cause and session_cause): a failed source stage every later
    script; a failed build its variant's tests and, for an application
    without variants, its data stage and sessions; a failed data stage every
    session; a session's failed harness stage that session's resume. A
    failed test or resume stage stops nothing: no session depends on the
    application's own tests, nor on another session. Every other
    application and variant runs on, so one broken build cannot keep the
    others' test jobs from being submitted. Every script's outcome — run or
    skipped — goes into the stage record as soon as it is known
    (cli/stage_outcomes), for `report` and `watch`."""
    failures: list[_Failure] = []
    sources: dict[str, _Failure] = {}
    built: dict[tuple[str, str | None], _Failure] = {}
    data: dict[str, _Failure] = {}
    submitted: dict[tuple[str, str | None], _Failure] = {}
    for step in to_run:
        path, app, build, stage, session = step
        cause = sources.get(app) or built.get((app, build))
        if cause is None and stage in (Stage.HARNESS, Stage.RESUME):
            cause = data.get(app)
        if cause is None and stage is Stage.RESUME:
            cause = submitted.get((app, session))
        if cause is not None:
            logger.warning(
                "skipped %s: %s failed (exit %s)", path.name, cause[0].name, cause[1]
            )
            now = datetime.now(UTC)
            record_outcome(
                scripts_dir,
                _outcome(
                    step,
                    toolchain=toolchain,
                    run_id=run_id,
                    state=StageState.SKIPPED,
                    code=None,
                    started=now,
                    log=None,
                    cause=cause[0].name,
                ),
            )
            continue
        started = datetime.now(UTC)
        code = run_bash(path, logs_dir, run_id=run_id)
        log = newest_stage_log(logs_dir, path.stem)
        state = StageState.PASSED if code == 0 else StageState.FAILED
        record_outcome(
            scripts_dir,
            _outcome(
                step,
                toolchain=toolchain,
                run_id=run_id,
                state=state,
                code=code,
                started=started,
                log=log,
                cause=None,
            ),
        )
        if code == 0:
            continue
        logger.error("stage %s failed with exit %s", path.stem, code)
        failures.append((path, code))
        if stage is Stage.SOURCE:
            sources[app] = (path, code)
        elif stage is Stage.BUILD:
            built[(app, build)] = (path, code)
        elif stage is Stage.DATA:
            data[app] = (path, code)
        elif stage is Stage.HARNESS:
            submitted[(app, session)] = (path, code)
    return failures


def _refuse_driver_stages_with_builds(
    config: RunConfig, applications: list[Application], stages: list[Stage]
) -> None:
    """A driver session runs against one build, and a run config cannot yet
    say which variant: an application with builds: takes no driver stage."""
    driver = [s for s in (Stage.DATA, Stage.HARNESS, Stage.RESUME) if s in stages]
    for app in applications:
        variants = config.applications[app.name].builds
        if variants and any(stage in app.stages for stage in driver):
            raise ValueError(
                f"applications.{app.name} has builds: ({', '.join(variants)}), and "
                "the data and harness stages run a driver against one build, which "
                "a run config cannot name yet: run the source, build and test "
                "stages (--stage source --stage build --stage test)"
            )


def handler(args: argparse.Namespace) -> int:
    # The transcript, opened once the root is known, lasts until run returns.
    with ExitStack() as transcript:
        return _run(args, transcript)


def _start_facts(config_file: Path) -> list[str]:
    """What the transcript opens with: where and what ran (the file and each
    one it extends), and the library paths the shell handed the harness
    (after the bootstrap's module reset)."""
    _, files = load_extended(config_file)  # it loaded once already
    sources = [f"config {files[0]}", *(f"extends {path}" for path in files[1:])]
    return [
        (
            f"assay-run on {socket.gethostname()}: harness {harness_version()} "
            f"{harness_commit() or 'unknown'}"
        ),
        "; ".join(sources),
        (
            f"LD_LIBRARY_PATH=[{os.environ.get('LD_LIBRARY_PATH', '')}] "
            f"PYTHONPATH=[{os.environ.get('PYTHONPATH', '')}]"
        ),
    ]


def _run(args: argparse.Namespace, transcript: ExitStack) -> int:
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
        stages = selected_stages(args.stage, config.selected_stages)
        # One build per selected toolchain; the config itself without any.
        builds = toolchain_configs(config, args.toolchain)
        selected_builds = check_builds(config, applications, args.build)
        if Stage.RESUME in stages and not all(
            session.harness.submit_only for session in sessions
        ):
            raise ValueError(_RESUME_NEEDS_SUBMIT_ONLY)
        _refuse_driver_stages_with_builds(config, applications, stages)
        # The test stage is an error for an application with no test suite
        # to enable: ask every variant that will be rendered, before any is.
        if Stage.TEST in stages:
            for build in builds:
                for app in applications:
                    if Stage.TEST in app.stages:
                        for variant in build_configs(build, app, args.build):
                            tests_enabled(variant, app)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    toolchains = [build.toolchain for build in builds if build.toolchain is not None]
    scripts_dir = config.root_dir / "scripts"
    logs_dir = config.root_dir / "logs"

    # A bad root_dir: say so instead of tracing back from mkdir.
    try:
        scripts_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            "cannot create root_dir %s: %s (edit root_dir in the run config or pass --root-dir)",
            config.root_dir,
            exc.strerror,
        )
        return 1
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")  # local, as `date`
    transcript.enter_context(log_to_file(logs_dir / f"assay-run-{stamp}.log"))
    for fact in _start_facts(args.config_file):
        logger.info("%s", fact)
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

    # The effective configuration — the file plus every override, as
    # validated, and the toolchains this run selected — beside the scripts
    # it produced: re-runs the same run with no override list, and `watch`
    # and `report` read it for the sessions' and the tests' roots.
    effective = scripts_dir / CONVENTIONS.effective_config
    config.model_copy(
        update={
            "selected_toolchains": toolchains,
            "selected_builds": selected_builds,
            "selected_applications": [app.name for app in applications],
            "selected_stages": stages,
        }
    ).to_yaml(effective)
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
                build, applications, sessions, stages, render, args.build, scripts_dir
            ),
        )
        for build in builds
    ]
    if args.dry_run:
        logger.info("dry run: nothing executed")
        if args.watch:
            logger.info("run: dry run, watch skipped")
        return 0
    # Toolchain-major: the whole stage list for one toolchain, then for the
    # next. A failure stops only what depends on it (_execute), inside its
    # toolchain and never across toolchains; the run's status is the first
    # failure's.
    status = 0
    outcomes: list[tuple[str, list[tuple[Path, int]]]] = []
    run_id = str(ULID())  # one per invocation, in every outcome it records
    for toolchain, to_run in plans:
        failures = _execute(
            to_run,
            logs_dir,
            toolchain=toolchain,
            scripts_dir=scripts_dir,
            run_id=run_id,
        )
        if failures and not status:
            status = failures[0][1]
        if toolchain is not None:
            outcomes.append((toolchain, failures))
    for toolchain, failures in outcomes:
        if not failures:
            logger.info("%s: ok", toolchain)
        else:
            logger.error(
                "%s: failed at %s",
                toolchain,
                ", ".join(f"{path.name} (exit {code})" for path, code in failures),
            )
    if not args.watch:
        return status
    # Watch whatever the stages returned: a failed build still leaves the
    # other builds' test jobs submitted, and watch evaluates and reports them.
    if not _WAITED_FOR & set(stages):
        logger.info(
            "run: stages exit %s, watch skipped: no test, harness or resume stage",
            status,
        )
        return status
    watch_status = watch_root(config.root_dir)
    logger.info("run: stages exit %s, watch exit %s", status, watch_status)
    return status or watch_status
