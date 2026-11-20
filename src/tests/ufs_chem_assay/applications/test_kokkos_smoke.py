"""The kokkos-smoke adapter: a tiny CMake project shipped with the harness
that requires the module environment's Kokkos and kokkos-kernels (no
fallback), builds one executable, and runs it — the direct check that an
environment provides the two packages. No source stage, no driver suite."""

import shlex
from pathlib import Path

import pytest
from pydantic import ValidationError

from applications.base import Application, NoDriverSuite, Stage
from applications.kokkos_smoke.cli import PROJECT_DIR, KokkosSmokeRunSection
from applications.kokkos_smoke.settings import KokkosSmokeSettings
from applications.registry import get_application
from cli.run_config import HARNESS_ROOT, RunConfig
from cli.stages import HARNESS_VENV_OFF, render_stage
from tests.ufs_chem_assay.run_configs import load_run_config, run_config_file

# The first body line of a build or test stage script: after the shebang, strict
# mode, the banner and the lines that leave the harness venv.
_BODY = 3 + len(HARNESS_VENV_OFF)

_ROOT = Path("/scratch/ursa-run")
_CHECKOUT = _ROOT / "kokkos-smoke"
_BUILD_SRUN = (
    "srun -A epic -q debug -p u1-compute --nodes=1 --ntasks=1 "
    "--cpus-per-task=8 --time=10"
)


@pytest.fixture
def app() -> Application:
    return get_application("kokkos-smoke")


def _config(tmp_path: Path, **section: object) -> RunConfig:
    values: dict[str, object] = {"modulefile": "ufs_ursa.oneapi", **section}
    return load_run_config(
        run_config_file(
            tmp_path, overrides={"applications.kokkos-smoke": values}, root_dir=_ROOT
        )
    )


def test_constants_and_stages(app: Application) -> None:
    assert app.name == "kokkos-smoke"
    assert app.env_prefix == "KOKKOS_SMOKE_"
    assert app.checkout_dirname == "kokkos-smoke"
    assert app.stages == frozenset({Stage.BUILD, Stage.TEST})
    assert app.settings_model is KokkosSmokeSettings
    with pytest.raises(NoDriverSuite, match="the build and test stages only"):
        app.expected_output_count(None)  # type: ignore[arg-type]


def test_the_project_ships_with_the_harness() -> None:
    assert (
        PROJECT_DIR == HARNESS_ROOT / "src" / "applications" / "kokkos_smoke" / "cmake"
    )
    cmake = (PROJECT_DIR / "CMakeLists.txt").read_text()
    assert "find_package(Kokkos REQUIRED)" in cmake
    assert "find_package(KokkosKernels REQUIRED)" in cmake
    assert "FetchContent_Declare" not in cmake  # no fallback: that is the point
    assert 'message(STATUS "Found Kokkos ' in cmake
    assert 'message(STATUS "Found KokkosKernels ' in cmake
    source = (PROJECT_DIR / "smoke.cpp").read_text()
    assert "SMOKE TEST PASSED" in source
    assert "KokkosBlas::axpby" in source  # one kokkos-kernels routine


def test_the_section_needs_no_source(tmp_path: Path) -> None:
    section = KokkosSmokeRunSection()  # no git_url, no ref: nothing to clone
    assert (section.git_url, section.ref) == ("", "")
    assert section.modulepath == [Path("modulefiles")]  # the harness's own
    assert section.cmake_args == ["-DCMAKE_BUILD_TYPE=Release"]
    assert section.build_jobs == 8
    assert section.tests.ntasks == 1
    assert section.build_flags() == ["-DCMAKE_BUILD_TYPE=Release"]
    config = _config(tmp_path)
    assert config.checkout_dir(get_application("kokkos-smoke")) == _CHECKOUT


