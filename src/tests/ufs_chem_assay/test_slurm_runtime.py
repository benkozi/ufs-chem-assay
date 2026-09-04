"""Slurm runtime: pytest on a login node, one `sbatch --wait` job per driver
call, the module wrapper as the job script. Popen is mocked throughout."""

import io
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest
from pytest_mock import MockerFixture

from examples import run_example_command
from platforms import Platform, Runtime, default_runtime
from resolution import resolve_output_roots
from runner import WRAPPER, build_command, run_driver, slurm_minutes
from settings import Settings


def _slurm(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "platform": Platform.URSA,
        "root_dir": Path("/host/cece"),
        "sbatch_args": "-A epic -q debug -p u1-compute -N 1 -n 1 -c 8",
        "modulefile": "cece_ursa.intelllvm",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_ursa_defaults_to_the_slurm_runtime() -> None:
    assert default_runtime(Platform.URSA) is Runtime.SLURM
    assert _slurm().runtime is Runtime.SLURM
    assert _slurm().sbatch_argv == ["-A", "epic", "-q", "debug", "-p", "u1-compute", "-N", "1", "-n", "1", "-c", "8"]


@pytest.mark.parametrize(("seconds", "minutes"), [(10, 1), (60, 1), (61, 2), (120, 2), (300, 5)])
def test_timeout_rounds_up_to_whole_minutes(seconds: int, minutes: int) -> None:
    assert slurm_minutes(seconds) == minutes


def test_wrapper_is_the_checked_in_job_script() -> None:
    assert WRAPPER.name == "cece-modules.sh"
    assert WRAPPER.is_file()


def test_slurm_build_command_golden() -> None:
    command = build_command(
        _slurm(),
        PurePosixPath("/host/out/x/x.yaml"),
        out_path=Path("/host/out/x/x.out"),
        timeout_s=10,
    )
    assert command == [
        "sbatch",
        "--wait",
        "--parsable",
        "--chdir",
        "/host/cece",
        "-o",
        "/host/out/x/x.out",
        "-t",
        "1",
        "-A", "epic", "-q", "debug", "-p", "u1-compute", "-N", "1", "-n", "1", "-c", "8",
        str(WRAPPER),
        "./build/cece_standalone_driver",
        "/host/out/x/x.yaml",
    ]


def test_slurm_build_command_without_args_still_valid() -> None:
    command = build_command(
        _slurm(sbatch_args=""), PurePosixPath("/o/x.yaml"), out_path=Path("/o/x.out"), timeout_s=90
    )
    assert command[:2] == ["sbatch", "--wait"] and "-t" in command
    assert command[command.index("-t") + 1] == "2"
    assert command[-3:] == [str(WRAPPER), "./build/cece_standalone_driver", "/o/x.yaml"]


class _FakeJob:
    """A Popen stand-in: prints the job id, then ends with `returncode`."""

    def __init__(self, returncode: int, hang: bool = False) -> None:
        self.returncode = returncode
        self.hang = hang
        self.stdout = io.BytesIO(b"12345\n")
        self.killed = False
        self.last_timeout: float | None = None

    def wait(self, timeout: float | None = None) -> int:
        self.last_timeout = timeout
        if self.hang:
            raise subprocess.TimeoutExpired(cmd="sbatch", timeout=timeout or 0)
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def test_slurm_run_driver_reads_the_job_output_file(mocker: MockerFixture, tmp_path: Path) -> None:
    job = _FakeJob(returncode=0)
    popen = mocker.patch("runner.subprocess.Popen", return_value=job)
    out_path = tmp_path / "x.out"
    # Slurm writes the job's output; simulate it having happened.
    out_path.write_bytes(b"driver ok\n")

    run_driver(_slurm(), PurePosixPath("/host/out/x/x.yaml"), out_path, timeout_s=10)

    command = popen.call_args.args[0]
    assert command[0] == "sbatch" and str(out_path) in command
    assert popen.call_args.kwargs["stdout"] == subprocess.PIPE
    assert out_path.read_bytes() == b"driver ok\n"  # untouched: Slurm owns it


def test_slurm_run_driver_nonzero_exit_reraises_with_job_output(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch("runner.subprocess.Popen", return_value=_FakeJob(returncode=3))
    out_path = tmp_path / "x.out"
    out_path.write_bytes(b"boom\n")
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_driver(_slurm(), PurePosixPath("/host/out/x/x.yaml"), out_path, timeout_s=10)
    assert excinfo.value.returncode == 3
    assert excinfo.value.output == b"boom\n"


def test_slurm_run_driver_writes_out_when_the_job_left_none(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch("runner.subprocess.Popen", return_value=_FakeJob(returncode=1))
    out_path = tmp_path / "x.out"
    with pytest.raises(subprocess.CalledProcessError):
        run_driver(_slurm(), PurePosixPath("/host/out/x/x.yaml"), out_path, timeout_s=10)
    assert out_path.is_file()
    assert b"12345" in out_path.read_bytes()  # the sbatch submission line at least


def test_slurm_run_driver_outer_timeout_cancels_the_job(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    job = _FakeJob(returncode=0, hang=True)
    mocker.patch("runner.subprocess.Popen", return_value=job)
    scancel = mocker.patch("runner.subprocess.run")
    with pytest.raises(subprocess.TimeoutExpired):
        run_driver(_slurm(slurm_queue_wait_s=5), PurePosixPath("/o/x.yaml"), tmp_path / "x.out", timeout_s=10)
    assert scancel.call_args.args[0] == ["scancel", "12345"]
    assert job.killed
    # outer bound = timeout_s + queue allowance
    assert job.last_timeout == 15


def test_slurm_roots_are_host_roots() -> None:
    host, driver = resolve_output_roots("runs", Path("/host/cece"), Runtime.SLURM)
    assert host == Path("/host/cece/runs") and driver == PurePosixPath("/host/cece/runs")


def test_examples_are_not_supported_under_slurm_yet() -> None:
    with pytest.raises(NotImplementedError, match="slurm"):
        run_example_command(_slurm(), "ex3")


def test_docker_and_native_paths_unchanged_by_the_new_runtime() -> None:
    docker = Settings(platform=Platform.LOCAL, root_dir=Path("/host/cece"))
    assert build_command(docker, PurePosixPath("/work/x.yaml"))[0] == "docker"
    native = Settings(platform=Platform.URSA, runtime=Runtime.NATIVE, root_dir=Path("/host/cece"))
    assert build_command(native, PurePosixPath("/h/x.yaml")) == [
        "/host/cece/build/cece_standalone_driver",
        "/h/x.yaml",
    ]
    assert sys.executable  # imported for the examples path; keeps ruff quiet
