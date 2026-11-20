"""The CATChem adapter: an application the harness clones, builds and tests
but does not sweep — its stages stop at `test`, and everything a driver
sweep would ask of it is one shared refusal."""

import shlex
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from applications.base import Application, NoDriverSuite, Stage
from applications.catchem.cli import CatchemRunSection
from applications.catchem.settings import CatchemSettings
from applications.registry import get_application
from cli.run_config import HARNESS_ROOT, RunConfig
from cli.stages import HARNESS_VENV_OFF, render_stage
from tests.ufs_chem_assay.run_configs import load_run_config, run_config_file

# The first body line of a build or test stage script: after the shebang, strict
# mode, the banner and the lines that leave the harness venv.
_BODY = 3 + len(HARNESS_VENV_OFF)

_ROOT = Path("/scratch/ursa-run")
_CLONE = _ROOT / "CATChem"
_SECTION: dict[str, object] = {
    "git_url": "git@github.com:ufs-community/CATChem.git",
    "ref": "develop",
    "modulefile": "ufs_ursa.oneapi",
    "modulepath": ["modulefiles", "/envs/stack/modules/Core"],
}
_BUILD_SRUN = (
    "srun -A epic -q debug -p u1-compute --nodes=1 --ntasks=1 "
    "--cpus-per-task=8 --time=10"
)
_ARGS = (
    "-DCMAKE_BUILD_TYPE=Debug -DCATCHEM_ENABLE_KOKKOS=ON "
    "-DCATCHEM_BUILD_MUSICA=ON -DCATCHEM_BUILD_NUOPC=ON"
)


@pytest.fixture
def app() -> Application:
    return get_application("catchem")


def _config(tmp_path: Path, **section: object) -> RunConfig:
    return load_run_config(
        run_config_file(
            tmp_path,
            overrides={"applications.catchem": {**_SECTION, **section}},
            root_dir=_ROOT,
        )
    )


def _lines(config: RunConfig, stage: Stage, app: Application) -> list[str]:
    return render_stage(stage, config, app).text.splitlines()


def test_constants_and_stages(app: Application) -> None:
    assert app.name == "catchem"
    assert app.env_prefix == "CATCHEM_"
    assert app.checkout_dirname == "CATChem"
    assert app.stages == frozenset({Stage.SOURCE, Stage.BUILD, Stage.TEST})
    assert app.settings_model is CatchemSettings
    assert app.run_section_model is CatchemRunSection
    assert CatchemSettings.env_prefix == "CATCHEM_"
    assert CatchemSettings().docker_image == ""  # no image: it builds natively


def test_everything_a_driver_sweep_asks_is_one_refusal(
    app: Application, tmp_path: Path
) -> None:
    message = (
        "catchem has no driver suite: it takes part in the source, build and "
        "test stages only"
    )
    calls = [
        lambda: app.dimensions(None, None),  # type: ignore[arg-type]
        lambda: app.build_config(None, "o", tmp_path),  # type: ignore[arg-type]
        lambda: app.effective_parameters(None, None),  # type: ignore[arg-type]
        lambda: app.expected_output_count(None),  # type: ignore[arg-type]
        lambda: app.expected_output_filenames(None),  # type: ignore[arg-type]
        lambda: app.output_variable_names(None),  # type: ignore[arg-type]
        lambda: app.selector_matches(None, None),  # type: ignore[arg-type]
    ]
    for call in calls:
        with pytest.raises(NoDriverSuite) as refused:
            call()
        assert str(refused.value) == message
    # The placeholder models refuse too: nothing can be loaded as its
    # driver configuration or its suite.
    with pytest.raises(NoDriverSuite):
        app.config_model.from_yaml(tmp_path / "x.yaml")
    with pytest.raises(NoDriverSuite, match="has no driver configuration"):
        app.config_model().to_yaml(tmp_path / "x.yaml")
    with pytest.raises(ValidationError, match="has no driver suite"):
        app.suite_model.model_validate({"name": "s", "application": "catchem"})
    assert app.default_suite == ""


def test_section_defaults() -> None:
    section = CatchemRunSection(git_url="u", ref="r")
    assert section.cmake_args == [
        "-DCMAKE_BUILD_TYPE=Debug",  # what its tests are built against
        "-DCATCHEM_ENABLE_KOKKOS=ON",
        "-DCATCHEM_BUILD_MUSICA=ON",
        "-DCATCHEM_BUILD_NUOPC=ON",
    ]
    assert section.targets == ["all"]
    assert section.build_jobs == 8
    assert section.tests.ntasks == 1  # every CATChem test is single-rank
    assert section.build_flags() == section.cmake_args


def test_source_stage_clones_with_submodules_and_guards_musica(
    app: Application, tmp_path: Path
) -> None:
    lines = _lines(_config(tmp_path), Stage.SOURCE, app)
    assert (
        "  git clone --recurse-submodules --branch develop "
        f"git@github.com:ufs-community/CATChem.git {_CLONE}"
    ) in lines
    # MUSICA is a submodule: uninitialized, it is a confusing configure error.
    assert f"if [ ! -e {_CLONE}/src/external/musica/CMakeLists.txt ]; then" in lines
    assert any("musica submodule not initialized" in line for line in lines)
    assert lines[-1] == f"git -C {_CLONE} log -1 --oneline"


