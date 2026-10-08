"""Publishing baselines: a run's compared combinations become
`<store>/<ulid>/` in the private bucket, each with a manifest, and the suite
is repointed at the new ULIDs.

A baseline is the whole combination output directory (NetCDF, generated
config, captured output, driver log, stats, plots, GIFs), uploaded as it
stands with one unfiltered `aws s3 sync` through the S3 wrapper. Its name
is the combination's runtime ULID — minted fresh every run, never derived
from content or configuration — so the store is append-only by construction
and enforced by a probe: a prefix that already holds objects is refused.
`baseline.yaml`, written into the directory before the sync, records what
was published and from where; its presence is also what lets a re-run
resume (the suite edit redone, the upload skipped).

Generic: nothing here knows an application. The suite's
`baseline_comparisons` entries are the contract for what is published —
each one selects a combination and pins the ULID the next session compares
against. Both entry points — the CLI subcommand over a finished output root
and the pytest session at its end — build the same `plan` and call the same
`publish`; `load_run` is the CLI's way to the plan's inputs.
"""

from __future__ import annotations

import getpass
import hashlib
import re
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, InstanceOf

from applications.base import Application
from combos import enumerate_combos
from comparison import resolve_baseline_comparisons
from logs import get_logger
from models.base import StrictModel
from models.suite_config import BaselineComparison, RunManifest, SuiteConfig
from platforms import Platform, Runtime
from report import TestReportRow
from s3_sync import S3SyncConfig, sync

logger = get_logger("baselines")

BASELINE_MANIFEST = "baseline.yaml"
COMPARISON_TEST = "test_baseline_comparison"  # excluded from the gate
DRIVER_TEST = "test_driver_execution"  # must have passed
_CHUNK = 1024 * 1024

Action = Literal["published", "would-publish", "skipped", "failed"]


class BaselineFile(StrictModel):
    """One file of a published baseline, as it was at publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(
        description=(
            "Path relative to the baseline directory (posix; subdirectories "
            "such as plots-overview/ included)"
        )
    )
    bytes: int = Field(description="Size on disk at publication")
    sha256: str = Field(description="SHA-256 (lowercase hex) at publication")


class BaselineParameter(StrictModel):
    """One combos.csv row of the published combination: an effective
    parameter, swept or pinned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str = Field(description="Attachment target (e.g. a stream or species name)")
    field: str = Field(description="Configuration field name")
    value: str = Field(description="The value in the combination's generated config")
    swept: bool = Field(description="Whether the suite swept this dimension")


class BaselineManifest(StrictModel):
    """baseline.yaml: what a baseline is, where it came from, and what it
    holds. Output only, like run.yaml; the download side reads it back."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ulid: str = Field(
        description="The combination's runtime ULID: the store key and the directory name"
    )
    store: str = Field(description="The s3://bucket/prefix it was published to")
    application: str = Field(description="Registry name of the application")
    application_commit: str = Field(
        description="HEAD commit of the application checkout the run was built from"
    )
    suite: str = Field(
        description="Unique name of the suite the combination belongs to"
    )
    combo: str = Field(description="Canonical combination name")
    run_id: str = Field(description="Session ULID of the run that produced it")
    harness_version: str = Field(
        description="Harness package version that produced the run"
    )
    harness_commit: str | None = Field(
        description="Harness checkout HEAD (-dirty when edited); null outside a checkout"
    )
    platform: Platform = Field(description="Machine the run happened on")
    runtime: Runtime = Field(description="How the driver was spawned")
    published_at: datetime = Field(description="Publication time, UTC, timezone-aware")
    published_by: str = Field(description="<user>@<hostname> that published it")
    superseded_ulid: str | None = Field(
        description=(
            "The ULID the suite entry pinned when this baseline was published "
            "(whether or not the suite was repointed); null when it had none"
        )
    )
    parameters: list[BaselineParameter] = Field(
        description="The combination's effective-parameter rows (combos.csv)"
    )
    files: list[BaselineFile] = Field(
        description=(
            "Every file under the combination directory (this manifest excepted), "
            "sorted by path"
        )
    )


class SuiteInput(BaseModel):
    """One selected suite as the run executed it: the file (the edit
    target), the loaded suite, its resolved entries, and the run's ids."""

    model_config = ConfigDict(frozen=True)

    path: Path = Field(description="The suite file")
    suite: InstanceOf[SuiteConfig] = Field(description="The suite as loaded")
    baselines: dict[str, InstanceOf[BaselineComparison]] = Field(
        description="Combination name -> baseline_comparisons entry (the session's resolution)"
    )
    combo_ids: dict[str, str] = Field(
        description="Combination name -> the run's combo_id (the directory name)"
    )


