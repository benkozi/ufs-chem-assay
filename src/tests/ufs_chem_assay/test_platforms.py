"""Platform detection, runtime derivation, and the launcher setting."""

from pathlib import Path

import pytest
import yaml

from models.suite_config import RunManifest, SuiteConfig
from platforms import Platform, Runtime, detect_platform
from settings import Settings


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    for key in ("CECE_PLATFORM", "CECE_RUNTIME", "CECE_LAUNCHER", "CECE_ROOT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("ufe01", Platform.URSA),
        ("ufe03.rdhpcs.noaa.gov", Platform.URSA),
        ("my-laptop.local", Platform.LOCAL),
        ("", Platform.LOCAL),
    ],
)
def test_detect_platform_from_hostname(hostname: str, expected: Platform) -> None:
    assert detect_platform(hostname) == expected


def test_settings_default_platform_is_detected_local(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "laptop")
    settings = Settings()
    assert settings.platform is Platform.LOCAL
    assert settings.runtime is Runtime.DOCKER
    assert settings.launcher == ""


def test_settings_detects_ursa_from_hostname(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "ufe02")
    settings = Settings()
    assert settings.platform is Platform.URSA
    assert settings.runtime is Runtime.NATIVE


def test_explicit_platform_beats_detection(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "ufe02")
    clean_env.setenv("CECE_PLATFORM", "local")
    assert Settings().platform is Platform.LOCAL
    assert Settings().runtime is Runtime.DOCKER


def test_runtime_derives_from_platform_but_env_wins(
    clean_env: pytest.MonkeyPatch,
) -> None:
    assert Settings(platform=Platform.URSA).runtime is Runtime.NATIVE
    clean_env.setenv("CECE_RUNTIME", "docker")
    assert Settings(platform=Platform.URSA).runtime is Runtime.DOCKER


def test_launcher_is_split_with_shlex(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CECE_LAUNCHER", "srun --ntasks=1 --comment='a b'")
    assert Settings().launcher_argv == ["srun", "--ntasks=1", "--comment=a b"]
    assert Settings(launcher="").launcher_argv == []


def test_run_manifest_records_platform_and_runtime(
    suite_path: Path, tmp_path: Path
) -> None:
    manifest = RunManifest(
        run_id="01JZZZZZZZZZZZZZZZZZZZZZZZ",
        cece_commit=None,
        platform=Platform.URSA,
        runtime=Runtime.NATIVE,
        suites=[SuiteConfig.from_yaml(suite_path)],
    )
    manifest.to_yaml(tmp_path / "run.yaml")
    recorded = yaml.safe_load((tmp_path / "run.yaml").read_text())
    assert recorded["platform"] == "ursa"
    assert recorded["runtime"] == "native"


def test_explicit_local_platform_beats_an_ursa_hostname(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first Ursa run: tests that name no platform picked up the login
    node's hostname. An explicit platform must win regardless of where the
    suite runs, and the docker command shape must follow it."""
    from pathlib import PurePosixPath

    from runner import build_command

    monkeypatch.setattr("platforms.socket.gethostname", lambda: "ufe01")
    settings = Settings(platform=Platform.LOCAL, root_dir=Path("/host/cece"))
    assert settings.runtime is Runtime.DOCKER
    command = build_command(settings, PurePosixPath("/work/x.yaml"))
    assert command[:3] == ["docker", "run", "--rm"]
