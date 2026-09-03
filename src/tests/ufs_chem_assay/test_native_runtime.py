"""Native runtime: the driver as a host process, host-side paths throughout."""

import subprocess
import sys
from pathlib import Path, PurePosixPath

from pytest_mock import MockerFixture

from examples import run_example_command
from platforms import Platform, Runtime
from resolution import resolve_output_roots
from runner import build_command, docker_prefix, run_driver
from settings import Settings


def _native(launcher: str = "") -> Settings:
    return Settings(
        root_dir=Path("/host/cece"),
        platform=Platform.URSA,
        launcher=launcher,
        driver_path="./build/cece_standalone_driver",
    )


def test_native_build_command_runs_driver_directly() -> None:
    command = build_command(_native(), PurePosixPath("/host/out/x/x.yaml"))
    assert command == [
        "/host/cece/build/cece_standalone_driver",
        "/host/out/x/x.yaml",
    ]


def test_native_build_command_prefixes_launcher_and_ignores_mount() -> None:
    command = build_command(
        _native("srun --ntasks=1"),
        PurePosixPath("/host/out/x/x.yaml"),
        output_mount=(Path("/host/out"), PurePosixPath("/combo_runs")),
    )
    assert command == [
        "srun",
        "--ntasks=1",
        "/host/cece/build/cece_standalone_driver",
        "/host/out/x/x.yaml",
    ]


def test_native_run_driver_uses_checkout_as_cwd(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    check_output = mocker.patch("runner.subprocess.check_output", return_value=b"ok\n")
    out_path = tmp_path / "x.out"
    run_driver(_native(), PurePosixPath("/host/out/x.yaml"), out_path, timeout_s=5)
    assert out_path.read_bytes() == b"ok\n"
    assert check_output.call_args.kwargs["cwd"] == Path("/host/cece")
    assert check_output.call_args.kwargs["stderr"] == subprocess.STDOUT


def test_docker_run_driver_sets_no_cwd(mocker: MockerFixture, tmp_path: Path) -> None:
    check_output = mocker.patch("runner.subprocess.check_output", return_value=b"ok\n")
    settings = Settings(root_dir=Path("/host/cece"), platform=Platform.LOCAL)
    assert settings.runtime is Runtime.DOCKER
    run_driver(settings, PurePosixPath("/work/x.yaml"), tmp_path / "x.out", timeout_s=5)
    assert check_output.call_args.kwargs["cwd"] is None


def test_native_output_roots_relative_lands_in_checkout() -> None:
    host, driver = resolve_output_roots("runs", Path("/host/cece"), Runtime.NATIVE)
    assert host == Path("/host/cece/runs")
    assert driver == PurePosixPath("/host/cece/runs")


def test_native_output_roots_accepts_any_absolute_path() -> None:
    host, driver = resolve_output_roots(
        "/scratch/out", Path("/host/cece"), Runtime.NATIVE
    )
    assert host == Path("/scratch/out")
    assert driver == PurePosixPath("/scratch/out")


def test_docker_output_roots_keep_the_work_rule() -> None:
    host, driver = resolve_output_roots("runs", Path("/host/cece"), Runtime.DOCKER)
    assert host == Path("/host/cece/runs")
    assert driver == PurePosixPath("/work/runs")


def test_native_example_command_runs_entrypoint_with_harness_python() -> None:
    # The entrypoint needs Python >= 3.11 (StrEnum); natively the harness's
    # own interpreter is the one guaranteed to satisfy that.
    assert run_example_command(_native(), "ex3") == [
        sys.executable,
        "/host/cece/examples/run-example.py",
        "--example",
        "ex3",
    ]


def test_docker_prefix_is_shared_by_driver_and_examples_commands() -> None:
    # One docker preamble (mounts, cwd, MPI-as-root env, image) serves both
    # the driver and the examples entrypoint; only the tail differs.
    settings = Settings(root_dir=Path("/host/cece"), docker_image="img:tag")
    prefix = docker_prefix(settings)
    assert prefix[0:3] == ["docker", "run", "--rm"] and prefix[-1] == "img:tag"
    driver = build_command(settings, PurePosixPath("/work/x.yaml"))
    example = run_example_command(settings, "ex3")
    assert driver[: len(prefix)] == prefix
    assert example[: len(prefix)] == prefix
    assert driver[len(prefix) :] == ["./build/cece_standalone_driver", "/work/x.yaml"]
    assert example[len(prefix) :] == [
        "python3",
        "examples/run-example.py",
        "--example",
        "ex3",
    ]
