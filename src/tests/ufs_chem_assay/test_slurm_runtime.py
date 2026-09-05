"""Slurm runtime: pytest on a login node, one rendered sbatch script per
driver call, submitted with `sbatch --wait`. Popen is mocked throughout."""

import io
import subprocess
from pathlib import Path, PurePosixPath

import pytest
from jinja2 import UndefinedError
from pytest_mock import MockerFixture

from examples import run_example_command
from platforms import Platform, Runtime, default_runtime
from resolution import resolve_output_roots
from runner import (
    TEMPLATE_PATH,
    build_command,
    driver_command,
    job_script_path,
    render_job_script,
    run_driver,
    sbatch_directives,
    slurm_minutes,
    write_job_script,
)
from settings import Settings


def _slurm(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "platform": Platform.URSA,
        "root_dir": Path("/host/cece"),
        "sbatch_args": "-A epic -q debug -p u1-compute -N 1 -n 1 -c 8",
        "modulefile": "cece_ursa.intelllvm",
        "job_env": "I_MPI_FABRICS=shm FI_PROVIDER=tcp",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_ursa_defaults_to_the_slurm_runtime() -> None:
    assert default_runtime(Platform.URSA) is Runtime.SLURM
    assert _slurm().runtime is Runtime.SLURM


@pytest.mark.parametrize(
    ("seconds", "minutes"), [(10, 1), (60, 1), (61, 2), (120, 2), (300, 5)]
)
def test_timeout_rounds_up_to_whole_minutes(seconds: int, minutes: int) -> None:
    assert slurm_minutes(seconds) == minutes


def test_sbatch_directives_pair_options_with_values() -> None:
    assert sbatch_directives("-A epic -q debug -c 8") == ["-A epic", "-q debug", "-c 8"]
    assert sbatch_directives("--exclusive -A epic --mem=4g") == [
        "--exclusive",
        "-A epic",
        "--mem=4g",
    ]
    assert sbatch_directives("") == []


def test_job_env_pairs() -> None:
    assert _slurm().job_env_pairs == {"I_MPI_FABRICS": "shm", "FI_PROVIDER": "tcp"}
    assert _slurm(job_env="").job_env_pairs == {}
    with pytest.raises(ValueError, match="NAME=VALUE"):
        _ = _slurm(job_env="NOT_A_PAIR").job_env_pairs


def test_template_is_shipped_and_strict() -> None:
    assert TEMPLATE_PATH.name == "driver-job.sbatch.j2" and TEMPLATE_PATH.is_file()
    with pytest.raises(UndefinedError):
        render_job_script(
            job_name="x",
            out_path=Path("/o/x.out"),
            root_dir=Path("/host/cece"),
            minutes=1,
            directives=[],
            modulefile=None,
            env={},
            command="",
            extra={"unused": 1},
            template_text="{{ missing }}",
        )


def test_render_job_script_golden() -> None:
    text = render_job_script(
        job_name="ufs-chem-assay-01ABC",
        out_path=Path("/host/out/01ABC/01ABC.out"),
        root_dir=Path("/host/cece"),
        minutes=1,
        directives=sbatch_directives(_slurm().sbatch_args),
        modulefile="cece_ursa.intelllvm",
        env={"I_MPI_FABRICS": "shm", "FI_PROVIDER": "tcp"},
        command="./build/cece_standalone_driver /host/out/01ABC/01ABC.yaml",
    )
    lines = text.splitlines()
    assert lines[0] == "#!/bin/bash"
    directives = [line for line in lines if line.startswith("#SBATCH")]
    assert directives == [
        "#SBATCH --job-name=ufs-chem-assay-01ABC",
        "#SBATCH --output=/host/out/01ABC/01ABC.out",
        "#SBATCH --chdir=/host/cece",
        "#SBATCH --time=1",
        "#SBATCH -A epic",
        "#SBATCH -q debug",
        "#SBATCH -p u1-compute",
        "#SBATCH -N 1",
        "#SBATCH -n 1",
        "#SBATCH -c 8",
    ]
    body = lines[len(directives) + 1 :]
    assert body[0] == "set -euo pipefail"
    assert "module purge" in body and "module use /host/cece/modulefiles" in body
    assert "module load cece_ursa.intelllvm" in body
    assert "export I_MPI_FABRICS=shm" in body and "export FI_PROVIDER=tcp" in body
    # The PMI fix: MPI programs inside a batch job must be launched by srun.
    assert (
        body[-1]
        == "srun --ntasks=1 ./build/cece_standalone_driver /host/out/01ABC/01ABC.yaml"
    )
    assert body.index("module load cece_ursa.intelllvm") < body.index(
        "export I_MPI_FABRICS=shm"
    )


def test_render_job_script_without_modules_or_env() -> None:
    text = render_job_script(
        job_name="j",
        out_path=Path("/o/j.out"),
        root_dir=Path("/r"),
        minutes=2,
        directives=[],
        modulefile=None,
        env={},
        command="ctest --test-dir /r/build",
    )
    assert "module" not in text and "export" not in text
    assert text.rstrip().endswith("srun --ntasks=1 ctest --test-dir /r/build")


def test_driver_command_and_job_script_path() -> None:
    assert driver_command(_slurm(), PurePosixPath("/o/x/x.yaml")) == [
        "./build/cece_standalone_driver",
        "/o/x/x.yaml",
    ]
    assert job_script_path(Path("/o/x/x.out")) == Path("/o/x/x.sbatch")


def test_write_job_script_renders_from_settings(tmp_path: Path) -> None:
    out_path = tmp_path / "x.out"
    script = write_job_script(
        _slurm(), PurePosixPath(str(tmp_path / "x.yaml")), out_path, timeout_s=90
    )
    assert script == tmp_path / "x.sbatch"
    text = script.read_text()
    assert f"#SBATCH --output={out_path}" in text
    assert "#SBATCH --time=2" in text
    assert "#SBATCH -A epic" in text
    assert "#SBATCH --job-name=ufs-chem-assay-x" in text
    assert text.rstrip().endswith(
        f"srun --ntasks=1 ./build/cece_standalone_driver {tmp_path}/x.yaml"
    )


def test_slurm_build_command_submits_the_script() -> None:
    command = build_command(
        _slurm(), PurePosixPath("/o/x/x.yaml"), job_script=Path("/o/x/x.sbatch")
    )
    assert command == ["sbatch", "--wait", "--parsable", "/o/x/x.sbatch"]


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


def test_slurm_run_driver_writes_the_script_and_submits_it(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    job = _FakeJob(returncode=0)
    popen = mocker.patch("runner.subprocess.Popen", return_value=job)
    out_path = tmp_path / "x.out"
    out_path.write_bytes(b"driver ok\n")  # Slurm wrote the job's output

    run_driver(
        _slurm(), PurePosixPath(str(tmp_path / "x.yaml")), out_path, timeout_s=10
    )

    script = tmp_path / "x.sbatch"
    assert script.is_file() and "srun --ntasks=1" in script.read_text()
    assert popen.call_args.args[0] == ["sbatch", "--wait", "--parsable", str(script)]
    assert popen.call_args.kwargs["stdout"] == subprocess.PIPE
    assert out_path.read_bytes() == b"driver ok\n"  # untouched: Slurm owns it


def test_slurm_run_driver_nonzero_exit_reraises_with_job_output(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch("runner.subprocess.Popen", return_value=_FakeJob(returncode=3))
    out_path = tmp_path / "x.out"
    out_path.write_bytes(b"boom\n")
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_driver(
            _slurm(), PurePosixPath(str(tmp_path / "x.yaml")), out_path, timeout_s=10
        )
    assert excinfo.value.returncode == 3
    assert excinfo.value.output == b"boom\n"


def test_slurm_run_driver_writes_out_when_the_job_left_none(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch("runner.subprocess.Popen", return_value=_FakeJob(returncode=1))
    out_path = tmp_path / "x.out"
    with pytest.raises(subprocess.CalledProcessError):
        run_driver(
            _slurm(), PurePosixPath(str(tmp_path / "x.yaml")), out_path, timeout_s=10
        )
    assert out_path.is_file()
    assert b"12345" in out_path.read_bytes()  # the sbatch submission line at least


def test_slurm_run_driver_outer_timeout_cancels_the_job(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    job = _FakeJob(returncode=0, hang=True)
    mocker.patch("runner.subprocess.Popen", return_value=job)
    scancel = mocker.patch("runner.subprocess.run")
    with pytest.raises(subprocess.TimeoutExpired):
        run_driver(
            _slurm(slurm_queue_wait_s=5),
            PurePosixPath(str(tmp_path / "x.yaml")),
            tmp_path / "x.out",
            timeout_s=10,
        )
    assert scancel.call_args.args[0] == ["scancel", "12345"]
    assert job.killed
    assert job.last_timeout == 15  # timeout_s + queue allowance


def test_slurm_roots_are_host_roots() -> None:
    host, driver = resolve_output_roots("runs", Path("/host/cece"), Runtime.SLURM)
    assert host == Path("/host/cece/runs") and driver == PurePosixPath(
        "/host/cece/runs"
    )


def test_examples_are_not_supported_under_slurm_yet() -> None:
    with pytest.raises(NotImplementedError, match="slurm"):
        run_example_command(_slurm(), "ex3")


def test_docker_and_native_paths_unchanged_by_the_new_runtime() -> None:
    docker = Settings(platform=Platform.LOCAL, root_dir=Path("/host/cece"))
    assert build_command(docker, PurePosixPath("/work/x.yaml"))[0] == "docker"
    native = Settings(
        platform=Platform.URSA, runtime=Runtime.NATIVE, root_dir=Path("/host/cece")
    )
    assert build_command(native, PurePosixPath("/h/x.yaml")) == [
        "/host/cece/build/cece_standalone_driver",
        "/h/x.yaml",
    ]
