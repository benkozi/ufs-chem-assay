"""`ufs-chem-assay record-benchmarks`: a finished run's benchmark rows appended
to each suite's committed configuration file."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from baselines import default_selector
from benchmarks import (
    append_benchmarks,
    append_test_benchmarks,
    read_benchmarks_csv,
    read_test_benchmarks_csv,
)
from cli.commands import Subparsers
from logs import get_logger
from models.conventions import CONVENTIONS
from models.suite_config import RunManifest
from selection import BUILTIN_CONFIG_DIR, resolve_suites
from settings import Settings

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    record = subparsers.add_parser(
        name,
        help=(
            "append a finished run's benchmark rows (<output root>/benchmarks.csv, "
            "one per executed combination) to each suite's committed configuration "
            "benchmarks.csv — or, with --tests-root, an application's own test "
            "runs (one row each) to its committed tests-benchmarks.csv; review "
            "the diff and commit"
        ),
    )
    roots = record.add_mutually_exclusive_group(required=True)
    roots.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="the run's output root (the directory holding run.yaml and benchmarks.csv)",
    )
    roots.add_argument(
        "--tests-root",
        type=Path,
        default=None,
        help=(
            "an application's tests root (<root_dir>/tests/<application>): its "
            "benchmarks.csv holds one row per evaluated test run"
        ),
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


def _record_tests(args: argparse.Namespace) -> int:
    """A tests root's rows — one per evaluated run of the application's own
    tests — appended to the committed per-application file,
    <config dir>/<application>/tests-benchmarks.csv; runs already there are
    left alone."""
    tests_root = args.tests_root.resolve()
    per_root = tests_root / CONVENTIONS.benchmarks
    if not per_root.is_file():
        logger.error(
            "%s has no %s: no test run was evaluated there (`ufs-chem-assay watch` "
            "or evaluate-tests does that)",
            tests_root,
            CONVENTIONS.benchmarks,
        )
        return 1
    try:
        rows = read_test_benchmarks_csv(per_root)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    failed = False
    for application in sorted({row.application for row in rows}):
        mine = [row for row in rows if row.application == application]
        target = BUILTIN_CONFIG_DIR / application / CONVENTIONS.tests_benchmarks
        if args.dry_run:
            for row in mine:
                logger.info(
                    "dry run: would append test run %s to %s", row.run_id, target
                )
            continue
        try:
            result = append_test_benchmarks(target, mine)
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


def handler(args: argparse.Namespace) -> int:
    """Append a finished run's rows to the committed per-configuration
    files: run.yaml names the application and suites, the per-run
    benchmarks.csv holds the rows, and each suite's rows go to
    <suite dir>/benchmarks.csv; rows already there are left alone. Every failure is
    one ERROR line and exit 1."""
    if args.tests_root is not None:
        return _record_tests(args)
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
