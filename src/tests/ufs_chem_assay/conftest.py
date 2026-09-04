"""Harness-test fixtures: paths to the checked-in maccity configs, and the
machine-independence guard every test runs under."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def hermetic_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every harness test starts from platform local / runtime docker
    wherever the suite runs: the hostname is pinned (the first Ursa run
    resolved `ursa` from the login node's name and turned docker-shaped
    assertions native) and the platform variables are scrubbed, like the
    settings tests scrub CECE_ROOT_DIR. A test that wants another hostname
    patches it itself — a test-level setattr overrides this one."""
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "localhost")
    for key in ("CECE_PLATFORM", "CECE_RUNTIME", "CECE_LAUNCHER"):
        monkeypatch.delenv(key, raising=False)


_CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config"

# Expected timestep count for simple-maccity.yaml (3 h at 3600 s,
# frequency_steps 1). Deliberately a literal, not derived with the same
# arithmetic the code under test uses — otherwise the timestep-count
# calculator would be tested against itself. Change here when the config's
# duration changes.
MACCITY_N_TIMESTEPS = 3


@pytest.fixture()
def cece_config_path() -> Path:
    return _CONFIG_ROOT / "cece" / "simple-maccity.yaml"


@pytest.fixture()
def suite_path() -> Path:
    return _CONFIG_ROOT / "suite" / "simple-maccity-suite.yaml"


@pytest.fixture()
def suite_dir() -> Path:
    return _CONFIG_ROOT / "suite"


@pytest.fixture()
def exhaustive_suite_path() -> Path:
    return _CONFIG_ROOT / "suite" / "exhaustive-maccity-run-only-suite.yaml"


@pytest.fixture()
def maccity_n_timesteps() -> int:
    return MACCITY_N_TIMESTEPS


@pytest.fixture()
def maccity_expected_filenames(maccity_n_timesteps: int) -> set[str]:
    """Correct output names: hours 1..N (first write at the end of the first
    timestep, not t=0)."""
    return {
        f"cece_20100101_{hour:02d}0000.nc" for hour in range(1, maccity_n_timesteps + 1)
    }
