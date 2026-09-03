"""RunConfig: the one YAML file `ufs-chem-assay run` assembles a run from."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from cli.run_config import RunConfig
from platforms import Platform, Runtime
from tests.ufs_chem_assay.run_configs import REMOVE, TEMPLATES_DIR, run_config_file


def test_ursa_template_loads() -> None:
    config = RunConfig.from_yaml(TEMPLATES_DIR / "ursa.yaml")
    assert config.platform is Platform.URSA
    assert config.runtime is Runtime.NATIVE
    assert config.cece.modulefile == "cece_ursa.intelllvm"
    assert config.clone_dir == config.root_dir / "CECE"
    assert config.slurm is not None and config.slurm.account == "epic"
    assert config.harness.launcher == "srun --ntasks=1"
    assert config.uv_cache_dir == config.root_dir / "uv-cache"


def test_local_template_loads_and_runs_directly() -> None:
    config = RunConfig.from_yaml(TEMPLATES_DIR / "local.yaml")
    assert config.platform is Platform.LOCAL
    assert config.runtime is Runtime.DOCKER
    assert config.slurm is None
    assert config.cece.modulefile is None
    assert config.root_dir.is_absolute()  # ~ expanded


def test_unknown_key_rejected(tmp_path: Path) -> None:
    path = run_config_file(tmp_path, overrides={"harness.suite": "typo"})
    with pytest.raises(ValidationError, match="suite"):
        RunConfig.from_yaml(path)


def test_platform_override_beats_file() -> None:
    config = RunConfig.from_yaml(TEMPLATES_DIR / "ursa.yaml", platform=Platform.LOCAL)
    assert config.platform is Platform.LOCAL
    assert config.runtime is Runtime.DOCKER


def test_platform_detected_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = run_config_file(tmp_path, overrides={"platform": REMOVE})
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "ufe04")
    assert RunConfig.from_yaml(path).platform is Platform.URSA
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "laptop")
    assert RunConfig.from_yaml(path).platform is Platform.LOCAL


def test_platform_is_required_on_direct_validation() -> None:
    # Only from_yaml resolves the platform; the model itself never guesses.
    with pytest.raises(ValidationError, match="platform"):
        RunConfig.model_validate(
            {"root_dir": "/r", "cece": {"git_url": "u", "ref": "r"}}
        )


def test_harness_env_numbers_become_strings(tmp_path: Path) -> None:
    path = run_config_file(
        tmp_path,
        overrides={"harness.env": {"OMP_NUM_THREADS": 8, "FI_PROVIDER": "tcp"}},
    )
    assert RunConfig.from_yaml(path).harness.env == {
        "OMP_NUM_THREADS": "8",
        "FI_PROVIDER": "tcp",
    }


def test_explicit_clone_dir_is_used(tmp_path: Path) -> None:
    path = run_config_file(tmp_path, overrides={"cece.clone_dir": "/elsewhere/CECE"})
    assert RunConfig.from_yaml(path).clone_dir == Path("/elsewhere/CECE")


def test_run_tests_requires_all_target(tmp_path: Path) -> None:
    path = run_config_file(tmp_path, overrides={"cece.run_tests": True})
    with pytest.raises(ValidationError, match="all"):
        RunConfig.from_yaml(path)


def test_slurm_account_defaults_to_epic(tmp_path: Path) -> None:
    path = run_config_file(tmp_path, overrides={"slurm.account": REMOVE})
    config = RunConfig.from_yaml(path)
    assert config.slurm is not None and config.slurm.account == "epic"


def test_uv_python_is_not_a_knob(tmp_path: Path) -> None:
    # UV_PYTHON only matters at the user's first `uv sync`; the CLI never
    # syncs, so the run config does not carry it.
    path = run_config_file(tmp_path, overrides={"uv.python": "3.13"})
    with pytest.raises(ValidationError, match="python"):
        RunConfig.from_yaml(path)
