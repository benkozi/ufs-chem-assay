"""The CATChem adapter: an application the harness clones, builds and tests
but does not sweep — its stages stop at `test`, and everything a driver
sweep would ask of it is one shared refusal."""

import shlex
from pathlib import Path

import pytest
from pydantic import ValidationError

from applications.base import Application, NoDriverSuite, Stage
from applications.catchem.cli import CatchemRunSection
from applications.catchem.settings import CatchemSettings
from applications.registry import get_application
from cli.run_config import HARNESS_ROOT, RunConfig
from cli.stages import render_stage
from tests.ufs_chem_assay.run_configs import load_run_config, run_config_file

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
    assert lines[3] == "echo 'build node: login'"
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


def test_the_kokkos_gate_follows_the_cmake_args(
    app: Application, tmp_path: Path
) -> None:
    config = _config(tmp_path, cmake_args=["-DCMAKE_BUILD_TYPE=Release"])
    assert "Using system Kokkos" not in "\n".join(_lines(config, Stage.BUILD, app))
    gated = _config(tmp_path, configure_gates=["Found ESMF"])
    text = "\n".join(_lines(gated, Stage.BUILD, app))
    assert text.index("Using system Kokkos") < text.index("Found ESMF")


def test_compute_build_swaps_musica_off_and_the_login_finish_restores_it(
    app: Application, tmp_path: Path
) -> None:
    # MUSICA fetches its own dependencies at configure time: off for the
    # compute-node probe, back on (the cmake_args as written) for the
    # login-node finish that the tests need.
    probe_only = _lines(_config(tmp_path, build_node="compute"), Stage.BUILD, app)
    offline = _ARGS.replace("-DCATCHEM_BUILD_MUSICA=ON", "-DCATCHEM_BUILD_MUSICA=OFF")
    log = f"{_CLONE}/build/configure.log"
    srun = [i for i, line in enumerate(probe_only) if line.startswith("srun ")]
    assert [probe_only[i] for i in srun] == [
        f"{_BUILD_SRUN} cmake -S {_CLONE} -B {_CLONE}/build {offline} 2>&1 | tee {log}",
        f"{_BUILD_SRUN} cmake --build {_CLONE}/build --target all --parallel 8",
    ]
    note = probe_only[srun[0] - 1]
    assert "-DCATCHEM_BUILD_MUSICA=OFF" in note
    assert "no network" in note
    assert srun[1] == len(probe_only) - 1  # tests off: the probe is the stage

    tested = _lines(
        _config(tmp_path, build_node="compute", tests={"enabled": True}),
        Stage.BUILD,
        app,
    )
    assert tested[3].endswith(", then login (tests)'")
    finish = tested.index(
        f"cmake -S {_CLONE} -B {_CLONE}/build {_ARGS} 2>&1 | tee {log}"
    )
    assert finish > max(i for i, line in enumerate(tested) if line.startswith("srun "))
    assert "-DCATCHEM_BUILD_MUSICA=ON" in tested[finish - 1]  # the note
    assert tested[-1] == f"cmake --build {_CLONE}/build --target all --parallel 8"
    assert sum("grep -Eq 'Using system Kokkos'" in line for line in tested) == 2


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
            cmake_args=["-DCMAKE_BUILD_TYPE=Release"],
        )
    with pytest.raises(ValidationError, match="requires `all` among targets"):
        _config(tmp_path, tests={"enabled": True}, targets=["catchem"])
    _config(tmp_path, cmake_args=["-DCMAKE_BUILD_TYPE=Release"])  # fine, tests off
    # There is no container build for it: native or slurm only.
    local = run_config_file(
        tmp_path, "local.yaml", overrides={"applications.catchem": dict(_SECTION)}
    )
    with pytest.raises(
        ValidationError, match=r"applications\.catchem: .*not the docker runtime"
    ):
        load_run_config(local)
