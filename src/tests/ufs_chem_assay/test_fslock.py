"""fslock.locked: the advisory exclusive lock the committed files' rewriters
hold for their read-modify-write — created when absent, exclusive while
held, released on exit and on exception."""

import fcntl
import os
import threading
from pathlib import Path

import pytest

from fslock import locked


def _try_lock(path: Path) -> bool:
    """One non-blocking attempt at the exclusive lock from another descriptor."""
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def test_locked_creates_a_missing_file_and_releases_on_exit(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "benchmarks.csv"
    path.parent.mkdir()
    with locked(path):
        assert path.is_file()
        assert path.stat().st_size == 0
        assert not _try_lock(path)  # exclusive while held
    assert _try_lock(path)  # released


def test_locked_releases_on_exception(tmp_path: Path) -> None:
    path = tmp_path / "x"

    def hold_and_fail() -> None:
        with locked(path):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        hold_and_fail()
    assert _try_lock(path)


def test_locked_blocks_a_second_holder_until_release(tmp_path: Path) -> None:
    path = tmp_path / "x"
    held = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def first() -> None:
        with locked(path):
            held.set()
            release.wait(timeout=10)
            order.append("first-out")

    def second() -> None:
        held.wait(timeout=10)
        with locked(path):
            order.append("second-in")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    held.wait(timeout=10)
    assert not _try_lock(path)
    release.set()
    for thread in threads:
        thread.join(timeout=10)
    assert order == ["first-out", "second-in"]
