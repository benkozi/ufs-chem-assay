"""Staging a suite's declared inputs into the application checkout's data
directory, through the S3 wrapper: one filtered `aws s3 sync` of the object's
parent prefix per missing file, verified by digest in a staging directory,
then moved into place. Nothing here raises for a transfer failure — every
input is attempted and the outcome is returned; the caller (the CLI's exit
code) decides what a failure means. The pytest session never calls this: it
only checks presence (`missing_inputs`) and fails fast.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import ConfigDict, Field
from ulid import ULID

from logs import get_logger
from models.base import StrictModel
from models.suite_config import InputFile, SuiteConfig
from s3_sync import S3SyncConfig, sync

logger = get_logger("staging")

# Per-fetch staging directories live here, under the data directory, so the
# final move is a rename on one filesystem; a leftover from a killed run is
# removed at the start of the next staging pass.
STAGING_DIRNAME = ".staging"
_CHUNK = 1024 * 1024

Action = Literal["skipped", "downloaded", "would-fetch", "failed"]


class StagedInput(StrictModel):
    """What staging did (or would do) for one declared input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(description="The input's S3 object URI")
    path: Path = Field(description="Host path the input is staged at")
    action: Action = Field(
        description=(
            "skipped: present and verified (or present, no digest declared); "
            "downloaded: fetched, verified, moved into place; would-fetch: a dry "
            "run's plan; failed: see detail"
        )
    )
    detail: str = Field(description="Why: the verification outcome or the error")
    sha256: str | None = Field(
        description="Digest of the file on disk when one was computed; null otherwise"
    )


def merge_inputs(suites: Sequence[SuiteConfig]) -> list[InputFile]:
    """Every selected suite's inputs, identical entries deduplicated (several
    suites of one configuration naturally declare the same file), in
    first-seen order. The same `dst` declared two different ways is a
    ValueError before any transfer."""
    merged: dict[PurePosixPath, tuple[InputFile, str]] = {}
    for suite in suites:
        for entry in suite.inputs:
            existing = merged.get(entry.dst)
            if existing is None:
                merged[entry.dst] = (entry, suite.name)
            elif existing[0] != entry:
                raise ValueError(
                    f"inputs: {str(entry.dst)!r} is declared differently by suites "
                    f"{existing[1]!r} and {suite.name!r} (url/public/sha256 must agree)"
                )
    return [entry for entry, _ in merged.values()]


def missing_inputs(inputs: Sequence[InputFile], data_dir: Path) -> list[Path]:
    """The declared inputs with no file at their destination (existence only —
    the session's fail-fast guard; digests are staging's business)."""
    return [
        data_dir / entry.dst for entry in inputs if not (data_dir / entry.dst).is_file()
    ]


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            sha.update(chunk)
    return sha.hexdigest()


def _present(entry: InputFile, target: Path) -> tuple[bool, str, str | None]:
    """(usable as-is, detail, digest-if-computed) for the file at target."""
    if not target.is_file():
        return False, "absent", None
    if entry.sha256 is None:
        if target.stat().st_size > 0:
            return True, "present, no digest declared", None
        return False, "present but empty", None
    digest = _digest(target)
    if digest == entry.sha256:
        return True, "sha256 verified", digest
    return False, f"present but sha256 {digest} != declared {entry.sha256}", digest


def _sync_config(entry: InputFile, destination: Path, *, dry_run: bool) -> S3SyncConfig:
    # One object through the wrapper's one verb: the parent prefix, filtered
    # to the file name. A dry run keeps the per-file lines (they are the plan).
    return S3SyncConfig(
        source=entry.parent_prefix,
        destination=destination,
        exclude=["*"],
        include=[entry.filename],
        no_sign_request=entry.public,
        only_show_errors=not dry_run,
        dry_run=dry_run,
    )


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        output = (exc.output or "").strip()
        tail = output.splitlines()[-1] if output else ""
        return f"aws s3 sync exited {exc.returncode}: {tail}"
    return str(exc)


def _fetch(entry: InputFile, target: Path, staging_root: Path) -> StagedInput:
    staging_dir = staging_root / str(ULID())
    try:
        sync(_sync_config(entry, staging_dir, dry_run=False))
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return StagedInput(
            url=entry.url,
            path=target,
            action="failed",
            detail=_error_detail(exc),
            sha256=None,
        )
    staged = staging_dir / entry.filename
    if not staged.is_file():
        shutil.rmtree(staging_dir, ignore_errors=True)
        return StagedInput(
            url=entry.url,
            path=target,
            action="failed",
            detail=f"not fetched: no such object {entry.filename!r} under {entry.parent_prefix}",
            sha256=None,
        )
    digest = _digest(staged)
    if entry.sha256 is not None and digest != entry.sha256:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return StagedInput(
            url=entry.url,
            path=target,
            action="failed",
            detail=f"sha256 mismatch after download: got {digest}, declared {entry.sha256}",
            sha256=digest,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged, target)
    shutil.rmtree(staging_dir, ignore_errors=True)
    return StagedInput(
        url=entry.url,
        path=target,
        action="downloaded",
        detail="sha256 verified" if entry.sha256 else "downloaded, no digest declared",
        sha256=digest,
    )


def stage_inputs(
    inputs: Sequence[InputFile], data_dir: Path, *, dry_run: bool = False
) -> list[StagedInput]:
    """Stage every input into data_dir: present-and-verified files are
    skipped without touching the network; the rest are fetched one by one
    (every input attempted even after a failure). A dry run classifies and
    passes the wrapper's own dry run through for the files it would fetch;
    it writes nothing."""
    staging_root = data_dir / STAGING_DIRNAME
    if staging_root.exists():
        logger.info("removing stale staging directory %s", staging_root)
        shutil.rmtree(staging_root)
    results: list[StagedInput] = []
    for entry in inputs:
        target = data_dir / entry.dst
        usable, detail, digest = _present(entry, target)
        if usable:
            logger.info("%s: skipped (%s)", target, detail)
            results.append(
                StagedInput(
                    url=entry.url,
                    path=target,
                    action="skipped",
                    detail=detail,
                    sha256=digest,
                )
            )
            continue
        logger.info("%s: %s; fetching %s", target, detail, entry.url)
        if dry_run:
            try:
                sync(_sync_config(entry, staging_root / "dry-run", dry_run=True))
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                results.append(
                    StagedInput(
                        url=entry.url,
                        path=target,
                        action="failed",
                        detail=_error_detail(exc),
                        sha256=None,
                    )
                )
                continue
            results.append(
                StagedInput(
                    url=entry.url,
                    path=target,
                    action="would-fetch",
                    detail=detail,
                    sha256=None,
                )
            )
            continue
        result = _fetch(entry, target, staging_root)
        log = logger.error if result.action == "failed" else logger.info
        log("%s: %s (%s)", target, result.action, result.detail)
        results.append(result)
    if staging_root.exists():
        shutil.rmtree(staging_root, ignore_errors=True)
    return results
