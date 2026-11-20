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
    RunConfigView,
    Stage,
    TestPlan,
    ToolchainSection,
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
_DEBUG = "-DCMAKE_BUILD_TYPE=Debug"
# With Kokkos on, the configure must have found the module environment's:
# CATChem logs this line then, and fetches its own Kokkos otherwise.
_KOKKOS_ON = "-DCATCHEM_ENABLE_KOKKOS=ON"
_KOKKOS_GATE = "Using system Kokkos"
# MUSICA fetches its own dependencies (micm, tuv-x, MechanismConfiguration)
# at configure time: a MUSICA build is the login node's.
_MUSICA = "-DCATCHEM_BUILD_MUSICA"
_MUSICA_SUBMODULE = "src/external/musica/CMakeLists.txt"
# With this on, CATChem's NUOPC build takes an installed yaml-cpp instead of
# its vendored, namespaced copy. It prints nothing when it finds one, and this
# one status line when it falls back to the vendored copy.
_SYSTEM_YAML_CPP = "-DCATCHEM_USE_SYSTEM_YAML_CPP"
_YAML_CPP_FALLBACK = "yaml-cpp not found on system, using internal version"


def _flag_on(cmake_args: list[str], flag: str) -> bool:
    """Whether `flag` (-DNAME) is ON in `cmake_args`: the last definition is
    the one CMake keeps."""
    values = [arg.split("=", 1)[1] for arg in cmake_args if arg.startswith(f"{flag}=")]
    return bool(values) and values[-1].upper() in {"ON", "TRUE", "YES", "1"}


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

    def toolchain_updates(self, toolchain: ToolchainSection) -> dict[str, object]:
        # The application's configure arguments first, then the toolchain's.
        return {
            **super().toolchain_updates(toolchain),
            "cmake_args": [*self.cmake_args, *toolchain.cmake_args],
        }

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
        if not _flag_on(self.cmake_args, _KOKKOS_ON.split("=")[0]):
            problems.append(
                f"every CATChem build needs {_KOKKOS_ON} in cmake_args: the "
                "harness builds it against the environment's Kokkos (and MUSICA "
                "requires it)"
            )
        if _flag_on(self.cmake_args, _MUSICA) and self.build_node is BuildNode.COMPUTE:
            problems.append(
                f"{_MUSICA}=ON needs the network at configure time: build it on "
                "the login node (build_node: login), or turn MUSICA off for a "
                "compute-node build"
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
    system_yaml_cpp = _flag_on(section.cmake_args, _SYSTEM_YAML_CPP)
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
