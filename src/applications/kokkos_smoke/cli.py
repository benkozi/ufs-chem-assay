"""The kokkos-smoke adapter's side of `ufs-chem-assay run`: its run-config
section, its build stage — the CMake project shipped beside this module,
which requires the module environment's Kokkos and kokkos-kernels with no
fallback — and its test: the one executable that build produces. Nothing
is cloned; the adapter's "checkout" holds only the build directory."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from applications.base import (
    ApplicationRunSection,
    RunConfigView,
    Stage,
    TestPlan,
    ToolchainSection,
)
from cli.stages import cmake_stage, gate_lines
from platforms import Runtime

# The project: CMakeLists.txt and smoke.cpp, shipped with the harness.
PROJECT_DIR = Path(__file__).resolve().parent / "cmake"
# What its CMakeLists.txt prints for each package it found (the trailing
# space keeps the first from matching the second).
_GATES = ["Found Kokkos ", "Found KokkosKernels "]
# What the executable prints when its parallel_for and axpby checked out.
_PASSED = "SMOKE TEST PASSED"


class KokkosSmokeRunSection(ApplicationRunSection):
    git_url: str = Field(
        default="", description="Unused: the project ships with the harness"
    )
    ref: str = Field(
        default="", description="Unused: the project ships with the harness"
    )
    modulepath: list[Path] = Field(
        default_factory=lambda: [Path("modulefiles")],
        description=(
            "Directories `module use`d before the load: by default the harness's "
            "own modulefiles/ (there is no checkout with modulefiles of its own); "
            "add the environment's modules/Core"
        ),
    )
    cmake_args: list[str] = Field(
        default_factory=lambda: ["-DCMAKE_BUILD_TYPE=Release"],
        description="cmake configure arguments",
    )
    build_jobs: int = Field(default=8, gt=0, description="cmake --build --parallel N")

    def build_flags(self) -> list[str]:
        return list(self.cmake_args)

    def toolchain_updates(self, toolchain: ToolchainSection) -> dict[str, object]:
        # The application's configure arguments first, then the toolchain's.
        return {
            **super().toolchain_updates(toolchain),
            "cmake_args": [*self.cmake_args, *toolchain.cmake_args],
        }

    def runtime_problems(self, runtime: Runtime) -> list[str]:
        if runtime is Runtime.DOCKER:
            return [
                (
                    "the smoke project builds natively against a module "
                    "environment (the native or slurm runtime), not the docker runtime"
                )
            ]
        return []


def _as_smoke(section: ApplicationRunSection) -> KokkosSmokeRunSection:
    assert isinstance(section, KokkosSmokeRunSection), type(section)
    return section


def test_plan(section: ApplicationRunSection, junit: str) -> TestPlan:
    """The smoke test: the executable itself, from the build root. It
    writes no junit report — its last line is the verdict."""
    _ = junit
    smoke = f"{section.build_dirname}/smoke"
    return TestPlan(requires=smoke, command=f"./{smoke}", passed_marker=_PASSED)


def stage_lines(
    stage: Stage, config: RunConfigView, section: ApplicationRunSection, checkout: Path
) -> list[str]:
    smoke = _as_smoke(section)
    if stage is Stage.BUILD:
        build = checkout / smoke.build_dirname
        log = build / "configure.log"
        # It fetches nothing, so a compute-node build is the whole build:
        # no offline arguments, no login-node finish.
        return cmake_stage(
            config,
            smoke,
            module_dir=checkout,
            source=PROJECT_DIR,
            build=build,
            cmake_args=smoke.cmake_args,
            targets=[],
            build_jobs=smoke.build_jobs,
            gates=[*gate_lines(log, _GATES), *gate_lines(log, smoke.configure_gates)],
            default_probe="which cmake ${CXX:-}",
        )
    raise ValueError(f"the {stage.value} stage is shared, not the adapter's")
