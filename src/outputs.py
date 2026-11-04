"""Output upload: a run's whole output root to `<store>/<run_id>/` and back
by ULID. The store is an `s3://bucket/prefix` (through the S3 wrapper: one
`aws s3 sync`, resumable, nothing deleted) or a local directory (a copy).
Uploading never refuses an occupied prefix — a run is the same run every
time it is re-uploaded — and the download treats `run.yaml` as the marker
that the ULID exists, since an absent prefix syncs nothing and exits 0.
`upload.yaml`, written into the root before the sync, says when and by whom
the copy was taken. Generic: nothing here knows the application.
"""

from __future__ import annotations

import contextlib
import getpass
import os
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ConfigDict, Field

from baselines.store import is_s3, location
from logs import get_logger
from models.base import StrictModel
from models.conventions import CONVENTIONS
from s3_sync import S3SyncConfig, sync

logger = get_logger("outputs")


class OutputUploadRecord(StrictModel):
    """upload.yaml: the copy of a run's output root taken by upload_output —
    which run, where to, when, by whom, how much. Rewritten on every upload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(
        description="The run's session ULID (run.yaml's run_id): the store key"
    )
    store: str = Field(
        description="The output store: an s3://bucket/prefix or a local directory"
    )
    destination: str = Field(description="Where the copy lives: <store>/<run_id>/")
    uploaded_at: datetime = Field(description="When the copy was taken (UTC, aware)")
    uploaded_by: str = Field(description="<user>@<hostname> that took it")
    files: int = Field(
        description="Files in the root at upload time (this record excluded)"
    )
    bytes: int = Field(description="Their total size")


def output_location(store: str, run_id: str) -> str:
    """Where one run's output lives in the store: a prefix URI or a directory."""
    return location(store, run_id)


def _run_id(output_root: Path) -> str:
    manifest = output_root / CONVENTIONS.run_manifest
    if not manifest.is_file():
        raise ValueError(
            f"{output_root} is not a run's output root: no {CONVENTIONS.run_manifest}"
        )
    with open(manifest) as f:
        raw = yaml.safe_load(f)
    run_id = raw.get("run_id") if isinstance(raw, dict) else None
    if not isinstance(run_id, str) or not run_id:
        raise ValueError(f"{manifest} has no run_id")
    return run_id


def _inventory(output_root: Path) -> tuple[int, int]:
    files = [
        path
        for path in output_root.rglob("*")
        if path.is_file()
        and path.relative_to(output_root).as_posix() != CONVENTIONS.upload_record
    ]
    return len(files), sum(path.stat().st_size for path in files)


def upload_output(
    output_root: Path, store: str, *, dry_run: bool = False
) -> OutputUploadRecord:
    """The whole root to <store>/<run_id>/: upload.yaml written first (so it
    goes up too; not under a dry run), then one sync (S3) or a copy (a
    directory store; a dry run logs the count). Resumable and never refused;
    transfer errors propagate to the caller."""
    run_id = _run_id(output_root)
    files, size = _inventory(output_root)
    destination = output_location(store, run_id)
    record = OutputUploadRecord(
        run_id=run_id,
        store=store,
        destination=destination,
        uploaded_at=datetime.now(UTC),
        uploaded_by=f"{getpass.getuser()}@{socket.gethostname()}",
        files=files,
        bytes=size,
    )
    if dry_run:
        logger.info("DRY RUN would record %s", record.model_dump(mode="json"))
    else:
        with open(output_root / CONVENTIONS.upload_record, "w") as f:
            yaml.safe_dump(record.model_dump(mode="json"), f, sort_keys=False)
    if is_s3(store):
        sync(
            S3SyncConfig(
                source=output_root,
                destination=destination,
                only_show_errors=not dry_run,
                dry_run=dry_run,
            )
        )
    elif dry_run:
        logger.info(
            "DRY RUN would copy %s file(s) from %s to %s",
            files,
            output_root,
            destination,
        )
    else:
        shutil.copytree(output_root, destination, dirs_exist_ok=True)
    logger.info(
        "%soutput of run %s (%s file(s), %s bytes) -> %s",
        "dry run: " if dry_run else "",
        run_id,
        files,
        size,
        destination,
    )
    return record


def download_output(
    run_id: str, store: str, dest: Path, *, dry_run: bool = False
) -> Path:
    """<store>/<run_id>/ into <dest>/<run_id>/. S3: one sync into
    <dest>/.staging/<run_id>/, moved into place file by file (os.replace)
    only when run.yaml arrived — an absent prefix syncs nothing, a ValueError
    naming the ULID and the store — so an existing target is refreshed in
    place, never removed, and files it holds that the store lacks stay; only
    this run's staging directory is cleaned up (concurrent downloads into one
    dest do not disturb each other). A directory store copies with
    dirs_exist_ok. A dry run plans and writes nothing. Returns the target
    path."""
    target = dest / run_id
    source = output_location(store, run_id)
    if not is_s3(store):
        if not Path(source).is_dir():
            raise ValueError(f"no output for {run_id} under {store}")
        if dry_run:
            files, size = _inventory(Path(source))
            logger.info(
                "DRY RUN would copy %s file(s), %s bytes, from %s to %s",
                files,
                size,
                source,
                target,
            )
            return target
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        if dry_run:
            sync(S3SyncConfig(source=source, destination=target, dry_run=True))
            return target
        staging = dest / CONVENTIONS.staging_dirname / run_id
        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        try:
            sync(
                S3SyncConfig(source=source, destination=staging, only_show_errors=True)
            )
            if not (staging / CONVENTIONS.run_manifest).is_file():
                raise ValueError(
                    f"no output for {run_id} under {store} (nothing downloaded)"
                )
            target.mkdir(parents=True, exist_ok=True)
            for path in staging.rglob("*"):
                if path.is_file():
                    final = target / path.relative_to(staging)
                    final.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(path, final)
        finally:
            # This run's staging only: another download into the same dest
            # may be mid-transfer in its own; the shared parent goes once empty.
            shutil.rmtree(staging, ignore_errors=True)
            with contextlib.suppress(OSError):
                (dest / CONVENTIONS.staging_dirname).rmdir()
    record_path = target / CONVENTIONS.upload_record
    if record_path.is_file():
        with open(record_path) as f:
            record = OutputUploadRecord.model_validate(yaml.safe_load(f))
        logger.info(
            "run %s: %s file(s) uploaded %s by %s -> %s",
            run_id,
            record.files,
            record.uploaded_at.isoformat(),
            record.uploaded_by,
            target,
        )
    else:
        logger.info("run %s -> %s", run_id, target)
    return target
