"""The harness CLI.

`ufs-chem-assay run --config-file=X [--session NAME ...] [--override
K:P=V ...]`: render the stage scripts for every configured application —
per session for the harness and resume stages — and run them in order
with bash, on this node. Under the slurm runtime the harness stage's
pytest session submits every driver job at once and, with
harness.submit_only, stops; the resume stage (rendered beside it,
executed under --stage resume) evaluates the finished jobs.

`ufs-chem-assay watch [--root-dir DIR] [--session NAME ...] [--interval S]
[--once]`: the loop over the last `run`'s submit-only sessions — poll
Slurm, run each session's resume script when its jobs are done, exit with
the sessions' statuses. `ufs-chem-assay cancel --output-root PATH`:
scancel a session's jobs still queued (cli/watch.py).

`ufs-chem-assay fetch [--application NAME] [--suite-config SELECTOR]
[--dry-run]`: stage the inputs the selected suites declare into the
application checkout's data directory — the same selection and settings a
pytest session would use; the run config's data stage calls it.

`ufs-chem-assay publish-baselines --output-root PATH [--application NAME]
[--suite-config SELECTOR] [--store s3://bucket/prefix] [--dry-run]
[--no-suite-update]`: publish a finished run's compared combinations as
new baselines in the store and repoint the suites at them (baselines.py;
the pytest session's --publish-baselines does the same at session end)."""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Callable
from enum import StrEnum, unique
from pathlib import Path, PurePosixPath

import yaml

