"""`ufs-chem-assay fetch`: stage the inputs the selected suites declare into
the application checkout's data directory, and pre-warm the pinned baselines."""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath

from baselines import ensure_baselines
from cli.commands import Subparsers
from logs import get_logger
from selection import resolve_suites
from settings import Settings
from staging import merge_inputs, rewrite_suite_digests, stage_inputs

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    fetch = subparsers.add_parser(
        name,
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


def handler(args: argparse.Namespace) -> int:
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
