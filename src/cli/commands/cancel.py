"""`ufs-chem-assay cancel`: scancel a session's driver jobs still in the queue
(its jobs.yaml)."""

from __future__ import annotations

import argparse
from pathlib import Path

from cli.commands import Subparsers
from jobs import cancel_jobs, read_ledger
from logs import get_logger

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    cancel_parser = subparsers.add_parser(
        name,
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


def handler(args: argparse.Namespace) -> int:
    """scancel a session's jobs still in the queue (one call); a root with
    no ledger or with nothing queued is a no-op."""
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
