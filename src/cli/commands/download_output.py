"""`ufs-chem-assay download-output`: a run's output root back from the store
by its run ULID."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from cli.commands import Subparsers
from cli.commands.output_store import output_store, transfer_detail
from logs import get_logger
from outputs import download_output

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    download = subparsers.add_parser(
        name,
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


def handler(args: argparse.Namespace) -> int:
    """Download a run's output root by ULID into --dest. Every failure is one
    ERROR line and exit 1."""
    store = output_store(args)
    if store is None:
        return 1
    try:
        target = download_output(
            args.ulid, store, args.dest.resolve(), dry_run=args.dry_run
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        logger.error("download of %s failed: %s", args.ulid, transfer_detail(exc))
        return 1
    logger.info(
        "%srun %s -> %s",
        "dry run: " if args.dry_run else "downloaded ",
        args.ulid,
        target,
    )
    return 0
