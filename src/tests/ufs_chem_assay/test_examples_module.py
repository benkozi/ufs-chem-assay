"""examples.py unit tests: discovery, the download/run entrypoint
invocations (mocked subprocess), the report writer, and model strictness.
No docker, no network."""

import logging
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest_mock import MockerFixture

from examples import (
    DOWNLOAD_ENTRYPOINT,
    EXAMPLES_SUBDIR,
    RUN_ENTRYPOINT,
    DownloadResult,
    ExampleRunResult,
    discover_examples,
    download_example_data,
    example_id,
    run_example_command,
    write_examples_report,
)
from logs import LOGGER_NAME
from platforms import Platform
from settings import Settings


@pytest.fixture()
def cece_root(tmp_path: Path) -> Path:
    (tmp_path / EXAMPLES_SUBDIR).mkdir(parents=True)
    return tmp_path


def test_examples_live_under_config_dir() -> None:
    # The unified layout (design/feat/20260722-1553): configs under
    # examples/config/, entrypoints beside them.
    assert EXAMPLES_SUBDIR == Path("examples") / "config"
    assert DOWNLOAD_ENTRYPOINT == Path("examples") / "download-example-data.py"
    assert RUN_ENTRYPOINT == Path("examples") / "run-example.py"


def test_discover_examples_sorted(cece_root: Path) -> None:
    examples = cece_root / EXAMPLES_SUBDIR
    (examples / "cece_config_ex2.yaml").touch()
    (examples / "cece_config_ex1.yaml").touch()
    (examples / "unrelated.yaml").touch()
    assert discover_examples(cece_root) == [
        examples / "cece_config_ex1.yaml",
        examples / "cece_config_ex2.yaml",
    ]


def test_discover_examples_empty(cece_root: Path) -> None:
    assert discover_examples(cece_root) == []


def test_example_id_strips_config_prefix() -> None:
    assert example_id(Path("examples/config/cece_config_ex3.yaml")) == "ex3"


def test_download_invokes_entrypoint_per_example(
    cece_root: Path, mocker: MockerFixture
) -> None:
    examples = cece_root / EXAMPLES_SUBDIR
    (examples / "cece_config_ex2.yaml").touch()
    (examples / "cece_config_ex1.yaml").touch()
    run = mocker.patch(
        "examples.subprocess.run",
        return_value=subprocess.CompletedProcess([], returncode=0, stdout="fetched\n"),
    )

    results = download_example_data(cece_root)

    assert [result.script for result in results] == ["ex1", "ex2"]
    assert all(result.returncode == 0 for result in results)
    first = run.call_args_list[0]
    assert first.args[0] == [
        sys.executable,
        str(cece_root / DOWNLOAD_ENTRYPOINT),
        "--example",
        "ex1",
        "--dst-dir",
        str(cece_root / "data"),
    ]
    assert first.kwargs["cwd"] == cece_root


def test_download_failure_logged_and_does_not_abort(
    cece_root: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    examples = cece_root / EXAMPLES_SUBDIR
    (examples / "cece_config_ex1.yaml").touch()
    (examples / "cece_config_ex2.yaml").touch()
    mocker.patch(
        "examples.subprocess.run",
        side_effect=[
            subprocess.CompletedProcess([], returncode=1, stdout="404\n"),
            subprocess.CompletedProcess([], returncode=0, stdout="fetched\n"),
        ],
    )

    with caplog.at_level(logging.WARNING, logger=f"{LOGGER_NAME}.examples"):
        results = download_example_data(cece_root)

    assert [result.returncode for result in results] == [1, 0]
    assert results[0].output == "404\n"
    assert any("ex1" in record.message for record in caplog.records)


def test_download_timeout_recorded_not_raised(
    cece_root: Path, mocker: MockerFixture
) -> None:
    (cece_root / EXAMPLES_SUBDIR / "cece_config_ex1.yaml").touch()
    mocker.patch(
        "examples.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=1, output=b"partial"),
    )

    (result,) = download_example_data(cece_root, timeout_s=1)

    assert result.returncode == -1
    assert "partial" in result.output


def test_run_example_command_wraps_entrypoint_in_docker() -> None:
    settings = Settings(
        platform=Platform.LOCAL, root_dir=Path("/host/cece"), docker_image="img:tag"
    )
    assert run_example_command(settings, "ex3") == [
        "docker",
        "run",
        "--rm",
        "-v",
        "/host/cece:/work",
        "-w",
        "/work",
        "-e",
        "OMPI_ALLOW_RUN_AS_ROOT=1",
        "-e",
        "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1",
        "img:tag",
        "python3",
        "examples/run-example.py",
        "--example",
        "ex3",
    ]


def test_write_examples_report(tmp_path: Path) -> None:
    downloads = [
        DownloadResult(script="ex1", returncode=0, output="fetched\n"),
        DownloadResult(script="ex2", returncode=1, output="404 not found\n"),
    ]
    runs = [
        ExampleRunResult(
            example="cece_config_ex1", returncode=0, out_path=tmp_path / "ex1.out"
        ),
        ExampleRunResult(
            example="cece_config_ex2", returncode=2, out_path=tmp_path / "ex2.out"
        ),
    ]
    report_path = tmp_path / "examples-report.md"

    write_examples_report(downloads, runs, report_path)

    text = report_path.read_text()
    assert "ex1" in text and "ok" in text
    assert "ex2" in text and "404 not found" in text
    assert "cece_config_ex1" in text and "PASS" in text
    assert "cece_config_ex2" in text and "FAIL (exit 2)" in text
    assert str(tmp_path / "ex2.out") in text


def test_write_examples_report_empty(tmp_path: Path) -> None:
    report_path = tmp_path / "examples-report.md"
    write_examples_report([], [], report_path)
    assert report_path.is_file()


def test_models_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        DownloadResult(script="ex1", returncode=0, output="", surprise=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ExampleRunResult(
            example="x",
            returncode=0,
            out_path=Path("x.out"),
            surprise=1,  # type: ignore[call-arg]
        )
