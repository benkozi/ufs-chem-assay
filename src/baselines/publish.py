"""Publishing: each would-publish record, in order, through the local
manifest, the append-only probe, the manifest write, the upload (one
unfiltered `aws s3 sync`, or a directory copy), the cache copy, and the
suite repoint. One baseline's failure never stops the next; nothing
raises. Under a dry run: the probe and the wrapper's own --dryrun upload,
the manifest logged, nothing written."""

from __future__ import annotations

import getpass
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import yaml

from baselines.models import (
    CONVENTIONS,
    BaselineFile,
    BaselineManifest,
    PublishRecord,
    SuiteInput,
)
from baselines.plan import plan, read_parameters, read_run
from baselines.store import digest, error_detail
from baselines.suite import rewrite_suite_ulid
from logs import get_logger
from models.suite_config import RunManifest
from report import TestReportRow
from s3_sync import S3SyncConfig, sync

logger = get_logger("baselines")


# ── the manifest ──────────────────────────────────────────────────────────────


def _files(source_dir: Path) -> list[BaselineFile]:
    paths = sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file()
        and path.relative_to(source_dir).as_posix() != CONVENTIONS.manifest
    )
    return [
        BaselineFile(
            path=path.relative_to(source_dir).as_posix(),
            bytes=path.stat().st_size,
            sha256=digest(path),
        )
        for path in paths
    ]


def _manifest(
    record: PublishRecord, run: RunManifest, output_root: Path
) -> BaselineManifest:
    assert run.application_commit is not None  # the plan refused a null
    return BaselineManifest(
        ulid=record.ulid,
        store=record.store,
        application=run.application,
        application_commit=run.application_commit,
        suite=record.suite,
        combo=record.combo,
        run_id=run.run_id,
        harness_version=run.harness_version,
        harness_commit=run.harness_commit,
        platform=run.platform,
        runtime=run.runtime,
        published_at=datetime.now(timezone.utc),
        published_by=f"{getpass.getuser()}@{socket.gethostname()}",
        superseded_ulid=record.previous_ulid,
        parameters=read_parameters(output_root, record.ulid),
        files=_files(record.source_dir),
    )


def _manifest_text(manifest: BaselineManifest) -> str:
    return yaml.safe_dump(
        manifest.model_dump(mode="json"), default_flow_style=False, sort_keys=False
    )


def _local_manifest(record: PublishRecord) -> tuple[bool, str | None]:
    """(resume, error): no manifest -> a fresh publish; one naming this ULID
    and store -> a resume (a previous attempt wrote it); anything else is
    the error to report (only a hand copy can produce it)."""
    path = record.source_dir / CONVENTIONS.manifest
    if not path.is_file():
        return False, None
    with open(path) as f:
        raw = yaml.safe_load(f)
    ulid = raw.get("ulid") if isinstance(raw, dict) else None
    store = raw.get("store") if isinstance(raw, dict) else None
    if ulid == record.ulid and store == record.store:
        return True, None
    return False, f"directory carries a manifest for {ulid} at {store}"


# ── the store ─────────────────────────────────────────────────────────────────


def _prefix_occupied(record: PublishRecord) -> bool:
    """S3: one --dryrun download of the prefix into an empty directory — any
    `(dryrun) download:` line means objects are there. A directory store:
    the directory exists (a missing store is FileNotFoundError)."""
    if record.local_store is not None:
        if not record.local_store.is_dir():
            raise FileNotFoundError(
                f"store directory {record.local_store} does not exist"
            )
        return Path(record.destination).exists()
    with tempfile.TemporaryDirectory() as empty:
        result = sync(
            S3SyncConfig(
                source=record.destination, destination=Path(empty), dry_run=True
            )
        )
    return "download:" in result.output


