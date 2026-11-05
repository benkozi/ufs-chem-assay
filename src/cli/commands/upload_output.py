"""`ufs-chem-assay upload-output`: a finished run's whole output root to the
output store under its run ULID."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from cli.commands import Subparsers
from cli.commands.output_store import output_store, transfer_detail
from logs import get_logger
from models.conventions import CONVENTIONS
from outputs import upload_output

logger = get_logger("cli")


def register(subparsers: Subparsers, name: str) -> None:
    upload = subparsers.add_parser(
        name,
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


def handler(args: argparse.Namespace) -> int:
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
    store = output_store(args)
    if store is None:
        return 1
    try:
        record = upload_output(output_root, store, dry_run=args.dry_run)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        logger.error("upload of %s failed: %s", output_root, transfer_detail(exc))
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
