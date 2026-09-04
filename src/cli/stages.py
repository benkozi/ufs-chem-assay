"""One shell script per stage, rendered from a RunConfig.

The scripts are the deliverable as much as their execution: they are written
under <root_dir>/scripts/ for review, `--dry-run` stops after rendering, and
the Ursa runbook (docs/ursa-runbook.md) is the same commands by hand — when
one changes, the other does. Every stage runs where the CLI runs; under the
slurm runtime the driver and ctest stages submit their own jobs. Paths and
values are shell-quoted.
"""

from __future__ import annotations

import shlex
from enum import StrEnum, unique

from pydantic import ConfigDict, Field

from cli.run_config import HARNESS_ROOT, RunConfig
from models.base import StrictModel
from platforms import Runtime
from runner import render_job_script, sbatch_directives

_DRIVER_COMBOS = "src/tests/test_driver_combos.py"
_SHEBANG = "#!/bin/bash"


@unique
class Stage(StrEnum):
    """The stages of a run, in execution order."""

    SOURCE = "source"  # clone / update the CECE checkout
    BUILD = "build"  # configure + build the driver (and optionally the tests)
    DATA = "data"  # example data + cartopy cache
    CECE_TESTS = "cece-tests"  # ctest: one Slurm job (slurm), launcher (native), container (docker)
    HARNESS = "harness"  # the pytest session, on this node; driver calls per runtime


# Every stage runs where the CLI runs (a login node on RDHPC): the stages
# that need compiled code — driver runs, ctest — submit Slurm jobs
# themselves under the slurm runtime.


