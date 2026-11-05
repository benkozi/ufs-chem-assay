"""Slurm runtime: pytest on a login node, one rendered sbatch script per
driver call, submitted with `sbatch --parsable` and finished by polling
(jobs.py). Every Slurm command is mocked."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest
from jinja2 import UndefinedError
from pytest_mock import MockerFixture

from applications.cece.settings import CONTAINER_WORKDIR, CeceSettings
from applications.registry import get_application
from benchmarks import SlurmAccounting
from jobs import SlurmJobError, SlurmJobTimeout, SubmittedJob
from platforms import Platform, Runtime, default_runtime
from resolution import resolve_output_roots
from runner import (
    TEMPLATE_PATH,
    build_command,
    driver_command,
    finish_driver_job,
    job_script_path,
    render_job_script,
    run_driver,
    sbatch_directives,
    slurm_minutes,
    submit_driver_job,
    write_job_script,
)
from settings import Settings

_APP = get_application("cece")
_CECE = CeceSettings(root_dir=Path("/host/cece"), modulefile="cece_ursa.intelllvm")


def _slurm(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "platform": Platform.URSA,
        "sbatch_args": "-A epic -q debug -p u1-compute -N 1 -n 1 -c 8",
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
    assert TEMPLATE_PATH.name == "driver-job.sbatch.j2"
    assert TEMPLATE_PATH.is_file()
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
            srun_args="--ntasks=1",
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
        srun_args="--ntasks=1",
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
    assert "module purge" in body
    assert "module use /host/cece/modulefiles" in body
    assert "module load cece_ursa.intelllvm" in body
    assert "export I_MPI_FABRICS=shm" in body
    assert "export FI_PROVIDER=tcp" in body
    # The PMI fix: MPI programs inside a batch job must be launched by srun;
    # the srun options are the ASSAY_SRUN_ARGS setting, rendered verbatim.
    launch = body.index(
        "srun --ntasks=1 ./build/cece_standalone_driver /host/out/01ABC/01ABC.yaml"
    )
    # Wall time and memory in the .out (the author's cece_job.sh): tic/toc
    # around the launch, then RDHPCS's report-mem, guarded so the script runs
    # (and exits 0) on any Slurm machine.
    assert body[launch - 1] == 'echo "tic: $(date -u +%Y-%m-%dT%H:%M:%SZ)"'
    assert body[launch + 1] == 'echo "toc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"'
    assert (
        body[-1]
        == "if command -v report-mem >/dev/null 2>&1; then report-mem || true; fi"
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
        srun_args="--ntasks=1",
    )
    assert "module" not in text
    assert "export" not in text
    assert "srun --ntasks=1 ctest --test-dir /r/build" in text.splitlines()


def test_driver_command_and_job_script_path() -> None:
    assert driver_command(_CECE, PurePosixPath("/o/x/x.yaml")) == [
        "./build/cece_standalone_driver",
        "/o/x/x.yaml",
    ]
    assert job_script_path(Path("/o/x/x.out")) == Path("/o/x/x.sbatch")


def test_write_job_script_renders_from_settings(tmp_path: Path) -> None:
    out_path = tmp_path / "x.out"
    script = write_job_script(
        _slurm(), _CECE, PurePosixPath(str(tmp_path / "x.yaml")), out_path, timeout_s=90
    )
    assert script == tmp_path / "x.sbatch"
    text = script.read_text()
    assert f"#SBATCH --output={out_path}" in text
    assert "#SBATCH --time=2" in text
    assert "#SBATCH -A epic" in text
    assert "#SBATCH --job-name=ufs-chem-assay-x" in text
    assert (
        f"srun --ntasks=1 ./build/cece_standalone_driver {tmp_path}/x.yaml"
        in text.splitlines()
    )


def test_write_job_script_renders_the_srun_args_setting(tmp_path: Path) -> None:
    # Four MPI tasks, bound to cores: the setting is the whole srun option
    # list, so sbatch -n and srun --ntasks agree because the run config
    # renders both from one ntasks value.
    settings = _slurm(
        sbatch_args="-A epic -q batch -p u1-compute -N 1 -n 4 -c 1 --mem=0",
        srun_args="--ntasks=4 --cpu-bind=cores",
    )
    script = write_job_script(
        settings, _CECE, PurePosixPath(str(tmp_path / "x.yaml")), tmp_path / "x.out", 60
    )
    lines = script.read_text().splitlines()
    assert "#SBATCH --mem=0" in lines
    assert (
        f"srun --ntasks=4 --cpu-bind=cores ./build/cece_standalone_driver {tmp_path}/x.yaml"
        in lines
    )


def test_slurm_build_command_submits_the_script() -> None:
    command = build_command(
        _slurm(),
        _APP,
        _CECE,
        PurePosixPath("/o/x/x.yaml"),
        job_script=Path("/o/x/x.sbatch"),
    )
    assert command == ["sbatch", "--parsable", "/o/x/x.sbatch"]  # no --wait: polled


def _submitted(tmp_path: Path, job_id: str = "12345") -> SubmittedJob:
    return SubmittedJob(
        suite="simple-maccity",
        combo="x",
        combo_id="x",
        job_id=job_id,
        script=tmp_path / "x.sbatch",
        out_path=tmp_path / "x.out",
        timeout_s=10,
        queue_wait_s=5,
        submitted_at=datetime.now(UTC),
    )


def _accounting(state: str, exit_code: int | None) -> SlurmAccounting:
    return SlurmAccounting(
        job_id="12345",
        submit=None,
        start=None,
        end=None,
        elapsed_s=None,
        queue_s=None,
        max_rss_kb=None,
        state=state,
        exit_code=exit_code,
    )


def test_submit_driver_job_writes_the_script_and_submits_it(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    submit = mocker.patch("runner.submit_job", return_value="12345")
    script, job_id = submit_driver_job(
        _slurm(), _CECE, PurePosixPath(str(tmp_path / "x.yaml")), tmp_path / "x.out", 10
    )
    assert job_id == "12345"  # from sbatch --parsable, for the ledger
    assert script == tmp_path / "x.sbatch"
    assert script.is_file()
    assert "srun --ntasks=1" in script.read_text()
    submit.assert_called_once_with(script)


def test_finish_driver_job_completed_returns_the_accounting(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    accounting = _accounting("COMPLETED", 0)
    wait = mocker.patch("runner.wait_for_job", return_value=accounting)
    job = _submitted(tmp_path)
    job.out_path.write_bytes(b"driver ok\n")  # Slurm wrote the job's output
    execution = finish_driver_job(job, _slurm(slurm_poll_s=1))
    assert execution.job_id == "12345"
    assert execution.accounting is accounting
    assert wait.call_args.args[0] is job
    assert wait.call_args.kwargs == {"poll_s": 1}
    assert job.out_path.read_bytes() == b"driver ok\n"  # untouched: Slurm owns it


def test_finish_driver_job_nonzero_exit_reraises_with_job_output(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch("runner.wait_for_job", return_value=_accounting("FAILED", 3))
    job = _submitted(tmp_path)
    job.out_path.write_bytes(b"boom\n")
    with pytest.raises(SlurmJobError) as excinfo:
        finish_driver_job(job, _slurm(slurm_poll_s=1))
    assert isinstance(excinfo.value, subprocess.CalledProcessError)
    assert excinfo.value.returncode == 3
    assert excinfo.value.output == b"boom\n"
    assert excinfo.value.job_id == "12345"
    assert excinfo.value.cmd == ["sbatch", "--parsable", str(job.script)]


def test_finish_driver_job_without_an_exit_code_judges_by_state(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # NODE_FAIL and friends: no exit code to speak of, not COMPLETED -> failed.
    mocker.patch("runner.wait_for_job", return_value=_accounting("NODE_FAIL", None))
    job = _submitted(tmp_path)
    job.out_path.write_bytes(b"")
    with pytest.raises(SlurmJobError) as excinfo:
        finish_driver_job(job, _slurm(slurm_poll_s=1))
    assert excinfo.value.returncode == 1
    # COMPLETED with no parsed exit code is still a pass.
    mocker.patch("runner.wait_for_job", return_value=_accounting("COMPLETED", None))
    assert finish_driver_job(job, _slurm(slurm_poll_s=1)).job_id == "12345"


@pytest.mark.parametrize("state", ["TIMEOUT", "CANCELLED by 1234"])
def test_finish_driver_job_timeout_and_cancelled_are_timeouts(
    mocker: MockerFixture, tmp_path: Path, state: str
) -> None:
    mocker.patch("runner.wait_for_job", return_value=_accounting(state, 0))
    job = _submitted(tmp_path)
    job.out_path.write_bytes(b"partial\n")
    with pytest.raises(SlurmJobTimeout) as excinfo:
        finish_driver_job(job, _slurm(slurm_poll_s=1))
    assert isinstance(excinfo.value, subprocess.TimeoutExpired)
    assert excinfo.value.job_id == "12345"
    assert excinfo.value.timeout == 15  # timeout_s + queue allowance


def test_finish_driver_job_writes_out_when_the_job_left_none(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch("runner.wait_for_job", return_value=_accounting("FAILED", 1))
    job = _submitted(tmp_path)
    with pytest.raises(SlurmJobError):
        finish_driver_job(job, _slurm(slurm_poll_s=1))
    assert job.out_path.is_file()
    text = job.out_path.read_text()
    assert "12345" in text  # what Slurm said, at least
    assert "FAILED" in text


def test_finish_driver_job_propagates_the_waits_timeout(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # The deadline case is the wait's (jobs.wait_for_job scancels and raises);
    # the runner adds nothing to it.
    mocker.patch(
        "runner.wait_for_job",
        side_effect=SlurmJobTimeout(["sbatch"], 15, "12345"),
    )
    with pytest.raises(SlurmJobTimeout):
        finish_driver_job(_submitted(tmp_path), _slurm(slurm_poll_s=1))


def test_run_driver_refuses_the_slurm_runtime(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="submit_driver_job"):
        run_driver(
            _slurm(),
            _APP,
            _CECE,
            PurePosixPath(str(tmp_path / "x.yaml")),
            tmp_path / "x.out",
            timeout_s=10,
        )


def test_slurm_roots_are_host_roots() -> None:
    host, driver = resolve_output_roots(
        "runs", Path("/host/cece"), Runtime.SLURM, container_workdir=CONTAINER_WORKDIR
    )
    assert host == Path("/host/cece/runs")
    assert driver == PurePosixPath("/host/cece/runs")


def test_docker_and_native_paths_unchanged_by_the_new_runtime() -> None:
    docker = Settings(platform=Platform.LOCAL)
    assert (
        build_command(docker, _APP, _CECE, PurePosixPath("/work/x.yaml"))[0] == "docker"
    )
    native = Settings(platform=Platform.URSA, runtime=Runtime.NATIVE)
    assert build_command(native, _APP, _CECE, PurePosixPath("/h/x.yaml")) == [
        "/host/cece/build/cece_standalone_driver",
        "/h/x.yaml",
    ]
