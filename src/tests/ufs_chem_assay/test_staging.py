"""staging.py: inputs merged across suites, staged through the S3 wrapper
(mocked — no aws, no network), verified by digest, moved into place."""

import hashlib
import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath

import pytest
from pydantic import ValidationError
from pytest_mock import MockerFixture

from models.suite_config import InputFile, SuiteConfig
from platforms import Platform
from s3_sync import S3SyncConfig, S3SyncResult
from staging import (
    STAGING_DIRNAME,
    StagedInput,
    merge_inputs,
    missing_inputs,
    rewrite_suite_digests,
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
    with pytest.raises(ValueError, match=r"MACCity_4x5\.nc"):
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
    assert result.action == "skipped"
    assert "verified" in result.detail
    assert result.sha256 == _DIGEST
    assert result.path == tmp_path / "MACCity_4x5.nc"
    sync.assert_not_called()


def test_present_without_digest_is_skipped_unverified(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "MACCity_4x5.nc").write_bytes(b"whatever")
    sync = mocker.patch("staging.sync")
    (result,) = stage_inputs([_input(sha256=None)], tmp_path)
    assert result.action == "skipped"
    assert "no digest" in result.detail
    assert result.sha256 is None
    sync.assert_not_called()


def test_empty_present_file_is_refetched(tmp_path: Path, mocker: MockerFixture) -> None:
    (tmp_path / "MACCity_4x5.nc").write_bytes(b"")
    mocker.patch("staging.sync", side_effect=_fake_sync())
    (result,) = stage_inputs([_input(sha256=None)], tmp_path)
    assert result.action == "downloaded"
    assert (tmp_path / "MACCity_4x5.nc").read_bytes() == _PAYLOAD


def test_source_less_input_present_and_verified_is_skipped(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "placed.nc").write_bytes(_PAYLOAD)
    sync = mocker.patch("staging.sync")
    probe = mocker.patch("staging.check_reachable")
    (result,) = stage_inputs([_input(url=None, dst="placed.nc")], tmp_path)
    assert result.action == "skipped"
    assert "verified" in result.detail
    sync.assert_not_called()
    probe.assert_not_called()


def test_source_less_input_present_but_wrong_fails_without_a_fetch(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "placed.nc").write_bytes(b"stale")
    sync = mocker.patch("staging.sync")
    (result,) = stage_inputs([_input(url=None, dst="placed.nc")], tmp_path)
    assert result.action == "failed"
    assert _DIGEST in result.detail
    assert "no source" in result.detail
    assert str(tmp_path / "placed.nc") in result.detail
    assert (tmp_path / "placed.nc").read_bytes() == b"stale"  # never touched
    sync.assert_not_called()


@pytest.mark.parametrize("dry_run", [False, True])
def test_source_less_input_absent_fails_naming_the_destination(
    tmp_path: Path, mocker: MockerFixture, dry_run: bool
) -> None:
    # A dry run cannot plan a fetch for it either: the outcome is the same.
    sync = mocker.patch("staging.sync")
    probe = mocker.patch("staging.check_reachable")
    (result,) = stage_inputs(
        [_input(url=None, dst="sub/placed.nc")], tmp_path, dry_run=dry_run
    )
    assert result.action == "failed"
    assert "no input search path" in result.detail  # nothing to link from either
    assert "place or link the file at" in result.detail
    assert str(tmp_path / "sub" / "placed.nc") in result.detail
    assert not (tmp_path / "sub").exists()
    sync.assert_not_called()
    probe.assert_not_called()


# ── Source-less inputs linked from the input search path ─────────────────────


def _originals(tmp_path: Path, *names: str, payload: bytes = _PAYLOAD) -> Path:
    """A search-path directory holding the named files, nested (the lookup
    is recursive, by basename)."""
    root = tmp_path / "originals"
    for index, name in enumerate(names):
        path = root / f"tree{index}" / "deep" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    root.mkdir(exist_ok=True)
    return root


def test_source_less_absent_input_is_linked_from_the_search_path(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    originals = _originals(tmp_path, "placed.nc")
    data_dir = tmp_path / "data"
    sync = mocker.patch("staging.sync")
    probe = mocker.patch("staging.check_reachable")
    (result,) = stage_inputs(
        [_input(url=None, dst="sub/placed.nc")], data_dir, search_path=[originals]
    )
    link = data_dir / "sub" / "placed.nc"
    original = originals / "tree0" / "deep" / "placed.nc"
    assert result.action == "linked"
    assert str(original) in result.detail
    assert "verified" in result.detail  # the linked file is verified like any other
    assert result.sha256 == _DIGEST
    assert link.is_symlink()
    assert link.resolve() == original.resolve()
    assert Path(os.readlink(link)).is_absolute()
    sync.assert_not_called()
    probe.assert_not_called()


def test_linked_file_with_a_wrong_digest_keeps_the_link_and_fails(
    tmp_path: Path,
) -> None:
    originals = _originals(tmp_path, "placed.nc", payload=b"other bytes")
    data_dir = tmp_path / "data"
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc")], data_dir, search_path=[originals]
    )
    assert result.action == "failed"
    assert "linked" in result.detail
    assert _DIGEST in result.detail
    assert (data_dir / "placed.nc").is_symlink()  # the link is the user's to judge


def test_link_target_not_found_names_the_search_path(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    data_dir = tmp_path / "data"
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc")], data_dir, search_path=[empty]
    )
    assert result.action == "failed"
    assert "not found under the input search path" in result.detail
    assert str(empty) in result.detail
    assert "place or link the file at" in result.detail
    assert not (data_dir / "placed.nc").exists()
    # An empty search path says so instead of listing nothing.
    (result,) = stage_inputs([_input(url=None, dst="placed.nc")], data_dir)
    assert result.action == "failed"
    assert "no input search path" in result.detail


def test_ambiguous_link_target_is_refused(tmp_path: Path) -> None:
    originals = _originals(tmp_path, "placed.nc", "placed.nc")
    data_dir = tmp_path / "data"
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc")], data_dir, search_path=[originals]
    )
    assert result.action == "failed"
    assert "ambiguous" in result.detail
    assert "tree0" in result.detail
    assert "tree1" in result.detail
    assert not (data_dir / "placed.nc").exists()


