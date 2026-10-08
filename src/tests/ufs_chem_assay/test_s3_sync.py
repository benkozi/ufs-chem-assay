"""The stand-alone `aws s3 sync` wrapper: the sync configuration model, the
exact argv, and the one-call-per-sync invariant — all with the process call
mocked. Credentials are the AWS CLI's own business (its credentials file,
profiles, environment, SSO); the wrapper never sees them.

The one live test (`data_integration`) talks to the real private test
bucket, arn:aws:s3:::ufs-chem, and is deselected unless asked for with
`-m data_integration`.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest_mock import MockerFixture
from ulid import ULID

from s3_sync import S3SyncConfig, S3SyncResult, s3_uri_from_arn, sync
from s3_sync import logger as s3_logger

# The private test bucket: always this one (user direction, 2026-10-06), as a
# constant rather than configuration. The live test writes only under
# TEST_PREFIX/<ULID>/ and empties exactly that.
TEST_BUCKET_ARN = "arn:aws:s3:::ufs-chem"
TEST_PREFIX = "ufs-chem-assay-tests"


@pytest.fixture()
def local_dir(tmp_path: Path) -> Path:
    path = tmp_path / "local"
    path.mkdir()
    (path / "probe.txt").write_text("probe\n")
    return path


def _completed(
    argv: list[str], returncode: int = 0, output: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(argv, returncode, stdout=output)


@pytest.fixture()
def aws_on_path(mocker: MockerFixture) -> str:
    """shutil.which resolves `aws` to a fixed path; nothing is executed."""
    mocker.patch("s3_sync.shutil.which", return_value="/usr/local/bin/aws")
    return "/usr/local/bin/aws"


# --- S3SyncConfig --------------------------------------------------------------


def test_local_to_s3_and_s3_to_local_accepted(local_dir: Path, tmp_path: Path) -> None:
    up = S3SyncConfig(source=local_dir, destination="s3://ufs-chem/prefix")
    assert up.source == local_dir.resolve() and up.destination == "s3://ufs-chem/prefix"
    down = S3SyncConfig(source="s3://ufs-chem/prefix", destination=tmp_path / "new")
    assert isinstance(down.destination, Path) and not (tmp_path / "new").exists()


def test_string_local_path_becomes_absolute_path(
    local_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(local_dir.parent)
    config = S3SyncConfig(source="local", destination="s3://ufs-chem/prefix")
    assert config.source == local_dir.resolve()
    assert config.argv[3] == str(local_dir.resolve())


def test_local_to_local_rejected(local_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="s3://"):
        S3SyncConfig(source=local_dir, destination=tmp_path / "other")


def test_s3_to_s3_rejected() -> None:
    with pytest.raises(ValidationError, match="S3-to-S3"):
        S3SyncConfig(source="s3://ufs-chem/a", destination="s3://ufs-chem/b")


@pytest.mark.parametrize(
    "uri",
    [
        "s3://UFS-CHEM/x",
        "s3://ab",
        "s3://ufs-chem./x",
        "s3:/ufs-chem",
        "http://ufs-chem",
    ],
)
def test_bad_bucket_uris_rejected(local_dir: Path, uri: str) -> None:
    with pytest.raises(ValidationError):
        S3SyncConfig(source=local_dir, destination=uri)


def test_prefix_with_slashes_accepted(local_dir: Path) -> None:
    config = S3SyncConfig(source=local_dir, destination="s3://ufs-chem/a/b/c/")
    assert config.destination == "s3://ufs-chem/a/b/c/"


def test_missing_local_source_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="existing directory"):
        S3SyncConfig(source=tmp_path / "nope", destination="s3://ufs-chem/x")


def test_file_as_local_source_rejected(local_dir: Path) -> None:
    with pytest.raises(ValidationError, match="existing directory"):
        S3SyncConfig(source=local_dir / "probe.txt", destination="s3://ufs-chem/x")


@pytest.mark.parametrize("root", ["s3://ufs-chem", "s3://ufs-chem/"])
def test_delete_refused_on_a_bucket_root(local_dir: Path, root: str) -> None:
    with pytest.raises(ValidationError, match="bucket root"):
        S3SyncConfig(source=local_dir, destination=root, delete=True)


def test_delete_allowed_under_a_prefix(local_dir: Path) -> None:
    config = S3SyncConfig(
        source=local_dir, destination="s3://ufs-chem/prefix", delete=True
    )
    assert "--delete" in config.argv


def test_delete_guard_applies_to_an_s3_source_too(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="bucket root"):
        S3SyncConfig(source="s3://ufs-chem", destination=tmp_path / "down", delete=True)


def test_only_show_errors_with_dry_run_rejected(local_dir: Path) -> None:
    with pytest.raises(ValidationError, match="only_show_errors"):
        S3SyncConfig(
            source=local_dir,
            destination="s3://ufs-chem/x",
            dry_run=True,
            only_show_errors=True,
        )


def test_timeout_must_be_positive(local_dir: Path) -> None:
    with pytest.raises(ValidationError):
        S3SyncConfig(source=local_dir, destination="s3://ufs-chem/x", timeout_s=0)


def test_profile_must_not_be_empty(local_dir: Path) -> None:
    with pytest.raises(ValidationError):
        S3SyncConfig(source=local_dir, destination="s3://ufs-chem/x", profile="")


def test_unknown_keys_rejected_and_frozen(local_dir: Path) -> None:
    with pytest.raises(ValidationError):
        S3SyncConfig(source=local_dir, destination="s3://ufs-chem/x", recursive=True)  # type: ignore[call-arg]
    config = S3SyncConfig(source=local_dir, destination="s3://ufs-chem/x")
    with pytest.raises(ValidationError):
        config.dry_run = True  # type: ignore[misc]


# --- argv ----------------------------------------------------------------------


def test_argv_plain(local_dir: Path) -> None:
    config = S3SyncConfig(source=local_dir, destination="s3://ufs-chem/prefix/")
    assert config.argv == [
        "aws",
        "s3",
        "sync",
        str(local_dir.resolve()),
        "s3://ufs-chem/prefix/",
        "--no-progress",
    ]


def test_argv_flags_and_filter_order(local_dir: Path) -> None:
    config = S3SyncConfig(
        source="s3://ufs-chem/prefix",
        destination=local_dir,
        dry_run=True,
        delete=True,
        profile="ufs",
        exclude=["*"],
        include=["*.nc", "fix/*"],
        aws_executable="/opt/aws-cli/bin/aws",
    )
    assert config.argv == [
        "/opt/aws-cli/bin/aws",
        "s3",
        "sync",
        "s3://ufs-chem/prefix",
        str(local_dir.resolve()),
        "--no-progress",
        "--dryrun",
        "--delete",
        "--profile",
        "ufs",
        "--exclude",
        "*",
        "--include",
        "*.nc",
        "--include",
        "fix/*",
    ]


def test_argv_only_show_errors(local_dir: Path) -> None:
    config = S3SyncConfig(
        source=local_dir, destination="s3://ufs-chem/x", only_show_errors=True
    )
    assert config.argv[-1] == "--only-show-errors"


def test_argv_has_no_profile_by_default(local_dir: Path) -> None:
    config = S3SyncConfig(source=local_dir, destination="s3://ufs-chem/x")
    assert "--profile" not in config.argv


def test_argv_no_sign_request_flag_and_position(local_dir: Path) -> None:
    # Anonymous reads of a public bucket: the flag sits with the other
    # boolean flags, after --only-show-errors and before any filter.
    config = S3SyncConfig(
        source="s3://geos-chem/HEMCO/MACCITY/v2014-07/",
        destination=local_dir,
        no_sign_request=True,
        only_show_errors=True,
        exclude=["*"],
        include=["MACCity_4x5.nc"],
    )
    argv = config.argv
    assert argv[5:] == [
        "--no-progress",
        "--only-show-errors",
        "--no-sign-request",
        "--exclude",
        "*",
        "--include",
        "MACCity_4x5.nc",
    ]


def test_no_sign_request_absent_by_default(local_dir: Path) -> None:
    config = S3SyncConfig(source=local_dir, destination="s3://ufs-chem/x")
    assert config.no_sign_request is False
    assert "--no-sign-request" not in config.argv


def test_no_sign_request_with_profile_rejected(local_dir: Path) -> None:
    # Anonymous access and a named credential profile contradict each other.
    with pytest.raises(ValidationError, match="no_sign_request"):
        S3SyncConfig(
            source="s3://geos-chem/x/",
            destination=local_dir,
            no_sign_request=True,
            profile="work",
        )


# --- sync() --------------------------------------------------------------------


def test_sync_runs_exactly_once_with_resolved_executable(
    aws_on_path: str,
    local_dir: Path,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = S3SyncConfig(
        source=local_dir, destination="s3://ufs-chem/prefix", timeout_s=30
    )
    run = mocker.patch(
        "s3_sync.subprocess.run",
        side_effect=lambda argv, **kwargs: _completed(
            argv, output=b"upload: probe.txt to s3://ufs-chem/prefix/probe.txt\n"
        ),
    )
    with caplog.at_level(logging.DEBUG, logger=s3_logger.name):
        result = sync(config)

    assert run.call_count == 1
    args, kwargs = run.call_args
    assert args[0][0] == aws_on_path and args[0][1:] == config.argv[1:]
    # The child inherits the environment: the CLI resolves credentials itself.
    assert "env" not in kwargs
    assert kwargs["stdout"] is subprocess.PIPE and kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["timeout"] == 30 and kwargs["check"] is False

    assert isinstance(result, S3SyncResult)
    assert result.argv == args[0] and result.returncode == 0 and result.dry_run is False
    assert result.output.startswith("upload: probe.txt")
    assert any(aws_on_path in record.message for record in caplog.records)
    # A real run's output is DEBUG only.
    output_records = [r for r in caplog.records if "upload: probe.txt" in r.message]
    assert output_records and all(r.levelno == logging.DEBUG for r in output_records)


@pytest.mark.usefixtures("aws_on_path")
def test_sync_dry_run_passes_the_flag_and_logs_the_plan_at_info(
    local_dir: Path,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    plan = b"(dryrun) upload: local/probe.txt to s3://ufs-chem/x/probe.txt\n"
    mocker.patch(
        "s3_sync.subprocess.run",
        side_effect=lambda argv, **kwargs: _completed(argv, output=plan),
    )
    with caplog.at_level(logging.INFO, logger=s3_logger.name):
        result = sync(
            S3SyncConfig(source=local_dir, destination="s3://ufs-chem/x", dry_run=True)
        )
    assert result.argv[-1] == "--dryrun" and result.dry_run is True
    assert result.output == plan.decode()
    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("(dryrun) upload:" in r.message for r in info)


@pytest.mark.usefixtures("aws_on_path")
def test_sync_failure_raises_with_output(
    local_dir: Path,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The CLI's own message when its credential chain finds nothing: the
    # wrapper has no earlier knowledge, so this is how "no credentials" fails.
    message = b"fatal error: Unable to locate credentials\n"
    mocker.patch(
        "s3_sync.subprocess.run",
        side_effect=lambda argv, **kwargs: _completed(
            argv, returncode=1, output=message
        ),
    )
    config = S3SyncConfig(source=local_dir, destination="s3://ufs-chem/x")
    with (
        caplog.at_level(logging.ERROR, logger=s3_logger.name),
        pytest.raises(subprocess.CalledProcessError) as excinfo,
    ):
        sync(config)
    assert excinfo.value.returncode == 1
    assert excinfo.value.output == message.decode()
    assert excinfo.value.cmd[1:] == config.argv[1:]
    assert any("Unable to locate credentials" in r.message for r in caplog.records)


@pytest.mark.usefixtures("aws_on_path")
def test_sync_timeout_propagates(local_dir: Path, mocker: MockerFixture) -> None:
    mocker.patch(
        "s3_sync.subprocess.run", side_effect=subprocess.TimeoutExpired("aws", 5)
    )
    with pytest.raises(subprocess.TimeoutExpired):
        sync(S3SyncConfig(source=local_dir, destination="s3://ufs-chem/x", timeout_s=5))


def test_sync_without_aws_on_path(local_dir: Path, mocker: MockerFixture) -> None:
    mocker.patch("s3_sync.shutil.which", return_value=None)
    run = mocker.patch("s3_sync.subprocess.run")
    with pytest.raises(FileNotFoundError, match="aws") as excinfo:
        sync(S3SyncConfig(source=local_dir, destination="s3://ufs-chem/x"))
    assert "README" in str(excinfo.value)
    run.assert_not_called()


# --- the test-bucket helpers and the mocked round trip -------------------------


def test_s3_uri_from_arn() -> None:
    assert s3_uri_from_arn(TEST_BUCKET_ARN) == "s3://ufs-chem"
    assert s3_uri_from_arn("arn:aws:s3:::ufs-chem/some/key") == "s3://ufs-chem/some/key"
    for bad in ("ufs-chem", "arn:aws:iam::123:user/x", "arn:aws:s3:::"):
        with pytest.raises(ValueError, match="S3 bucket ARN"):
            s3_uri_from_arn(bad)


@pytest.mark.usefixtures("aws_on_path")
def test_round_trip_is_two_syncs_two_calls(
    local_dir: Path, tmp_path: Path, mocker: MockerFixture
) -> None:
    run = mocker.patch(
        "s3_sync.subprocess.run", side_effect=lambda argv, **kwargs: _completed(argv)
    )
    prefix = f"{s3_uri_from_arn(TEST_BUCKET_ARN)}/{TEST_PREFIX}/{ULID()}/"
    sync(S3SyncConfig(source=local_dir, destination=prefix))
    sync(S3SyncConfig(source=prefix, destination=tmp_path / "down"))
    assert run.call_count == 2
    first, second = (call.args[0] for call in run.call_args_list)
    assert first[3:5] == [str(local_dir.resolve()), prefix]
    assert second[3:5] == [prefix, str((tmp_path / "down").resolve())]


# --- the live test -------------------------------------------------------------


def _files(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# The one public object the harness's maccity suites declare (CECE's own
# source bucket); its digest matches the checked-in suites' sha256.
PUBLIC_MACCITY_PREFIX = "s3://geos-chem/HEMCO/MACCITY/v2014-07/"
PUBLIC_MACCITY_FILE = "MACCity_4x5.nc"
PUBLIC_MACCITY_SHA256 = (
    "ef70975af53499ab45f620a778043ff6fd29ec4e7e2777f3fd1f2db59a12aab9"
)


@pytest.mark.data_integration
def test_public_bucket_single_object(tmp_path: Path) -> None:
    """A filtered, unsigned sync of a public bucket fetches exactly one
    object — no credentials involved (AWS_PROFILE may be unset)."""
    import hashlib

    result = sync(
        S3SyncConfig(
            source=PUBLIC_MACCITY_PREFIX,
            destination=tmp_path,
            no_sign_request=True,
            exclude=["*"],
            include=[PUBLIC_MACCITY_FILE],
        )
    )
    assert result.returncode == 0
    assert [p.name for p in tmp_path.iterdir()] == [PUBLIC_MACCITY_FILE]
    digest = hashlib.sha256((tmp_path / PUBLIC_MACCITY_FILE).read_bytes()).hexdigest()
    assert digest == PUBLIC_MACCITY_SHA256


@pytest.mark.data_integration
def test_private_bucket_round_trip(tmp_path: Path) -> None:
    """Authenticate to arn:aws:s3:::ufs-chem through the CLI's own credential
    chain, upload a small tree under a fresh ULID prefix, download it back,
    prove the second upload is a no-op, and empty the prefix — nothing left
    behind, even on a failed assertion. Fails (never skips) without
    credentials the CLI can find, or without the aws CLI."""
    ulid = str(ULID())
    prefix = f"{s3_uri_from_arn(TEST_BUCKET_ARN)}/{TEST_PREFIX}/{ulid}/"

    up = tmp_path / "up"
    (up / "nested").mkdir(parents=True)
    (up / "probe.txt").write_text(f"{ulid} {datetime.now(UTC).isoformat()}\n")
    (up / "nested" / "second.bin").write_bytes(os.urandom(4096))
    empty = tmp_path / "empty"
    empty.mkdir()
    body_passed = False
    try:
        plan = sync(S3SyncConfig(source=up, destination=prefix, dry_run=True))
        assert plan.output.count("(dryrun) upload:") == 2, plan.output
        assert "probe.txt" in plan.output and "second.bin" in plan.output

        sync(S3SyncConfig(source=up, destination=prefix))
        sync(S3SyncConfig(source=prefix, destination=tmp_path / "down"))
        assert _files(tmp_path / "down") == _files(up)

        again = sync(S3SyncConfig(source=up, destination=prefix))
        assert "upload:" not in again.output, again.output  # unchanged files skipped
        body_passed = True
    finally:
        try:
            plan = sync(
                S3SyncConfig(
                    source=empty, destination=prefix, delete=True, dry_run=True
                )
            )
            if body_passed:
                assert plan.output.count("(dryrun) delete:") == 2, plan.output
            sync(S3SyncConfig(source=empty, destination=prefix, delete=True))
            sync(S3SyncConfig(source=prefix, destination=tmp_path / "check"))
            if body_passed:
                assert _files(tmp_path / "check") == {}
        except Exception:
            if body_passed:
                raise
            # Best effort: the body's own failure is the one to report.
            s3_logger.exception(
                "cleanup of %s failed after the test body failed", prefix
            )
