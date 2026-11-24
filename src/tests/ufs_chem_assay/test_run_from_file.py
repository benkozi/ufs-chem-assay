"""`run` takes its selection from the run config file — a flag replacing only
its own key — so the ops bootstrap passes one argument, the file; and every
`run` writes its transcript, <root>/logs/assay-run-<stamp>.log, holding the
harness's lines from the start facts on."""

import logging
import re
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from cli.main import main
from logs import LOGGER_NAME
from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

_TRANSCRIPT = re.compile(r"assay-run-\d{8}-\d{6}\.log")


def _daily(tmp_path: Path, **keys: object) -> Path:
    """A personal file over the C7 template, its root under tmp_path."""
    data: dict[str, object] = {
        "extends": str(TEMPLATES_DIR / "gaea-c7.yaml"),
        "root_dir": str(tmp_path / "root"),
        **keys,
    }
    path = tmp_path / "c7.local.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _run(mocker: MockerFixture, config: Path, *args: str) -> int:
    mocker.patch("cli.commands.run.run_bash", return_value=0)
    return main(["run", f"--config-file={config}", *args])


def _effective(tmp_path: Path) -> dict[str, object]:
    path = tmp_path / "root" / "scripts" / "run-config.yaml"
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


def _scripts(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "root" / "scripts").glob("*.sh"))


def _transcripts(tmp_path: Path) -> list[Path]:
    logs = tmp_path / "root" / "logs"
    return sorted(p for p in logs.iterdir() if _TRANSCRIPT.fullmatch(p.name))


def test_the_files_selection_is_the_run(tmp_path: Path, mocker: MockerFixture) -> None:
    config = _daily(
        tmp_path,
        selected_applications=["catchem"],
        selected_stages=["source", "build"],
    )
    assert _run(mocker, config, "--dry-run") == 0
    effective = _effective(tmp_path)
    assert effective["selected_applications"] == ["catchem"]
    assert effective["selected_stages"] == ["source", "build"]
    assert "extends" not in effective
    scripts = _scripts(tmp_path)
    assert scripts
    assert all("catchem" in name for name in scripts)
    assert not any("-test-" in name for name in scripts)


def test_no_selection_anywhere_is_every_application_and_stage_but_resume(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    assert _run(mocker, _daily(tmp_path), "--dry-run") == 0
    effective = _effective(tmp_path)
    assert effective["selected_applications"] == ["cece", "catchem"]
    assert "resume" not in effective["selected_stages"]  # type: ignore[operator]
    assert "test" in effective["selected_stages"]  # type: ignore[operator]


def test_a_flag_replaces_only_its_own_key(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    config = _daily(
        tmp_path,
        selected_applications=["catchem"],
        selected_stages=["source", "build", "test"],
    )
    assert _run(mocker, config, "--dry-run", "--stage=build") == 0
    effective = _effective(tmp_path)
    assert effective["selected_applications"] == ["catchem"]
    assert effective["selected_stages"] == ["build"]

    assert _run(mocker, config, "--dry-run", "--application=cece") == 0
    effective = _effective(tmp_path)
    assert effective["selected_applications"] == ["cece"]
    assert effective["selected_stages"] == ["source", "build", "test"]


def test_the_effective_config_reruns_the_same_run(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    config = _daily(
        tmp_path,
        transfer_dir=str(tmp_path / "transfer"),
        selected_applications=["catchem"],
        selected_builds=["musica"],
        selected_stages=["build", "test"],
    )
    assert _run(mocker, config, "--dry-run") == 0
    first = _effective(tmp_path)
    scripts = _scripts(tmp_path)
    effective = tmp_path / "rerun.yaml"
    effective.write_text(yaml.safe_dump(first, sort_keys=False))
    assert _run(mocker, effective, "--dry-run") == 0
    assert _effective(tmp_path) == first
    assert _scripts(tmp_path) == scripts
    assert first["transfer_dir"] == str(tmp_path / "transfer")


def test_every_run_writes_its_transcript(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/lib")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    namespace = logging.getLogger(LOGGER_NAME)
    handlers = list(namespace.handlers)
    config = _daily(tmp_path, selected_stages=["build"])
    assert _run(mocker, config) == 0
    (transcript,) = _transcripts(tmp_path)
    text = transcript.read_text()
    lines = text.splitlines()
    assert all("[run]" in line for line in lines)
    assert lines[0].split("[run]: ")[1].startswith("assay-run on ")
    assert " harness " in lines[0]
    assert f"config {config.resolve()}" in text
    assert f"extends {(TEMPLATES_DIR / 'gaea-c7.yaml').resolve()}" in text
    assert "LD_LIBRARY_PATH=[/opt/lib]" in text
    assert "PYTHONPATH=[]" in text
    assert "run-config.yaml" in text  # `wrote <root>/scripts/run-config.yaml`
    assert "oneapi: ok" in text  # the stages' outcome
    # The handler is the run's alone: removed when run returns.
    assert namespace.handlers == handlers


def test_a_dry_run_writes_a_transcript_too(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    assert _run(mocker, _daily(tmp_path), "--dry-run") == 0
    (transcript,) = _transcripts(tmp_path)
    assert "dry run: nothing executed" in transcript.read_text()


def test_a_config_that_does_not_load_writes_no_transcript(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    config = _daily(tmp_path, selected_applications=["nope"])
    assert _run(mocker, config, "--dry-run") == 1
    assert not (tmp_path / "root" / "logs").exists()
