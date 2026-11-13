"""CECE's side of `ufs-chem-assay run`: its run-config section and the
source and build stage bodies (clone with submodules, cmake against its
modulefiles or its container build script). Input data is the shared data
stage's business (the suites declare it; `ufs-chem-assay fetch` stages it),
so CECE adds no data lines."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from applications.base import ApplicationRunSection, RunConfigView, Stage
from cli.stages import module_block, q, source_lines
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


def _as_cece(section: ApplicationRunSection) -> CeceRunSection:
    assert isinstance(section, CeceRunSection), type(section)
    return section


def _build(config: RunConfigView, checkout: Path, section: CeceRunSection) -> list[str]:
    clone = checkout
    targets = " ".join(f"--target {q(target)}" for target in section.targets)
    if config.runtime is Runtime.DOCKER:
        # CECE's own container build entrypoint owns configure + build in
        # cece/cece-dev; nothing to reinvent locally.
        return [
            (
                f"python3 {q(clone / 'scripts/build-and-test-container.py')} --no-test "
                f"{targets} --jobs {section.build_jobs}"
            )
        ]
    build = clone / "build"
    log = build / "configure.log"
    modules = module_block(clone, section)
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


def stage_lines(
    stage: Stage, config: RunConfigView, section: ApplicationRunSection, checkout: Path
) -> list[str]:
    """The adapter passes its own checkout (config.checkout_dir(self)): this
    module never names the adapter, so the registry is not needed here."""
    cece = _as_cece(section)
    if stage is Stage.SOURCE:
        clone = q(checkout)
        return source_lines(
            checkout,
            cece,
            recurse_submodules=True,
            extra_checks=[
                # An uninitialized submodule shows up much later as a confusing
                # configure error — fail fast instead.
                f"if [ ! -e {clone}/extern/helm/libs ]; then",
                f'  echo "extern/helm submodule not initialized in {clone}" >&2; exit 1',
                "fi",
            ],
        )
    if stage is Stage.BUILD:
        return _build(config, checkout, cece)
    if stage is Stage.DATA:
        # Nothing beyond the shared stage's fetch: CECE's inputs are declared
        # by its suites.
        return []
    raise ValueError(f"the {stage.value} stage is shared, not the adapter's")
