"""The docker runtime through the generic runner, with CECE as the concrete
adapter: the command shape and the .out capture (process call mocked)."""

import subprocess
from pathlib import Path, PurePosixPath

import pytest
from pytest_mock import MockerFixture

from applications.cece.settings import CeceSettings
from applications.registry import get_application
from platforms import Platform
from runner import build_command, run_driver
from settings import Settings

_APP = get_application("cece")


def _settings() -> Settings:
    # Explicit values so ambient variables cannot influence assertions; the
    # docker shape is the point of these tests.
    return Settings(platform=Platform.LOCAL)


def _app_settings() -> CeceSettings:
    return CeceSettings(
        root_dir=Path("/host/cece"),
        docker_image="img:tag",
        driver_path="./build/cece_standalone_driver",
    )


def test_build_command_work_mount_and_yaml_argument() -> None:
    command = build_command(
        _settings(), _APP, _app_settings(), PurePosixPath("/work/combo_runs/x/x.yaml")
    )
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
        _APP,
        _app_settings(),
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

    execution = run_driver(
        _settings(),
        _APP,
        _app_settings(),
        driver_yaml=PurePosixPath("/work/x.yaml"),
        out_path=out_path,
        timeout_s=7,
    )

    # The driver's output bracketed by the clock lines every runtime writes
    # (the slurm job echoes its own); docker knows no job id.
    tic, body, toc = out_path.read_text().splitlines()
    assert tic.startswith("tic: ")
    assert tic.endswith("Z")
    assert body == "driver ok"
    assert toc.startswith("toc: ")
    assert toc.endswith("Z")
    assert execution.job_id is None
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
            _APP,
            _app_settings(),
            driver_yaml=PurePosixPath("/work/x.yaml"),
            out_path=out_path,
            timeout_s=7,
        )

    lines = out_path.read_text().splitlines()
    assert lines[0].startswith("tic: ")
    assert lines[1:] == ["boom"]


def test_run_driver_timeout_writes_out_and_reraises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    error = subprocess.TimeoutExpired(cmd=["docker"], timeout=7, output=b"partial\n")
    mocker.patch("runner.subprocess.check_output", side_effect=error)
    out_path = tmp_path / "x.out"

    with pytest.raises(subprocess.TimeoutExpired):
        run_driver(
            _settings(),
            _APP,
            _app_settings(),
            driver_yaml=PurePosixPath("/work/x.yaml"),
            out_path=out_path,
            timeout_s=7,
        )

    lines = out_path.read_text().splitlines()
    assert lines[0].startswith("tic: ")
    assert lines[1:] == ["partial"]


def test_output_banner_names_no_application(
    mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch("runner.subprocess.check_output", return_value=b"ok\n")
    run_driver(
        _settings(),
        _APP,
        _app_settings(),
        driver_yaml=PurePosixPath("/work/x.yaml"),
        out_path=tmp_path / "x.out",
        timeout_s=7,
    )
    out = capsys.readouterr().out
    assert "----- driver output [x] -----" in out
    assert "cece driver" not in out


def test_build_command_uses_application_driver_arguments_in_docker_and_native(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    from platforms import Runtime
    from runner import driver_command, write_job_script
    from tests.ufs_chem_assay.stubs import stub_application

    stub = stub_application()
    mocker.patch.object(
        stub,
        "driver_arguments",
        side_effect=lambda yaml_path, host_yaml=None: [
            "load",
            "aeronet",
            "--config",
            str(host_yaml or yaml_path),
        ],
    )
    app_settings = _app_settings()

    docker_cmd = build_command(
        _settings(),
        stub,
        app_settings,
        PurePosixPath("/work/x/x.yaml"),
        host_yaml=tmp_path / "x.yaml",
    )
    assert docker_cmd[-5:] == [
        "./build/cece_standalone_driver",
        "load",
        "aeronet",
        "--config",
        str(tmp_path / "x.yaml"),
    ]

    native_cmd = build_command(
        Settings(platform=Platform.LOCAL, runtime=Runtime.NATIVE),
        stub,
        app_settings,
        PurePosixPath("/host/out/x/x.yaml"),
    )
    assert native_cmd == [
        "/host/cece/build/cece_standalone_driver",
        "load",
        "aeronet",
        "--config",
        "/host/out/x/x.yaml",
    ]

    assert driver_command(app_settings, PurePosixPath("/work/x/x.yaml"), app=stub) == [
        "./build/cece_standalone_driver",
        "load",
        "aeronet",
        "--config",
        "/work/x/x.yaml",
    ]

    script = write_job_script(
        Settings(platform=Platform.URSA),
        app_settings,
        PurePosixPath("/host/out/x/x.yaml"),
        tmp_path / "x.out",
        timeout_s=60,
        app=stub,
    )
    assert (
        "./build/cece_standalone_driver load aeronet --config /host/out/x/x.yaml"
        in script.read_text()
    )
