"""CeceSettings: the CECE_* namespace and the application commit for run.yaml."""

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from applications.cece.settings import CONTAINER_WORKDIR, ENV_PREFIX, CeceSettings


@pytest.fixture()
def clean_cece_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> pytest.MonkeyPatch:
    for key in (
        "CECE_ROOT_DIR",
        "CECE_ROOT",
        "CECE_DOCKER_IMAGE",
        "CECE_DRIVER_PATH",
        "CECE_MODULEFILE",
        "ASSAY_ROOT_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


def test_defaults(clean_cece_env: pytest.MonkeyPatch) -> None:
    settings = CeceSettings()
    assert settings.root_dir is None
    assert settings.docker_image == "cece/cece-dev"
    assert settings.driver_path == "./build/cece_standalone_driver"
    assert settings.modulefile is None
    assert ENV_PREFIX == "CECE_" and str(CONTAINER_WORKDIR) == "/work"


def test_root_dir_reads_cece_root_dir_env(clean_cece_env: pytest.MonkeyPatch) -> None:
    clean_cece_env.setenv("CECE_ROOT_DIR", "/host/cece")
    assert CeceSettings().root_dir == Path("/host/cece")


def test_assay_prefix_never_applies_to_application_settings(
    clean_cece_env: pytest.MonkeyPatch,
) -> None:
    clean_cece_env.setenv("ASSAY_ROOT_DIR", "/host/cece")
    assert CeceSettings().root_dir is None


def test_legacy_cece_root_env_has_no_effect(
    clean_cece_env: pytest.MonkeyPatch,
) -> None:
    clean_cece_env.setenv("CECE_ROOT", "/host/cece")
    assert CeceSettings().root_dir is None


def test_settings_is_frozen(clean_cece_env: pytest.MonkeyPatch) -> None:
    settings = CeceSettings()
    with pytest.raises(ValidationError):
        settings.root_dir = Path("/host/cece")  # type: ignore[misc]


def test_env_file_supplies_values_and_ignores_harness_keys(
    clean_cece_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "cece_root_dir=/from/dotenv\ncece_modulefile=cece_ursa.gnu\n"
        "cece_dask_nworkers=2\nassay_log_level=DEBUG\n"
    )
    settings = CeceSettings()
    assert settings.root_dir == Path("/from/dotenv")
    assert settings.modulefile == "cece_ursa.gnu"


def test_real_env_beats_env_file(
    clean_cece_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("cece_root_dir=/from/dotenv\n")
    clean_cece_env.setenv("CECE_ROOT_DIR", "/from/env")
    assert CeceSettings().root_dir == Path("/from/env")


# -- commit_sha: the checkout's HEAD SHA for run.yaml -------------------------


def _git_repo_with_commit(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "initial",
        ],
        cwd=path,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return head.stdout.strip()


def test_commit_sha_of_git_checkout(tmp_path: Path) -> None:
    expected = _git_repo_with_commit(tmp_path)
    assert CeceSettings(root_dir=tmp_path).commit_sha() == expected
    assert len(expected) == 40


def test_commit_sha_raises_for_non_repo(tmp_path: Path) -> None:
    # Sessions always run against a checked-out application: a configured
    # root without a resolvable SHA is fatal, not a recordable state.
    with pytest.raises(ValueError, match="git checkout"):
        CeceSettings(root_dir=tmp_path).commit_sha()


def test_commit_sha_raises_for_repo_without_commits(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with pytest.raises(ValueError, match="rev-parse"):
        CeceSettings(root_dir=tmp_path).commit_sha()


def test_commit_sha_none_when_no_checkout_configured() -> None:
    # An explicit None root (init kwarg beats env) means "no checkout":
    # recorded as null, never an error.
    assert CeceSettings(root_dir=None).commit_sha() is None
