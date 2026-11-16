"""CATChem's side of `ufs-chem-assay run`: its run-config section and the
source and build stage bodies (clone with submodules — MUSICA is one —
then cmake against a module environment), and its own tests (ctest).
CATChem is built and tested, never swept: no data stage, no pytest session."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from applications.base import ApplicationRunSection, RunConfigView, Stage, TestPlan
from cli.stages import (
    OfflineBuild,
    cmake_stage,
    ctest_plan,
    gate_lines,
    q,
    source_lines,
)
from platforms import Runtime

# What CATChem's unit tests are built and run against.
_DEBUG = "-DCMAKE_BUILD_TYPE=Debug"
# With Kokkos on, the configure must have found the module environment's:
# CATChem logs this line then, and fetches its own Kokkos otherwise.
_KOKKOS_ON = "-DCATCHEM_ENABLE_KOKKOS=ON"
_KOKKOS_GATE = "Using system Kokkos"
# MUSICA fetches its own dependencies (micm, tuv-x, MechanismConfiguration)
# at configure time.
_MUSICA = "-DCATCHEM_BUILD_MUSICA"
_MUSICA_SUBMODULE = "src/external/musica/CMakeLists.txt"


class CatchemRunSection(ApplicationRunSection):
    cmake_args: list[str] = Field(
        default_factory=lambda: [
            _DEBUG,
            _KOKKOS_ON,
            f"{_MUSICA}=ON",
            "-DCATCHEM_BUILD_NUOPC=ON",
        ],
        description=(
            "cmake configure arguments: by default a Debug build (what the unit "
            "tests need) with Kokkos, MUSICA and NUOPC on"
        ),
    )
    build_jobs: int = Field(default=8, gt=0, description="cmake --build --parallel N")
    targets: list[str] = Field(
        default_factory=lambda: ["all"],
        description="CMake targets to build; `all` includes CATChem's test stack",
    )

    def build_flags(self) -> list[str]:
        return list(self.cmake_args)

    def runtime_problems(self, runtime: Runtime) -> list[str]:
        """CATChem's rules: no container build, and its own tests need a
        Debug build with the test stack (part of `all`)."""
        problems: list[str] = []
        if runtime is Runtime.DOCKER:
            problems.append(
                "CATChem builds natively against a module environment (the native "
                "or slurm runtime), not the docker runtime: there is no container "
                "build for it"
            )
        if self.tests.enabled and _DEBUG not in self.cmake_args:
            problems.append(
                f"tests.enabled requires {_DEBUG} in cmake_args (missing: {_DEBUG}): "
                "CATChem's unit tests are built and run against a Debug build"
            )
        if self.tests.enabled and "all" not in self.targets:
            problems.append(
                "tests.enabled requires `all` among targets: CATChem's test stack "
                "is part of it"
            )
        return problems


def _as_catchem(section: ApplicationRunSection) -> CatchemRunSection:
    assert isinstance(section, CatchemRunSection), type(section)
    return section


def _build(
    config: RunConfigView, checkout: Path, section: CatchemRunSection
) -> list[str]:
    build = checkout / "build"
    log = build / "configure.log"
    offline_args = [
        arg.replace(f"{_MUSICA}=ON", f"{_MUSICA}=OFF") for arg in section.cmake_args
    ]
    return cmake_stage(
        config,
        section,
        module_dir=checkout,
        source=checkout,
        build=build,
        cmake_args=section.cmake_args,
        targets=section.targets,
        build_jobs=section.build_jobs,
        gates=[
            # With Kokkos on, it must be the environment's, never a fetched one.
            *gate_lines(
                log, [_KOKKOS_GATE] if _KOKKOS_ON in section.cmake_args else []
            ),
            # The run config's gates: what this module environment claims to supply.
            *gate_lines(log, section.configure_gates),
        ],
        default_probe="which cmake ${CC:-} ${CXX:-} ${FC:-}",
        # A compute node cannot fetch: MUSICA is off for the probe (the
        # tests it gates do not exist in that build) and back on — the
        # cmake_args as written — for the login-node finish.
        offline=(
            None
            if offline_args == section.cmake_args
            else OfflineBuild(
                args=offline_args,
                note=(
                    f"compute-node configure with {_MUSICA}=OFF: MUSICA fetches its "
                    "dependencies at configure time and a compute node has no network"
                ),
                finish_args=list(section.cmake_args),
                finish_note=(
                    f"login-node finish with {_MUSICA}=ON: the cmake_args as "
                    "written, built into the same build directory"
                ),
            )
        ),
    )


def test_plan(section: ApplicationRunSection, junit: str) -> TestPlan:
    """CATChem's own tests: ctest over the build directory. Every test is
    single-rank; the job's cores are for its OpenMP Kokkos tests."""
    return ctest_plan(section.tests, junit)


def stage_lines(
    stage: Stage, config: RunConfigView, section: ApplicationRunSection, checkout: Path
) -> list[str]:
    catchem = _as_catchem(section)
    if stage is Stage.SOURCE:
        clone = q(checkout)
        return source_lines(
            checkout,
            catchem,
            recurse_submodules=True,
            extra_checks=[
                # An uninitialized submodule shows up much later as a confusing
                # configure error — fail fast instead.
                f"if [ ! -e {clone}/{_MUSICA_SUBMODULE} ]; then",
                f'  echo "musica submodule not initialized in {clone}" >&2; exit 1',
                "fi",
            ],
        )
    if stage is Stage.BUILD:
        return _build(config, checkout, catchem)
    raise ValueError(f"the {stage.value} stage is shared, not the adapter's")
