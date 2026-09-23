"""CECE's side of `ufs-chem-assay run`: its run-config section and the
source, build, and data stage bodies (clone with submodules, cmake against
its modulefiles or its container build script, example data through its
download entrypoint)."""

from __future__ import annotations

from pydantic import Field

from applications.base import ApplicationRunSection
from cli.run_config import RunConfig
from cli.stages import LMOD_INIT, Stage, q
from platforms import Runtime


class CeceRunSection(ApplicationRunSection):
    cmake_args: list[str] = Field(
        default_factory=lambda: ["-DCMAKE_BUILD_TYPE=Release"],
        description="Extra cmake configure arguments (native build only)",
    )
    build_jobs: int = Field(default=8, gt=0, description="cmake --build --parallel N")
    targets: list[str] = Field(
        default_factory=lambda: ["cece_standalone_driver"],
        description=(
            "CMake targets to build; `all` also builds CECE's test stack "
            "(running it is issue #9)"
        ),
    )
    examples: list[str] = Field(
        default_factory=lambda: ["ex3"],
        description="Example ids whose input data download-example-data.py stages",
    )


def _as_cece(section: ApplicationRunSection) -> CeceRunSection:
    assert isinstance(section, CeceRunSection), type(section)
    return section


def _module_block(config: RunConfig, section: CeceRunSection) -> list[str]:
    """Load CECE's modulefile in this shell — only where compiled code runs
    in-process (the build stage). Non-interactive bash has no `module`
    function until the Lmod init is sourced; the guard keeps the script
    runnable where Lmod is absent."""
    assert section.modulefile is not None
    return [
        LMOD_INIT,
        "module purge",
        f"module use {q(config.checkout_dir('cece'))}/modulefiles",
        f"module load {q(section.modulefile)}",
        "module list",
    ]


def _source(config: RunConfig, section: CeceRunSection) -> list[str]:
    clone = q(config.checkout_dir("cece"))
    ref = q(section.ref)
    lines = [
        f"if [ ! -d {clone}/.git ]; then",
        f"  git clone --recurse-submodules --branch {ref} {q(section.git_url)} {clone}",
    ]
    if section.update_source:
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


def _build(config: RunConfig, section: CeceRunSection) -> list[str]:
    clone = config.checkout_dir("cece")
    targets = " ".join(f"--target {q(target)}" for target in section.targets)
    if config.runtime is Runtime.DOCKER:
        # CECE's own container build entrypoint owns configure + build in
        # cece/cece-dev; nothing to reinvent locally.
        return [
            f"python3 {q(clone / 'scripts/build-and-test-container.py')} --no-test "
            f"{targets} --jobs {section.build_jobs}"
        ]
    build = clone / "build"
    log = build / "configure.log"
    modules = _module_block(config, section) if section.modulefile is not None else []
    configure = " ".join(
        [
            f"cmake -S {q(clone)} -B {q(build)}",
            *(q(arg) for arg in section.cmake_args),
        ]
    )
    return [
        *modules,
        "which cmake ${CC:-} ${CXX:-} ${FC:-}",
        f"mkdir -p {q(build)}",
        f"{configure} 2>&1 | tee {q(log)}",
        # Configure-log gates: the toolchain must come from the modules.
        f'grep -q "Found MPI" {q(log)} || {{ echo "configure did not find MPI" >&2; exit 1; }}',
        f'grep -qi "netcdf" {q(log)} || {{ echo "configure did not mention netCDF" >&2; exit 1; }}',
        f"cmake --build {q(build)} {targets} --parallel {section.build_jobs}",
    ]


def _data(config: RunConfig, section: CeceRunSection) -> list[str]:
    # CECE's examples tooling needs Python >= 3.11 (StrEnum): the harness
    # venv's interpreter, from the harness checkout (the stage cd's there).
    if not section.examples:
        return []
    clone = config.checkout_dir("cece")
    return [
        f"uv run --no-sync python {q(clone / 'examples/download-example-data.py')} "
        f"--example {q(','.join(section.examples))} --dst-dir {q(clone / 'data')}"
    ]


def stage_lines(
    stage: Stage, config: RunConfig, section: ApplicationRunSection
) -> list[str]:
    cece = _as_cece(section)
    if stage is Stage.SOURCE:
        return _source(config, cece)
    if stage is Stage.BUILD:
        return _build(config, cece)
    if stage is Stage.DATA:
        return _data(config, cece)
    raise ValueError(f"the {stage.value} stage is shared, not the adapter's")
