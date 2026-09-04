"""One cece_standalone_driver invocation per combination: docker run against
the cece/cece-dev image (local); a host process behind an optional launcher
prefix (native, inside an allocation); or one `sbatch --wait` job submitted
from a login node (slurm — RDHPC machines have no docker, and the harness's
Python must never see the driver's module environment)."""

from __future__ import annotations

import math
import shlex
import subprocess
from pathlib import Path, PurePosixPath

from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel, ConfigDict, InstanceOf

from combos import Combo
from models.cece_config import CeceConfig
from platforms import Runtime
from settings import Settings


# The native launcher helper (loads CECE_MODULEFILE, execs its argv) — used
# inside an salloc shell; the slurm runtime renders its own job scripts.
WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "cece-modules.sh"
# The slurm job-script template: one rendered `<combo_id>.sbatch` per driver
# call, written beside the combo's yaml and out so a job is reproducible by
# hand (`sbatch <combo_id>.sbatch`). StrictUndefined: a missing variable
# fails the render, never the job.
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "driver-job.sbatch.j2"
_JOB_NAME_PREFIX = "ufs-chem-assay-"


def slurm_minutes(timeout_s: int) -> int:
    """Slurm time limits are whole minutes: round the suite timeout up, floor 1."""
    return max(1, math.ceil(timeout_s / 60))


def sbatch_directives(sbatch_args: str) -> list[str]:
    """CECE_SBATCH_ARGS as one directive per option: an option followed by a
    value that is not itself an option pairs with it (`-A epic`); anything
    else stands alone (`--exclusive`, `--mem=4g`)."""
    tokens = shlex.split(sbatch_args)
    directives: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        if (
            token.startswith("-")
            and "=" not in token
            and nxt is not None
            and not nxt.startswith("-")
        ):
            directives.append(f"{token} {nxt}")
            index += 2
        else:
            directives.append(token)
            index += 1
    return directives


def render_job_script(
    *,
    job_name: str,
    out_path: Path,
    root_dir: Path,
    minutes: int,
    directives: list[str],
    modulefile: str | None,
    env: dict[str, str],
    command: str,
    template_text: str | None = None,
    extra: dict[str, object] | None = None,
) -> str:
    """The job script text for one command: every per-job setting as a
    #SBATCH directive, the module block when a modulefile is given, the
    exports, then `srun --ntasks=1 <command>` — MPI programs inside a batch
    job need Slurm's PMI endpoint, which only srun provides."""
    text = TEMPLATE_PATH.read_text() if template_text is None else template_text
    template = Environment(
        undefined=StrictUndefined, keep_trailing_newline=True
    ).from_string(text)
    return template.render(
        job_name=job_name,
        out_path=str(out_path),
        root_dir=str(root_dir),
        minutes=minutes,
        directives=directives,
        modulefile=modulefile,
        env=env,
        command=command,
        **(extra or {}),
    )


def driver_command(settings: Settings, driver_yaml: PurePosixPath) -> list[str]:
    """The driver invocation as the job (or host process) runs it, relative
    to the checkout it runs in."""
    return [settings.driver_path, str(driver_yaml)]


def job_script_path(out_path: Path) -> Path:
    """<combo_dir>/<combo_id>.sbatch, beside the .out and .yaml."""
    return out_path.with_suffix(".sbatch")


def write_job_script(
    settings: Settings, driver_yaml: PurePosixPath, out_path: Path, timeout_s: int
) -> Path:
    """Render and write the combo's job script from the settings; returns
    its path. Idempotent — the dry run writes it as a recorded artifact and
    the real run writes the same text before submitting."""
    assert settings.root_dir is not None  # guarded at collection
    script = job_script_path(out_path)
    script.write_text(
        render_job_script(
            job_name=f"{_JOB_NAME_PREFIX}{out_path.stem}",
            out_path=out_path,
            root_dir=settings.root_dir,
            minutes=slurm_minutes(timeout_s),
            directives=sbatch_directives(settings.sbatch_args),
            modulefile=settings.modulefile,
            env=settings.job_env_pairs,
            command=" ".join(
                shlex.quote(part) for part in driver_command(settings, driver_yaml)
            ),
        )
    )
    return script


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
    job_script: Path | None = None,
) -> list[str]:
    """The command the harness runs for one combination, per settings.runtime.

    docker: the shared docker prefix, then the driver; driver_yaml is a
    container path.
    native: the launcher prefix, the driver resolved against the checkout,
    and driver_yaml as a host path; output_mount is meaningless and ignored.
    slurm: the submission of the combo's rendered job script — everything
    else (cwd, output, time limit, directives, modules, the srun launch)
    is inside the script.
    """
    if settings.runtime is Runtime.NATIVE:
        assert settings.root_dir is not None  # guarded at collection
        driver = settings.root_dir / settings.driver_path
        return [*settings.launcher_argv, str(driver), str(driver_yaml)]
    if settings.runtime is Runtime.SLURM:
        assert job_script is not None, "slurm submits a rendered job script"
        return ["sbatch", "--wait", "--parsable", str(job_script)]
    return [
        *docker_prefix(settings, output_mount),
        *driver_command(settings, driver_yaml),
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
    """One `sbatch --wait` job for the driver, from the combo's rendered job
    script (`<combo_id>.sbatch`, kept beside the yaml and out so the job is
    reproducible by hand). Slurm writes the job's output to out_path itself;
    the harness reads it back afterwards. The job's own
    time limit is timeout_s rounded up to minutes; the outer bound adds the
    queue allowance and, when hit, cancels the job (scancel) before raising
    TimeoutExpired — queue time must not count against the driver, but a
    job stuck forever must not hang the session."""
    script = write_job_script(settings, driver_yaml, out_path, timeout_s)
    command = build_command(settings, driver_yaml, job_script=script)
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
