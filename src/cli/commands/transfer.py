"""`ufs-chem-assay transfer`: a root's evidence — its logs, rendered scripts
and records, tests roots, CMake caches, and the report — zipped into one
payload, <transfer_dir>/<name>.zip (unzipping it gives <name>/) — the
transfer repository pulled first (fast-forward only), the payload then
added, committed and pushed — for reading on another machine. Logs compress well, and one file is one object to push.
The root and the destination come from the run config (`root_dir`,
`transfer_dir`)."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from applications.registry import get_application
from cli.commands import Subparsers
from cli.commands.report import report_to
from cli.run_config import RunConfig
from logs import get_logger
from models.conventions import CONVENTIONS

logger = get_logger("cli")

# What is archived, as <name>/<prefix>/<the match's path under the root>: the
# evidence a run leaves, and nothing it checked out or built. COPIES are
# globs under the root; BUILD_COPIES are globs under each build root — the
# root itself, or each toolchain's directory under it (_build_roots) — named
# from the run config, never by a wildcard: without toolchains a checkout
# sits directly under the root, and `*/tests` would be its source tree's.
COPIES: tuple[tuple[str, str], ...] = (
    ("logs", "."),  # stage logs and run transcripts
    ("scripts/*.yaml", "."),  # stages.yaml, run-config.yaml
    ("scripts/*.sh", "."),  # the rendered scripts
    ("scripts/*.sbatch", "."),
)
BUILD_COPIES: tuple[tuple[str, str], ...] = (
    (CONVENTIONS.tests_dirname, "."),  # the tests roots
    ("*/build*/CMakeCache.txt", "cmake"),  # each build's configured options
)
REPORT = "report.txt"
# A payload's name: a plain file name. It is a path in the transfer
# repository, the top directory in the zip and an argument to git.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def register(subparsers: Subparsers, name: str) -> None:
    transfer = subparsers.add_parser(
        name,
        help=(
            "zip a root's logs, rendered scripts and records, tests roots, CMake "
            "caches and report into <transfer_dir>/<name>.zip (unzipping to "
            "<name>/), then git pull, add, commit and push the transfer repository"
        ),
    )
    transfer.add_argument(
        "--config-file",
        required=True,
        type=Path,
        help="the run config whose root_dir is archived and whose transfer_dir receives it",
    )
    transfer.add_argument(
        "--name",
        default=None,
        help=(
            "the payload's name, <name>.zip holding <name>/ (default: "
            "assay-<YYYYmmdd-HHMM>): a plain file name (letters, digits, `.`, "
            "`_`, `-`); neither may exist in transfer_dir"
        ),
    )
    transfer.add_argument(
        "--dry-run",
        action="store_true",
        help="log what would be archived and the git commands; archive and push nothing",
    )


def _build_roots(config: RunConfig) -> list[Path]:
    """Where the config's builds keep their checkouts and tests roots: each
    toolchain's directory under the root, or the root itself without any."""
    return [config.root_dir / name for name in config.toolchains] or [config.root_dir]


def _files(root: Path, build_roots: list[Path]) -> list[tuple[Path, Path]]:
    """(file, path under the payload's top directory) for every file COPIES
    names under the root and BUILD_COPIES names under each build root, a
    matched directory contributing every file under it."""
    found: list[tuple[Path, Path]] = []
    globs = [
        (root, COPIES),
        *((build_root, BUILD_COPIES) for build_root in build_roots),
    ]
    for base, copies in globs:
        for pattern, prefix in copies:
            for source in sorted(base.glob(pattern)):
                sources = sorted(p for p in source.rglob("*") if p.is_file())
                for path in sources if source.is_dir() else [source]:
                    found.append((path, Path(prefix) / path.relative_to(root)))
    return found


def _archive(
    archive: Path, name: str, files: list[tuple[Path, Path]], report: Path
) -> None:
    """The payload, written beside its name and renamed once complete, so a
    failed write never leaves something that looks finished."""
    partial = archive.with_name(f".{archive.name}.partial")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, under in files:
                zf.write(path, f"{name}/{under.as_posix()}")
            zf.write(report, f"{name}/{REPORT}")
        os.replace(partial, archive)
    finally:
        partial.unlink(missing_ok=True)


def _size(total_bytes: int) -> str:
    """A size in the largest unit (to GB) that keeps it at or above 1."""
    total = float(total_bytes)
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
    name = args.name
    if name is None:
        name = f"assay-{datetime.now().astimezone().strftime('%Y%m%d-%H%M')}"
    if not _NAME.fullmatch(name):
        logger.error(
            "transfer: --name %r is not a plain file name (letters, digits, "
            "`.`, `_` and `-`, starting with a letter or digit)",
            name,
        )
        return 1
    repo = config.transfer_dir
    archive = repo / f"{name}.zip"
    pull = ["pull", "--ff-only"]
    git = [
        ["add", "-f", "--", archive.name],
        ["commit", "-m", name],
        ["push"],
    ]
    if not args.dry_run:
        # First, before anything is written: what the pull brings in may
        # hold the name, and a repository that cannot be brought up to date
        # gets no untracked payload left in it.
        shown = " ".join(["git", *pull])
        logger.info("transfer: %s", shown)
        result = subprocess.run(["git", *pull], cwd=repo, check=False)
        if result.returncode != 0:
            logger.error(
                "transfer: `%s` failed (exit %s) in %s; nothing archived",
                shown,
                result.returncode,
                repo,
            )
            return 1
    # The directory too: unzipping the payload would land on it.
    for taken in (archive, repo / name):
        if taken.exists():
            logger.error("transfer: %s already exists: pass another --name", taken)
            return 1
    files = _files(root, _build_roots(config))
    if args.dry_run:
        for path, under in files:
            logger.info(
                "dry run: would archive %s as %s/%s", path, name, under.as_posix()
            )
        logger.info("dry run: would archive the report as %s/%s", name, REPORT)
        logger.info("dry run: would write %s", archive)
        for command in (pull, *git):
            logger.info("dry run: would run `git %s`", " ".join(command))
        logger.info("transfer: dry run; nothing archived or pushed")
        return 0

    logger.info("transfer: %s to %s", root, archive)
    with tempfile.TemporaryDirectory() as scratch:
        # The report on the live root; its issues are still the report.
        report = Path(scratch) / REPORT
        status = report_to(root, report)
        logger.info("transfer: report exit %s", status)
        try:
            _archive(archive, name, files, report)
        except OSError as exc:
            logger.error("transfer: cannot write %s: %s", archive, exc.strerror or exc)
            return 1
        unzipped = sum(path.stat().st_size for path, _ in files) + report.stat().st_size

    for command in git:
        shown = " ".join(["git", *command])
        logger.info("transfer: %s", shown)
        result = subprocess.run(["git", *command], cwd=repo, check=False)
        if result.returncode != 0:
            logger.error(
                "transfer: `%s` failed (exit %s) in %s; the archive stays in %s",
                shown,
                result.returncode,
                repo,
                archive,
            )
            return 1
    logger.info(
        "transfer: pushed %s (%s, %s unzipped)",
        archive,
        _size(archive.stat().st_size),
        _size(unzipped),
    )
    return 0