from applications.base import Application, Stage
from applications.registry import get_application
from baselines import default_selector, ensure_baselines, load_run, plan, publish
from benchmarks import append_benchmarks, read_benchmarks_csv
from cli.run_config import ResolvedSession, RunConfig
from cli.shell import run_bash, write_script
from cli.stages import PER_SESSION, render_stage
from cli.watch import cancel, watch
from logs import configure_logging, get_logger
from models.conventions import CONVENTIONS
from models.suite_config import RunManifest
from outputs import download_output, upload_output
from platforms import Platform
from s3_sync import check_reachable
from selection import resolve_suites
from settings import Settings
from staging import merge_inputs, rewrite_suite_digests, stage_inputs

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
    run = subparsers.add_parser(
        Command.RUN, help="render the stage scripts and execute them"
    )
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
    fetch = subparsers.add_parser(
        Command.FETCH,
        help=(
            "stage the inputs the selected suites declare into the application "
            "checkout's data directory (present, verified files are skipped)"
        ),
    )
    fetch.add_argument(
        "--application",
        default=None,
        metavar="NAME",
        help="the session's application (overrides ASSAY_APPLICATION), as for pytest",
    )
    fetch.add_argument(
        "--suite-config",
        default=None,
        metavar="SELECTOR",
        help=(
            "suite selector, exactly as pytest's --suite-config: an existing file "
            "or a regex fullmatched against *-suite.yaml names and search-root-"
            "relative paths; default: the application's default suite"
        ),
    )
    fetch.add_argument(
        "--dry-run",
        action="store_true",
        help="report what is present and what would be fetched; transfer nothing",
    )
    fetch.add_argument(
        "--record-digests",
        action="store_true",
        help=(
            "for every source-less input (url null) present whose digest differs "
            "from the declared one, write the on-disk sha256 into the suite file(s) "
            "declaring it — then review the diff and commit"
        ),
    )
    publish_parser = subparsers.add_parser(
        Command.PUBLISH_BASELINES,
        help=(
            "publish a finished run's compared combinations (the suites' "
            "baseline_comparisons entries) as new baselines in the S3 store and "
            "repoint the suite files at the new ULIDs"
        ),
    )
    publish_parser.add_argument(
        "--output-root",
        required=True,
        type=Path,
        help="the run's output root (the directory holding run.yaml), as a host path",
    )
    publish_parser.add_argument(
        "--application",
        default=None,
        metavar="NAME",
        help="the run's application (overrides ASSAY_APPLICATION), as for pytest",
    )
    publish_parser.add_argument(
        "--suite-config",
        default=None,
        metavar="SELECTOR",
        help=(
            "suite selector, exactly as pytest's --suite-config; default: the "
            "suites run.yaml records, by the <name>-suite.yaml convention"
        ),
    )
    publish_parser.add_argument(
        "--store",
        default=None,
        metavar="S3_PREFIX",
        help=(
            "override ASSAY_BASELINE_STORE for this call: an s3://bucket/prefix "
            "or a local directory"
        ),
    )
    publish_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "the full plan: the probe, the wrapper's own --dryrun upload lines, the "
            "manifest each baseline would carry; nothing written, no suite edited"
        ),
    )
    publish_parser.add_argument(
        "--no-suite-update",
        action="store_true",
        help="publish, but leave the suite files alone (the ULIDs are logged)",
    )
    record = subparsers.add_parser(
        Command.RECORD_BENCHMARKS,
        help=(
            "append a finished run's benchmark rows (<output root>/benchmarks.csv, "
            "one per executed combination) to each suite's committed configuration "
            "benchmarks.csv; review the diff and commit"
        ),
    )
    record.add_argument(
        "--output-root",
        required=True,
        type=Path,
        help="the run's output root (the directory holding run.yaml and benchmarks.csv)",
    )
    record.add_argument(
        "--application",
        default=None,
        metavar="NAME",
        help="the run's application (overrides ASSAY_APPLICATION), as for pytest",
    )
    record.add_argument(
        "--suite-config",
        default=None,
        metavar="SELECTOR",
        help=(
            "suite selector, exactly as pytest's --suite-config; default: the "
            "suites run.yaml records, by the <name>-suite.yaml convention"
        ),
    )
    record.add_argument(
        "--dry-run",
        action="store_true",
        help="log the rows that would be appended to which file; write nothing",
    )
    upload = subparsers.add_parser(
        Command.UPLOAD_OUTPUT,
        help=(
            "copy a finished run's whole output root to the output store under "
            "its run ULID (<store>/<run_id>/); resumable, nothing deleted"
        ),
    )
    upload.add_argument(
        "--output-root",
        required=True,
        type=Path,
        help="the run's output root (the directory holding run.yaml), as a host path",
    )
    upload.add_argument(
        "--store",
        default=None,
        metavar="STORE",
        help="override ASSAY_OUTPUT_STORE: an s3://bucket/prefix or a local directory",
    )
    upload.add_argument(
        "--dry-run",
        action="store_true",
        help="the wrapper's own --dryrun plan (or a file count for a directory store); nothing written",
    )
    download = subparsers.add_parser(
        Command.DOWNLOAD_OUTPUT,
        help="copy a run's output root back from the output store by its run ULID",
    )
    download.add_argument(
        "--ulid", required=True, metavar="RUN_ID", help="the run ULID"
    )
    download.add_argument(
        "--dest",
        required=True,
        type=Path,
        help="the directory to download into; the root lands at <dest>/<run ULID>/",
    )
    download.add_argument(
        "--store",
        default=None,
        metavar="STORE",
        help="override ASSAY_OUTPUT_STORE: an s3://bucket/prefix or a local directory",
    )
    download.add_argument(
        "--dry-run",
        action="store_true",
        help="the wrapper's own --dryrun plan (or a file count for a directory store); nothing written",
    )
    watch_parser = subparsers.add_parser(
        Command.WATCH,
        help=(
            "poll Slurm for the submit-only sessions of the last `run` (its "
            "effective config under <root_dir>/scripts/) and run each session's "
            "resume script when its jobs are done, one session at a time; ends "
            "when every session is evaluated, with the sessions' pytest statuses"
        ),
    )
    watch_parser.add_argument(
        "--root-dir",
        type=Path,
        default=None,
        help="the run root `run` used (default: the harness checkout's parent directory)",
    )
    watch_parser.add_argument(
        "--application",
        action="append",
        default=None,
        metavar="NAME",
        help="watch only this configured application (repeatable)",
    )
    watch_parser.add_argument(
        "--session",
        action="append",
        default=None,
        metavar="NAME",
        help="watch only this configured session (repeatable)",
    )
    watch_parser.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="S",
        help="seconds between polls (default: the config's slurm.poll_s)",
    )
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="report every session's state once and exit 0; evaluate nothing",
    )
    cancel_parser = subparsers.add_parser(
        Command.CANCEL,
        help="scancel a session's driver jobs still in the Slurm queue (its jobs.yaml)",
    )
    cancel_parser.add_argument(
        "--output-root",
        required=True,
        type=Path,
        help="the session's output root (the directory holding jobs.yaml)",
    )
    cancel_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="name the jobs that would be cancelled; cancel nothing",
    )
    return parser


