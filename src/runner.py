"""One cece_standalone_driver invocation per combination: docker run against
the cece/cece-dev image (local); a host process behind an optional launcher
prefix (native, inside an allocation); or one `sbatch --wait` job submitted
from a login node (slurm — RDHPC machines have no docker, and the harness's
Python must never see the driver's module environment)."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, InstanceOf

from combos import Combo
from models.cece_config import CeceConfig
from platforms import Runtime
from settings import Settings


# The slurm job script: loads CECE_MODULEFILE (when set) and execs its
# arguments — sbatch takes a script followed by the script's arguments, so
# the wrapper is the script and the driver command is its argv.
WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "cece-modules.sh"


def slurm_minutes(timeout_s: int) -> int:
    """Slurm time limits are whole minutes: round the suite timeout up, floor 1."""
    return max(1, math.ceil(timeout_s / 60))


class DriverRunResult(BaseModel):
    """Outcome of one driver invocation. The driver-run fixture never raises;
    execution failure is reported explicitly by test_driver_execution and
    downstream assertion tests skip."""

    model_config = ConfigDict(frozen=True)

    # InstanceOf: Combo is enumeration machinery (callables, enum members),
    # validated by isinstance rather than deep pydantic validation.
    combo: InstanceOf[Combo]
    combo_dir: Path
    out_path: Path
    config: CeceConfig
    error: InstanceOf[Exception] | None


def docker_prefix(
    settings: Settings, output_mount: tuple[Path, PurePosixPath] | None = None
) -> list[str]:
    """`docker run` up to and including the image: the checkout bind-mounted
    at /work (plus the output root when it lies outside it), cwd /work, and
    the run-as-root MPI environment. The driver and the examples entrypoint
    share it; only what runs in the container differs."""
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{settings.root_dir}:/work",  # bind mount: <host path>:<container path>
    ]
    if output_mount is not None:
        # Output root outside the /work mount (the pytest tmp default) needs
        # its own bind mount so artifacts survive --rm.
        host_root, container_root = output_mount
        command += ["-v", f"{host_root}:{container_root}"]
    command += [
        "-w",
        "/work",
        "-e",
        "OMPI_ALLOW_RUN_AS_ROOT=1",
        "-e",
        "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1",
        settings.docker_image,
    ]
    return command


def build_command(
    settings: Settings,
    driver_yaml: PurePosixPath,
    output_mount: tuple[Path, PurePosixPath] | None = None,
    *,
    out_path: Path | None = None,
    timeout_s: int | None = None,
) -> list[str]:
    """The driver command for one combination, per settings.runtime.

    docker: the shared docker prefix, then the driver; driver_yaml is a
    container path.
    native: the launcher prefix, the driver resolved against the checkout,
    and driver_yaml as a host path; output_mount is meaningless and ignored.
    slurm: `sbatch --wait --parsable` with the job's cwd at the checkout,
    its stdout+stderr at out_path, its time limit from timeout_s (whole
    minutes), the configured sbatch args, then the wrapper as the job
    script and the driver command as its arguments.
    """
    if settings.runtime is Runtime.NATIVE:
        assert settings.root_dir is not None  # guarded at collection
        driver = settings.root_dir / settings.driver_path
        return [*settings.launcher_argv, str(driver), str(driver_yaml)]
    if settings.runtime is Runtime.SLURM:
        assert settings.root_dir is not None  # guarded at collection
        assert out_path is not None and timeout_s is not None, (
            "slurm needs out_path/timeout_s"
        )
        return [
            "sbatch",
            "--wait",
            "--parsable",
            "--chdir",
            str(settings.root_dir),
            "-o",
            str(out_path),
            "-t",
            str(slurm_minutes(timeout_s)),
            *settings.sbatch_argv,
            str(WRAPPER),
            settings.driver_path,
            str(driver_yaml),
        ]
    return [
        *docker_prefix(settings, output_mount),
        settings.driver_path,
        str(driver_yaml),
    ]


def _print_output(out_path: Path, output: bytes) -> None:
    # out_path.stem is the combo name. Visibility is delegated to pytest's
    # capture model: shown live with -s, in failure reports otherwise.
    print(f"----- cece driver output [{out_path.stem}] -----")
    print(output.decode("utf-8", errors="replace"), end="")
    print(f"----- end driver output [{out_path.stem}] -----")


def run_driver(
    settings: Settings,
    driver_yaml: PurePosixPath,
    out_path: Path,
    timeout_s: int,
    output_mount: tuple[Path, PurePosixPath] | None = None,
) -> None:
    """Run one driver invocation (a fresh container, a host process, or a
    Slurm job).

    Combined stdout/stderr is written to out_path and printed whether the run
    passes or fails; a nonzero exit re-raises CalledProcessError to fail the
    test, and a hung driver fails with TimeoutExpired after timeout_s.
    Natively the process runs in the checkout (cwd = root_dir), so the
    relative driver path and cwd-relative data paths resolve as they do
    under docker's -w /work; the environment is inherited as-is (modules,
    MPI hints are the caller's job). Under slurm see _run_slurm_job.
    """
    if settings.runtime is Runtime.SLURM:
        _run_slurm_job(settings, driver_yaml, out_path, timeout_s)
        return
    command = build_command(settings, driver_yaml, output_mount=output_mount)
    cwd = settings.root_dir if settings.runtime is Runtime.NATIVE else None
    try:
        output = subprocess.check_output(
            command, stderr=subprocess.STDOUT, timeout=timeout_s, cwd=cwd
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        out_path.write_bytes(exc.output or b"")
        _print_output(out_path, exc.output or b"")
        raise
    out_path.write_bytes(output)
    _print_output(out_path, output)


def _run_slurm_job(
    settings: Settings, driver_yaml: PurePosixPath, out_path: Path, timeout_s: int
) -> None:
    """One `sbatch --wait` job for the driver. Slurm writes the job's output
    to out_path itself; the harness reads it back afterwards. The job's own
    time limit is timeout_s rounded up to minutes; the outer bound adds the
    queue allowance and, when hit, cancels the job (scancel) before raising
    TimeoutExpired — queue time must not count against the driver, but a
    job stuck forever must not hang the session."""
    command = build_command(
        settings, driver_yaml, out_path=out_path, timeout_s=timeout_s
    )
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    assert process.stdout is not None
    submission = process.stdout.readline()  # --parsable: the job id
    job_id = submission.decode("utf-8", errors="replace").strip().split(";")[0]
    try:
        returncode = process.wait(timeout=timeout_s + settings.slurm_queue_wait_s)
    except subprocess.TimeoutExpired:
        if job_id:
            subprocess.run(["scancel", job_id], check=False)
        process.kill()
        raise
    if not out_path.is_file():
        # The job never wrote (rejected submission, failed before start):
        # keep whatever sbatch said so the .out exists and explains itself.
        out_path.write_bytes(submission + process.stdout.read())
    output = out_path.read_bytes()
    _print_output(out_path, output)
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, command, output=output)
