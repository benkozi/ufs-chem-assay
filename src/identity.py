"""Who the harness is: its name, its checkout, its version, its commit.

Free of harness imports on purpose, so the pytest session and the CLI can
both record the harness's identity in run.yaml without importing each
other (logs.py takes HARNESS_NAME from here).
"""

from __future__ import annotations

import logging
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# The harness name: the one place code names it (logger namespace, plot
# footer, installed package). Kept identical to the repository name.
HARNESS_NAME = "ufs-chem-assay"

# <repo root>/src/identity.py -> <repo root>: the harness checkout, where
# `uv run pytest` runs from and whose parent is the CLI's default run root.
HARNESS_ROOT = Path(__file__).resolve().parents[1]

_logger = logging.getLogger(f"{HARNESS_NAME}.identity")


def harness_version() -> str:
    """The installed package version (pyproject.toml's, bumped by
    semantic-release); "unknown" when the package is not installed."""
    try:
        return version(HARNESS_NAME)
    except PackageNotFoundError:
        return "unknown"


def harness_commit(root: Path = HARNESS_ROOT) -> str | None:
    """HEAD SHA of the harness checkout, suffixed `-dirty` when the working
    tree has uncommitted changes (the `git describe --dirty` convention).

    None, with one WARNING, when the harness does not run from a git
    checkout (an installed wheel, git missing): unlike the application
    commit that is a legitimate way to run, never fatal."""
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if head.returncode != 0 or not head.stdout.strip():
            _logger.warning(
                "harness commit unknown: %s is not a git checkout (%s)",
                root,
                head.stderr.strip() or "git produced no output",
            )
            return None
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _logger.warning("harness commit unknown: git failed for %s (%s)", root, exc)
        return None
    sha = head.stdout.strip()
    if status.returncode == 0 and status.stdout.strip():
        sha += "-dirty"
    return sha