def _selected_stages(requested: list[Stage] | None) -> list[Stage]:
    """The requested stages in canonical order; all but resume when none
    given — a plain `run` must not block on the evaluation it just deferred."""
    if requested is None:
        return [stage for stage in Stage if stage is not Stage.RESUME]
    return [stage for stage in Stage if stage in requested]


def _selected_sessions(
    config: RunConfig, requested: list[str] | None
) -> list[ResolvedSession]:
    """The configured sessions, in the config's order, narrowed to the
    requested names; naming an unconfigured session is an error, as is
    naming any on a config without sessions:."""
    sessions = config.resolved_sessions()
    if requested is None:
        return sessions
    if not config.sessions:
        raise ValueError(
            f"--session {requested}: the run config has no sessions: map "
            "(it is one nameless session)"
        )
    unknown = sorted(set(requested) - set(config.sessions))
    if unknown:
        raise ValueError(
            f"--session {unknown} not in the run config's sessions "
            f"({list(config.sessions)})"
        )
    return [session for session in sessions if session.name in requested]


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
            resolve_application=get_application,
        )
        applications = _selected_applications(config, args.application)
        sessions = _selected_sessions(config, args.session)
        stages = _selected_stages(args.stage)
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


def _fetch(args: argparse.Namespace) -> int:
    """Stage the selected suites' inputs. Settings come from the environment
    and .env exactly as in a pytest session (the flag wins over
    ASSAY_APPLICATION, as there); every failure is one ERROR line and exit 1,
    and a failed transfer never hides the others' outcomes."""
    if args.record_digests and args.dry_run:
        logger.error("--record-digests writes the suite files; drop --dry-run")
        return 1
    settings = (
        Settings()
        if args.application is None
        else Settings(application=args.application)
    )
    try:
        resolved = resolve_suites(settings, args.suite_config)
        inputs = merge_inputs([suite for _, suite in resolved.suites])
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    app, app_settings = resolved.application, resolved.app_settings
    if app_settings.root_dir is None:
        logger.error(
            "fetch stages inputs into the %s checkout, which is not configured; set %sROOT_DIR",
            app.name,
            app.env_prefix,
        )
        return 1
    if not app_settings.root_dir.is_dir():
        logger.error(
            "%s root %s is not an existing directory; check %sROOT_DIR",
            app.name,
            app_settings.root_dir,
            app.env_prefix,
        )
        return 1
    data_dir = app.data_dir(app_settings.root_dir)
    logger.info(
        "%s%s input(s) declared by %s into %s",
        "dry run: " if args.dry_run else "staging ",
        len(inputs),
        ", ".join(suite.name for _, suite in resolved.suites),
        data_dir,
    )
    results = stage_inputs(
        inputs,
        data_dir,
        dry_run=args.dry_run,
        search_path=settings.input_search_path,
        record_digests=args.record_digests,
        platform=settings.platform,
    )
    failed = [result for result in results if result.action == "failed"]
    for result in failed:
        logger.error("%s: %s", result.path, result.detail)
    # The recorded digests go into every selected suite that declares the
    # dst (identical entries across suites); a failed rewrite is an error.
    recorded = {
        PurePosixPath(result.path.relative_to(data_dir)): result.sha256
        for result in results
        if result.action == "recorded" and result.sha256 is not None
    }
    rewrite_failed = False
    for suite_path, suite in resolved.suites:
        updates = {
            entry.dst: recorded[entry.dst]
            for entry in suite.inputs
            if entry.url is None and entry.dst in recorded
        }
        if not updates:
            continue
        try:
            rewrite_suite_digests(suite_path, updates)
        except ValueError as exc:
            logger.error("%s", exc)
            rewrite_failed = True
            continue
        for dst, digest in updates.items():
            logger.info("%s: recorded sha256 %s for %s", suite_path, digest, dst)
        logger.info("%s: review the diff and commit it", suite_path)
    counts = {
        action: sum(1 for r in results if r.action == action)
        for action in (
            "skipped",
            "downloaded",
            "would-fetch",
            "linked",
            "would-link",
            "recorded",
            "failed",
        )
    }
    logger.info(
        "fetch summary: %s",
        ", ".join(f"{count} {action}" for action, count in counts.items() if count),
    )
    # The pinned baselines too, into the baseline root (the pre-warm a
    # login node does for a session that may run without network); skipped
    # when comparisons are off (CI: no credentials for the private store).
    failed_baselines = 0
    if settings.enable_baseline_comparisons:
        staged = ensure_baselines(
            [suite for _, suite in resolved.suites],
            settings.baseline_store,
            settings.baseline_root_dir,
            dry_run=args.dry_run,
        )
        # ensure_baselines logs the states itself, grouped by state and reason.
        failed_baselines = sum(1 for r in staged if r.state == "failed")
    return 1 if failed or rewrite_failed or failed_baselines else 0


