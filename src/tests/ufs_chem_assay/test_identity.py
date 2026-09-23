"""identity.py: the harness's own version and commit for run.yaml."""

import logging
import subprocess
from importlib.metadata import version
from pathlib import Path

import pytest

from identity import HARNESS_NAME, HARNESS_ROOT, harness_commit, harness_version


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_harness_root_is_the_repository() -> None:
    assert (HARNESS_ROOT / "pyproject.toml").is_file()
    assert (HARNESS_ROOT / "src" / "identity.py").is_file()


def test_harness_version_is_the_installed_metadata() -> None:
    assert harness_version() == version(HARNESS_NAME)


def test_harness_commit_of_a_clean_checkout(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "initial")
    head = _git(tmp_path, "rev-parse", "HEAD")
    assert harness_commit(tmp_path) == head
    assert len(head) == 40


def test_harness_commit_marks_a_dirty_tree(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "tracked.txt").write_text("v1\n")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    head = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "tracked.txt").write_text("v2\n")  # edited, uncommitted
    assert harness_commit(tmp_path) == f"{head}-dirty"


def test_harness_commit_is_null_with_a_warning_outside_git(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # An installed wheel is a legitimate way to run: never fatal.
    with caplog.at_level(logging.WARNING, logger=f"{HARNESS_NAME}.identity"):
        assert harness_commit(tmp_path) is None
    assert any("not a git checkout" in record.message for record in caplog.records)
