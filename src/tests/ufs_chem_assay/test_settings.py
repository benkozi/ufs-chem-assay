"""Harness-wide Settings: the ASSAY_* namespace with CECE_* fallbacks, .env,
precedence, and immutability. Env-var tests scrub the ambient variables via
monkeypatch so a developer's shell cannot influence assertions, and chdir to
tmp_path so the repo-root .env file is out of scope (each test opts in by
writing its own)."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from platforms import Platform, Runtime
from settings import ENV_PREFIX, LEGACY_ENV_PREFIX, Settings

_SCRUBBED = (
    "APPLICATION",
    "PLATFORM",
    "RUNTIME",
    "SUITE_CONFIG_SEARCH_PATH",
    "DASK_NWORKERS",
    "BASELINE_ROOT_DIR",
    "LOG_LEVEL",
)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    for prefix in (ENV_PREFIX, LEGACY_ENV_PREFIX):
        for key in _SCRUBBED:
            monkeypatch.delenv(f"{prefix}{key}", raising=False)
    monkeypatch.delenv("CECE_ROOT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


def test_prefixes() -> None:
    assert ENV_PREFIX == "ASSAY_" and LEGACY_ENV_PREFIX == "CECE_"


def test_no_application_settings_on_the_harness_model(
    clean_env: pytest.MonkeyPatch,
) -> None:
    # root_dir / docker_image / driver_path / modulefile are the adapters'.
    for name in ("root_dir", "docker_image", "driver_path", "modulefile"):
        assert name not in Settings.model_fields
    clean_env.setenv("CECE_ROOT_DIR", "/host/cece")
    assert not hasattr(Settings(), "root_dir")


def test_application_defaults_to_none(clean_env: pytest.MonkeyPatch) -> None:
    assert Settings().application is None


def test_application_from_env_and_kwarg(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ASSAY_APPLICATION", "cece")
    assert Settings().application == "cece"
    # The --application flag wiring relies on init kwargs beating env.
    assert Settings(application="other").application == "other"


def test_assay_prefix_reads(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ASSAY_DASK_NWORKERS", "3")
    assert Settings().dask_nworkers == 3


def test_legacy_prefix_is_a_fallback(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CECE_DASK_NWORKERS", "3")
    assert Settings().dask_nworkers == 3


def test_assay_wins_over_legacy_within_the_environment(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("CECE_DASK_NWORKERS", "3")
    clean_env.setenv("ASSAY_DASK_NWORKERS", "5")
    assert Settings().dask_nworkers == 5


def test_env_file_supplies_values_under_either_prefix(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "cece_root_dir=/from/dotenv\n"  # the adapter's key: ignored here
        "cece_baseline_root_dir=/baselines\n"
        "assay_dask_nworkers=4\n"
    )
    settings = Settings()
    assert settings.baseline_root_dir == Path("/baselines")
    assert settings.dask_nworkers == 4


def test_real_env_beats_env_file_across_prefixes(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # environment > .env holds even when the .env uses the new spelling and
    # the environment the legacy one.
    (tmp_path / ".env").write_text("assay_dask_nworkers=4\n")
    clean_env.setenv("CECE_DASK_NWORKERS", "9")
    assert Settings().dask_nworkers == 9


def test_init_kwarg_beats_env_file(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("assay_platform=ursa\n")
    assert Settings(platform=Platform.LOCAL).platform is Platform.LOCAL


def test_settings_is_frozen(clean_env: pytest.MonkeyPatch) -> None:
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]


def test_search_path_splits_on_pathsep(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv(
        "ASSAY_SUITE_CONFIG_SEARCH_PATH", os.pathsep.join(["/suites/a", "/suites/b"])
    )
    assert Settings().suite_config_search_path == [
        Path("/suites/a"),
        Path("/suites/b"),
    ]


def test_search_path_single_directory_legacy_spelling(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("CECE_SUITE_CONFIG_SEARCH_PATH", "/suites/a")
    assert Settings().suite_config_search_path == [Path("/suites/a")]


def test_search_path_defaults_to_empty(clean_env: pytest.MonkeyPatch) -> None:
    assert Settings().suite_config_search_path == []


def test_runtime_from_legacy_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CECE_RUNTIME", "native")
    assert Settings(platform=Platform.LOCAL).runtime is Runtime.NATIVE