def _publish_baselines(args: argparse.Namespace) -> int:
    """Publish a finished run: run.yaml names the suites and the application,
    the selector (default: those suites) loads the files to repoint, and
    the shared plan/publish do the rest. Every failure is one ERROR line
    and exit 1; one baseline's failure never hides the others' outcomes."""
    output_root = args.output_root.resolve()
    run_yaml = output_root / CONVENTIONS.run_manifest
    if not run_yaml.is_file():
        logger.error(
            "%s is not a run's output root: no %s",
            output_root,
            CONVENTIONS.run_manifest,
        )
        return 1
    with open(run_yaml) as f:
        run = RunManifest.model_validate(yaml.safe_load(f))
    try:
        # Init kwargs beat the environment: the flags win, as for pytest.
        if args.application is None and args.store is None:
            settings = Settings()
        elif args.store is None:
            settings = Settings(application=args.application)
        elif args.application is None:
            settings = Settings(baseline_store=args.store)
        else:
            settings = Settings(application=args.application, baseline_store=args.store)
        resolved = resolve_suites(settings, args.suite_config or default_selector(run))
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    if resolved.application.name != run.application:
        logger.error(
            "run %s is a %s run, but the selected suites are %s suites",
            run.run_id,
            run.application,
            resolved.application.name,
        )
        return 1
    recorded = {suite.name for suite in run.suites}
    loaded = {suite.name for _, suite in resolved.suites}
    if recorded - loaded:
        logger.error(
            "run %s executed suite(s) %s that the selection does not include "
            "(loaded: %s); pass --suite-config to select them",
            run.run_id,
            sorted(recorded - loaded),
            sorted(loaded),
        )
        return 1
    if settings.baseline_store.startswith("s3://"):
        try:
            check_reachable(settings.baseline_store)
        except ConnectionError as exc:
            logger.error("%s; publish from a node with network", exc)
            return 1
    try:
        manifest, inputs, rows = load_run(
            output_root, resolved.application, resolved.suites
        )
    except (ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    records = plan(manifest, inputs, rows, output_root, settings.baseline_store)
    logger.info(
        "%s%s baseline(s) to publish from run %s to %s",
        "dry run: " if args.dry_run else "",
        sum(1 for r in records if r.action == "would-publish"),
        manifest.run_id,
        settings.baseline_store,
    )
    results = publish(
        records,
        run=manifest,
        output_root=output_root,
        baseline_root=settings.baseline_root_dir,
        dry_run=args.dry_run,
        suite_update=not args.no_suite_update,
    )
    return 1 if any(r.action == "failed" for r in results) else 0


def _record_benchmarks(args: argparse.Namespace) -> int:
    """Append a finished run's rows to the committed per-configuration
    files: run.yaml names the application and suites, the per-run
    benchmarks.csv holds the rows, and each suite's rows go to
    <suite dir>/benchmarks.csv; rows already there are left alone. Every failure is
    one ERROR line and exit 1."""
    output_root = args.output_root.resolve()
    run_yaml = output_root / CONVENTIONS.run_manifest
    per_run = output_root / CONVENTIONS.benchmarks
    if not run_yaml.is_file():
        logger.error(
            "%s is not a run's output root: no %s",
            output_root,
            CONVENTIONS.run_manifest,
        )
        return 1
    if not per_run.is_file():
        logger.error(
            "%s has no %s (a run older than benchmarking, or a dry run); re-run "
            "the session to produce one",
            output_root,
            CONVENTIONS.benchmarks,
        )
        return 1
    with open(run_yaml) as f:
        run = RunManifest.model_validate(yaml.safe_load(f))
    try:
        rows = read_benchmarks_csv(per_run)
        settings = (
            Settings()
            if args.application is None
            else Settings(application=args.application)
        )
        resolved = resolve_suites(settings, args.suite_config or default_selector(run))
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    if resolved.application.name != run.application:
        logger.error(
            "run %s is a %s run, but the selected suites are %s suites",
            run.run_id,
            run.application,
            resolved.application.name,
        )
        return 1
    if not rows:
        logger.info("%s holds no rows (nothing executed); nothing to record", per_run)
        return 0
    paths = {suite.name: path for path, suite in resolved.suites}
    failed = False
    for suite_name in sorted({row.suite for row in rows}):
        mine = [row for row in rows if row.suite == suite_name]
        if suite_name not in paths:
            logger.error(
                "suite %r of run %s is not in the selection; its %s row(s) were not recorded",
                suite_name,
                run.run_id,
                len(mine),
            )
            failed = True
            continue
        target = paths[suite_name].parent / CONVENTIONS.benchmarks
        if args.dry_run:
            for row in mine:
                logger.info(
                    "dry run: would append %s %s to %s", row.combo_id, row.combo, target
                )
            continue
        try:
            result = append_benchmarks(target, mine)
        except ValueError as exc:
            logger.error("%s", exc)
            failed = True
            continue
        logger.info(
            "%s: %s appended, %s already recorded; review the diff and commit it",
            target,
            result.appended,
            result.skipped,
        )
    return 1 if failed else 0


def _output_store(args: argparse.Namespace) -> str | None:
    """The output store for the two output subcommands: the --store override
    as an init kwarg (validated like the setting), else the setting. None
    with the error logged when the store is unreachable or invalid."""
    try:
        settings = (
            Settings() if args.store is None else Settings(output_store=args.store)
        )
    except ValueError as exc:
        logger.error("%s", exc)
        return None
    store = settings.output_store
    if store.startswith("s3://"):
        try:
            check_reachable(store)
        except ConnectionError as exc:
            logger.error("%s; run this from a node with network", exc)
            return None
    return store


def _upload_output(args: argparse.Namespace) -> int:
    """Upload a finished run's output root under its run ULID. Every failure
    is one ERROR line and exit 1."""
    output_root = args.output_root.resolve()
    if not (output_root / CONVENTIONS.run_manifest).is_file():
        logger.error(
            "%s is not a run's output root: no %s",
            output_root,
            CONVENTIONS.run_manifest,
        )
        return 1
    store = _output_store(args)
    if store is None:
        return 1
    try:
        record = upload_output(output_root, store, dry_run=args.dry_run)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        logger.error("upload of %s failed: %s", output_root, _transfer_detail(exc))
        return 1
    logger.info(
        "%srun %s -> %s (%s file(s), %s bytes)",
        "dry run: " if args.dry_run else "uploaded ",
        record.run_id,
        record.destination,
        record.files,
        record.bytes,
    )
    return 0


def _download_output(args: argparse.Namespace) -> int:
    """Download a run's output root by ULID into --dest. Every failure is one
    ERROR line and exit 1."""
    store = _output_store(args)
    if store is None:
        return 1
    try:
        target = download_output(
            args.ulid, store, args.dest.resolve(), dry_run=args.dry_run
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        logger.error("download of %s failed: %s", args.ulid, _transfer_detail(exc))
        return 1
    logger.info(
        "%srun %s -> %s",
        "dry run: " if args.dry_run else "downloaded ",
        args.ulid,
        target,
    )
    return 0


def _transfer_detail(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        output = (exc.output or "").strip()
        tail = output.splitlines()[-1] if output else ""
        return f"aws s3 sync exited {exc.returncode}: {tail}"
    return str(exc)


def main(argv: list[str] | None = None) -> int:
    # The harness's namespace logger, level from the same variable pytest
    # honours (settings.log_level, with the legacy spelling as fallback).
    level = os.environ.get(CONVENTIONS.env_var("log_level")) or os.environ.get(
        CONVENTIONS.env_var("log_level", legacy=True), "INFO"
    )
    configure_logging(level)
    args = _parser().parse_args(argv)
    return HANDLERS[Command(args.command)](args)


# One handler per subcommand; `argparse` makes an unknown name unreachable and
# the test suite makes a member without a handler a failure.
HANDLERS: dict[Command, Callable[[argparse.Namespace], int]] = {
    Command.RUN: _run,
    Command.FETCH: _fetch,
    Command.PUBLISH_BASELINES: _publish_baselines,
    Command.RECORD_BENCHMARKS: _record_benchmarks,
    Command.UPLOAD_OUTPUT: _upload_output,
    Command.DOWNLOAD_OUTPUT: _download_output,
    Command.WATCH: lambda args: watch(args, _selected_applications, _selected_sessions),
    Command.CANCEL: cancel,
}