class PublishRecord(StrictModel):
    """What publication did, will do, or would not do for one entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite: str = Field(description="Unique suite name")
    suite_path: Path = Field(description="The suite file the entry lives in")
    combo: str = Field(description="Canonical combination name the entry selects")
    ulid: str = Field(description="The new baseline's ULID (the run's combo_id)")
    previous_ulid: str = Field(
        description="The ULID the entry pins before this publish"
    )
    source_dir: Path = Field(description="The combination's output directory")
    store: str = Field(description="The s3://bucket/prefix published to")
    action: Action = Field(
        description=(
            "published: in the store and (unless disabled) the suite repointed; "
            "would-publish: the plan, or a dry run's outcome; skipped: the gate, "
            "already published, or nothing to compare; failed: see detail"
        )
    )
    detail: str = Field(
        description="Why: the gate's verdict, the probe's finding, the error"
    )
    suite_updated: bool = Field(description="Whether the suite file was rewritten")

    @property
    def destination(self) -> str:
        return f"{self.store}/{self.ulid}/"


# ── the gate ──────────────────────────────────────────────────────────────────


def _test_name(row: TestReportRow) -> str:
    return row.pytest_name.split("[", 1)[0]


def publishable(rows: Sequence[TestReportRow]) -> tuple[bool, str]:
    """(ok, reason): the driver test passed and no test but the comparison
    against the old baseline failed. Skips are allowed (a suite with stats
    off, a dry run) — a dry run publishes nothing because its driver test
    skipped, with no special case."""
    driver = [row for row in rows if _test_name(row) == DRIVER_TEST]
    if not driver:
        return False, f"{DRIVER_TEST} did not run"
    if driver[0].result != "passed":
        return False, f"{DRIVER_TEST} {driver[0].result}"
    failed = sorted(
        {
            _test_name(row)
            for row in rows
            if row.result == "failed" and _test_name(row) != COMPARISON_TEST
        }
    )
    if failed:
        return False, "failed: " + ", ".join(failed)
    return True, "driver passed; no other test failed"


# ── plan ──────────────────────────────────────────────────────────────────────


def plan(
    run: RunManifest,
    suites: Sequence[SuiteInput],
    rows: Sequence[TestReportRow],
    output_root: Path,
    store: str,
) -> list[PublishRecord]:
    """Pure, in memory: one record per baseline_comparisons entry of every
    suite — would-publish, skipped (the gate, an already pinned ULID, no
    NetCDF output), or failed (no application commit, directory missing).
    Nothing is touched."""
    records: list[PublishRecord] = []
    for suite_input in suites:
        for combo_name, entry in suite_input.baselines.items():
            combo_id = suite_input.combo_ids[combo_name]
            source_dir = output_root / combo_id
            record = PublishRecord(
                suite=suite_input.suite.name,
                suite_path=suite_input.path,
                combo=combo_name,
                ulid=combo_id,
                previous_ulid=entry.ulid,
                source_dir=source_dir,
                store=store,
                action="would-publish",
                detail="",
                suite_updated=False,
            )
            if run.application_commit is None:
                record = _with(
                    record,
                    "failed",
                    "run has no application commit (no checkout configured)",
                )
            else:
                combo_rows = [row for row in rows if row.combo_id == combo_id]
                ok, reason = publishable(combo_rows)
                if not ok:
                    record = _with(record, "skipped", reason)
                elif entry.ulid == combo_id:
                    record = _with(record, "skipped", "already published")
                elif not source_dir.is_dir():
                    record = _with(
                        record, "failed", f"combination directory missing: {source_dir}"
                    )
                elif not any(source_dir.glob("*.nc")):
                    record = _with(
                        record,
                        "skipped",
                        "no NetCDF output: nothing to compare against",
                    )
                else:
                    record = _with(record, "would-publish", reason)
            records.append(record)
    return records


def _with(record: PublishRecord, action: Action, detail: str) -> PublishRecord:
    return record.model_copy(update={"action": action, "detail": detail})


# ── load_run (the CLI's reader) ──────────────────────────────────────────────


def default_selector(run: RunManifest) -> str:
    """The --suite-config selector naming the run's suites, by the
    `X-suite.yaml` convention."""
    names = "|".join(re.escape(suite.name) for suite in run.suites)
    return rf"(?:{names})-suite\.yaml"


def read_test_report(output_root: Path) -> list[TestReportRow]:
    frame = pd.read_csv(output_root / "test-report.csv", dtype=str)
    return [TestReportRow.model_validate(row) for row in frame.to_dict("records")]


def read_parameters(output_root: Path, combo_id: str) -> list[BaselineParameter]:
    frame = pd.read_csv(output_root / "combos.csv", dtype=str)
    mine = frame[frame["combo_id"] == combo_id]
    return [
        BaselineParameter(
            target=str(row["target"]),
            field=str(row["field"]),
            value=str(row["value"]),
            swept=str(row["swept"]).lower() == "true",
        )
        for row in mine.to_dict("records")
    ]


def load_run(
    output_root: Path,
    app: Application,
    suites: Sequence[tuple[Path, SuiteConfig]],
) -> tuple[RunManifest, list[SuiteInput], list[TestReportRow]]:
    """From a finished output root to the plan's inputs: run.yaml,
    test-report.csv, and — per loaded suite — a re-enumeration against its
    base config (fresh ULIDs, but the canonical names are the run's), the
    entries resolved as the session resolved them, and the names joined to
    combos.csv for the run's ids. A suite the run did not execute, or one
    whose entries differ from the recorded ones (edited since the run), is a
    ValueError — the run no longer describes the file."""
    with open(output_root / "run.yaml") as f:
        run = RunManifest.model_validate(yaml.safe_load(f))
    recorded = {suite.name: suite for suite in run.suites}
    combos_csv = pd.read_csv(output_root / "combos.csv", dtype=str)
    inputs: list[SuiteInput] = []
    for path, suite in suites:
        if suite.name not in recorded:
            raise ValueError(
                f"suite {suite.name!r} ({path}) was not executed by run {run.run_id} "
                f"(recorded suites: {sorted(recorded)})"
            )
        dumped = [e.model_dump() for e in suite.baseline_comparisons]
        recorded_dump = [
            e.model_dump() for e in recorded[suite.name].baseline_comparisons
        ]
        if dumped != recorded_dump:
            raise ValueError(
                f"suite {suite.name!r} ({path}): baseline_comparisons differ from "
                f"the ones run {run.run_id} executed (edited since the run?); "
                "publish from a run of the file as it is now"
            )
        base = app.config_model.from_yaml(suite.config_path)
        combos = enumerate_combos(app.dimensions(suite.sweep, base))
        baselines = resolve_baseline_comparisons(
            app, suite.baseline_comparisons, combos
        )
        mine = combos_csv[combos_csv["suite"] == suite.name]
        ids = dict(zip(mine["name"], mine["combo_id"]))
        missing = sorted(set(baselines) - set(ids))
        if missing:
            raise ValueError(
                f"suite {suite.name!r}: combinations {missing} are not in combos.csv "
                f"of run {run.run_id} (the sweep changed since the run?)"
            )
        inputs.append(
            SuiteInput(
                path=path,
                suite=suite,
                baselines=baselines,
                combo_ids={name: ids[name] for name in baselines},
            )
        )
    return run, inputs, read_test_report(output_root)


# ── publish ───────────────────────────────────────────────────────────────────


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            sha.update(chunk)
    return sha.hexdigest()


def _files(source_dir: Path) -> list[BaselineFile]:
    files = sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file()
        and path.relative_to(source_dir).as_posix() != BASELINE_MANIFEST
    )
    return [
        BaselineFile(
            path=path.relative_to(source_dir).as_posix(),
            bytes=path.stat().st_size,
            sha256=_digest(path),
        )
        for path in files
    ]


def _manifest(
    record: PublishRecord, run: RunManifest, output_root: Path
) -> BaselineManifest:
    assert run.application_commit is not None  # plan refused a null
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
    path = record.source_dir / BASELINE_MANIFEST
    if not path.is_file():
        return False, None
    with open(path) as f:
        raw = yaml.safe_load(f)
    ulid = raw.get("ulid") if isinstance(raw, dict) else None
    store = raw.get("store") if isinstance(raw, dict) else None
    if ulid == record.ulid and store == record.store:
        return True, None
    return False, f"directory carries a manifest for {ulid} at {store}"


def _prefix_occupied(record: PublishRecord) -> bool:
    """One --dryrun download of the prefix into an empty directory: any
    `(dryrun) download:` line means objects are there."""
    with tempfile.TemporaryDirectory() as empty:
        result = sync(
            S3SyncConfig(
                source=record.destination, destination=Path(empty), dry_run=True
            )
        )
    return "download:" in result.output


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        output = (exc.output or "").strip()
        tail = output.splitlines()[-1] if output else ""
        return f"aws s3 sync exited {exc.returncode}: {tail}"
    return str(exc)


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
        return _with(record, "failed", error)
    try:
        occupied = _prefix_occupied(record)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return _with(record, "failed", f"probe: {_error_detail(exc)}")
    if occupied and not resume:
        return _with(
            record,
            "failed",
            f"prefix {record.destination} already holds objects; the store is append-only",
        )

    manifest_path = record.source_dir / BASELINE_MANIFEST
    if not resume:
        manifest = _manifest(record, run, output_root)
        if dry_run:
            logger.info(
                "DRY RUN manifest for %s (%s):\n%s",
                record.combo,
                manifest_path,
                _manifest_text(manifest),
            )
        else:
            manifest_path.write_text(_manifest_text(manifest))

    detail = "published"
    if occupied:
        detail = "already in the store (resumed)"
    else:
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
            return _with(record, "failed", _error_detail(exc))
    if dry_run:
        return _with(
            record,
            "would-publish",
            "already in the store (would resume)"
            if occupied
            else "prefix empty; would upload",
        )

    if baseline_root is not None:
        cached = baseline_root / record.ulid
        if cached.exists():
            # Expected on a resume; worth a warning on a fresh publish.
            (logger.info if resume else logger.warning)(
                "cache %s already exists; left as is", cached
            )
        else:
            try:
                shutil.copytree(record.source_dir, cached)
                logger.info("cached %s", cached)
            except OSError as exc:
                logger.error("cache copy to %s failed: %s", cached, exc)
    else:
        logger.warning(
            "ASSAY_BASELINE_ROOT_DIR is unset: %s is in the store but not cached "
            "locally; the next local run of this suite needs it at <root>/%s",
            record.ulid,
            record.ulid,
        )

    updated = False
    if suite_update:
        try:
            rewrite_suite_ulid(record.suite_path, record.previous_ulid, record.ulid)
            updated = True
        except (ValueError, OSError) as exc:
            logger.error(
                "%s: baseline %s is in the store but the suite was not repointed: %s",
                record.suite_path,
                record.ulid,
                exc,
            )
    return record.model_copy(
        update={"action": "published", "detail": detail, "suite_updated": updated}
    )


def publish(
    records: Sequence[PublishRecord],
    *,
    run: RunManifest,
    output_root: Path,
    baseline_root: Path | None,
    dry_run: bool,
    suite_update: bool,
) -> list[PublishRecord]:
    """For each would-publish record, in order: the local manifest (absent:
    fresh; naming this ULID and store: resume), the probe, the manifest
    write, the sync, the cache copy, the suite edit — or, under dry_run, the
    probe and a --dryrun sync only, the manifest logged. One baseline's
    failure never stops the next; nothing raises."""
    results: list[PublishRecord] = []
    for record in records:
        if record.action != "would-publish":
            results.append(record)
            logger.info(
                "%s/%s: %s (%s)",
                record.suite,
                record.combo,
                record.action,
                record.detail,
            )
            continue
        result = _publish_one(
            record,
            run=run,
            output_root=output_root,
            baseline_root=baseline_root,
            dry_run=dry_run,
            suite_update=suite_update,
        )
        log = logger.error if result.action == "failed" else logger.info
        log(
            "%s/%s -> %s: %s (%s)",
            result.suite,
            result.combo,
            result.destination,
            result.action,
            result.detail,
        )
        results.append(result)
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
    with open(output_root / "run.yaml") as f:
        run = RunManifest.model_validate(yaml.safe_load(f))
    records = plan(run, suites, rows, output_root, store)
    return publish(
        records,
        run=run,
        output_root=output_root,
        baseline_root=baseline_root,
        dry_run=False,
        suite_update=suite_update,
    )


# ── the suite edit ────────────────────────────────────────────────────────────


def rewrite_suite_ulid(path: Path, old: str, new: str) -> None:
    """Textual repoint of exactly one `ulid: <old>` line to `ulid: <new>`,
    keeping every other byte (the files are hand-commented, which a YAML
    dump would lose). Zero matches (the entry was edited since the run) and
    two or more (two entries pin the same ULID) are refused; after writing,
    the file is reloaded and must equal the previous suite with that one
    ULID changed, or the original text is restored."""
    from applications.registry import load_suite

    original = path.read_text()
    pattern = re.compile(
        rf"^(?P<head>[ \t]*(?:-[ \t]+)?ulid:[ \t]*){re.escape(old)}(?P<tail>[ \t]*(?:#.*)?)$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(original))
    if not matches:
        raise ValueError(f"{path}: no `ulid:` line pins {old}")
    if len(matches) > 1:
        raise ValueError(
            f"{path}: {len(matches)} `ulid:` lines pin {old}; repoint by hand"
        )
    before = load_suite(path)
    expected = before.model_dump()
    entries = expected["baseline_comparisons"]
    (index,) = [i for i, entry in enumerate(entries) if entry["ulid"] == old]
    entries[index]["ulid"] = new
    match = matches[0]
    text = (
        original[: match.start()]
        + match["head"]
        + new
        + match["tail"]
        + original[match.end() :]
    )
    path.write_text(text)
    try:
        after = load_suite(path).model_dump()
    except (
        ValueError,
        OSError,
        yaml.YAMLError,
    ) as exc:  # ValidationError is a ValueError
        path.write_text(original)
        raise ValueError(
            f"{path}: reload after the repoint failed ({exc}); restored"
        ) from exc
    if after != expected:
        path.write_text(original)
        raise ValueError(f"{path}: reload differs beyond the one ULID; restored")