def test_dangling_link_counts_as_absent_and_is_replaced(tmp_path: Path) -> None:
    originals = _originals(tmp_path, "placed.nc")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "placed.nc").symlink_to(tmp_path / "gone.nc")
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc")], data_dir, search_path=[originals]
    )
    assert result.action == "linked"
    assert (data_dir / "placed.nc").read_bytes() == _PAYLOAD


def test_present_file_is_never_relinked(tmp_path: Path) -> None:
    originals = _originals(tmp_path, "placed.nc")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "placed.nc").write_bytes(_PAYLOAD)
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc")], data_dir, search_path=[originals]
    )
    assert result.action == "skipped"
    assert not (data_dir / "placed.nc").is_symlink()


def test_dry_run_plans_the_link_and_creates_nothing(tmp_path: Path) -> None:
    originals = _originals(tmp_path, "placed.nc")
    data_dir = tmp_path / "data"
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc")],
        data_dir,
        search_path=[originals],
        dry_run=True,
    )
    assert result.action == "would-link"
    assert str(originals / "tree0" / "deep" / "placed.nc") in result.detail
    assert not data_dir.exists()


def test_url_inputs_are_never_linked(tmp_path: Path, mocker: MockerFixture) -> None:
    originals = _originals(tmp_path, "MACCity_4x5.nc")
    mocker.patch("staging.sync", side_effect=_fake_sync())
    mocker.patch("staging.check_reachable")
    (result,) = stage_inputs([_input()], tmp_path / "data", search_path=[originals])
    assert result.action == "downloaded"
    assert not (tmp_path / "data" / "MACCity_4x5.nc").is_symlink()


# ── Declared per-platform sources ────────────────────────────────────────────


