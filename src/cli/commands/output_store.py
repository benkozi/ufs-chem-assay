"""Shared by upload-output and download-output: the output store from the
flag or the setting (probed when it is S3), and a transfer error's one-line
form."""

from __future__ import annotations

import argparse
import subprocess

from logs import get_logger
from s3_sync import check_reachable
from settings import Settings

logger = get_logger("cli")


def output_store(args: argparse.Namespace) -> str | None:
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


def transfer_detail(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        output = (exc.output or "").strip()
        tail = output.splitlines()[-1] if output else ""
        return f"aws s3 sync exited {exc.returncode}: {tail}"
    return str(exc)
