"""CATChem's side of `ufs-chem-assay run`: its run-config section and the
source and build stage bodies (clone with submodules — MUSICA is one —
then cmake against a module environment), and its own tests (ctest).
CATChem is built and tested, never swept: no data stage, no pytest session."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from applications.base import (
    ApplicationRunSection,
    BuildNode,
    CMakeRunSection,
    RunConfigView,
    Stage,
    TestPlan,
)
from cli.stages import (
    cmake_stage,
    ctest_plan,
    gate_lines,
    q,
    source_lines,
)
from platforms import Runtime

# What CATChem's unit tests are built and run against.
_BUILD_TYPE = "CMAKE_BUILD_TYPE"
_DEBUG = f"-D{_BUILD_TYPE}=Debug"
# With Kokkos on, the configure must have found the module environment's:
# CATChem logs this line then, and fetches its own Kokkos otherwise.
_KOKKOS = "CATCHEM_ENABLE_KOKKOS"
_KOKKOS_ON = f"-D{_KOKKOS}=ON"
_KOKKOS_GATE = "Using system Kokkos"
# MUSICA fetches its own dependencies (micm, tuv-x, MechanismConfiguration)
# at configure time: a MUSICA build is the login node's.
_MUSICA = "-DCATCHEM_BUILD_MUSICA"  # as written in cmake_args, without a value
_MUSICA_SUBMODULE = "src/external/musica/CMakeLists.txt"
# With this on, CATChem's NUOPC build takes an installed yaml-cpp instead of
# its vendored, namespaced copy. It prints nothing when it finds one, and this
# one status line when it falls back to the vendored copy.
_SYSTEM_YAML_CPP = "-DCATCHEM_USE_SYSTEM_YAML_CPP"
_YAML_CPP_FALLBACK = "yaml-cpp not found on system, using internal version"


class CatchemRunSection(CMakeRunSection):
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
    targets: list[str] = Field(
        default_factory=lambda: ["all"],
        description="CMake targets to build; `all` includes CATChem's test stack",
    )

    def runtime_problems(self, runtime: Runtime) -> list[str]:
        """CATChem's rules: no container build; Kokkos always on; MUSICA,
        which fetches at configure time, on the login node only; and its own
        tests need a Debug build with the test stack (part of `all`)."""
        problems: list[str] = []
        if runtime is Runtime.DOCKER:
            problems.append(
                "CATChem builds natively against a module environment (the native "
                "or slurm runtime), not the docker runtime: there is no container "
                "build for it"
            )
        if not self.cmake_on(_KOKKOS):
            problems.append(
                f"every CATChem build needs {_KOKKOS_ON} in cmake_args: the "
                "harness builds it against the environment's Kokkos (and MUSICA "
                "requires it)"
            )
        musica = self.cmake_on(_MUSICA.removeprefix("-D"))
        if musica and self.build_node is BuildNode.COMPUTE:
            problems.append(
                f"{_MUSICA}=ON needs the network at configure time: build it on "
                "the login node (build_node: login), or turn MUSICA off for a "
                "compute-node build"
            )
        if self.tests.enabled and self.cmake_value(_BUILD_TYPE) != "Debug":
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


def _system_yaml_cpp_lines(log: Path, cache: Path) -> list[str]:
    """After a configure with the system yaml-cpp asked for: the fallback to
    the vendored copy must not have happened, and the cache must name the
    install found (printed, so the log says which)."""
    fallback = (
        f"configure fell back to CATChem's vendored yaml-cpp ({_YAML_CPP_FALLBACK})"
    )
    missing = "configure found no system yaml-cpp (yaml-cpp_DIR in CMakeCache.txt)"
    entry = "'^yaml-cpp_DIR:[A-Z]+='"
    return [
        f"if grep -q {q(_YAML_CPP_FALLBACK)} {q(log)}; then echo {q(fallback)} >&2; exit 1; fi",
        (
            f"grep -Eq {entry} {q(cache)} && ! grep -Eq '^yaml-cpp_DIR:[A-Z]+=.*-NOTFOUND$' "
            f"{q(cache)} || {{ echo {q(missing)} >&2; exit 1; }}"
        ),
        f"grep -E '^yaml-cpp_DIR:' {q(cache)}",
    ]


def _build(
    config: RunConfigView, checkout: Path, section: CatchemRunSection
) -> list[str]:
    build = checkout / section.build_dirname
    log = build / "configure.log"
    system_yaml_cpp = section.cmake_on(_SYSTEM_YAML_CPP.removeprefix("-D"))
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
            *gate_lines(log, [_KOKKOS_GATE]),
            # The run config's gates: what this module environment claims to supply.
            *gate_lines(log, section.configure_gates),
            *(
                _system_yaml_cpp_lines(log, build / "CMakeCache.txt")
                if system_yaml_cpp
                else []
            ),
        ],
        default_probe="which cmake ${CC:-} ${CXX:-} ${FC:-}",
        # Nothing is fetched once MUSICA is off (a MUSICA build is refused on
        # a compute node), so a compute-node build is the whole build, its
        # test stack included: no offline arguments, no login-node finish.
    )


def test_plan(section: ApplicationRunSection, junit: str) -> TestPlan:
    """CATChem's own tests: ctest over the build directory. Every test is
    single-rank; the job's cores are for its OpenMP Kokkos tests."""
    return ctest_plan(section.tests, junit, section.build_dirname)


def stage_lines(
    stage: Stage, config: RunConfigView, section: ApplicationRunSection, checkout: Path
) -> list[str]:
    catchem = _as_catchem(section)
    if stage is Stage.SOURCE:
        return source_lines(
            checkout,
            catchem,
            recurse_submodules=True,
            extra_checks=[
                # An uninitialized submodule shows up much later as a confusing
                # configure error — fail fast instead.
                f"if [ ! -e {q(checkout / _MUSICA_SUBMODULE)} ]; then",
                f"  echo {q(f'musica submodule not initialized in {checkout}')} >&2; exit 1",
                "fi",
            ],
        )
    if stage is Stage.BUILD:
        return _build(config, checkout, catchem)
    raise ValueError(f"the {stage.value} stage is shared, not the adapter's")
