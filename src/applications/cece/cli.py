"""CECE's side of `ufs-chem-assay run`: its run-config section and the
source and build stage bodies (clone with submodules, cmake against its
modulefiles or its container build script). Input data is the shared data
stage's business (the suites declare it; `ufs-chem-assay fetch` stages it),
so CECE adds no data lines."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from applications.base import (
    ApplicationRunSection,
    CMakeRunSection,
    RunConfigView,
    Stage,
    TestPlan,
)
from cli.stages import (
    OfflineBuild,
    cmake_stage,
    ctest_plan,
    gate_lines,
    q,
    source_lines,
)
from platforms import Runtime

# What CECE's unit tests are built and run against.
_BUILD_TYPE = "CMAKE_BUILD_TYPE"
_DEBUG = f"-D{_BUILD_TYPE}=Debug"
_ESMF = "ENABLE_ESMF"
_ESMF_ON = f"-D{_ESMF}=ON"
# CECE logs each dependency's provider at configure time: `system <version>`
# for one found in the environment, `FetchContent (...)` for one it fetched.
_ESMF_GATE = "CECE dependency ESMF: system"
# GTest and RapidCheck are fetched at configure time when the test stack is on.
_TESTING = "-DCECE_BUILD_TESTING"


class CeceRunSection(CMakeRunSection):
    cmake_args: list[str] = Field(
        default_factory=lambda: ["-DCMAKE_BUILD_TYPE=Release"],
        description="Extra cmake configure arguments (native build only)",
    )
    targets: list[str] = Field(
        default_factory=lambda: ["cece_standalone_driver"],
        description=(
            "CMake targets to build; `all` also builds CECE's test stack, which "
            "tests.enabled requires and the test stage runs"
        ),
    )

    def runtime_problems(self, runtime: Runtime) -> list[str]:
        """The rules of CECE's own tests. Under docker the container build
        script owns the configure, so the flags are not this section's to
        require there; the test stack is part of `all` everywhere."""
        if not self.tests.enabled:
            return []
        problems: list[str] = []
        # As CMake reads the arguments: the last definition of each.
        missing = [
            flag
            for flag, met in (
                (_DEBUG, self.cmake_value(_BUILD_TYPE) == "Debug"),
                (_ESMF_ON, self.cmake_on(_ESMF)),
            )
            if not met
        ]
        if missing and runtime is not Runtime.DOCKER:
            problems.append(
                f"tests.enabled requires {_DEBUG} and {_ESMF_ON} in cmake_args "
                f"(missing: {', '.join(missing)}): CECE's unit tests are built and "
                "run against a Debug build with ESMF; other builds keep tests off"
            )
        if "all" not in self.targets:
            problems.append(
                "tests.enabled requires `all` among targets: CECE's test stack is "
                "part of it"
            )
        return problems


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
    build = clone / section.build_dirname
    log = build / "configure.log"
    return cmake_stage(
        config,
        section,
        module_dir=clone,
        source=clone,
        build=build,
        cmake_args=section.cmake_args,
        targets=section.targets,
        build_jobs=section.build_jobs,
        gates=[
            # Configure-log gates: the toolchain must come from the modules.
            f'grep -q "Found MPI" {q(log)} || {{ echo "configure did not find MPI" >&2; exit 1; }}',
            f'grep -qi "netcdf" {q(log)} || {{ echo "configure did not mention netCDF" >&2; exit 1; }}',
            # With ESMF on, it must be the environment's, never a fetched one.
            *gate_lines(log, [_ESMF_GATE] if section.cmake_on(_ESMF) else []),
            # The run config's gates: what this module environment claims to supply.
            *gate_lines(log, section.configure_gates),
        ],
        default_probe="which cmake ${CC:-} ${CXX:-} ${FC:-}",
        # A compute node cannot fetch: the test stack is off for the probe
        # and back on for the login-node finish; the test stage never compiles.
        offline=OfflineBuild(
            args=[*section.cmake_args, f"{_TESTING}=OFF"],
            note=(
                f"compute-node configure with {_TESTING}=OFF: GTest and RapidCheck "
                "are fetched at configure time and a compute node has no network"
            ),
            finish_args=[*section.cmake_args, f"{_TESTING}=ON"],
            finish_note=(
                f"login-node finish with {_TESTING}=ON: the test stack, built "
                "into the same build directory"
            ),
        ),
    )


def test_plan(section: ApplicationRunSection, junit: str) -> TestPlan:
    """CECE's own tests: ctest over the build directory, from the checkout
    root (cli/stages.ctest_plan)."""
    return ctest_plan(section.tests, junit, section.build_dirname)


def stage_lines(
    stage: Stage, config: RunConfigView, section: ApplicationRunSection, checkout: Path
) -> list[str]:
    """The adapter passes its own checkout (config.checkout_dir(self)): this
    module never names the adapter, so the registry is not needed here."""
    cece = _as_cece(section)
    if stage is Stage.SOURCE:
        return source_lines(
            checkout,
            cece,
            recurse_submodules=True,
            extra_checks=[
                # An uninitialized submodule shows up much later as a confusing
                # configure error — fail fast instead.
                f"if [ ! -e {q(checkout / 'extern/helm/libs')} ]; then",
                f"  echo {q(f'extern/helm submodule not initialized in {checkout}')} >&2; exit 1",
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