class ShellScript(StrictModel):
    """A rendered stage script: its name, full text, and any companion files
    it refers to (written beside it under <root_dir>/scripts/)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="Stage name")
    text: str = Field(description="Complete bash source, shebang included")
    companions: dict[str, str] = Field(
        default_factory=dict,
        description="Extra files the script uses, filename -> text (e.g. a rendered sbatch job)",
    )


def _q(value: object) -> str:
    return shlex.quote(str(value))


_LMOD_INIT = 'if [ -n "${MODULESHOME:-}" ]; then source "$MODULESHOME/init/bash"; fi'


def _module_block(config: RunConfig) -> list[str]:
    """Load CECE's modulefile in this shell — only where compiled code runs
    in-process (the build stage). Non-interactive bash has no `module`
    function until the Lmod init is sourced; the guard keeps the script
    runnable where Lmod is absent."""
    assert config.cece.modulefile is not None
    return [
        _LMOD_INIT,
        "module purge",
        f"module use {_q(config.clone_dir)}/modulefiles",
        f"module load {_q(config.cece.modulefile)}",
        "module list",
    ]


def _clean_python_env(config: RunConfig) -> list[str]:
    """The harness venv must never see the module environment (spack-stack
    sets PYTHONPATH to python3.11 site-packages that shadow the venv's
    numpy): undo any modules the calling shell had loaded."""
    if config.cece.modulefile is None:
        return []
    return [_LMOD_INIT, "module purge", "unset PYTHONPATH"]


def _cece_env_exports(config: RunConfig) -> list[str]:
    """What the rendered job scripts read from the environment."""
    lines = [f"export CECE_ROOT_DIR={_q(config.clone_dir)}"]
    if config.cece.modulefile is not None:
        lines.append(f"export CECE_MODULEFILE={_q(config.cece.modulefile)}")
    return lines


def _source(config: RunConfig) -> list[str]:
    clone = _q(config.clone_dir)
    ref = _q(config.cece.ref)
    lines = [
        f"if [ ! -d {clone}/.git ]; then",
        f"  git clone --recurse-submodules --branch {ref} {_q(config.cece.git_url)} {clone}",
    ]
    if config.cece.update_source:
        lines += [
            "else",
            f"  git -C {clone} fetch origin",
            f"  git -C {clone} checkout {ref}",
            f"  git -C {clone} pull --ff-only origin {ref}",
            f"  git -C {clone} submodule update --init --recursive",
        ]
    else:
        lines += ["else", f'  echo "using existing clone at {clone} as-is"']
    lines += [
        "fi",
        # An uninitialized submodule shows up much later as a confusing
        # configure error — fail fast instead.
        f"if [ ! -e {clone}/extern/helm/libs ]; then",
        f'  echo "extern/helm submodule not initialized in {clone}" >&2; exit 1',
        "fi",
        f"git -C {clone} log -1 --oneline",
    ]
    return lines


def _build(config: RunConfig) -> list[str]:
    clone = config.clone_dir
    targets = " ".join(f"--target {_q(target)}" for target in config.cece.targets)
    if config.runtime is Runtime.DOCKER:
        # CECE's own container build entrypoint owns configure + build in
        # cece/cece-dev; nothing to reinvent locally.
        return [
            f"python3 {_q(clone / 'scripts/build-and-test-container.py')} --no-test "
            f"{targets} --jobs {config.cece.build_jobs}"
        ]
    build = clone / "build"
    log = build / "configure.log"
    modules = _module_block(config) if config.cece.modulefile is not None else []
    configure = " ".join(
        [
            f"cmake -S {_q(clone)} -B {_q(build)}",
            *(_q(arg) for arg in config.cece.cmake_args),
        ]
    )
    return [
        *modules,
        "which cmake ${CC:-} ${CXX:-} ${FC:-}",
        f"mkdir -p {_q(build)}",
        f"{configure} 2>&1 | tee {_q(log)}",
        # Configure-log gates: the toolchain must come from the modules.
        f'grep -q "Found MPI" {_q(log)} || {{ echo "configure did not find MPI" >&2; exit 1; }}',
        f'grep -qi "netcdf" {_q(log)} || {{ echo "configure did not mention netCDF" >&2; exit 1; }}',
        f"cmake --build {_q(build)} {targets} --parallel {config.cece.build_jobs}",
    ]


def _data(config: RunConfig) -> list[str]:
    # Everything here runs the harness venv's interpreter: CECE's examples
    # tooling needs Python >= 3.11 (StrEnum), and after `module purge` the
    # only python3 on an RDHPC login node is the OS one.
    clone = config.clone_dir
    lines = [*_clean_python_env(config), f"cd {_q(HARNESS_ROOT)}"]
    if config.data.examples:
        lines.append(
            f"uv run --no-sync python {_q(clone / 'examples/download-example-data.py')} "
            f"--example {_q(','.join(config.data.examples))} --dst-dir {_q(clone / 'data')}"
        )
    if config.data.warm_cartopy:
        snippet = (
            "import cartopy.io.shapereader as s; "
            "[s.natural_earth(resolution=r, category='physical', name='coastline') "
            "for r in ('110m', '50m')]"
        )
        lines.append(f"uv run --no-sync python -c {_q(snippet)}")
    return lines


def _launcher(config: RunConfig) -> str:
    return f"{config.harness.launcher} " if config.harness.launcher else ""


_CECE_TESTS_JOB = "cece-tests.sbatch"
_CECE_TESTS_MINUTES = 30


def _cece_tests(config: RunConfig) -> list[str]:
    clone = config.clone_dir
    build = clone / "build"
    ctest = f"ctest --test-dir {_q(build)} --output-on-failure"
    if config.runtime is Runtime.DOCKER:
        return [
            f"python3 {_q(clone / 'scripts/build-and-test-container.py')} --no-build"
        ]
    if config.runtime is Runtime.SLURM:
        # The job script itself is a companion file (see render_stage).
        job = config.root_dir / "scripts" / _CECE_TESTS_JOB
        return [f"sbatch --wait --parsable {_q(job)}"]
    return [f"{_launcher(config)}{ctest}"]


def _cece_tests_job(config: RunConfig) -> str:
    assert config.slurm is not None, "slurm runtime needs a slurm: section"
    clone = config.clone_dir
    return render_job_script(
        job_name="ufs-chem-assay-cece-tests",
        out_path=config.root_dir / "logs" / "cece-tests-%j.out",
        root_dir=clone,
        minutes=_CECE_TESTS_MINUTES,
        directives=sbatch_directives(config.slurm.sbatch_args),
        modulefile=config.cece.modulefile,
        env=config.harness.env,
        command=f"ctest --test-dir {_q(clone / 'build')} --output-on-failure",
    )


def _harness(config: RunConfig) -> list[str]:
    harness = config.harness
    exports: list[tuple[str, str]] = [
        ("CECE_ROOT_DIR", _q(config.clone_dir)),
        ("CECE_PLATFORM", config.platform.value),
        ("CECE_RUNTIME", config.runtime.value),
    ]
    if config.runtime is Runtime.SLURM:
        assert config.slurm is not None, "slurm runtime needs a slurm: section"
        exports.append(("CECE_SBATCH_ARGS", _q(config.slurm.sbatch_args)))
        if config.cece.modulefile is not None:
            exports.append(("CECE_MODULEFILE", _q(config.cece.modulefile)))
        if harness.env:
            # The driver jobs' environment; the login-node shell never needs it.
            pairs = " ".join(f"{key}={value}" for key, value in harness.env.items())
            exports.append(("CECE_JOB_ENV", _q(pairs)))
    elif config.runtime is Runtime.NATIVE and harness.launcher:
        exports.append(("CECE_LAUNCHER", _q(harness.launcher)))
    exports.append(
        (
            "CECE_ENABLE_BASELINE_COMPARISONS",
            "true" if config.baselines.enabled else "false",
        )
    )
    if config.baselines.root_dir is not None:
        exports.append(("CECE_BASELINE_ROOT_DIR", _q(config.baselines.root_dir)))
    exports.append(("CECE_RUN_TIMEOUT_S", str(harness.run_timeout_s)))
    if harness.dask_nworkers is not None:
        exports.append(("CECE_DASK_NWORKERS", str(harness.dask_nworkers)))
    exports.append(("PATH", '"$HOME/.local/bin:$PATH"'))
    exports.append(("UV_CACHE_DIR", _q(config.uv_cache_dir)))
    if config.runtime is not Runtime.SLURM:
        # docker/native run the driver as a local process: env applies here.
        exports += [(key, _q(value)) for key, value in harness.env.items()]

    pytest_args = [
        f"--suite-config={harness.suite_config}",
        f"--combo-output-root={harness.output_root}",
    ]
    if harness.clean_root:
        pytest_args.append("--combo-clean-root")
    pytest_args += harness.pytest_args
    return [
        *_clean_python_env(config),
        *(f"export {key}={value}" for key, value in exports),
        f"cd {_q(HARNESS_ROOT)}",
        f"uv run --no-sync pytest {_DRIVER_COMBOS} "
        + " ".join(_q(arg) for arg in pytest_args),
    ]


_BODIES = {
    Stage.SOURCE: _source,
    Stage.BUILD: _build,
    Stage.DATA: _data,
    Stage.CECE_TESTS: _cece_tests,
    Stage.HARNESS: _harness,
}


def render_stage(stage: Stage, config: RunConfig) -> ShellScript:
    """The stand-alone script for one stage: shebang, strict mode, banner,
    body. Modules are loaded only by the build body and by the job script."""
    lines = [
        _SHEBANG,
        "set -euo pipefail",
        f'echo ">>> stage: {stage.value}"',
        *_BODIES[stage](config),
    ]
    companions: dict[str, str] = {}
    if stage is Stage.CECE_TESTS and config.runtime is Runtime.SLURM:
        companions[_CECE_TESTS_JOB] = _cece_tests_job(config)
    return ShellScript(
        name=stage.value, text="\n".join(lines) + "\n", companions=companions
    )
