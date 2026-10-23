"""One driver invocation per combination: docker run against the
application's image (local); a host process behind an optional launcher
prefix (native, inside an allocation); or one `sbatch --wait` job submitted
from a login node (slurm — RDHPC machines have no docker, and the harness's
Python must never see the driver's module environment).

The application adapter supplies the image, driver path, checkout, and
container mount point (through its settings and constants); everything
about *how* to run is here and shared."""

from __future__ import annotations

import math
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel, ConfigDict, InstanceOf

from applications.base import Application, ApplicationSettings, DriverConfig
from combos import Combo
from identity import HARNESS_NAME
from platforms import Runtime
from settings import Settings

# The slurm job-script template: one rendered `<combo_id>.sbatch` per driver
# call, written beside the combo's yaml and out so a job is reproducible by
# hand (`sbatch <combo_id>.sbatch`). StrictUndefined: a missing variable
# fails the render, never the job.
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "driver-job.sbatch.j2"
_JOB_NAME_PREFIX = f"{HARNESS_NAME}-"


def slurm_minutes(timeout_s: int) -> int:
    """Slurm time limits are whole minutes: round the suite timeout up, floor 1."""
    return max(1, math.ceil(timeout_s / 60))


def sbatch_directives(sbatch_args: str) -> list[str]:
    """ASSAY_SBATCH_ARGS as one directive per option: an option followed by a
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
    srun_args: str,
    template_text: str | None = None,
    extra: dict[str, object] | None = None,
) -> str:
    """The job script text for one command: every per-job setting as a
    #SBATCH directive, the module block when a modulefile is given, the
    exports, then `srun <srun_args> <command>` — MPI programs inside a batch
    job need Slurm's PMI endpoint, which only srun provides — bracketed by
    tic/toc timestamps and followed by RDHPCS's report-mem when present, so
    the job's output records its own wall time and memory."""
    text = TEMPLATE_PATH.read_text() if template_text is None else template_text
    # Not HTML: the template is a bash/Slurm batch script and the values are
    # the harness's own validated config. Autoescaping would corrupt shell
    # operators and quotes; CodeQL's py/jinja2/autoescape-false is dismissed
    # as a false positive for this site (design/fix/20261016-1547-codeql-dependabot-alerts).
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
        srun_args=srun_args,
        **(extra or {}),
    )


def driver_command(
    app_settings: ApplicationSettings, driver_yaml: PurePosixPath
) -> list[str]:
    """The driver invocation as the job (or host process) runs it, relative
    to the checkout it runs in."""
    return [app_settings.driver_path, str(driver_yaml)]


def job_script_path(out_path: Path) -> Path:
    """<combo_dir>/<combo_id>.sbatch, beside the .out and .yaml."""
    return out_path.with_suffix(".sbatch")


def write_job_script(
    settings: Settings,
    app_settings: ApplicationSettings,
    driver_yaml: PurePosixPath,
    out_path: Path,
    timeout_s: int,
) -> Path:
    """Render and write the combo's job script from the settings; returns
    its path. Idempotent — the dry run writes it as a recorded artifact and
    the real run writes the same text before submitting."""
    assert app_settings.root_dir is not None  # guarded at collection
    script = job_script_path(out_path)
    script.write_text(
        render_job_script(
            job_name=f"{_JOB_NAME_PREFIX}{out_path.stem}",
            out_path=out_path,
            root_dir=app_settings.root_dir,
            minutes=slurm_minutes(timeout_s),
            directives=sbatch_directives(settings.sbatch_args),
            modulefile=app_settings.modulefile,
            env=settings.job_env_pairs,
            command=" ".join(
                shlex.quote(part) for part in driver_command(app_settings, driver_yaml)
            ),
            srun_args=settings.srun_args,
        )
    )
    return script


# The two clock lines the launch writes around the driver, in every runtime:
# the slurm template echoes them in the job; docker and native get them from
# the runner around the subprocess. ISO-8601 UTC to the second, parseable by
# benchmarks.parse_tic_toc.
CLOCK_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _clock_line(label: str) -> bytes:
    return f"{label}: {datetime.now(UTC).strftime(CLOCK_FORMAT)}\n".encode()


class SlurmJobError(subprocess.CalledProcessError):
    """A Slurm driver job ended nonzero; carries the job id for accounting."""

    def __init__(
        self, returncode: int, cmd: list[str], output: bytes, job_id: str
    ) -> None:
        super().__init__(returncode, cmd, output=output)
        self.job_id = job_id


class SlurmJobTimeout(subprocess.TimeoutExpired):
    """The harness's outer bound (timeout + queue allowance) passed and the
    job was cancelled; carries the job id for accounting."""

    def __init__(self, cmd: list[str], timeout: float, job_id: str) -> None:
        super().__init__(cmd, timeout)
        self.job_id = job_id


class DriverExecution(BaseModel):
    """What the runner knows about how one driver call was launched: the
    Slurm job id under the slurm runtime (for accounting), None elsewhere."""

    model_config = ConfigDict(frozen=True)

    job_id: str | None


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
    config: InstanceOf[DriverConfig]  # the adapter's subclass, kept as-is
    error: InstanceOf[Exception] | None
    execution: DriverExecution = DriverExecution(job_id=None)
    duration_s: float = 0.0  # the harness's clock around the call
    timeout_s: int = 0  # the effective per-combination timeout


