"""outputs.py: a run's whole output root to <store>/<run_id>/ and back by
ULID — S3 through the wrapper (mocked here), a directory store by copy, dry
runs that write nothing, and the upload.yaml record."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from outputs import (
    UPLOAD_RECORD,
    OutputUploadRecord,
    download_output,
    output_location,
    upload_output,
)
from s3_sync import S3SyncConfig, S3SyncResult, sync

_STORE = "s3://ufs-chem/assay-output"
_RUN_ID = "01JRUNRUNRUNRUNRUNRUNRUNRU"


def _root(tmp_path: Path, run_id: str = _RUN_ID) -> Path:
    """A fabricated output root: run.yaml plus a combination directory."""
    root = tmp_path / "output"
    combo = root / "01JCOMBOCOMBOCOMBOCOMBOCOM"
    combo.mkdir(parents=True)
    (root / "run.yaml").write_text(f"run_id: {run_id}\napplication: cece\n")
    (root / "test-report.csv").write_text("pytest_name,result\n")
    (combo / "cece_20100101_010000.nc").write_bytes(b"netcdf")
    (combo / "01JCOMBOCOMBOCOMBOCOMBOCOM.out").write_text("tic\ntoc\n")
    return root


def _fake_sync(
    *, downloads: Callable[[Path], None] | None = None
) -> Callable[[S3SyncConfig], S3SyncResult]:
    """A sync that, for a download, populates the destination like aws would."""

    def fake(config: S3SyncConfig) -> S3SyncResult:
        if (
            downloads is not None
            and isinstance(config.destination, Path)
            and not config.dry_run
        ):
            downloads(config.destination)
        return S3SyncResult(
            argv=config.argv,
            returncode=0,
            output="(dryrun) upload: x\n",
            dry_run=config.dry_run,
        )

    return fake


# ── location and the record ───────────────────────────────────────────────────


def test_output_location_for_s3_and_directory_stores(tmp_path: Path) -> None:
    assert output_location(_STORE, _RUN_ID) == f"{_STORE}/{_RUN_ID}/"
    assert output_location(str(tmp_path), _RUN_ID) == str(tmp_path / _RUN_ID)


def test_record_fields_are_described() -> None:
    for field in OutputUploadRecord.model_fields.values():
        assert field.description, field
    assert UPLOAD_RECORD == "upload.yaml"


# ── upload ───────────────────────────────────────────────────────────────────


def test_upload_syncs_the_whole_root_under_its_run_id(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    root = _root(tmp_path)
    sync = mocker.patch("outputs.sync", side_effect=_fake_sync())
    record = upload_output(root, _STORE)
    config = sync.call_args.args[0]
    assert config.source == root
    assert config.destination == f"{_STORE}/{_RUN_ID}/"
    assert config.only_show_errors
    assert not config.dry_run
    assert not config.delete
    assert config.exclude == []
    assert config.include == []
    assert record.run_id == _RUN_ID
    assert record.store == _STORE
    assert record.destination == f"{_STORE}/{_RUN_ID}/"
    assert record.files == 4
    assert record.bytes > 0
    assert "@" in record.uploaded_by
    assert record.uploaded_at.tzinfo is not None
    # The record was written into the root before the sync, so it went up too.
    saved = OutputUploadRecord.model_validate(
        yaml.safe_load((root / UPLOAD_RECORD).read_text())
    )
    assert saved == record


def test_upload_dry_run_plans_and_writes_nothing(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    root = _root(tmp_path)
    sync = mocker.patch("outputs.sync", side_effect=_fake_sync())
    record = upload_output(root, _STORE, dry_run=True)
    config = sync.call_args.args[0]
    assert config.dry_run
    assert not config.only_show_errors
    assert not (root / UPLOAD_RECORD).exists()
    assert record.run_id == _RUN_ID


def test_upload_requires_a_run_root(tmp_path: Path, mocker: MockerFixture) -> None:
    sync = mocker.patch("outputs.sync")
    (tmp_path / "not-a-root").mkdir()
    with pytest.raises(ValueError, match=r"no run\.yaml"):
        upload_output(tmp_path / "not-a-root", _STORE)
    (tmp_path / "no-id").mkdir()
    (tmp_path / "no-id" / "run.yaml").write_text("application: cece\n")
    with pytest.raises(ValueError, match="no run_id"):
        upload_output(tmp_path / "no-id", _STORE)
    sync.assert_not_called()


def test_upload_to_a_directory_store_copies_and_resumes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    store = tmp_path / "store"
    record = upload_output(root, str(store))
    copy = store / _RUN_ID
    assert (copy / "run.yaml").is_file()
    assert (
        copy / "01JCOMBOCOMBOCOMBOCOMBOCOM" / "cece_20100101_010000.nc"
    ).read_bytes() == b"netcdf"
    assert (copy / UPLOAD_RECORD).is_file()
    assert record.destination == str(copy)
    # A re-upload after a change refreshes the copy; nothing is refused.
    (root / "test-report.csv").write_text("pytest_name,result\nx,passed\n")
    again = upload_output(root, str(store))
    assert (copy / "test-report.csv").read_text().endswith("x,passed\n")
    assert again.uploaded_at >= record.uploaded_at


def test_upload_to_a_directory_store_dry_run_counts_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = _root(tmp_path)
    store = tmp_path / "store"
    upload_output(root, str(store), dry_run=True)
    assert not store.exists()
    assert not (root / UPLOAD_RECORD).exists()
    assert any("4 file(s)" in r.getMessage() for r in caplog.records)


def test_upload_transfer_errors_propagate(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    root = _root(tmp_path)
    mocker.patch(
        "outputs.sync",
        side_effect=subprocess.CalledProcessError(1, ["aws"], output="denied"),
    )
    with pytest.raises(subprocess.CalledProcessError):
        upload_output(root, _STORE)


# ── download ─────────────────────────────────────────────────────────────────


def _arrives(root: Path) -> Callable[[Path], None]:
    """What aws would download: the root's tree."""
    import shutil

    def populate(destination: Path) -> None:
        shutil.copytree(root, destination, dirs_exist_ok=True)

    return populate


