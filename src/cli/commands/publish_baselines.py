"""`ufs-chem-assay publish-baselines`: a finished run's compared combinations
to the store, and the suites repointed."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from baselines import default_selector, load_run, plan, publish
from cli.commands import Subparsers
from logs import get_logger
from models.conventions import CONVENTIONS
from models.suite_config import RunManifest
from s3_sync import check_reachable
from selection import resolve_suites
from settings import Settings

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    publish_parser = subparsers.add_parser(
        name,
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


def handler(args: argparse.Namespace) -> int:
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
