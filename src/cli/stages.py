"""One shell script per stage, rendered from a RunConfig.

The scripts are the deliverable as much as their execution: they are written
under <root_dir>/scripts/ for review, `--dry-run` stops after rendering, and
the Ursa runbook (docs/ursa-runbook.md) is the same commands by hand — when
one changes, the other does. Paths and values are shell-quoted; the only
unquoted expansions are Slurm's own variables inside the batch job.
"""

from __future__ import annotations

import shlex
from enum import StrEnum, unique
from pathlib import Path

from pydantic import ConfigDict, Field

from cli.run_config import RunConfig
from models.base import StrictModel
from platforms import Runtime

# src/cli/stages.py -> <repo root>: where `uv run pytest` runs from.
HARNESS_ROOT = Path(__file__).resolve().parents[2]

_DRIVER_COMBOS = "src/tests/test_driver_combos.py"
_SHEBANG = "#!/bin/bash"


@unique
class Stage(StrEnum):
    """The stages of a run, in execution order."""

    SOURCE = "source"  # clone / update the CECE checkout
    BUILD = "build"  # configure + build the driver (and optionally the tests)
    DATA = "data"  # example data + cartopy cache
    CECE_TESTS = "cece-tests"  # ctest under the launcher
    HARNESS = "harness"  # the pytest session


# Compute stages need MPI and cores and run inside the Slurm job when one is
# configured; the earlier stages need network (clone, FetchContent,
# downloads) and stay on the login node.
COMPUTE_STAGES: tuple[Stage, ...] = (Stage.CECE_TESTS, Stage.HARNESS)


class ShellScript(StrictModel):
    """A rendered script: its name and full text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="Stage name, or 'batch' for the sbatch wrapper")
    text: str = Field(description="Complete bash source, shebang included")


def _q(value: object) -> str:
    return shlex.quote(str(value))


def _env(config: RunConfig) -> list[str]:
    """Strict mode plus the module environment (when the config names a
    modulefile): every stage runs in the same environment as the build, and
    `module purge` keeps the login shell's own modules out of it."""
    lines = ["set -euo pipefail"]
    if config.cece.modulefile is not None:
        # Non-interactive bash has no `module` function until the Lmod init
        # is sourced; the guard keeps the script runnable where Lmod is
        # absent (a local dry-run inspection, for instance).
        lines += [
            'if [ -n "${MODULESHOME:-}" ]; then source "$MODULESHOME/init/bash"; fi',
            "module purge",
            f"module use {_q(config.clone_dir)}/modulefiles",
            f"module load {_q(config.cece.modulefile)}",
            "module list",
        ]
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
    configure = " ".join(
        [
            f"cmake -S {_q(clone)} -B {_q(build)}",
            *(_q(arg) for arg in config.cece.cmake_args),
        ]
    )
    return [
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
    lines = [f"cd {_q(HARNESS_ROOT)}"]
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


def _cece_tests(config: RunConfig) -> list[str]:
    build = config.clone_dir / "build"
    return [f"{_launcher(config)}ctest --test-dir {_q(build)} --output-on-failure"]


def _harness(config: RunConfig) -> list[str]:
    harness = config.harness
    exports: list[tuple[str, str]] = [
        ("CECE_ROOT_DIR", _q(config.clone_dir)),
        ("CECE_PLATFORM", config.platform.value),
        ("CECE_RUNTIME", config.runtime.value),
    ]
    if config.runtime is Runtime.NATIVE and harness.launcher:
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
    elif config.slurm is not None:
        exports.append(("CECE_DASK_NWORKERS", '"${SLURM_CPUS_PER_TASK}"'))
    exports.append(("PATH", '"$HOME/.local/bin:$PATH"'))
    exports.append(("UV_CACHE_DIR", _q(config.uv_cache_dir)))
    if config.slurm is not None:
        exports.append(("UV_OFFLINE", "1"))  # compute nodes have no network
    exports += [(key, _q(value)) for key, value in harness.env.items()]

    pytest_args = [
        f"--suite-config={harness.suite_config}",
        f"--combo-output-root={harness.output_root}",
    ]
    if harness.clean_root:
        pytest_args.append("--combo-clean-root")
    pytest_args += harness.pytest_args
    return [
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


def _body(stage: Stage, config: RunConfig) -> list[str]:
    return [f'echo ">>> stage: {stage.value}"', *_BODIES[stage](config)]


def render_stage(stage: Stage, config: RunConfig) -> ShellScript:
    """The stand-alone script for one stage: shebang, environment, body."""
    lines = [_SHEBANG, *_env(config), *_body(stage, config)]
    return ShellScript(name=stage.value, text="\n".join(lines) + "\n")


def render_batch(config: RunConfig, stages: list[Stage]) -> ShellScript:
    """One sbatch script running the given compute stages in order: the
    Slurm header, the environment once, then each stage's body."""
    if config.slurm is None:
        raise ValueError("render_batch needs a slurm section in the run config")
    login = [stage.value for stage in stages if stage not in COMPUTE_STAGES]
    if login:
        raise ValueError(f"login-node stage(s) cannot go in the batch job: {login}")
    slurm = config.slurm
    logs = config.root_dir / "logs"
    lines = [
        _SHEBANG,
        f"#SBATCH -A {slurm.account} -q {slurm.qos} -p {slurm.partition} "
        f"-N 1 -n 1 -c {slurm.cpus} -t {slurm.time}",
        f"#SBATCH -J ufs-chem-assay -o {logs}/slurm-%j.out",
        *_env(config),
    ]
    for stage in stages:
        lines += _body(stage, config)
    return ShellScript(name="batch", text="\n".join(lines) + "\n")
