"""The harness CLI.

`ufs-chem-assay run --config-file=X [--override K:P=V ...]`: render the
stage scripts for every configured application and run them in order with
bash, on this node. Under the slurm runtime the harness itself submits one
Slurm job per driver call.

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
from pathlib import Path

import yaml

from applications.base import Application
from applications.registry import get_application
from baselines import default_selector, load_run, plan, publish
from cli.run_config import RunConfig
from cli.shell import run_bash, write_script
from cli.stages import Stage, render_stage
from logs import configure_logging, get_logger
from models.suite_config import RunManifest
from platforms import Platform
from selection import resolve_suites
from settings import ENV_PREFIX, LEGACY_ENV_PREFIX, Settings
from staging import merge_inputs, stage_inputs

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
    fetch = subparsers.add_parser(
        "fetch",
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
    publish_parser = subparsers.add_parser(
        "publish-baselines",
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


def _fetch(args: argparse.Namespace) -> int:
    """Stage the selected suites' inputs. Settings come from the environment
    and .env exactly as in a pytest session (the flag wins over
    ASSAY_APPLICATION, as there); every failure is one ERROR line and exit 1,
    and a failed transfer never hides the others' outcomes."""
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
    results = stage_inputs(inputs, data_dir, dry_run=args.dry_run)
    failed = [result for result in results if result.action == "failed"]
    for result in failed:
        logger.error("%s: %s", result.path, result.detail)
    counts = {
        action: sum(1 for r in results if r.action == action)
        for action in ("skipped", "downloaded", "would-fetch", "failed")
    }
    logger.info(
        "fetch summary: %s",
        ", ".join(f"{count} {action}" for action, count in counts.items() if count),
    )
    return 1 if failed else 0


def _publish_baselines(args: argparse.Namespace) -> int:
    """Publish a finished run: run.yaml names the suites and the application,
    the selector (default: those suites) loads the files to repoint, and
    the shared plan/publish do the rest. Every failure is one ERROR line
    and exit 1; one baseline's failure never hides the others' outcomes."""
    output_root = args.output_root.resolve()
    run_yaml = output_root / "run.yaml"
    if not run_yaml.is_file():
        logger.error("%s is not a run's output root: no run.yaml", output_root)
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


def main(argv: list[str] | None = None) -> int:
    # The harness's namespace logger, level from the same variable pytest
    # honours (settings.log_level, with the legacy spelling as fallback).
    level = os.environ.get(f"{ENV_PREFIX}LOG_LEVEL") or os.environ.get(
        f"{LEGACY_ENV_PREFIX}LOG_LEVEL", "INFO"
    )
    configure_logging(level)
    args = _parser().parse_args(argv)
    if args.command == "fetch":
        return _fetch(args)
    if args.command == "publish-baselines":
        return _publish_baselines(args)
    return _run(args)