def test_declared_source_for_the_platform_is_linked_without_a_search(
    tmp_path: Path,
) -> None:
    original = tmp_path / "known" / "placed.nc"
    original.parent.mkdir()
    original.write_bytes(_PAYLOAD)
    decoy = _originals(
        tmp_path, "placed.nc", payload=b"decoy"
    )  # would be ambiguous otherwise
    data_dir = tmp_path / "data"
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc", sources={"ursa": str(original)})],
        data_dir,
        search_path=[decoy],
        platform=Platform.URSA,
    )
    assert result.action == "linked"
    assert str(original) in result.detail
    assert (data_dir / "placed.nc").resolve() == original.resolve()
    assert result.sha256 == _DIGEST


def test_declared_source_missing_is_a_named_failure_not_a_fallback(
    tmp_path: Path,
) -> None:
    originals = _originals(tmp_path, "placed.nc")  # a search-path copy exists
    data_dir = tmp_path / "data"
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc", sources={"ursa": "/gone/placed.nc"})],
        data_dir,
        search_path=[originals],
        platform=Platform.URSA,
    )
    assert result.action == "failed"
    assert "declared source for ursa is missing: /gone/placed.nc" in result.detail
    assert not (data_dir / "placed.nc").exists()


def test_other_platforms_fall_back_to_the_search_path(tmp_path: Path) -> None:
    originals = _originals(tmp_path, "placed.nc")
    data_dir = tmp_path / "data"
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc", sources={"ursa": "/gone/placed.nc"})],
        data_dir,
        search_path=[originals],
        platform=Platform.LOCAL,
    )
    assert result.action == "linked"


def test_declared_source_dry_run_plans_the_link(tmp_path: Path) -> None:
    original = tmp_path / "known.nc"
    original.write_bytes(_PAYLOAD)
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc", sources={"ursa": str(original)})],
        tmp_path / "data",
        platform=Platform.URSA,
        dry_run=True,
    )
    assert result.action == "would-link"
    assert str(original) in result.detail


# ── fetch --record-digests ────────────────────────────────────────────────────


def test_record_digests_records_a_differing_source_less_file(tmp_path: Path) -> None:
    (tmp_path / "placed.nc").write_bytes(b"new bytes")
    new_digest = hashlib.sha256(b"new bytes").hexdigest()
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc")], tmp_path, record_digests=True
    )
    assert result.action == "recorded"
    assert result.sha256 == new_digest
    assert new_digest in result.detail
    assert _DIGEST in result.detail  # the declared (old) digest named


def test_record_digests_after_linking(tmp_path: Path) -> None:
    originals = _originals(tmp_path, "placed.nc", payload=b"new bytes")
    data_dir = tmp_path / "data"
    (result,) = stage_inputs(
        [_input(url=None, dst="placed.nc")],
        data_dir,
        search_path=[originals],
        record_digests=True,
    )
    assert result.action == "recorded"
    assert "linked" in result.detail
    assert result.sha256 == hashlib.sha256(b"new bytes").hexdigest()


def test_record_digests_leaves_verified_and_url_inputs_alone(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "placed.nc").write_bytes(_PAYLOAD)
    (tmp_path / "MACCity_4x5.nc").write_bytes(b"stale")
    mocker.patch("staging.sync", side_effect=_fake_sync())
    mocker.patch("staging.check_reachable")
    placed, fetched = stage_inputs(
        [_input(url=None, dst="placed.nc"), _input()], tmp_path, record_digests=True
    )
    assert placed.action == "skipped"  # verified: nothing to record
    assert fetched.action == "downloaded"  # a URL input is re-fetched, never recorded


_PLACEHOLDER = "0" * 64


def _suite_text(config_path: Path, digests: dict[str, str]) -> str:
    entries = "".join(
        f"  # {dst}\n  - url: null\n    dst: {dst}\n    sha256: '{digest}'  # placed\n"
        for dst, digest in digests.items()
    )
    return (
        f"name: placed\nconfig_path: {config_path}\ntimeout_s: 5\n"
        "# inputs placed by hand\n"
        f"inputs:\n{entries}"
        "analysis:\n  compute_descriptive_stats: false\n"
        "plotting:\n  enabled: false\n  gif_enabled: false\n"
    )