def test_login_build_configures_gates_on_system_kokkos_and_builds(
    app: Application, tmp_path: Path
) -> None:
    lines = _lines(_config(tmp_path), Stage.BUILD, app)
    log = f"{_CLONE}/build/configure.log"
    assert lines[_BODY] == "echo 'build node: login'"
    load = lines.index("module load ufs_ursa.oneapi")
    assert lines[load - 2 : load] == [
        f"module use {HARNESS_ROOT}/modulefiles",
        "module use /envs/stack/modules/Core",
    ]
    configure = lines.index(
        f"cmake -S {_CLONE} -B {_CLONE}/build {_ARGS} 2>&1 | tee {log}"
    )
    gate = "Using system Kokkos"
    failing = (
        f"grep -Eq {shlex.quote(gate)} {log} || "
        f"{{ echo {shlex.quote('configure gate failed: ' + gate)} >&2; exit 1; }}"
    )
    assert load < configure < lines.index(failing)
    assert lines[-1] == f"cmake --build {_CLONE}/build --target all --parallel 8"
    assert "CECE" not in "\n".join(lines)


def test_the_kokkos_gate_is_always_there(app: Application, tmp_path: Path) -> None:
    # Kokkos is always on (a rule below), so its gate is always rendered,
    # before the run config's own.
    gated = _config(tmp_path, configure_gates=["Found ESMF"])
    text = "\n".join(_lines(gated, Stage.BUILD, app))
    assert text.index("Using system Kokkos") < text.index("Found ESMF")


def test_every_build_has_kokkos_on(tmp_path: Path) -> None:
    with pytest.raises(
        ValidationError,
        match=r"applications\.catchem: every CATChem build needs "
        "-DCATCHEM_ENABLE_KOKKOS=ON",
    ):
        _config(tmp_path, cmake_args=["-DCMAKE_BUILD_TYPE=Release"])
    # The last of two definitions is the one CMake keeps.
    with pytest.raises(ValidationError, match="needs -DCATCHEM_ENABLE_KOKKOS=ON"):
        _config(
            tmp_path,
            cmake_args=["-DCATCHEM_ENABLE_KOKKOS=ON", "-DCATCHEM_ENABLE_KOKKOS=OFF"],
        )


def test_musica_builds_on_the_login_node_only(tmp_path: Path) -> None:
    # MUSICA fetches micm, TUV-x and their configuration at configure time,
    # and a compute node has no network.
    with pytest.raises(
        ValidationError,
        match=r"applications\.catchem: -DCATCHEM_BUILD_MUSICA=ON needs the "
        "network at configure time: build it on the login node",
    ):
        _config(tmp_path, build_node="compute")
    # A variant is judged as it builds, and named.
    builds = {"compute": {"build_node": "compute"}}
    with pytest.raises(
        ValidationError,
        match=r"applications\.catchem\.builds\.compute: -DCATCHEM_BUILD_MUSICA=ON",
    ):
        _config(tmp_path, builds=builds)


def test_a_compute_build_is_the_whole_build(app: Application, tmp_path: Path) -> None:
    # MUSICA off, nothing is fetched: configure and compile on the compute
    # node, the test stack included, and no login-node finish.
    offline = _ARGS.replace("-DCATCHEM_BUILD_MUSICA=ON", "-DCATCHEM_BUILD_MUSICA=OFF")
    config = _config(
        tmp_path,
        build_node="compute",
        cmake_args=offline.split(),
        tests={"enabled": True},
    )
    lines = _lines(config, Stage.BUILD, app)
    log = f"{_CLONE}/build/configure.log"
    srun = [line for line in lines if line.startswith("srun ")]
    assert srun == [
        f"{_BUILD_SRUN} cmake -S {_CLONE} -B {_CLONE}/build {offline} 2>&1 | tee {log}",
        f"{_BUILD_SRUN} cmake --build {_CLONE}/build --target all --parallel 8",
    ]
    assert (
        lines[_BODY]
        == f"echo {shlex.quote('build node: compute (' + _BUILD_SRUN + ')')}"
    )
    assert lines[-1] == srun[-1]  # the compute-node compile is the last line
    assert not any("login-node finish" in line for line in lines)


_FALLBACK = "yaml-cpp not found on system, using internal version"


