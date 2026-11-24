"""`ufs-chem-assay cancel`: scancel what is still in the queue — a session's
driver jobs (its jobs.yaml) or an application's own test job (its
tests.yaml)."""

from __future__ import annotations

import argparse
from pathlib import Path

import apptests
from cli.commands import Subparsers
from jobs import cancel_jobs, read_ledger
from logs import get_logger

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    cancel_parser = subparsers.add_parser(
        name,
        help=(
            "scancel what is still in the Slurm queue: a session's driver jobs "
            "(--output-root, its jobs.yaml) or an application's own test job "
            "(--tests-root, its tests.yaml)"
        ),
    )
    roots = cancel_parser.add_mutually_exclusive_group(required=True)
    roots.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="the session's output root (the directory holding jobs.yaml)",
    )
    roots.add_argument(
        "--tests-root",
        type=Path,
        default=None,
        help=(
            "an application's tests root (<root_dir>/tests/<application>, the "
            "directory holding tests.yaml)"
        ),
    )
    cancel_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="name the jobs that would be cancelled; cancel nothing",
    )


def handler(args: argparse.Namespace) -> int:
    """scancel a session's jobs — or a tests root's job — still in the
    queue (one call); a root with no ledger or with nothing queued is a
    no-op."""
    if args.tests_root is not None:
        output_root = args.tests_root.resolve()
        try:
            live = apptests.cancel(output_root, dry_run=args.dry_run)
        except ValueError as exc:  # a tests.yaml that is not a test ledger
            logger.info("%s; nothing to cancel", exc)
            return 0
    else:
        output_root = args.output_root.resolve()
        try:
            ledger = read_ledger(output_root)
        except ValueError as exc:
            logger.info("%s; nothing to cancel", exc)
            return 0
        live = cancel_jobs(ledger, dry_run=args.dry_run)
    if not live:
        logger.info("%s: no job in the queue; nothing to cancel", output_root)
    elif args.dry_run:
        logger.info("dry run: would cancel %s job(s): %s", len(live), ", ".join(live))
    else:
        logger.info("cancelled %s job(s): %s", len(live), ", ".join(live))
    return 0