def docker_prefix(
    app_settings: ApplicationSettings,
    workdir: PurePosixPath,
    output_mount: tuple[Path, PurePosixPath] | None = None,
) -> list[str]:
    """`docker run` up to and including the image: the checkout bind-mounted
    at the application's workdir (plus the output root when it lies outside
    it), cwd there, and the run-as-root MPI environment (harmless where not
    needed)."""
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{app_settings.root_dir}:{workdir}",  # bind mount: <host path>:<container path>
    ]
    if output_mount is not None:
        # Output root outside the checkout mount (the pytest tmp default)
        # needs its own bind mount so artifacts survive --rm.
        host_root, container_root = output_mount
        command += ["-v", f"{host_root}:{container_root}"]
    command += [
        "-w",
        str(workdir),
        "-e",
        "OMPI_ALLOW_RUN_AS_ROOT=1",
        "-e",
        "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1",
        app_settings.docker_image,
    ]
    return command


def build_command(
    settings: Settings,
    app: Application,
    app_settings: ApplicationSettings,
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
        assert app_settings.root_dir is not None  # guarded at collection
        driver = app_settings.root_dir / app_settings.driver_path
        return [*settings.launcher_argv, str(driver), str(driver_yaml)]
    if settings.runtime is Runtime.SLURM:
        assert job_script is not None, "slurm submits a rendered job script"
        return ["sbatch", "--wait", "--parsable", str(job_script)]
    return [
        *docker_prefix(app_settings, app.container_workdir, output_mount),
        *driver_command(app_settings, driver_yaml),
    ]


def _print_output(out_path: Path, output: bytes) -> None:
    # out_path.stem is the combo name. Visibility is delegated to pytest's
    # capture model: shown live with -s, in failure reports otherwise.
    print(f"----- driver output [{out_path.stem}] -----")
    print(output.decode("utf-8", errors="replace"), end="")
    print(f"----- end driver output [{out_path.stem}] -----")


def run_driver(
    settings: Settings,
    app: Application,
    app_settings: ApplicationSettings,
    driver_yaml: PurePosixPath,
    out_path: Path,
    timeout_s: int,
    output_mount: tuple[Path, PurePosixPath] | None = None,
) -> DriverExecution:
    """Run one driver invocation (a fresh container, a host process, or a
    Slurm job); returns what the runner knows about the launch (the job id
    under slurm).

    Combined stdout/stderr is written to out_path and printed whether the run
    passes or fails, bracketed by `tic:`/`toc:` clock lines (the slurm job
    writes its own; a failed or timed-out call has tic and no toc); a nonzero
    exit re-raises CalledProcessError to fail the test, and a hung driver
    fails with TimeoutExpired after timeout_s. Natively the process runs in
    the checkout (cwd = root_dir), so the relative driver path and
    cwd-relative data paths resolve as they do under docker's -w <workdir>;
    the environment is inherited as-is (modules, MPI hints are the caller's
    job). Under slurm see _run_slurm_job.
    """
    if settings.runtime is Runtime.SLURM:
        return _run_slurm_job(
            settings, app, app_settings, driver_yaml, out_path, timeout_s
        )
    command = build_command(
        settings, app, app_settings, driver_yaml, output_mount=output_mount
    )
    cwd = app_settings.root_dir if settings.runtime is Runtime.NATIVE else None
    tic = _clock_line("tic")
    try:
        output = subprocess.check_output(
            command, stderr=subprocess.STDOUT, timeout=timeout_s, cwd=cwd
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        captured = tic + (exc.output or b"")
        out_path.write_bytes(captured)
        _print_output(out_path, captured)
        raise
    captured = tic + output + _clock_line("toc")
    out_path.write_bytes(captured)
    _print_output(out_path, captured)
    return DriverExecution(job_id=None)


def _run_slurm_job(
    settings: Settings,
    app: Application,
    app_settings: ApplicationSettings,
    driver_yaml: PurePosixPath,
    out_path: Path,
    timeout_s: int,
) -> DriverExecution:
    """One `sbatch --wait` job for the driver, from the combo's rendered job
    script (`<combo_id>.sbatch`, kept beside the yaml and out so the job is
    reproducible by hand); returns the job id for accounting. Slurm writes
    the job's output to out_path itself; the harness reads it back
    afterwards. The job's own
    time limit is timeout_s rounded up to minutes; the outer bound adds the
    queue allowance and, when hit, cancels the job (scancel) before raising
    TimeoutExpired — queue time must not count against the driver, but a
    job stuck forever must not hang the session."""
    script = write_job_script(settings, app_settings, driver_yaml, out_path, timeout_s)
    command = build_command(settings, app, app_settings, driver_yaml, job_script=script)
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    assert process.stdout is not None
    submission = process.stdout.readline()  # --parsable: the job id
    job_id = submission.decode("utf-8", errors="replace").strip().split(";")[0]
    try:
        returncode = process.wait(timeout=timeout_s + settings.slurm_queue_wait_s)
    except subprocess.TimeoutExpired as exc:
        if job_id:
            subprocess.run(["scancel", job_id], check=False)
        process.kill()
        raise SlurmJobTimeout(command, exc.timeout, job_id) from exc
    if not out_path.is_file():
        # The job never wrote (rejected submission, failed before start):
        # keep whatever sbatch said so the .out exists and explains itself.
        out_path.write_bytes(submission + process.stdout.read())
    output = out_path.read_bytes()
    _print_output(out_path, output)
    if returncode != 0:
        raise SlurmJobError(returncode, command, output, job_id)
    return DriverExecution(job_id=job_id or None)