def _upload(record: PublishRecord, *, dry_run: bool) -> str | None:
    """The directory into the store; the error to report, or None."""
    if record.local_store is not None:
        if dry_run:
            count = sum(1 for path in record.source_dir.rglob("*") if path.is_file())
            logger.info(
                "DRY RUN would copy %s file(s) from %s to %s",
                count,
                record.source_dir,
                record.destination,
            )
            return None
        try:
            shutil.copytree(record.source_dir, record.destination)
        except OSError as exc:
            return f"copy to {record.destination}: {exc}"
        return None
    try:
        sync(
            S3SyncConfig(
                source=record.source_dir,
                destination=record.destination,
                only_show_errors=not dry_run,
                dry_run=dry_run,
            )
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return error_detail(exc)
    return None


def _cache(record: PublishRecord, baseline_root: Path | None, *, resume: bool) -> None:
    """The local copy the next session reads, when a baseline root is set."""
    if baseline_root is None:
        if record.local_store is None:
            logger.warning(
                "ASSAY_BASELINE_ROOT_DIR is unset: %s is in the store but not cached "
                "locally; the next local run of this suite needs it at <root>/%s",
                record.ulid,
                record.ulid,
            )
        return
    cached = baseline_root / record.ulid
    if (
        record.local_store is not None
        and cached.resolve() == Path(record.destination).resolve()
    ):
        return  # the store is the cache: the upload was the local copy
    if cached.exists():
        # Expected on a resume; worth a warning on a fresh publish.
        (logger.info if resume else logger.warning)(
            "cache %s already exists; left as is", cached
        )
        return
    try:
        shutil.copytree(record.source_dir, cached)
        logger.info("cached %s", cached)
    except OSError as exc:
        logger.error("cache copy to %s failed: %s", cached, exc)


def _repoint(record: PublishRecord) -> bool:
    try:
        rewrite_suite_ulid(record.suite_path, record.previous_ulid, record.ulid)
    except (ValueError, OSError) as exc:
        logger.error(
            "%s: baseline %s is in the store but the suite was not repointed: %s",
            record.suite_path,
            record.ulid,
            exc,
        )
        return False
    return True


def _publish_one(
    record: PublishRecord,
    *,
    run: RunManifest,
    output_root: Path,
    baseline_root: Path | None,
    dry_run: bool,
    suite_update: bool,
) -> PublishRecord:
    resume, error = _local_manifest(record)
    if error is not None:
        return record.with_outcome("failed", error)
    try:
        occupied = _prefix_occupied(record)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return record.with_outcome("failed", f"probe: {error_detail(exc)}")
    if occupied and not resume:
        return record.with_outcome(
            "failed",
            f"prefix {record.destination} already holds objects; the store is append-only",
        )
    if not resume:
        text = _manifest_text(_manifest(record, run, output_root))
        if dry_run:
            logger.info("DRY RUN manifest for %s:\n%s", record.combo, text)
        else:
            (record.source_dir / CONVENTIONS.manifest).write_text(text)
    if not occupied:
        error = _upload(record, dry_run=dry_run)
        if error is not None:
            return record.with_outcome("failed", error)
    if dry_run:
        if occupied:
            return record.with_outcome(
                "would-publish", "already in the store (would resume)"
            )
        verb = "copy" if record.local_store is not None else "upload"
        return record.with_outcome("would-publish", f"store free; would {verb}")
    _cache(record, baseline_root, resume=resume)
    updated = suite_update and _repoint(record)
    detail = "already in the store (resumed)" if occupied else "published"
    return record.with_outcome("published", detail, suite_updated=updated)


def publish(
    records: Sequence[PublishRecord],
    *,
    run: RunManifest,
    output_root: Path,
    baseline_root: Path | None,
    dry_run: bool,
    suite_update: bool,
) -> list[PublishRecord]:
    """Every would-publish record through _publish_one, the others passed
    through; one log line per record and a summary."""
    results: list[PublishRecord] = []
    for record in records:
        if record.action == "would-publish":
            record = _publish_one(
                record,
                run=run,
                output_root=output_root,
                baseline_root=baseline_root,
                dry_run=dry_run,
                suite_update=suite_update,
            )
        (logger.error if record.action == "failed" else logger.info)(
            "%s/%s -> %s: %s (%s)",
            record.suite,
            record.combo,
            record.destination,
            record.action,
            record.detail,
        )
        results.append(record)
    counts = {
        action: sum(1 for r in results if r.action == action)
        for action in ("published", "would-publish", "skipped", "failed")
    }
    logger.info(
        "publish summary: %s",
        ", ".join(f"{count} {action}" for action, count in counts.items() if count)
        or "nothing to publish",
    )
    return results


def publish_session(
    output_root: Path,
    suites: Sequence[SuiteInput],
    rows: Sequence[TestReportRow],
    *,
    store: str,
    baseline_root: Path | None,
    suite_update: bool,
) -> list[PublishRecord]:
    """The pytest session's entry point at session end: run.yaml just
    written is re-read (one RunManifest path for both entry points), the
    plan is built from the session's own contexts and report rows, and
    publication runs for real (no dry run: dry-run the output root with the
    subcommand afterwards)."""
    run = read_run(output_root)
    return publish(
        plan(run, suites, rows, output_root, store),
        run=run,
        output_root=output_root,
        baseline_root=baseline_root,
        dry_run=False,
        suite_update=suite_update,
    )
