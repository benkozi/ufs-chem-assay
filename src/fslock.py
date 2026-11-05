"""An exclusive file lock for the committed files' read-modify-write.

Sessions no longer run one at a time: with asynchronous Slurm runs, two
pytest sessions may end together and both append to the same committed
`benchmarks.csv`, or both repoint the same suite file. Each rewriter reads
the whole file, edits it, writes it back and verifies the reload; two of
them interleaving would lose rows or restore over each other. `locked`
holds an advisory `fcntl.flock` (LOCK_EX) on the file itself for the
duration — no sidecar lock file, nothing new to ignore in git — and
creates the file empty when it does not exist yet, so a caller treats
"absent" and "empty" alike.

Advisory locks across nodes need the filesystem's cooperation (Lustre
honours flock only on mounts with the `flock`/`localflock` option); on one
node they work regardless. The supported workflow — `ufs-chem-assay
watch` evaluating finished runs one after another on the login node —
never relies on cross-node locking; the lock covers two sessions started
by hand on one node.
"""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from logs import get_logger

logger = get_logger("fslock")


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock on `path` (created empty when absent)
    for the block; released on exit, exception included."""
    # Append mode: created when absent (the process umask decides the
    # permissions, as for every other file the harness writes), never
    # truncated, and nothing is written through this handle.
    with open(path, "a") as handle:
        try:
            logger.debug("acquiring lock on %s", path)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            logger.debug("locked %s", path)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            logger.debug("released %s", path)
