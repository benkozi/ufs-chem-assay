"""The store, as both halves see it: an s3://bucket/prefix (through the S3
wrapper) or a local directory (plain file operations), and the small
helpers both share."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from baselines.models import CONVENTIONS


def is_s3(store: str) -> bool:
    return store.startswith("s3://")


def location(store: str, ulid: str) -> str:
    """Where one baseline lives in the store: a prefix URI or a directory."""
    return f"{store}/{ulid}/" if is_s3(store) else str(Path(store) / ulid)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CONVENTIONS.digest_chunk):
            sha.update(chunk)
    return sha.hexdigest()


def error_detail(exc: Exception) -> str:
    """One line for a failed wrapper call: the exit status and the CLI's
    last line (its own message, e.g. AccessDenied)."""
    if isinstance(exc, subprocess.CalledProcessError):
        output = (exc.output or "").strip()
        tail = output.splitlines()[-1] if output else ""
        return f"aws s3 sync exited {exc.returncode}: {tail}"
    return str(exc)
