"""root_dir/.env/search-path resolution and immutability on Settings. Env-var
tests scrub the ambient CECE_* variables via monkeypatch so a developer's
shell cannot influence assertions, and chdir to tmp_path so the repo-root
.env file is out of scope (each test opts in by writing its own)."""

import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from settings import Settings


@pytest.fixture()
def clean_cece_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> pytest.MonkeyPatch:
    for key in ("CECE_ROOT_DIR", "CECE_ROOT", "CECE_SUITE_CONFIG_SEARCH_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


def test_root_dir_defaults_to_none(clean_cece_env: pytest.MonkeyPatch) -> None:
    assert Settings().root_dir is None


def test_root_dir_reads_cece_root_dir_env(clean_cece_env: pytest.MonkeyPatch) -> None:
    clean_cece_env.setenv("CECE_ROOT_DIR", "/host/cece")
    assert Settings().root_dir == Path("/host/cece")


def test_init_kwarg_beats_env(clean_cece_env: pytest.MonkeyPatch) -> None:
    # The --cece-root-dir flag wiring relies on this precedence.
    clean_cece_env.setenv("CECE_ROOT_DIR", "/from/env")
    assert Settings(root_dir=Path("/from/flag")).root_dir == Path("/from/flag")


def test_legacy_cece_root_env_has_no_effect(
    clean_cece_env: pytest.MonkeyPatch,
) -> None:
    clean_cece_env.setenv("CECE_ROOT", "/host/cece")
    assert Settings().root_dir is None


def test_settings_is_frozen(clean_cece_env: pytest.MonkeyPatch) -> None:
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.root_dir = Path("/host/cece")  # type: ignore[misc]


def test_search_path_splits_on_pathsep(clean_cece_env: pytest.MonkeyPatch) -> None:
    clean_cece_env.setenv(
        "CECE_SUITE_CONFIG_SEARCH_PATH", os.pathsep.join(["/suites/a", "/suites/b"])
    )
    assert Settings().suite_config_search_path == [
        Path("/suites/a"),
        Path("/suites/b"),
    ]


def test_search_path_single_directory(clean_cece_env: pytest.MonkeyPatch) -> None:
    clean_cece_env.setenv("CECE_SUITE_CONFIG_SEARCH_PATH", "/suites/a")
    assert Settings().suite_config_search_path == [Path("/suites/a")]


def test_search_path_defaults_to_empty(clean_cece_env: pytest.MonkeyPatch) -> None:
    assert Settings().suite_config_search_path == []


def test_env_file_supplies_values(
    clean_cece_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "cece_root_dir=/from/dotenv\ncece_baseline_root_dir=/baselines\n"
    )
    settings = Settings()
    assert settings.root_dir == Path("/from/dotenv")
    assert settings.baseline_root_dir == Path("/baselines")


def test_real_env_beats_env_file(
    clean_cece_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("cece_root_dir=/from/dotenv\n")
    clean_cece_env.setenv("CECE_ROOT_DIR", "/from/env")
    assert Settings().root_dir == Path("/from/env")


def test_init_kwarg_beats_env_file(
    clean_cece_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("cece_root_dir=/from/dotenv\n")
    assert Settings(root_dir=Path("/from/flag")).root_dir == Path("/from/flag")


# -- cece_commit_sha: the checkout's HEAD SHA for run.yaml -------------------


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


def test_cece_commit_sha_of_git_checkout(tmp_path: Path) -> None:
    expected = _git_repo_with_commit(tmp_path)
    assert Settings(root_dir=tmp_path).get_cece_commit_sha() == expected
    assert len(expected) == 40


def test_cece_commit_sha_raises_for_non_repo(tmp_path: Path) -> None:
    # Sessions always run against a checked-out CECE: a configured root
    # without a resolvable SHA is fatal, not a recordable state.
    with pytest.raises(ValueError, match="git checkout"):
        Settings(root_dir=tmp_path).get_cece_commit_sha()


def test_cece_commit_sha_raises_for_repo_without_commits(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with pytest.raises(ValueError, match="rev-parse"):
        Settings(root_dir=tmp_path).get_cece_commit_sha()


def test_cece_commit_sha_none_when_no_checkout_configured() -> None:
    # An explicit None root (init kwarg beats env) means "no checkout":
    # recorded as null, never an error.
    assert Settings(root_dir=None).get_cece_commit_sha() is None
