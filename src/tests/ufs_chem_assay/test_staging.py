"""staging.py: inputs merged across suites, staged through the S3 wrapper
(mocked — no aws, no network), verified by digest, moved into place."""

import hashlib
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from models.suite_config import InputFile, SuiteConfig
from s3_sync import S3SyncConfig, S3SyncResult
from staging import (
    STAGING_DIRNAME,
    StagedInput,
    merge_inputs,
    missing_inputs,
    stage_inputs,
)

_URL = "s3://geos-chem/HEMCO/MACCITY/v2014-07/MACCity_4x5.nc"
_PAYLOAD = b"netcdf bytes"
_DIGEST = hashlib.sha256(_PAYLOAD).hexdigest()


def _input(**overrides: object) -> InputFile:
    values: dict[str, object] = {
        "url": _URL,
        "dst": "MACCity_4x5.nc",
        "sha256": _DIGEST,
    }
    values.update(overrides)
    return InputFile.model_validate(values)


def _suite(name: str, inputs: list[InputFile], config_path: Path) -> SuiteConfig:
    return SuiteConfig.model_validate(
        {
            "name": name,
            "config_path": str(config_path),
            "timeout_s": 5,
            "inputs": inputs,
        }
    )


def _fake_sync(
    payload: bytes = _PAYLOAD, *, write: bool = True
) -> Callable[[S3SyncConfig], S3SyncResult]:
    """A sync that behaves like aws: writes the one included object into the
    destination directory (unless `write` is False: nothing matched)."""

    def fake(config: S3SyncConfig) -> S3SyncResult:
        assert isinstance(config.destination, Path)
        if write and not config.dry_run:
            config.destination.mkdir(parents=True, exist_ok=True)
            (config.destination / config.include[0]).write_bytes(payload)
        return S3SyncResult(
            argv=config.argv, returncode=0, output="", dry_run=config.dry_run
        )

    return fake


# ── merge_inputs ──────────────────────────────────────────────────────────────


def test_merge_inputs_dedupes_identical_entries(cece_config_path: Path) -> None:
    a = _suite("a", [_input()], cece_config_path)
    b = _suite(
        "b",
        [_input(), _input(url="s3://other/x.nc", dst="x.nc", sha256=None)],
        cece_config_path,
    )
    merged = merge_inputs([a, b])
    assert [str(i.dst) for i in merged] == ["MACCity_4x5.nc", "x.nc"]


def test_merge_inputs_rejects_a_dst_declared_two_ways(cece_config_path: Path) -> None:
    a = _suite("a", [_input()], cece_config_path)
    b = _suite("b", [_input(sha256=None)], cece_config_path)
    with pytest.raises(ValueError, match="MACCity_4x5.nc"):
        merge_inputs([a, b])
    c = _suite("c", [_input(url="s3://elsewhere/MACCity_4x5.nc")], cece_config_path)
    with pytest.raises(ValueError, match="declared differently"):
        merge_inputs([a, c])


# ── stage_inputs ──────────────────────────────────────────────────────────────


def test_present_and_verified_is_skipped_without_network(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "MACCity_4x5.nc").write_bytes(_PAYLOAD)
    sync = mocker.patch("staging.sync")
    (result,) = stage_inputs([_input()], tmp_path)
    assert result.action == "skipped" and "verified" in result.detail
    assert result.sha256 == _DIGEST and result.path == tmp_path / "MACCity_4x5.nc"
    sync.assert_not_called()


def test_present_without_digest_is_skipped_unverified(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "MACCity_4x5.nc").write_bytes(b"whatever")
    sync = mocker.patch("staging.sync")
    (result,) = stage_inputs([_input(sha256=None)], tmp_path)
    assert result.action == "skipped" and "no digest" in result.detail
    assert result.sha256 is None
    sync.assert_not_called()


def test_empty_present_file_is_refetched(tmp_path: Path, mocker: MockerFixture) -> None:
    (tmp_path / "MACCity_4x5.nc").write_bytes(b"")
    mocker.patch("staging.sync", side_effect=_fake_sync())
    (result,) = stage_inputs([_input(sha256=None)], tmp_path)
    assert result.action == "downloaded"
    assert (tmp_path / "MACCity_4x5.nc").read_bytes() == _PAYLOAD