def test_rewrite_suite_digests_replaces_only_the_named_entries(
    tmp_path: Path, cece_config_path: Path
) -> None:
    from applications.registry import load_suite

    suite_path = tmp_path / "placed-suite.yaml"
    suite_path.write_text(
        _suite_text(cece_config_path, {"a/x.nc": _PLACEHOLDER, "b/y.nc": _PLACEHOLDER})
    )
    before = suite_path.read_text()
    rewrite_suite_digests(suite_path, {PurePosixPath("a/x.nc"): _DIGEST})
    after = suite_path.read_text()
    assert f"    sha256: '{_DIGEST}'  # placed\n" in after
    assert after.count(_PLACEHOLDER) == 1  # y.nc untouched
    assert after.count("#") == before.count("#")  # every comment kept
    suite = load_suite(suite_path)
    assert {str(e.dst): e.sha256 for e in suite.inputs} == {
        "a/x.nc": _DIGEST,
        "b/y.nc": _PLACEHOLDER,
    }


def test_rewrite_suite_digests_refuses_an_unknown_dst(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_path = tmp_path / "placed-suite.yaml"
    suite_path.write_text(_suite_text(cece_config_path, {"a/x.nc": _PLACEHOLDER}))
    with pytest.raises(ValueError, match=r"no `dst: other\.nc` entry"):
        rewrite_suite_digests(suite_path, {PurePosixPath("other.nc"): _DIGEST})
    assert _PLACEHOLDER in suite_path.read_text()


def test_rewrite_suite_digests_needs_a_sha256_line_to_rewrite(
    tmp_path: Path, cece_config_path: Path
) -> None:
    # A URL entry without a digest has no line to rewrite (and is never
    # recorded anyway); the refusal names the entry.
    suite_path = tmp_path / "placed-suite.yaml"
    suite_path.write_text(
        f"name: placed\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        f"inputs:\n  - url: {_URL}\n    dst: MACCity_4x5.nc\n"
    )
    with pytest.raises(ValueError, match="has no `sha256:` line"):
        rewrite_suite_digests(suite_path, {PurePosixPath("MACCity_4x5.nc"): _DIGEST})


def test_rewrite_suite_digests_restores_the_file_when_the_reload_changes_more(
    tmp_path: Path, cece_config_path: Path, mocker: MockerFixture
) -> None:
    # The reload must equal the previous suite with only the digests changed;
    # anything else (here: a reloader that disagrees) restores the file.
    suite_path = tmp_path / "placed-suite.yaml"
    original = _suite_text(cece_config_path, {"a/x.nc": _PLACEHOLDER})
    suite_path.write_text(original)
    reloads = iter(
        [{"inputs": [{"dst": "a/x.nc", "sha256": _PLACEHOLDER}]}, {"other": 1}]
    )
    mocker.patch(
        "applications.registry.load_suite",
        return_value=mocker.Mock(model_dump=lambda: next(reloads)),
    )
    with pytest.raises(
        ValueError, match="differs beyond the recorded digests; restored"
    ):
        rewrite_suite_digests(suite_path, {PurePosixPath("a/x.nc"): _DIGEST})
    assert suite_path.read_text() == original


def test_rewrite_suite_digests_restores_the_file_when_the_reload_differs(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_path = tmp_path / "placed-suite.yaml"
    original = _suite_text(cece_config_path, {"a/x.nc": _PLACEHOLDER})
    suite_path.write_text(original)
    with pytest.raises(ValueError, match="restored"):
        rewrite_suite_digests(suite_path, {PurePosixPath("a/x.nc"): "not-a-digest"})
    assert suite_path.read_text() == original


def test_fetch_is_one_filtered_sync_of_the_parent_prefix(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    sync = mocker.patch("staging.sync", side_effect=_fake_sync())
    (result,) = stage_inputs([_input(public=True, dst="sub/MACCity_4x5.nc")], tmp_path)

    assert sync.call_count == 1
    config = sync.call_args.args[0]
    assert isinstance(config, S3SyncConfig)
    assert config.source == "s3://geos-chem/HEMCO/MACCITY/v2014-07/"
    assert config.exclude == ["*"]
    assert config.include == ["MACCity_4x5.nc"]
    assert config.no_sign_request is True
    assert config.only_show_errors is True
    assert config.dry_run is False
    assert isinstance(config.destination, Path)
    assert config.destination.parent == tmp_path / STAGING_DIRNAME
    assert result.action == "downloaded"
    assert result.sha256 == _DIGEST
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
    assert result.action == "failed"
    assert "sha256" in result.detail
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
    assert result.action == "downloaded"
    assert result.sha256 == _DIGEST
    assert (tmp_path / "MACCity_4x5.nc").read_bytes() == _PAYLOAD


def test_nothing_matched_is_a_failure(tmp_path: Path, mocker: MockerFixture) -> None:
    # aws exits 0 when the include filter matches no object; staging checks
    # that the file actually arrived.
    mocker.patch("staging.sync", side_effect=_fake_sync(write=False))
    (result,) = stage_inputs([_input()], tmp_path)
    assert result.action == "failed"
    assert "no such object" in result.detail
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
    assert config.dry_run is True
    assert config.only_show_errors is False
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
    with pytest.raises(ValidationError, match="frozen"):
        result.action = "failed"  # type: ignore[misc]


# ── missing_inputs ────────────────────────────────────────────────────────────


def test_missing_inputs_checks_existence_only(tmp_path: Path) -> None:
    (tmp_path / "here.nc").write_bytes(b"x")
    inputs = [
        _input(url="s3://bkt/here.nc", dst="here.nc"),
        _input(url="s3://bkt/gone.nc", dst="sub/gone.nc"),
    ]
    assert missing_inputs(inputs, tmp_path) == [tmp_path / "sub" / "gone.nc"]


# ── fail fast without network ─────────────────────────────────────────────────


def test_unreachable_bucket_fails_every_fetch_without_a_sync(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    probe = mocker.patch(
        "staging.check_reachable", side_effect=ConnectionError("no network to x")
    )
    sync = mocker.patch("staging.sync")
    results = stage_inputs(
        [_input(), _input(url="s3://geos-chem/other/b.nc", dst="b.nc", sha256=None)],
        tmp_path,
    )
    assert [r.action for r in results] == ["failed", "failed"]
    assert all("no network" in r.detail for r in results)
    probe.assert_called_once()  # one bucket, probed once
    sync.assert_not_called()


def test_present_inputs_never_probe_the_network(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    (tmp_path / "MACCity_4x5.nc").write_bytes(_PAYLOAD)
    probe = mocker.patch("staging.check_reachable")
    (result,) = stage_inputs([_input()], tmp_path)
    assert result.action == "skipped"
    probe.assert_not_called()


def test_reachable_bucket_is_probed_once_then_fetched(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    probe = mocker.patch("staging.check_reachable")
    mocker.patch("staging.sync", side_effect=_fake_sync())
    results = stage_inputs(
        [
            _input(),
            _input(url=_URL.replace("MACCity_4x5", "other"), dst="o.nc", sha256=None),
        ],
        tmp_path,
    )
    assert [r.action for r in results] == ["downloaded", "downloaded"]
    probe.assert_called_once_with(_URL)


def test_dry_run_sync_failure_is_recorded(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("staging.check_reachable")
    mocker.patch(
        "staging.sync",
        side_effect=subprocess.CalledProcessError(
            1, ["aws"], output="x\nAccessDenied\n"
        ),
    )
    (result,) = stage_inputs([_input()], tmp_path, dry_run=True)
    assert result.action == "failed"
    assert "AccessDenied" in result.detail