def test_build_stage_configures_the_shipped_project_and_gates_both_packages(
    app: Application, tmp_path: Path
) -> None:
    lines = render_stage(Stage.BUILD, _config(tmp_path), app).text.splitlines()
    log = f"{_CHECKOUT}/build/configure.log"
    assert lines[_BODY] == "echo 'build node: login'"
    load = lines.index("module load ufs_ursa.oneapi")
    assert lines[load - 1] == f"module use {HARNESS_ROOT}/modulefiles"
    configure = lines.index(
        f"cmake -S {PROJECT_DIR} -B {_CHECKOUT}/build -DCMAKE_BUILD_TYPE=Release "
        f"2>&1 | tee {log}"
    )
    assert lines[configure - 1] == f"mkdir -p {_CHECKOUT}/build"
    for gate in ("Found Kokkos ", "Found KokkosKernels "):
        failing = (
            f"grep -Eq {shlex.quote(gate)} {log} || "
            f"{{ echo {shlex.quote('configure gate failed: ' + gate)} >&2; exit 1; }}"
        )
        assert lines.index(failing) > configure
    assert lines[-1] == f"cmake --build {_CHECKOUT}/build --parallel 8"


def test_compute_build_changes_nothing_but_the_node(
    app: Application, tmp_path: Path
) -> None:
    # It fetches nothing: the offline probe is the whole build, tests or not.
    config = _config(tmp_path, build_node="compute", tests={"enabled": True})
    lines = render_stage(Stage.BUILD, config, app).text.splitlines()
    assert (
        lines[_BODY]
        == f"echo {shlex.quote('build node: compute (' + _BUILD_SRUN + ')')}"
    )
    srun = [line for line in lines if line.startswith("srun ")]
    assert srun == [
        (
            f"{_BUILD_SRUN} cmake -S {PROJECT_DIR} -B {_CHECKOUT}/build "
            f"-DCMAKE_BUILD_TYPE=Release 2>&1 | tee {_CHECKOUT}/build/configure.log"
        ),
        f"{_BUILD_SRUN} cmake --build {_CHECKOUT}/build --parallel 8",
    ]
    assert len([line for line in lines if "cmake -S" in line]) == 1  # no finish
    assert lines[-1] == srun[-1]


def test_test_stage_runs_the_executable_as_the_job(
    app: Application, tmp_path: Path
) -> None:
    config = _config(tmp_path, tests={"enabled": True})
    script = render_stage(Stage.TEST, config, app)
    assert script.text.splitlines()[_BODY].startswith(f"[ -f {_CHECKOUT}/build/smoke ]")
    job = script.attachments[".sbatch"].splitlines()
    assert f"#SBATCH --chdir={_CHECKOUT}" in job
    assert "#SBATCH -n 1" in job
    assert "./build/smoke || status=$?" in job
    assert "ctest" not in script.attachments[".sbatch"]
    plan = app.test_plan(config.applications["kokkos-smoke"], "unused")
    assert plan is not None
    assert plan.passed_marker == "SMOKE TEST PASSED"  # it writes no junit report


def test_it_has_no_source_or_driver_stages(app: Application, tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(ValueError, match="kokkos-smoke takes no part in the source"):
        render_stage(Stage.SOURCE, config, app)
    with pytest.raises(ValueError, match="kokkos-smoke takes no part in the data"):
        render_stage(Stage.DATA, config, app)


def test_the_adapter_owns_only_its_build_body(app: Application, tmp_path: Path) -> None:
    config = _config(tmp_path)
    section = config.applications["kokkos-smoke"]
    for stage in (Stage.SOURCE, Stage.TEST):
        with pytest.raises(ValueError, match="is shared, not the adapter's"):
            app.stage_lines(stage, config, section)


def test_it_has_no_checkout_commit_of_its_own(tmp_path: Path) -> None:
    # The project is the harness's: harness_commit is its version.
    assert KokkosSmokeSettings(root_dir=tmp_path).commit_sha() is None
    assert KokkosSmokeSettings(root_dir=tmp_path).driver_path == "./build/smoke"


def test_validation_rules(tmp_path: Path) -> None:
    local = run_config_file(
        tmp_path, "local.yaml", overrides={"applications.kokkos-smoke": {}}
    )
    with pytest.raises(
        ValidationError, match=r"applications\.kokkos-smoke: .*not the docker runtime"
    ):
        load_run_config(local)
