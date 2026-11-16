"""CECE's side of `ufs-chem-assay run`: its run-config section and the
source and build stage bodies (clone with submodules, cmake against its
modulefiles or its container build script). Input data is the shared data
stage's business (the suites declare it; `ufs-chem-assay fetch` stages it),
so CECE adds no data lines."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator

from applications.base import ApplicationRunSection, RunConfigView, Stage
from cli.stages import gate_lines, module_block, q, source_lines, toolchain_probe
from platforms import Runtime

# What CECE's unit tests are built and run against.
_DEBUG = "-DCMAKE_BUILD_TYPE=Debug"
_ESMF_ON = "-DENABLE_ESMF=ON"
# CECE logs each dependency's provider at configure time: `system <version>`
# for one found in the environment, `FetchContent (...)` for one it fetched.
_ESMF_GATE = "CECE dependency ESMF: system"
# GTest and RapidCheck are fetched at configure time when the test stack is on.
_TESTING = "-DCECE_BUILD_TESTING"


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

    @model_validator(mode="after")
    def _tests_need_a_debug_build_with_esmf(self) -> CeceRunSection:
        if not self.tests.enabled:
            return self
        missing = [flag for flag in (_DEBUG, _ESMF_ON) if flag not in self.cmake_args]
        if missing:
            raise ValueError(
                f"tests.enabled requires {_DEBUG} and {_ESMF_ON} in cmake_args "
                f"(missing: {', '.join(missing)}): CECE's unit tests are built and "
                "run against a Debug build with ESMF; other builds keep tests off"
            )
        return self


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
    launcher = config.build_launcher(section, cpus=section.build_jobs)
    prefix = "".join(f"{q(part)} " for part in launcher)

    def configure(*extra: str) -> list[str]:
        """One configure, teed to the log, and every gate on that log."""
        command = " ".join(
            [
                f"cmake -S {q(clone)} -B {q(build)}",
                *(q(arg) for arg in (*section.cmake_args, *extra)),
            ]
        )
        return [
            f"{command} 2>&1 | tee {q(log)}",
            # Configure-log gates: the toolchain must come from the modules.
            f'grep -q "Found MPI" {q(log)} || {{ echo "configure did not find MPI" >&2; exit 1; }}',
            f'grep -qi "netcdf" {q(log)} || {{ echo "configure did not mention netCDF" >&2; exit 1; }}',
            # With ESMF on, it must be the environment's, never a fetched one.
            *gate_lines(log, [_ESMF_GATE] if _ESMF_ON in section.cmake_args else []),
            # The run config's gates: what this module environment claims to supply.
            *gate_lines(log, section.configure_gates),
        ]

    compile_targets = (
        f"cmake --build {q(build)} {targets} --parallel {section.build_jobs}"
    )
    setup = [
        *module_block(config, clone, section),
        toolchain_probe(section, "which cmake ${CC:-} ${CXX:-} ${FC:-}"),
        f"mkdir -p {q(build)}",
    ]
    if not launcher:
        # The login node (or an allocation's shell): one configure with the
        # cmake_args as written, and the build.
        return [
            f"echo {q('build node: login')}",
            *setup,
            *configure(),
            compile_targets,
        ]
    # A compute node, as an offline compile probe: the configure and the
    # build are two srun steps (two allocations, never one spanning both)
    # in the module environment this shell loaded, with the test stack off —
    # it is fetched at configure time. With tests enabled the login node
    # then finishes the same build directory with the test stack on,
    # reusing the compute-built objects; the test stage never compiles.
    finish = section.tests.enabled
    where = f"build node: compute ({' '.join(launcher)})"
    probe_configure, *probe_gates = configure(f"{_TESTING}=OFF")
    lines = [
        f"echo {q(where + (', then login (tests)' if finish else ''))}",
        *setup,
        "echo "
        + q(
            f"compute-node configure with {_TESTING}=OFF: GTest and RapidCheck "
            "are fetched at configure time and a compute node has no network"
        ),
        f"{prefix}{probe_configure}",
        *probe_gates,
        f"{prefix}{compile_targets}",
    ]
    if finish:
        lines += [
            "echo "
            + q(
                f"login-node finish with {_TESTING}=ON: the test stack, built "
                "into the same build directory"
            ),
            *configure(f"{_TESTING}=ON"),
            compile_targets,
        ]
    return lines


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