def test_a_system_yaml_cpp_is_checked_after_each_configure(
    app: Application, tmp_path: Path
) -> None:
    # CATChem prints nothing when it finds a system yaml-cpp and one status
    # line when it falls back to its vendored copy, so the check is the
    # fallback line absent and yaml-cpp_DIR found in the cache.
    args = [*_ARGS.split(), "-DCATCHEM_USE_SYSTEM_YAML_CPP=ON"]
    text = "\n".join(_lines(_config(tmp_path, cmake_args=args), Stage.BUILD, app))
    assert text.count(_FALLBACK) == 2  # the check and its message
    assert text.count(f"{_CLONE}/build/CMakeCache.txt") == 3
    assert text.index(f"cmake -S {_CLONE}") < text.index(_FALLBACK)
    assert text.index(_FALLBACK) < text.index(f"cmake --build {_CLONE}/build")
    # Without the flag, CATChem's vendored copy is its own business.
    plain = "\n".join(_lines(_config(tmp_path), Stage.BUILD, app))
    assert _FALLBACK not in plain
    assert "CMakeCache.txt" not in plain


@pytest.mark.parametrize(
    ("log", "cache", "ok"),
    [
        (
            "-- Using system Kokkos 5.1.1\n",
            "yaml-cpp_DIR:PATH=/env/yaml-cpp-0.8.0/lib64/cmake/yaml-cpp\n",
            True,
        ),
        (f"-- {_FALLBACK}\n", "yaml-cpp_DIR:PATH=yaml-cpp_DIR-NOTFOUND\n", False),
        ("", "yaml-cpp_DIR:PATH=yaml-cpp_DIR-NOTFOUND\n", False),
        ("", "CMAKE_BUILD_TYPE:STRING=Debug\n", False),  # find_package never ran
    ],
)
def test_the_yaml_cpp_check_runs_in_bash(
    app: Application, tmp_path: Path, log: str, cache: str, ok: bool
) -> None:
    args = [*_ARGS.split(), "-DCATCHEM_USE_SYSTEM_YAML_CPP=ON"]
    lines = _lines(_config(tmp_path, cmake_args=args), Stage.BUILD, app)
    first = next(i for i, line in enumerate(lines) if _FALLBACK in line)
    check = lines[first : first + 3]
    build = tmp_path / "build"
    build.mkdir()
    (build / "configure.log").write_text(log)
    (build / "CMakeCache.txt").write_text(cache)
    script = "\n".join(["set -euo pipefail", *check]).replace(
        f"{_CLONE}/build", str(build)
    )
    done = subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert (done.returncode == 0) is ok, done.stderr
    if ok:
        assert done.stdout == cache  # the log names the install that was used
    else:
        assert "yaml-cpp" in done.stderr


def test_test_stage_is_one_single_rank_ctest_job(
    app: Application, tmp_path: Path
) -> None:
    config = _config(tmp_path, tests={"enabled": True})
    script = render_stage(Stage.TEST, config, app)
    assert script.name == "test-catchem"
    assert script.text.splitlines()[-1] == (
        f"uv run --no-sync ufs-chem-assay submit-tests --root-dir {_ROOT} "
        "--application catchem"
    )
    job = script.attachments[".sbatch"].splitlines()
    assert "#SBATCH --job-name=ufs-chem-assay-tests-catchem" in job
    assert f"#SBATCH --chdir={_CLONE}" in job
    assert "#SBATCH -n 1" in job
    assert "#SBATCH -c 8" in job  # its OpenMP Kokkos tests
    assert (
        "ctest --test-dir build --output-on-failure --output-junit "
        f'{_ROOT}/tests/catchem/tests-"$SLURM_JOB_ID".xml || status=$?'
    ) in job
    assert "module load ufs_ursa.oneapi" in job


def test_it_takes_no_part_in_the_driver_stages(
    app: Application, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    for stage in (Stage.DATA, Stage.HARNESS, Stage.RESUME):
        with pytest.raises(ValueError, match=f"catchem takes no part in the {stage}"):
            render_stage(stage, config, app, config.resolved_sessions()[0])


def test_the_adapter_owns_only_its_source_and_build_bodies(
    app: Application, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    section = config.applications["catchem"]
    for stage in (Stage.TEST, Stage.DATA, Stage.HARNESS):
        with pytest.raises(ValueError, match="is shared, not the adapter's"):
            app.stage_lines(stage, config, section)


def test_validation_rules(tmp_path: Path) -> None:
    # Its unit tests are built against a Debug build, and the test stack
    # is part of `all`.
    with pytest.raises(ValidationError, match="missing: -DCMAKE_BUILD_TYPE=Debug"):
        _config(
            tmp_path,
            tests={"enabled": True},
            cmake_args=["-DCMAKE_BUILD_TYPE=Release", "-DCATCHEM_ENABLE_KOKKOS=ON"],
        )
    with pytest.raises(ValidationError, match="requires `all` among targets"):
        _config(tmp_path, tests={"enabled": True}, targets=["catchem"])
    _config(  # fine, tests off
        tmp_path,
        cmake_args=["-DCMAKE_BUILD_TYPE=Release", "-DCATCHEM_ENABLE_KOKKOS=ON"],
    )
    # There is no container build for it: native or slurm only.
    local = run_config_file(
        tmp_path, "local.yaml", overrides={"applications.catchem": dict(_SECTION)}
    )
    with pytest.raises(
        ValidationError, match=r"applications\.catchem: .*not the docker runtime"
    ):
        load_run_config(local)
