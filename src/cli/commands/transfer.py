"""`ufs-chem-assay transfer`: a root's evidence — its logs, rendered scripts
and records, tests roots, CMake caches, and the report — copied to
<transfer_dir>/<name>, then pulled, added, committed and pushed in the
transfer repository, for reading on another machine. The root and the
destination come from the run config (`root_dir`, `transfer_dir`)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from applications.registry import get_application
from cli.commands import Subparsers
from cli.commands.report import report_to
from cli.run_config import RunConfig
from logs import get_logger
from models.conventions import CONVENTIONS

logger = get_logger("cli")

# What is copied: each glob under the root, to <destination>/<prefix>/<the
# match's path under the root>. The evidence a run leaves, and nothing it
# checked out or built.
COPIES: tuple[tuple[str, str], ...] = (
    ("logs", "."),  # stage logs and run transcripts
    ("scripts/*.yaml", "."),  # stages.yaml, run-config.yaml
    ("scripts/*.sh", "."),  # the rendered scripts
    ("scripts/*.sbatch", "."),
    ("*/tests", "."),  # every toolchain's tests roots
    ("*/*/build*/CMakeCache.txt", "cmake"),  # each build's configured options
)
REPORT = "report.txt"


def register(subparsers: Subparsers, name: str) -> None:
    transfer = subparsers.add_parser(
        name,
        help=(
            "copy a root's logs, rendered scripts and records, tests roots, CMake "
            "caches and report to <transfer_dir>/<name>, then git pull, add, "
            "commit and push the transfer repository"
        ),
    )
    transfer.add_argument(
        "--config-file",
        required=True,
        type=Path,
        help="the run config whose root_dir is copied and whose transfer_dir receives it",
    )
    transfer.add_argument(
        "--name",
        default=None,
        help="the destination directory's name (default: assay-<YYYYmmdd-HHMM>); it must not exist",
    )
    transfer.add_argument(
        "--dry-run",
        action="store_true",
        help="log what would be copied and the git commands; copy and push nothing",
    )


def _matches(root: Path) -> list[tuple[Path, Path]]:
    """(source, path under the destination) for everything COPIES names."""
    found: list[tuple[Path, Path]] = []
    for pattern, prefix in COPIES:
        for source in sorted(root.glob(pattern)):
            found.append((source, Path(prefix) / source.relative_to(root)))
    return found


def _size(path: Path) -> str:
    """The tree's size in the largest unit (to GB) that keeps it at or above 1."""
    total = float(sum(p.stat().st_size for p in path.rglob("*") if p.is_file()))
    units = ["B", "KB", "MB", "GB"]
    while total >= 1024 and len(units) > 1:
        total /= 1024
        units.pop(0)
    return f"{total:.1f} {units[0]}"


def handler(args: argparse.Namespace) -> int:
    try:
        config = RunConfig.from_yaml(
            args.config_file, resolve_application=get_application
        )
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    if config.transfer_dir is None:
        logger.error(
            "transfer: %s sets no transfer_dir: name the directory (inside the "
            "transfer git repository) this root's evidence goes to",
            args.config_file,
        )
        return 1
    root = config.root_dir
    if not (root / "scripts" / CONVENTIONS.effective_config).is_file():
        logger.error(
            "transfer: nothing to transfer: %s holds no run (no scripts/%s)",
            root,
            CONVENTIONS.effective_config,
        )
        return 1
    name = args.name or f"assay-{datetime.now().astimezone().strftime('%Y%m%d-%H%M')}"
    repo = config.transfer_dir
    destination = repo / name
    if destination.exists():
        logger.error("transfer: %s already exists: pass another --name", destination)
        return 1
    git = [
        ["pull"],
        ["add", "-f", name],
        ["commit", "-m", name],
        ["push"],
    ]
    matches = _matches(root)
    if args.dry_run:
        for source, under in matches:
            logger.info("dry run: would copy %s to %s", source, destination / under)
        logger.info("dry run: would write %s", destination / REPORT)
        for command in git:
            logger.info("dry run: would run `git %s`", " ".join(command))
        logger.info("transfer: dry run; nothing copied or pushed")
        return 0

    logger.info("transfer: %s to %s", root, destination)
    for source, under in matches:
        target = destination / under
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    destination.mkdir(parents=True, exist_ok=True)
    # The report on the live root; its issues are still the report.
    status = report_to(root, destination / REPORT)
    logger.info("transfer: wrote %s (report exit %s)", destination / REPORT, status)

    for command in git:
        shown = " ".join(["git", *command])
        logger.info("transfer: %s", shown)
        result = subprocess.run(["git", *command], cwd=repo, check=False)
        if result.returncode != 0:
            logger.error(
                "transfer: `%s` failed (exit %s) in %s; the copy stays in %s",
                shown,
                result.returncode,
                repo,
                destination,
            )
            return 1
    logger.info("transfer: pushed %s (%s)", destination, _size(destination))
    return 0
