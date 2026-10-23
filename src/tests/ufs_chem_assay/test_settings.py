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
    "BASELINE_STORE",
    "LOG_LEVEL",
    "SRUN_ARGS",
    "INPUT_SEARCH_PATH",
    "OUTPUT_STORE",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    for prefix in (ENV_PREFIX, LEGACY_ENV_PREFIX):
        for key in _SCRUBBED:
            monkeypatch.delenv(f"{prefix}{key}", raising=False)
    monkeypatch.delenv("CECE_ROOT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


def test_prefixes() -> None:
    assert ENV_PREFIX == "ASSAY_"
    assert LEGACY_ENV_PREFIX == "CECE_"


def test_no_application_settings_on_the_harness_model(
    clean_env: pytest.MonkeyPatch,
) -> None:
    # root_dir / docker_image / driver_path / modulefile are the adapters'.
    for name in ("root_dir", "docker_image", "driver_path", "modulefile"):
        assert name not in Settings.model_fields
    clean_env.setenv("CECE_ROOT_DIR", "/host/cece")
    assert not hasattr(Settings(), "root_dir")


@pytest.mark.usefixtures("clean_env")
def test_application_defaults_to_none() -> None:
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


@pytest.mark.usefixtures("clean_env")
def test_env_file_supplies_values_under_either_prefix(tmp_path: Path) -> None:
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


@pytest.mark.usefixtures("clean_env")
def test_init_kwarg_beats_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("assay_platform=ursa\n")
    assert Settings(platform=Platform.LOCAL).platform is Platform.LOCAL


@pytest.mark.usefixtures("clean_env")
def test_settings_is_frozen() -> None:
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


@pytest.mark.usefixtures("clean_env")
def test_search_path_defaults_to_empty() -> None:
    assert Settings().suite_config_search_path == []


def test_input_search_path_defaults_empty_and_splits_on_pathsep(
    clean_env: pytest.MonkeyPatch,
) -> None:
    # Where fetch looks for source-less inputs to link from: per-machine
    # directories, never in a suite.
    assert Settings().input_search_path == []
    clean_env.setenv(
        f"{ENV_PREFIX}INPUT_SEARCH_PATH", os.pathsep.join(["/data/a", "/data/b"])
    )
    assert Settings().input_search_path == [Path("/data/a"), Path("/data/b")]


def test_runtime_from_legacy_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CECE_RUNTIME", "native")
    assert Settings(platform=Platform.LOCAL).runtime is Runtime.NATIVE


# ── baseline tolerance overrides ──────────────────────────────────────────────


def test_baseline_tolerance_overrides_default_to_none_and_read_from_env(
    clean_env: pytest.MonkeyPatch,
) -> None:
    assert (Settings().baseline_atol, Settings().baseline_rtol) == (None, None)
    clean_env.setenv(f"{ENV_PREFIX}BASELINE_ATOL", "1e-24")
    clean_env.setenv(f"{ENV_PREFIX}BASELINE_RTOL", "1e-12")
    assert (Settings().baseline_atol, Settings().baseline_rtol) == (1e-24, 1e-12)
    assert (
        Settings(baseline_rtol=0.0).baseline_rtol == 0.0
    )  # 0 = bit-for-bit, not unset


def test_baseline_tolerance_overrides_reject_negatives() -> None:
    with pytest.raises(ValidationError, match="baseline_atol"):
        Settings(baseline_atol=-1e-15)
    with pytest.raises(ValidationError, match="baseline_rtol"):
        Settings(baseline_rtol=-1e-15)


# ── baseline_store ────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("clean_env")
def test_output_store_defaults_and_shares_the_store_validator(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert Settings().output_store == "s3://ufs-chem/assay-output"
    clean_env.setenv(f"{ENV_PREFIX}OUTPUT_STORE", "s3://other/outputs/")
    assert Settings().output_store == "s3://other/outputs"
    clean_env.setenv(f"{ENV_PREFIX}OUTPUT_STORE", str(tmp_path / "outputs"))
    assert Settings().output_store == str(tmp_path / "outputs")
    clean_env.setenv(f"{ENV_PREFIX}OUTPUT_STORE", "s3://bucket-root")
    with pytest.raises(ValidationError, match="output_store"):
        Settings()


def test_srun_args_defaults_to_one_task_and_reads_from_env(
    clean_env: pytest.MonkeyPatch,
) -> None:
    assert Settings(platform=Platform.LOCAL).srun_args == "--ntasks=1"
    clean_env.setenv(f"{ENV_PREFIX}SRUN_ARGS", "--ntasks=4 --cpu-bind=cores")
    assert Settings(platform=Platform.LOCAL).srun_args == "--ntasks=4 --cpu-bind=cores"


def test_baseline_store_defaults_to_the_ufs_chem_baselines_prefix() -> None:
    assert Settings().baseline_store == "s3://ufs-chem/baselines"


def test_baseline_store_from_env_trailing_slash_stripped(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("ASSAY_BASELINE_STORE", "s3://other-bucket/some/prefix/")
    assert Settings().baseline_store == "s3://other-bucket/some/prefix"


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(
    "value", ["s3://ufs-chem", "s3://ufs-chem/", "https://ufs-chem/baselines", ""]
)
def test_baseline_store_rejects_bucket_roots_other_schemes_and_empty(
    value: str,
) -> None:
    with pytest.raises(ValidationError, match="baseline_store"):
        Settings(baseline_store=value)


def test_baseline_store_accepts_a_directory(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A local store of record (a Dropbox folder, a scratch directory): the
    # path is kept absolute; it need not exist until publication.
    clean_env.setenv("ASSAY_BASELINE_STORE", str(tmp_path / "store") + "/")
    assert Settings().baseline_store == str(tmp_path / "store")
    assert Settings(baseline_store="relative/store").baseline_store == str(
        Path("relative/store").resolve()
    )
    assert Settings(baseline_store="~/baselines").baseline_store == str(
        Path("~/baselines").expanduser()
    )
