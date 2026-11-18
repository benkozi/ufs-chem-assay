"""Platform detection, runtime derivation, and the launcher setting."""

from pathlib import Path

import pytest
import yaml

from applications.registry import load_suite
from models.suite_config import RunManifest
from platforms import (
    ACCOUNTING,
    MODULE_INIT,
    Accounting,
    ModuleInit,
    Platform,
    Runtime,
    default_runtime,
    detect_platform,
    module_init,
)
from settings import Settings


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    for prefix in ("ASSAY_", "CECE_"):
        for key in ("PLATFORM", "RUNTIME", "LAUNCHER"):
            monkeypatch.delenv(f"{prefix}{key}", raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("ufe01", Platform.URSA),
        ("ufe03.rdhpcs.noaa.gov", Platform.URSA),
        # Gaea login nodes are gaea<cluster digit><node digit>: C6 is gaea61-68.
        ("gaea61", Platform.GAEA_C6),
        ("gaea68", Platform.GAEA_C6),
        ("gaea63.ncrc.gov", Platform.GAEA_C6),
        ("gaea71", Platform.GAEA_C7),  # the same scheme
        ("gaea75.ncrc.gov", Platform.GAEA_C7),  # seen on the first C7 login
        ("gaea78.ncrc.gov", Platform.GAEA_C7),
        ("gaea", Platform.LOCAL),  # no cluster named: not guessed
        ("gaea51", Platform.LOCAL),  # C5: a cluster the harness does not know
        ("gaea6", Platform.LOCAL),  # the cluster digit alone is not a node
        ("gaea61x", Platform.LOCAL),  # fullmatch: digits, then at most a domain
        ("gaeac6", Platform.LOCAL),  # not a hostname form Gaea uses
        ("xgaea61", Platform.LOCAL),
        ("my-laptop.local", Platform.LOCAL),
        ("", Platform.LOCAL),
    ],
)
def test_detect_platform_from_hostname(hostname: str, expected: Platform) -> None:
    assert detect_platform(hostname) == expected


@pytest.mark.usefixtures("clean_env")
def test_settings_default_platform_is_detected_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "laptop")
    settings = Settings()
    assert settings.platform is Platform.LOCAL
    assert settings.runtime is Runtime.DOCKER
    assert settings.launcher == ""


@pytest.mark.usefixtures("clean_env")
def test_settings_detects_ursa_from_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "ufe02")
    settings = Settings()
    assert settings.platform is Platform.URSA
    assert settings.runtime is Runtime.SLURM


def test_explicit_platform_beats_detection(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "ufe02")
    clean_env.setenv("ASSAY_PLATFORM", "local")
    assert Settings().platform is Platform.LOCAL
    assert Settings().runtime is Runtime.DOCKER


def test_runtime_derives_from_platform_but_env_wins(
    clean_env: pytest.MonkeyPatch,
) -> None:
    assert Settings(platform=Platform.URSA).runtime is Runtime.SLURM
    clean_env.setenv("ASSAY_RUNTIME", "docker")
    assert Settings(platform=Platform.URSA).runtime is Runtime.DOCKER


def test_launcher_is_split_with_shlex(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ASSAY_LAUNCHER", "srun --ntasks=1 --comment='a b'")
    assert Settings().launcher_argv == ["srun", "--ntasks=1", "--comment=a b"]
    assert Settings(launcher="").launcher_argv == []


def test_run_manifest_records_platform_and_runtime(
    suite_path: Path, tmp_path: Path
) -> None:
    manifest = RunManifest(
        run_id="01JZZZZZZZZZZZZZZZZZZZZZZZ",
        application="cece",
        application_commit=None,
        harness_version="0.1.0",
        harness_commit=None,
        platform=Platform.URSA,
        runtime=Runtime.SLURM,
        modulefile="cece_ursa.intelllvm",
        suites=[load_suite(suite_path)],
    )
    manifest.to_yaml(tmp_path / "run.yaml")
    recorded = yaml.safe_load((tmp_path / "run.yaml").read_text())
    assert recorded["platform"] == "ursa"
    assert recorded["runtime"] == "slurm"
    assert recorded["modulefile"] == "cece_ursa.intelllvm"


@pytest.mark.usefixtures("clean_env")
def test_explicit_local_platform_beats_an_ursa_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first Ursa run: tests that name no platform picked up the login
    node's hostname. An explicit platform must win regardless of where the
    suite runs, and the docker command shape must follow it."""
    from pathlib import PurePosixPath

    from applications.cece.settings import CeceSettings
    from applications.registry import get_application
    from runner import build_command

    monkeypatch.setattr("platforms.socket.gethostname", lambda: "ufe01")
    settings = Settings(platform=Platform.LOCAL)
    assert settings.runtime is Runtime.DOCKER
    command = build_command(
        settings,
        get_application("cece"),
        CeceSettings(root_dir=Path("/host/cece")),
        PurePosixPath("/work/x.yaml"),
    )
    assert command[:3] == ["docker", "run", "--rm"]


def test_accounting_is_known_per_platform() -> None:
    # Memory and queue numbers are recorded only where the harness knows how
    # to ask: Ursa and Gaea C7 through sacct; a laptop records nothing of the
    # kind.
    assert ACCOUNTING[Platform.URSA] is Accounting.SACCT
    assert ACCOUNTING[Platform.GAEA_C7] is Accounting.SACCT
    assert Platform.LOCAL not in ACCOUNTING
    assert set(ACCOUNTING) <= set(Platform)


def test_gaea_platforms_are_known_and_run_under_slurm() -> None:
    assert Platform("gaea-c6") is Platform.GAEA_C6
    assert Platform("gaea-c7") is Platform.GAEA_C7
    for platform in (Platform.GAEA_C6, Platform.GAEA_C7):
        assert default_runtime(platform) is Runtime.SLURM
        assert Settings(platform=platform).runtime is Runtime.SLURM
    # Accounting joins once sacct is confirmed on the machine: C7's answered
    # every column for the first test job there; until C6's has, a run there
    # records the harness's own clock.
    assert Platform.GAEA_C7 in ACCOUNTING
    assert Platform.GAEA_C6 not in ACCOUNTING


def test_module_init_is_a_platform_fact() -> None:
    # `module purge` strips Cray's craype/PrgEnv machinery on Gaea: reset
    # there, purge everywhere else (a platform without an entry purges).
    assert MODULE_INIT == {
        Platform.URSA: ModuleInit.PURGE,
        Platform.GAEA_C6: ModuleInit.RESET,
        Platform.GAEA_C7: ModuleInit.RESET,
    }
    assert module_init(Platform.URSA) is ModuleInit.PURGE
    assert module_init(Platform.GAEA_C7) is ModuleInit.RESET
    assert module_init(Platform.LOCAL) is ModuleInit.PURGE
    assert [init.value for init in ModuleInit] == ["purge", "reset"]


@pytest.mark.usefixtures("clean_env")
def test_settings_detects_gaea_from_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "gaea71")
    settings = Settings()
    assert settings.platform is Platform.GAEA_C7
    assert settings.runtime is Runtime.SLURM