def test_fetch_is_one_filtered_sync_of_the_parent_prefix(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    sync = mocker.patch("staging.sync", side_effect=_fake_sync())
    (result,) = stage_inputs([_input(public=True, dst="sub/MACCity_4x5.nc")], tmp_path)

    assert sync.call_count == 1
    config = sync.call_args.args[0]
    assert isinstance(config, S3SyncConfig)
    assert config.source == "s3://geos-chem/HEMCO/MACCITY/v2014-07/"
    assert config.exclude == ["*"] and config.include == ["MACCity_4x5.nc"]
    assert config.no_sign_request is True
    assert config.only_show_errors is True and config.dry_run is False
    assert isinstance(config.destination, Path)
    assert config.destination.parent == tmp_path / STAGING_DIRNAME
    assert result.action == "downloaded" and result.sha256 == _DIGEST
    assert (tmp_path / "sub" / "MACCity_4x5.nc").read_bytes() == _PAYLOAD
    assert not (tmp_path / STAGING_DIRNAME).exists()  # staging gone afterwards


def test_private_input_signs_requests(tmp_path: Path, mocker: MockerFixture) -> None:
    sync = mocker.patch("staging.sync", side_effect=_fake_sync())
    stage_inputs([_input(public=False)], tmp_path)
    assert sync.call_args.args[0].no_sign_request is False


def test_digest_mismatch_is_refetched_then_fails_if_still_wrong(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "MACCity_4x5.nc").write_bytes(b"stale")
    mocker.patch("staging.sync", side_effect=_fake_sync(b"still wrong"))
    (result,) = stage_inputs([_input()], tmp_path)
    assert result.action == "failed" and "sha256" in result.detail
    assert _DIGEST in result.detail  # expected digest named
    # Nothing replaced the stale file; nothing staged is left behind.
    assert (tmp_path / "MACCity_4x5.nc").read_bytes() == b"stale"
    assert not (tmp_path / STAGING_DIRNAME).exists()


def test_digest_mismatch_refetch_succeeds_when_the_store_is_right(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "MACCity_4x5.nc").write_bytes(b"stale")
    mocker.patch("staging.sync", side_effect=_fake_sync())
    (result,) = stage_inputs([_input()], tmp_path)
    assert result.action == "downloaded" and result.sha256 == _DIGEST
    assert (tmp_path / "MACCity_4x5.nc").read_bytes() == _PAYLOAD


def test_nothing_matched_is_a_failure(tmp_path: Path, mocker: MockerFixture) -> None:
    # aws exits 0 when the include filter matches no object; staging checks
    # that the file actually arrived.
    mocker.patch("staging.sync", side_effect=_fake_sync(write=False))
    (result,) = stage_inputs([_input()], tmp_path)
    assert result.action == "failed" and "no such object" in result.detail
    assert not (tmp_path / "MACCity_4x5.nc").exists()


def test_wrapper_errors_become_failed_results_and_the_rest_proceed(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    calls: Iterator[Exception | Callable[[S3SyncConfig], S3SyncResult]] = iter(
        [
            subprocess.CalledProcessError(
                255, ["aws"], output="Unable to locate credentials"
            ),
            FileNotFoundError("'aws' not found on PATH: install AWS CLI v2"),
            _fake_sync(),
        ]
    )

    def dispatch(config: S3SyncConfig) -> S3SyncResult:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item(config)

    mocker.patch("staging.sync", side_effect=dispatch)
    results = stage_inputs(
        [
            _input(url="s3://bkt/one.nc", dst="one.nc", sha256=None),
            _input(url="s3://bkt/two.nc", dst="two.nc", sha256=None),
            _input(url="s3://bkt/three.nc", dst="three.nc", sha256=None),
        ],
        tmp_path,
    )
    assert [r.action for r in results] == ["failed", "failed", "downloaded"]
    assert "Unable to locate credentials" in results[0].detail
    assert "install AWS CLI v2" in results[1].detail
    assert (tmp_path / "three.nc").is_file()


def test_dry_run_classifies_and_writes_nothing(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "present.nc").write_bytes(_PAYLOAD)
    sync = mocker.patch("staging.sync", side_effect=_fake_sync())
    results = stage_inputs(
        [
            _input(url="s3://bkt/present.nc", dst="present.nc"),
            _input(url="s3://bkt/absent.nc", dst="absent.nc"),
        ],
        tmp_path,
        dry_run=True,
    )
    assert [r.action for r in results] == ["skipped", "would-fetch"]
    # The plan passes straight through to the wrapper's dry run.
    assert sync.call_count == 1
    config = sync.call_args.args[0]
    assert config.dry_run is True and config.only_show_errors is False
    assert not (tmp_path / "absent.nc").exists()
    assert not (tmp_path / STAGING_DIRNAME).exists()


def test_stale_staging_directory_is_removed_first(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    leftover = tmp_path / STAGING_DIRNAME / "01OLD"
    leftover.mkdir(parents=True)
    (leftover / "half.nc").write_bytes(b"partial")
    mocker.patch("staging.sync")
    (tmp_path / "MACCity_4x5.nc").write_bytes(_PAYLOAD)
    stage_inputs([_input()], tmp_path)
    assert not (tmp_path / STAGING_DIRNAME).exists()


def test_results_are_frozen_models() -> None:
    result = StagedInput(
        url=_URL, path=Path("/x"), action="skipped", detail="", sha256=None
    )
    with pytest.raises(Exception):
        result.action = "failed"  # type: ignore[misc]


# ── missing_inputs ────────────────────────────────────────────────────────────


def test_missing_inputs_checks_existence_only(tmp_path: Path) -> None:
    (tmp_path / "here.nc").write_bytes(b"x")
    inputs = [
        _input(url="s3://bkt/here.nc", dst="here.nc"),
        _input(url="s3://bkt/gone.nc", dst="sub/gone.nc"),
    ]
    assert missing_inputs(inputs, tmp_path) == [tmp_path / "sub" / "gone.nc"]
