import subprocess
from pathlib import Path, PurePosixPath

import pytest
from pytest_mock import MockerFixture

from runner import build_command, run_driver
from settings import Settings


def _settings() -> Settings:
    # Explicit values so ambient CECE_* env vars cannot influence assertions.
    return Settings(
        root_dir=Path("/host/cece"),
        docker_image="img:tag",
        driver_path="./build/cece_standalone_driver",
    )


def test_build_command_work_mount_and_yaml_argument() -> None:
    command = build_command(_settings(), PurePosixPath("/work/combo_runs/x/x.yaml"))
    assert command == [
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
        "./build/cece_standalone_driver",
        "/work/combo_runs/x/x.yaml",
    ]


def test_build_command_adds_output_mount() -> None:
    command = build_command(
        _settings(),
        PurePosixPath("/combo_runs/x/x.yaml"),
        output_mount=(Path("/tmp/out"), PurePosixPath("/combo_runs")),
    )
    work_mount = command.index("/host/cece:/work")
    assert command[work_mount + 1 : work_mount + 3] == ["-v", "/tmp/out:/combo_runs"]


def test_run_driver_success_writes_out(mocker: MockerFixture, tmp_path: Path) -> None:
    check_output = mocker.patch(
        "runner.subprocess.check_output", return_value=b"driver ok\n"
    )
    out_path = tmp_path / "x.out"

    run_driver(
        _settings(),
        driver_yaml=PurePosixPath("/work/x.yaml"),
        out_path=out_path,
        timeout_s=7,
    )

    assert out_path.read_bytes() == b"driver ok\n"
    check_output.assert_called_once()
    assert check_output.call_args.kwargs["timeout"] == 7
    assert check_output.call_args.kwargs["stderr"] == subprocess.STDOUT


def test_run_driver_nonzero_exit_writes_out_and_reraises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    error = subprocess.CalledProcessError(1, ["docker"], output=b"boom\n")
    mocker.patch("runner.subprocess.check_output", side_effect=error)
    out_path = tmp_path / "x.out"

    with pytest.raises(subprocess.CalledProcessError):
        run_driver(
            _settings(),
            driver_yaml=PurePosixPath("/work/x.yaml"),
            out_path=out_path,
            timeout_s=7,
        )

    assert out_path.read_bytes() == b"boom\n"


def test_run_driver_timeout_writes_out_and_reraises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    error = subprocess.TimeoutExpired(cmd=["docker"], timeout=7, output=b"partial\n")
    mocker.patch("runner.subprocess.check_output", side_effect=error)
    out_path = tmp_path / "x.out"

    with pytest.raises(subprocess.TimeoutExpired):
        run_driver(
            _settings(),
            driver_yaml=PurePosixPath("/work/x.yaml"),
            out_path=out_path,
            timeout_s=7,
        )

    assert out_path.read_bytes() == b"partial\n"