def test_download_syncs_into_dest_under_the_ulid(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = _root(tmp_path)
    (root / UPLOAD_RECORD).write_text(
        yaml.safe_dump(
            {
                "run_id": _RUN_ID,
                "store": _STORE,
                "destination": f"{_STORE}/{_RUN_ID}/",
                "uploaded_at": "2026-10-23T15:00:00+00:00",
                "uploaded_by": "someone@ufe03",
                "files": 4,
                "bytes": 42,
            }
        )
    )
    sync = mocker.patch(
        "outputs.sync", side_effect=_fake_sync(downloads=_arrives(root))
    )
    dest = tmp_path / "back"
    target = download_output(_RUN_ID, _STORE, dest)
    config = sync.call_args.args[0]
    assert config.source == f"{_STORE}/{_RUN_ID}/"
    assert str(config.destination).startswith(
        str(dest / ".staging")
    )  # staged, then moved
    assert target == dest / _RUN_ID
    assert (target / "run.yaml").is_file()
    assert (target / "01JCOMBOCOMBOCOMBOCOMBOCOM" / "cece_20100101_010000.nc").is_file()
    assert not (dest / ".staging").exists()
    assert any("someone@ufe03" in r.getMessage() for r in caplog.records)


def test_download_of_an_absent_ulid_is_refused_and_leaves_nothing(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    # aws exits 0 and downloads nothing for an absent prefix: run.yaml is the marker.
    mocker.patch("outputs.sync", side_effect=_fake_sync())
    dest = tmp_path / "back"
    with pytest.raises(
        ValueError, match=rf"no output for {_RUN_ID} under {re.escape(_STORE)}"
    ):
        download_output(_RUN_ID, _STORE, dest)
    assert not (dest / _RUN_ID).exists()
    assert not (dest / ".staging").exists()


def test_download_dry_run_plans_and_writes_nothing(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    sync = mocker.patch("outputs.sync", side_effect=_fake_sync())
    dest = tmp_path / "back"
    target = download_output(_RUN_ID, _STORE, dest, dry_run=True)
    assert sync.call_args.args[0].dry_run
    assert target == dest / _RUN_ID
    assert not dest.exists()


def test_download_from_a_directory_store(tmp_path: Path) -> None:
    root = _root(tmp_path)
    store = tmp_path / "store"
    upload_output(root, str(store))
    dest = tmp_path / "back"
    target = download_output(_RUN_ID, str(store), dest)
    assert (target / "run.yaml").read_text() == (root / "run.yaml").read_text()
    with pytest.raises(ValueError, match="no output for 01JABSENT"):
        download_output("01JABSENTABSENTABSENTABSEN", str(store), dest)
    # A dry run from a directory store neither copies nor fails.
    assert download_output(_RUN_ID, str(store), tmp_path / "dry", dry_run=True) == (
        tmp_path / "dry" / _RUN_ID
    )
    assert not (tmp_path / "dry").exists()


def test_download_replaces_a_stale_staging_directory(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    root = _root(tmp_path)
    mocker.patch("outputs.sync", side_effect=_fake_sync(downloads=_arrives(root)))
    dest = tmp_path / "back"
    stale = dest / ".staging" / _RUN_ID
    stale.mkdir(parents=True)
    (stale / "leftover").write_text("from a killed download")
    target = download_output(_RUN_ID, _STORE, dest)
    assert (target / "run.yaml").is_file()
    assert not (target / "leftover").exists()
    assert not (dest / ".staging").exists()


def test_download_into_an_existing_target_resumes(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    root = _root(tmp_path)
    mocker.patch("outputs.sync", side_effect=_fake_sync(downloads=_arrives(root)))
    dest = tmp_path / "back"
    (dest / _RUN_ID).mkdir(parents=True)
    (dest / _RUN_ID / "kept.txt").write_text("local")
    target = download_output(_RUN_ID, _STORE, dest)
    assert (target / "kept.txt").read_text() == "local"  # nothing local is deleted
    assert (target / "run.yaml").is_file()


# ── the live round trip (data_integration; deselected by default) ─────────────


@pytest.mark.data_integration
def test_private_bucket_output_round_trip(tmp_path: Path) -> None:
    """Upload a fabricated root under the test prefix of arn:aws:s3:::ufs-chem,
    download it by ULID beside it, compare the trees, prove a re-upload is a
    no-op, and empty the prefix. Fails (never skips) without credentials the
    CLI can find, or without the aws CLI."""
    from ulid import ULID

    from s3_sync import s3_uri_from_arn
    from tests.ufs_chem_assay.test_s3_sync import TEST_BUCKET_ARN, TEST_PREFIX, _files

    run_id = str(ULID())
    store = f"{s3_uri_from_arn(TEST_BUCKET_ARN)}/{TEST_PREFIX}/{run_id}-output"
    root = _root(tmp_path, run_id=run_id)
    empty = tmp_path / "empty"
    empty.mkdir()
    prefix = f"{store}/{run_id}/"
    try:
        upload_output(root, store, dry_run=True)
        assert not (root / UPLOAD_RECORD).exists()
        record = upload_output(root, store)
        assert (root / UPLOAD_RECORD).is_file()
        target = download_output(run_id, store, tmp_path / "back")
        assert _files(target) == _files(root)
        again = upload_output(root, store)
        assert again.files == record.files
        with pytest.raises(ValueError, match="no output for"):
            download_output(str(ULID()), store, tmp_path / "absent")
    finally:
        sync(S3SyncConfig(source=empty, destination=prefix, delete=True))
