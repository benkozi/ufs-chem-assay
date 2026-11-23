"""Toolchains: the compiler suite as one named bundle of a run config —
the module environment, the configure flags, the MPI launcher, the tests'
environment — overlaid on every application section, and a path segment of
its own so two toolchains never share a checkout, a build, or a result."""

import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from pytest_mock import MockerFixture

from applications.base import BuildNode, MpiLauncher, Stage, ToolchainSection
from applications.registry import get_application
from benchmarks import read_test_benchmarks_csv
from cli.main import main
from cli.run_config import HARNESS_ROOT, RunConfig
from cli.run_report import newest_log, summarize_session
from cli.selectors import build_configs, toolchain_configs
from cli.sessions import configured_sessions, configured_tests, job_script_for_tests
from cli.stages import render_stage
from jobs import JobRef, read_test_ledger
from logs import LOGGER_NAME
from tests.ufs_chem_assay.run_configs import (
    TEMPLATES_DIR,
    TOOLCHAIN_OVERRIDES,
    TOOLCHAINS,
    load_run_config,
    run_config_file,
)

_CECE = get_application("cece")
_CLI_LOGGER = f"{LOGGER_NAME}.cli"
_ROOT = Path("/scratch/ursa-run")
_ARBORX = "AXIS: ArborX .* found via find_package"


def _config(tmp_path: Path, **overrides: object) -> RunConfig:
    return load_run_config(
        run_config_file(
            tmp_path, overrides={**TOOLCHAIN_OVERRIDES, **overrides}, root_dir=_ROOT
        )
    )


def test_a_config_without_toolchains_is_one_unnamed_build(tmp_path: Path) -> None:
    config = load_run_config(run_config_file(tmp_path, root_dir=_ROOT))
    assert config.toolchains == {}
    assert config.selected_toolchains == []
    assert config.toolchain is None
    assert toolchain_configs(config, None) == [config]
    assert config.mpi_configure_args() == []
    with pytest.raises(ValueError, match="the run config has no toolchains: map"):
        toolchain_configs(config, ["oneapi"])


def test_toolchain_section_defaults() -> None:
    section = ToolchainSection(modulefile="ufs_ursa.oneapi")
    assert section.model_dump() == {
        "modulefile": "ufs_ursa.oneapi",
        "modulepath": [],
        "module_env": {},
        "toolchain_tools": [],
        "cmake_args": [],
        "mpi_launcher": MpiLauncher.MPIEXEC,  # FindMPI's own default
        "mpiexec_preflags": None,
        "tests_env": {},
        "configure_gates": [],
    }
    for field in ToolchainSection.model_fields.values():
        assert field.description, field
    with pytest.raises(ValidationError, match="module_env"):
        ToolchainSection(modulefile="m", module_env={"not a name": "x"})
    with pytest.raises(ValidationError, match="tests_env"):
        ToolchainSection(modulefile="m", tests_env={"1st": "x"})
    pinned = ToolchainSection.model_validate(
        {"modulefile": "m", "module_env": {"kokkos_ver": 4.5}}
    )
    assert pinned.module_env == {"kokkos_ver": "4.5"}  # numbers export as strings


def test_a_toolchain_overlays_every_application_section(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        **{
            "applications.cece.cmake_args": ["-DCMAKE_BUILD_TYPE=Debug"],
            "applications.cece.configure_gates": [
                "CECE dependency Kokkos: system",
                _ARBORX,
            ],
            "applications.cece.tests": {
                "env": {"OMP_NUM_THREADS": "2", "I_MPI_HYDRA_BOOTSTRAP": "ssh"}
            },
        },
    )
    assert list(config.toolchains) == ["oneapi", "gnu"]  # file order
    base = config.applications["cece"]
    assert base.modulefile is None  # the base sections are untouched
    oneapi = config.for_toolchain("oneapi")
    assert oneapi.toolchain == "oneapi"
    cece = oneapi.applications["cece"]
    # The module environment is the toolchain's, whole.
    assert cece.modulefile == "ufs_ursa.oneapi"
    assert cece.modulepath == [Path("modulefiles"), Path("/envs/oneapi/modules/Core")]
    assert cece.module_env == {"kokkos_ver": "4.5.01"}
    assert cece.toolchain_tools == ["mpiicx", "mpiicpx", "mpiifx", "cmake", "ctest"]
    # The lists compose: the application's first, then the toolchain's.
    assert cece.build_flags() == [
        "-DCMAKE_BUILD_TYPE=Debug",
        "-DCMAKE_CXX_FLAGS=-fp-model=precise",
    ]
    assert cece.configure_gates == [
        "CECE dependency Kokkos: system",
        _ARBORX,
    ]  # a union
    # The tests' environment: the application's, updated by the toolchain's.
    assert cece.tests.env == {"OMP_NUM_THREADS": "2", "I_MPI_HYDRA_BOOTSTRAP": "fork"}
    gnu = config.for_toolchain("gnu").applications["cece"]
    assert gnu.modulefile == "ufs_ursa.gnu"
    assert gnu.module_env == {}
    assert gnu.build_flags() == ["-DCMAKE_BUILD_TYPE=Debug"]
    assert gnu.tests.env == {
        "OMP_NUM_THREADS": "2",
        "I_MPI_HYDRA_BOOTSTRAP": "ssh",
        "OMPI_MCA_plm": "isolated",
    }
    with pytest.raises(ValueError, match="no toolchain 'intel'"):
        config.for_toolchain("intel")


def test_an_application_without_cmake_takes_the_module_environment_only(
    tmp_path: Path,
) -> None:
    config = load_run_config(
        run_config_file(
            tmp_path,
            "local.yaml",
            overrides={
                "toolchains": {"gnu": TOOLCHAINS["gnu"]},
                "harness.runtime": "native",
            },
            root_dir=tmp_path,
        )
    )
    monetio = config.for_toolchain("gnu").applications["monetio"]
    assert monetio.modulefile == "ufs_ursa.gnu"
    assert monetio.tests.env == {"OMPI_MCA_plm": "isolated"}
    assert monetio.build_flags() == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("modulefile", "cece_ursa.intelllvm"),
        ("modulepath", ["/somewhere/modulefiles"]),
        ("module_env", {"esmf_ver": "8.8.0"}),
        ("toolchain_tools", ["icx"]),
    ],
)
def test_module_keys_belong_to_the_toolchain(
    tmp_path: Path, key: str, value: object
) -> None:
    # The point of a toolchain: these never drift per application.
    with pytest.raises(
        ValidationError,
        match=rf"applications\.cece: under toolchains:, `{key}` belongs to the toolchain",
    ):
        _config(tmp_path, **{f"applications.cece.{key}": value})


def test_every_cmake_application_takes_the_toolchains_configure_arguments(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path,
        **{
            "applications.catchem": {"git_url": "u", "ref": "r"},
            "applications.kokkos-smoke": {},
        },
    )
    oneapi = config.for_toolchain("oneapi")
    flag = "-DCMAKE_CXX_FLAGS=-fp-model=precise"
    assert oneapi.applications["catchem"].build_flags()[-2:] == [
        "-DCATCHEM_BUILD_NUOPC=ON",
        flag,
    ]
    smoke = oneapi.applications["kokkos-smoke"]
    assert smoke.build_flags() == ["-DCMAKE_BUILD_TYPE=Release", flag]
    # Its own default module path is not a claim on the toolchain's key.
    assert smoke.modulepath == [Path("modulefiles"), Path("/envs/oneapi/modules/Core")]
    assert smoke.modulefile == "ufs_ursa.oneapi"


def test_each_toolchain_has_its_own_checkout(tmp_path: Path) -> None:
    # An explicit clone_dir would be shared by every toolchain's build.
    with pytest.raises(ValidationError, match="under toolchains:, `clone_dir`"):
        _config(tmp_path, **{"applications.cece.clone_dir": "/elsewhere/CECE"})


def test_the_toolchain_is_a_segment_of_every_path(tmp_path: Path) -> None:
    config = _config(tmp_path)
    session = next(s for s in config.resolved_sessions() if s.name == "simple-maccity")
    assert config.checkout_dir(_CECE) == _ROOT / "CECE"  # the base: no toolchain
    for name in ("oneapi", "gnu"):
        derived = config.for_toolchain(name)
        assert derived.checkout_dir(_CECE) == _ROOT / name / "CECE"
        assert derived.tests_root(_CECE) == _ROOT / name / "tests" / "cece"
        assert derived.session_output_root(_CECE, session) == (
            f"ufs-chem-assay-output/{name}/simple-maccity"
        )
        assert derived.root_dir == _ROOT  # scripts, logs and the uv cache are shared
        assert derived.uv_cache_dir == _ROOT / "uv-cache"


def test_the_mpi_launcher_becomes_configure_arguments(tmp_path: Path) -> None:
    config = _config(tmp_path, **{"toolchains.gnu.mpiexec_preflags": "--mpi=pmi2"})
    assert config.mpi_configure_args() == []  # no toolchain: FindMPI's default
    assert config.for_toolchain("oneapi").mpi_configure_args() == []  # mpiexec
    # srun: every MPI test is its own job step. Shell words, already quoted —
    # the path is resolved where the configure runs.
    assert config.for_toolchain("gnu").mpi_configure_args() == [
        '-DMPIEXEC_EXECUTABLE="$(command -v srun)"',
        "-DMPIEXEC_NUMPROC_FLAG=-n",
        "-DMPIEXEC_PREFLAGS=--mpi=pmi2",
    ]


def test_the_rules_of_a_section_are_checked_per_toolchain(tmp_path: Path) -> None:
    # CECE's tests need a Debug build with ESMF: the flags may come from
    # the application or from the toolchain, and are judged together.
    tested = {
        "applications.cece.targets": ["all"],
        "applications.cece.tests": {"enabled": True},
        "applications.cece.cmake_args": ["-DENABLE_ESMF=ON"],
    }
    with pytest.raises(ValidationError) as missing:
        _config(tmp_path, **tested)
    assert "toolchains.oneapi: applications.cece: tests.enabled requires" in str(
        missing.value
    )
    both = {
        name: {**spec, "cmake_args": ["-DCMAKE_BUILD_TYPE=Debug"]}
        for name, spec in TOOLCHAINS.items()
    }
    config = _config(tmp_path, **{**tested, "toolchains": both})
    assert config.for_toolchain("gnu").applications["cece"].tests.enabled is True


def test_toolchain_names_are_path_segments(tmp_path: Path) -> None:
    for bad in ("OneAPI", "one api", "all", ""):
        with pytest.raises(ValidationError, match="is not a toolchain name"):
            _config(tmp_path, toolchains={bad: TOOLCHAINS["gnu"]})


def test_selecting_toolchains(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert [c.toolchain for c in toolchain_configs(config, ["all"])] == [
        "oneapi",
        "gnu",
    ]
    assert [c.toolchain for c in toolchain_configs(config, ["gnu oneapi"])] == [
        "oneapi",
        "gnu",
    ]  # the file's order, as for applications
    assert [c.toolchain for c in toolchain_configs(config, ["gnu"])] == ["gnu"]
    with pytest.raises(
        ValueError, match=r"--toolchain is required.*oneapi, gnu.*'all'"
    ):
        toolchain_configs(config, None)
    with pytest.raises(
        ValueError, match=r"--toolchain \['intel'\] not in the run config"
    ):
        toolchain_configs(config, ["intel"])
    with pytest.raises(ValueError, match="at least one toolchain name or 'all'"):
        toolchain_configs(config, [" "])
    # One toolchain needs no flag; a recorded selection is the default.
    single = _config(tmp_path, toolchains={"gnu": TOOLCHAINS["gnu"]})
    assert [c.toolchain for c in toolchain_configs(single, None)] == ["gnu"]
    recorded = config.model_copy(update={"selected_toolchains": ["gnu"]})
    assert [c.toolchain for c in toolchain_configs(recorded, None)] == ["gnu"]
    assert [c.toolchain for c in toolchain_configs(recorded, ["all"])] == [
        "oneapi",
        "gnu",
    ]


def test_the_effective_config_round_trips_with_its_selection(tmp_path: Path) -> None:
    config = _config(tmp_path).model_copy(
        update={"selected_toolchains": ["oneapi", "gnu"]}
    )
    path = tmp_path / "run-config.yaml"
    config.to_yaml(path)
    again = load_run_config(path)
    assert again == config
    assert again.selected_toolchains == ["oneapi", "gnu"]
    assert again.toolchains["gnu"].mpi_launcher is MpiLauncher.SRUN
    # A derived config is never what is written: the file is the base.
    assert again.toolchain is None
    with pytest.raises(ValidationError, match=r"selected_toolchains.*'intel'"):
        _config(tmp_path, selected_toolchains=["intel"])


# ── rendering: one set of scripts per toolchain ──────────────────────────────

_TESTED: dict[str, object] = {
    "applications.cece.cmake_args": ["-DCMAKE_BUILD_TYPE=Debug", "-DENABLE_ESMF=ON"],
    "applications.cece.targets": ["all"],
    "applications.cece.tests": {"enabled": True, "ntasks": 4},
}


def _lines(config: RunConfig, stage: Stage) -> list[str]:
    return render_stage(stage, config, _CECE).text.splitlines()


def test_scripts_are_named_and_bannered_by_toolchain(tmp_path: Path) -> None:
    config = _config(tmp_path)
    oneapi = config.for_toolchain("oneapi")
    session = next(s for s in config.resolved_sessions() if s.name == "simple-maccity")
    build = render_stage(Stage.BUILD, oneapi, _CECE)
    assert build.name == "build-cece-oneapi"
    assert 'echo ">>> stage: build (cece, oneapi)"' in build.text.splitlines()
    harness = render_stage(Stage.HARNESS, oneapi, _CECE, session)
    assert harness.name == "harness-cece-oneapi-simple-maccity"
    assert 'echo ">>> stage: harness (cece/simple-maccity, oneapi)"' in (
        harness.text.splitlines()
    )
    # The config as loaded renders nothing for itself under a toolchains map…
    assert render_stage(Stage.SOURCE, oneapi, _CECE).name == "source-cece-oneapi"
    # …and its own names are today's.
    plain = load_run_config(run_config_file(tmp_path, root_dir=_ROOT))
    assert render_stage(Stage.BUILD, plain, _CECE).name == "build-cece"


def test_build_stage_loads_the_toolchains_environment_and_appends_its_flags(
    tmp_path: Path,
) -> None:
    lines = _lines(_config(tmp_path).for_toolchain("oneapi"), Stage.BUILD)
    clone = _ROOT / "oneapi" / "CECE"
    start = lines.index("module purge")
    assert lines[start : start + 6] == [
        "module purge",
        "export kokkos_ver=4.5.01",
        f"module use {HARNESS_ROOT}/modulefiles",
        "module use /envs/oneapi/modules/Core",
        "module load ufs_ursa.oneapi",
        "module list",
    ]
    assert "which mpiicx mpiicpx mpiifx cmake ctest" in lines
    # The application's arguments, then the toolchain's; mpiexec adds none.
    assert (
        f"cmake -S {clone} -B {clone}/build -DCMAKE_BUILD_TYPE=Release "
        f"-DCMAKE_CXX_FLAGS=-fp-model=precise 2>&1 | tee {clone}/build/configure.log"
    ) in lines
    assert not any("MPIEXEC" in line for line in lines)
    assert any(f"grep -Eq '{_ARBORX}'" in line for line in lines)  # the env's claim


def test_srun_is_baked_into_every_configure(tmp_path: Path) -> None:
    config = _config(tmp_path, **_TESTED, **{"applications.cece.build_node": "compute"})
    lines = _lines(config.for_toolchain("gnu"), Stage.BUILD)
    launcher = '-DMPIEXEC_EXECUTABLE="$(command -v srun)" -DMPIEXEC_NUMPROC_FLAG=-n'
    configures = [line for line in lines if "cmake -S" in line]
    assert len(configures) == 2  # the compute-node probe and the login-node finish
    for line, testing in zip(configures, ("OFF", "ON"), strict=True):
        # Unquoted, after everything else: the shell resolves srun's path.
        assert f"-DCECE_BUILD_TESTING={testing} {launcher} 2>&1 | tee " in line
    assert "which mpicc mpic++ mpifort cmake ctest" in lines
    assert "module load ufs_ursa.gnu" in lines


def test_the_test_job_is_the_toolchains_own(tmp_path: Path) -> None:
    gnu = _config(tmp_path, **_TESTED).for_toolchain("gnu")
    script = render_stage(Stage.TEST, gnu, _CECE)
    assert script.name == "test-cece-gnu"
    assert script.text.splitlines()[-1] == (
        "uv run --no-sync ufs-chem-assay submit-tests "
        f"--root-dir {_ROOT} --application cece --toolchain gnu"
    )
    job = script.attachments[".sbatch"].splitlines()
    assert "#SBATCH --job-name=ufs-chem-assay-tests-cece-gnu" in job
    assert f"#SBATCH --output={_ROOT}/gnu/tests/cece/tests-%j.out" in job
    assert f"#SBATCH --chdir={_ROOT}/gnu/CECE" in job
    assert "module load ufs_ursa.gnu" in job
    assert "export OMPI_MCA_plm=isolated" in job  # the toolchain's tests_env
    assert any(
        f'{_ROOT}/gnu/tests/cece/tests-"$SLURM_JOB_ID".xml' in line for line in job
    )


def test_the_driver_sessions_run_against_the_toolchains_build(tmp_path: Path) -> None:
    config = _config(tmp_path)
    session = next(s for s in config.resolved_sessions() if s.name == "simple-maccity")
    text = render_stage(
        Stage.HARNESS, config.for_toolchain("oneapi"), _CECE, session
    ).text
    for line in (
        f"export CECE_ROOT_DIR={_ROOT}/oneapi/CECE",
        "export CECE_MODULEFILE=ufs_ursa.oneapi",
        f"export CECE_MODULEPATH={HARNESS_ROOT}/modulefiles:/envs/oneapi/modules/Core",
        "export CECE_MODULE_ENV=kokkos_ver=4.5.01",
    ):
        assert line in text, line
    assert "--combo-output-root=ufs-chem-assay-output/oneapi/simple-maccity" in text


def test_watch_and_report_locate_each_toolchains_roots(tmp_path: Path) -> None:
    config = _config(tmp_path, **_TESTED)
    scripts = _ROOT / "scripts"
    sessions = [s for s in config.resolved_sessions() if s.name == "simple-maccity"]
    gnu = config.for_toolchain("gnu")
    (watched,) = configured_sessions(gnu, [_CECE], sessions, scripts)
    assert watched.toolchain == "gnu"
    assert watched.label == "gnu/simple-maccity"
    assert watched.script == scripts / "06-resume-cece-gnu-simple-maccity.sh"
    assert watched.root == (
        _ROOT / "gnu" / "CECE" / "ufs-chem-assay-output" / "gnu" / "simple-maccity"
    )
    (tests,) = configured_tests(gnu, [_CECE])
    assert tests.toolchain == "gnu"
    assert tests.label == "tests cece/gnu"
    assert tests.root == _ROOT / "gnu" / "tests" / "cece"
    assert (
        job_script_for_tests(scripts, _CECE, "gnu")
        == scripts / "04-test-cece-gnu.sbatch"
    )
    assert job_script_for_tests(scripts, _CECE, None) == scripts / "04-test-cece.sbatch"
    # Without toolchains: today's names.
    plain = load_run_config(run_config_file(tmp_path, root_dir=_ROOT))
    (unnamed,) = configured_sessions(plain, [_CECE], sessions, scripts)
    assert (unnamed.toolchain, unnamed.label) == (None, "simple-maccity")
    assert unnamed.script == scripts / "06-resume-cece-simple-maccity.sh"


# ── the commands: run every toolchain, watch and report each ─────────────────


def _config_file(tmp_path: Path, **overrides: object) -> Path:
    return run_config_file(
        tmp_path,
        overrides={**TOOLCHAIN_OVERRIDES, **overrides},
        root_dir=tmp_path / "root",
    )


def _run(path: Path, *args: str) -> int:
    return main(["run", f"--config-file={path}", "--application=cece", *args])


def _cli(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _CLI_LOGGER]


def _scripts(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "root" / "scripts").glob("*.sh"))


def test_run_needs_a_toolchain_when_the_config_names_several(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    run_bash = mocker.patch("cli.commands.run.run_bash")
    path = _config_file(tmp_path)
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert _run(path, "--dry-run") == 1
        assert _run(path, "--toolchain", "intel", "--dry-run") == 1
    assert "--toolchain is required" in caplog.records[0].getMessage()
    assert (
        "--toolchain ['intel'] not in the run config" in caplog.records[1].getMessage()
    )
    plain = run_config_file(tmp_path, root_dir=tmp_path / "root")
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert _run(plain, "--toolchain", "gnu", "--dry-run") == 1
    assert "has no toolchains: map" in caplog.records[-1].getMessage()
    run_bash.assert_not_called()
    assert not (tmp_path / "root").exists()  # nothing rendered for a refused run


def test_run_renders_every_selected_toolchain_before_anything_executes(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.commands.run.run_bash")
    path = _config_file(tmp_path)
    assert (
        _run(path, "--toolchain", "all", "--stage=source", "--stage=build", "--dry-run")
        == 0
    )
    run_bash.assert_not_called()
    assert _scripts(tmp_path) == [
        "01-source-cece-gnu.sh",
        "01-source-cece-oneapi.sh",
        "02-build-cece-gnu.sh",
        "02-build-cece-oneapi.sh",
    ]
    scripts = tmp_path / "root" / "scripts"
    assert "module load ufs_ursa.gnu" in (scripts / "02-build-cece-gnu.sh").read_text()
    assert (
        f"{tmp_path}/root/oneapi/CECE"
        in (scripts / "01-source-cece-oneapi.sh").read_text()
    )
    # One effective config: the file as loaded, plus what was selected.
    effective = yaml.safe_load((scripts / "run-config.yaml").read_text())
    assert effective["selected_toolchains"] == ["oneapi", "gnu"]
    assert list(effective["toolchains"]) == ["oneapi", "gnu"]
    assert effective["applications"]["cece"]["modulefile"] is None  # not overlaid


def test_run_is_toolchain_major_in_the_configs_order(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    run_bash = mocker.patch("cli.commands.run.run_bash", return_value=0)
    path = _config_file(tmp_path)
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert (
            _run(path, "--toolchain", "gnu oneapi", "--stage=source", "--stage=build")
            == 0
        )
    assert [call.args[0].name for call in run_bash.call_args_list] == [
        "01-source-cece-oneapi.sh",  # the whole stage list for one toolchain…
        "02-build-cece-oneapi.sh",
        "01-source-cece-gnu.sh",  # …then for the next
        "02-build-cece-gnu.sh",
    ]
    assert _cli(caplog)[-2:] == ["oneapi: ok", "gnu: ok"]


def test_a_failing_toolchain_does_not_stop_the_others(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # oneapi's build fails: its sequence ends there, gnu still runs whole,
    # and the run's status is the first failure's.
    run_bash = mocker.patch("cli.commands.run.run_bash", side_effect=[0, 2, 0, 0, 0])
    path = _config_file(tmp_path)
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert (
            _run(
                path,
                "--toolchain=all",
                "--stage=source",
                "--stage=build",
                "--stage=data",
            )
            == 2
        )
    assert [call.args[0].name for call in run_bash.call_args_list] == [
        "01-source-cece-oneapi.sh",
        "02-build-cece-oneapi.sh",  # exit 2: 03-data-cece-oneapi.sh never runs
        "01-source-cece-gnu.sh",
        "02-build-cece-gnu.sh",
        "03-data-cece-gnu.sh",
    ]
    assert _cli(caplog)[-2:] == [
        "oneapi: failed at 02-build-cece-oneapi.sh (exit 2)",
        "gnu: ok",
    ]


def test_a_single_toolchain_needs_no_flag(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.commands.run.run_bash", return_value=3)
    path = _config_file(tmp_path, toolchains={"gnu": TOOLCHAINS["gnu"]})
    assert _run(path, "--stage=build") == 3
    assert [call.args[0].name for call in run_bash.call_args_list] == [
        "02-build-cece-gnu.sh"
    ]


@pytest.fixture
def rendered(tmp_path: Path, mocker: MockerFixture) -> Path:
    """A `run --toolchain all --dry-run` with CECE's tests on: both
    toolchains' scripts and the effective config on disk."""
    mocker.patch("cli.commands.run.run_bash")
    mocker.patch("apptests.submit_job", return_value=JobRef("4242", None))
    mocker.patch("apptests.queue_states", return_value={})
    assert _run(_config_file(tmp_path, **_TESTED), "--toolchain=all", "--dry-run") == 0
    return tmp_path / "root"


def test_submit_tests_takes_the_toolchain(
    rendered: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def submit(*extra: str) -> int:
        return main(
            ["submit-tests", f"--root-dir={rendered}", "--application=cece", *extra]
        )

    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert submit() == 1  # the run selected two: which one?
    assert "--toolchain is required" in caplog.text
    assert "oneapi, gnu" in caplog.text
    assert submit("--toolchain=gnu") == 0
    ledger = read_test_ledger(rendered / "gnu" / "tests" / "cece")
    assert ledger.toolchain == "gnu"
    assert ledger.job is not None
    assert ledger.job.script == rendered / "scripts" / "04-test-cece-gnu.sbatch"
    assert ledger.provenance.modulefile == "ufs_ursa.gnu"
    assert not (rendered / "oneapi" / "tests").exists()
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert submit("--toolchain=intel") == 1


def test_evaluate_tests_takes_the_toolchain(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    assert (
        main(
            [
                "submit-tests",
                f"--root-dir={rendered}",
                "--application=cece",
                "--toolchain=oneapi",
            ]
        )
        == 0
    )
    root = rendered / "oneapi" / "tests" / "cece"
    (root / "tests-4242.out").write_text(
        "tic: 2026-11-16T12:00:00Z\ntoc: 2026-11-16T12:00:30Z\n"
    )
    (root / "tests-4242.xml").write_text('<testsuite><testcase name="a"/></testsuite>')
    mocker.patch("apptests.slurm_accounting", return_value=None)
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert (
            main(
                [
                    "evaluate-tests",
                    f"--root-dir={rendered}",
                    "--application=cece",
                    "--toolchain=oneapi",
                ]
            )
            == 0
        )
    assert (
        "tests cece/oneapi: evaluated (exit 0), 1 passed / 0 failed / 0 skipped"
        in _cli(caplog)
    )
    (row,) = read_test_benchmarks_csv(root / "benchmarks.csv")
    assert (row.toolchain, row.modulefile) == ("oneapi", "ufs_ursa.oneapi")


def test_watch_and_report_cover_the_runs_toolchains(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    states = mocker.patch("cli.sessions.queue_states", return_value={})
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["watch", f"--root-dir={rendered}", "--once"]) == 0
    messages = _cli(caplog)
    states.assert_not_called()
    for label in (
        "oneapi/simple-maccity",
        "oneapi/gcafs-anthro",
        "gnu/simple-maccity",
        "gnu/gcafs-anthro",
        "tests cece/oneapi",
        "tests cece/gnu",
    ):
        assert f"{label}: not submitted" in messages, label
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert (
            main(
                [
                    "watch",
                    f"--root-dir={rendered}",
                    "--toolchain=gnu",
                    "--only=tests",
                    "--once",
                ]
            )
            == 0
        )
    assert [m for m in _cli(caplog) if "not submitted" in m] == [
        "tests cece/gnu: not submitted"
    ]
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["report", f"--root-dir={rendered}"]) == 1
    messages = _cli(caplog)
    assert messages[0].startswith(
        f"report: 4 session(s), 2 test run(s) under {rendered}"
    )
    assert any(m.startswith("oneapi/simple-maccity") for m in messages)
    assert any(m.startswith("gnu/gcafs-anthro") for m in messages)
    assert any(m.startswith("tests cece/gnu") for m in messages)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert (
            main(
                [
                    "report",
                    f"--root-dir={rendered}",
                    "--toolchain=oneapi",
                    "--only=tests",
                ]
            )
            == 1
        )
    assert _cli(caplog)[0].startswith("report: 0 session(s), 1 test run(s)")
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert (
            main(["watch", f"--root-dir={rendered}", "--toolchain=intel", "--once"])
            == 1
        )


def test_the_report_finds_a_toolchains_logs(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    mine = logs / "06-resume-cece-gnu-simple-maccity-20261116-120000.log"
    mine.write_text("")
    (logs / "06-resume-cece-oneapi-simple-maccity-20261116-130000.log").write_text("")
    (logs / "06-resume-cece-simple-maccity-20261116-140000.log").write_text("")
    assert newest_log(logs, "cece", "simple-maccity", "gnu") == mine
    summary = summarize_session(
        tmp_path / "nowhere",
        application="cece",
        session="simple-maccity",
        toolchain="gnu",
        logs_dir=logs,
    )
    assert summary.toolchain == "gnu"
    assert summary.label == "gnu/simple-maccity"


# ── config/ursa-kokkos.yaml, as shipped ──────────────────────────────────────

_KOKKOS = TEMPLATES_DIR / "ursa-kokkos.yaml"


def test_the_kokkos_template_loads_as_shipped() -> None:
    config = load_run_config(_KOKKOS)
    assert config.platform.value == "ursa"
    assert list(config.toolchains) == ["oneapi", "gnu"]
    assert config.application_names == ["cece", "catchem", "kokkos-smoke"]
    assert config.toolchains["oneapi"].mpi_launcher is MpiLauncher.MPIEXEC
    assert config.toolchains["gnu"].mpi_launcher is MpiLauncher.SRUN
    for build in toolchain_configs(config, ["all"]):
        built = [
            (name, variant.applications[name])
            for name in build.applications
            for variant in build_configs(build, get_application(name), None)
        ]
        # CATChem is two builds of one checkout: MUSICA off on a compute
        # node, MUSICA on (it fetches at configure time) on the login node.
        assert [(n, s.build_name) for n, s in built] == [
            ("cece", None),
            ("catchem", "compute"),
            ("catchem", "musica"),
            ("kokkos-smoke", None),
        ]
        for name, section in built:
            # Every build: the harness's UFS modulefile for this compiler,
            # against the environment's own module directory; tested.
            assert section.modulefile == f"ufs_ursa.{build.toolchain}", name
            harness_files, environment = section.modulepath
            assert harness_files == Path("modulefiles")
            assert environment.is_absolute()
            assert environment.parts[-2:] == ("modules", "Core")
            expected = (
                BuildNode.LOGIN if section.build_name == "musica" else BuildNode.COMPUTE
            )
            assert section.build_node is expected, (name, section.build_name)
            assert section.tests.enabled is True, name
            # Only CECE's configure prints the ArborX line (HELM's AXIS);
            # both environments carry ArborX, and the gate is CECE's own.
            assert (_ARBORX in section.configure_gates) is (name == "cece"), name
        assert "-DCATCHEM_BUILD_MUSICA=OFF" in built[1][1].build_flags()
        assert "-DCATCHEM_BUILD_MUSICA=ON" in built[2][1].build_flags()
        cece = build.applications["cece"]
        assert {"-DCMAKE_BUILD_TYPE=Debug", "-DENABLE_ESMF=ON"} <= set(
            cece.build_flags()
        )
        assert "CECE dependency Kokkos: system" in cece.configure_gates
        assert cece.tests.ntasks == 4
        assert build.applications["catchem"].tests.ntasks == 1
        catchem = build.applications["catchem"]
        # The CATChem branch the C7 template builds too (the coupling work).
        assert (catchem.git_url, catchem.ref) == (
            "git@github.com:benkozi/CATChem.git",
            "feature/cece-coupling-proto",
        )
        # Ursa's modulefiles load no yaml-cpp: CATChem keeps its vendored copy.
        assert not any(
            "CATCHEM_USE_SYSTEM_YAML_CPP" in flag
            for _, s in built
            for flag in s.build_flags()
        )
    # The driver sessions are there to be asked for, not run by default
    # against a Debug build; nothing is recorded into the committed files.
    assert list(config.sessions) == ["simple-maccity"]
    assert config.harness.submit_only is True
    assert config.harness.record_benchmarks is False
    assert config.baselines.enabled is False
    text = _KOKKOS.read_text()
    assert not any(line.startswith("root_dir:") for line in text.splitlines())
    for command in (
        "--application all --toolchain all --stage source --stage build --stage test",
        "ufs-chem-assay watch --only tests",
        "ufs-chem-assay report --only tests",
        "--stage data --stage harness --session simple-maccity",
        "docs/kokkos-runbook.md",
    ):
        assert command in text, command


def test_the_kokkos_template_renders_every_toolchain(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.commands.run.run_bash")
    command = ["run", f"--config-file={_KOKKOS}", f"--root-dir={tmp_path / 'root'}"]
    stages = ["--stage=source", "--stage=build", "--stage=test"]
    assert (
        main([*command, "--application=all", "--toolchain=all", *stages, "--dry-run"])
        == 0
    )
    run_bash.assert_not_called()
    scripts = tmp_path / "root" / "scripts"
    expected = []
    for toolchain in ("oneapi", "gnu"):
        # kokkos-smoke ships with the harness: no source stage; CATChem is
        # one checkout and two builds.
        expected += [
            f"01-source-cece-{toolchain}.sh",
            f"01-source-catchem-{toolchain}.sh",
        ]
        for slot, stage in (("02", "build"), ("04", "test")):
            expected += [
                f"{slot}-{stage}-cece-{toolchain}.sh",
                f"{slot}-{stage}-catchem-{toolchain}-compute.sh",
                f"{slot}-{stage}-catchem-{toolchain}-musica.sh",
                f"{slot}-{stage}-kokkos-smoke-{toolchain}.sh",
            ]
    assert sorted(p.name for p in scripts.glob("*.sh")) == sorted(expected)
    assert len(list(scripts.glob("04-test-*.sbatch"))) == 8
    gnu = (scripts / "02-build-cece-gnu.sh").read_text()
    assert "module load ufs_ursa.gnu" in gnu
    assert '-DMPIEXEC_EXECUTABLE="$(command -v srun)"' in gnu
    assert "-DCECE_BUILD_TESTING=OFF" in gnu  # the compute-node probe
    assert "-DCECE_BUILD_TESTING=ON" in gnu  # the login-node finish
    oneapi = (scripts / "02-build-cece-oneapi.sh").read_text()
    assert "-DCMAKE_CXX_FLAGS=-fp-model=precise" in oneapi
    assert "MPIEXEC" not in oneapi
    # The opt-in driver session, against one toolchain's build.
    assert (
        main(
            [
                *command,
                "--application=cece",
                "--toolchain=oneapi",
                "--stage=data",
                "--stage=harness",
                "--session=simple-maccity",
                "--dry-run",
            ]
        )
        == 0
    )
    harness = (scripts / "05-harness-cece-oneapi-simple-maccity.sh").read_text()
    assert f"export CECE_ROOT_DIR={tmp_path}/root/oneapi/CECE" in harness
    assert "export CECE_MODULEFILE=ufs_ursa.oneapi" in harness


# ── config/gaea-c7.yaml, as shipped ──────────────────────────────────────────

_GAEA_C7 = TEMPLATES_DIR / "gaea-c7.yaml"
_C7_ENVIRONMENT = Path(
    "/ncrc/proj/epic/spack-stack/c7/spack-stack-2.2.0/envs/ue-oneapi-2025.3.1"
)


def test_the_gaea_c7_template_loads_as_shipped() -> None:
    config = load_run_config(_GAEA_C7)
    assert config.platform.value == "gaea-c7"
    assert list(config.toolchains) == ["oneapi"]
    assert config.application_names == ["cece", "catchem"]
    toolchain = config.toolchains["oneapi"]
    assert toolchain.mpi_launcher is MpiLauncher.SRUN
    assert toolchain.mpiexec_preflags is None
    assert toolchain.tests_env == {}
    assert toolchain.configure_gates == []  # the ArborX gate is CECE's (below)
    # The environment builds its UFS stack against one ESMF; its default
    # esmf module is a later beta, which the mapl module does not match.
    assert toolchain.module_env == {
        "esmf_ver": "9.0.0b17",
        "mapl_ver": "2.69.1-esmf-9.0.0b17",
    }
    (build,) = toolchain_configs(config, ["all"])
    cece = build.applications["cece"]
    assert cece.modulefile == "ufs_gaeac7.oneapi"
    assert cece.modulepath == [
        Path("modulefiles"),
        _C7_ENVIRONMENT / "modules" / "Core",
    ]
    assert cece.toolchain_tools == ["icx", "icpx", "ifx", "cmake", "ctest"]
    assert {"-DCMAKE_BUILD_TYPE=Debug", "-DENABLE_ESMF=ON"} <= set(cece.build_flags())
    # The ref that builds against the environment's ESMF 9 (a FindESMF fix
    # not yet upstream); the template says when it goes back to develop.
    assert (cece.git_url, cece.ref) == (
        "git@github.com:benkozi/CECE.git",
        "feature/multi-tile-host-decomp",
    )
    # The environment's Kokkos, never a fetched one; built as the offline
    # compile probe on a compute node, then finished on the login node.
    assert cece.configure_gates == [
        "CECE dependency Kokkos: system",
        "CECE dependency yaml-cpp: system",  # the environment's, not the submodule
        _ARBORX,
    ]
    assert cece.build_node is BuildNode.COMPUTE
    assert cece.build_minutes == 10
    assert cece.tests.enabled is True
    assert (cece.tests.ntasks, cece.tests.cpus, cece.tests.minutes) == (4, 8, 15)
    assert cece.tests.env == {"PIP_NO_INDEX": "1"}  # no network on a compute node
    assert config.slurm is not None
    assert (config.slurm.account, config.slurm.qos, config.slurm.partition) == (
        "epic",
        "normal",
        "batch",
    )
    assert config.slurm.extra_args == "--clusters=c7"
    assert config.sessions == {}  # no driver session has run on C7
    text = _GAEA_C7.read_text()
    assert not any(line.startswith("root_dir:") for line in text.splitlines())
    for needle in (
        "--config-file=config/gaea-c7.yaml --application all",
        "--stage source --stage build --stage test",
        "ufs-chem-assay watch --only tests",
        "ufs-chem-assay report --only tests",
        "TODO(gaea-c7)",
        "applications:cece:build_node=login",  # the fallback
    ):
        assert needle in text, needle


def test_the_gaea_c7_template_renders_the_build_and_test_stages(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.commands.run.run_bash")
    root = tmp_path / "root"
    assert (
        main(
            [
                "run",
                f"--config-file={_GAEA_C7}",
                f"--root-dir={root}",
                "--application=cece",
                "--stage=source",
                "--stage=build",
                "--stage=test",
                "--dry-run",
            ]
        )
        == 0
    )
    run_bash.assert_not_called()
    scripts = root / "scripts"
    assert sorted(p.name for p in scripts.glob("*.sh")) == [
        "01-source-cece-oneapi.sh",
        "02-build-cece-oneapi.sh",
        "04-test-cece-oneapi.sh",
    ]
    build = (scripts / "02-build-cece-oneapi.sh").read_text().splitlines()
    module_lines = [
        line for line in build if line.startswith(("module ", "export ", "which "))
    ]
    assert module_lines == [
        "module reset",  # never purge on Gaea
        "export esmf_ver=9.0.0b17",
        "export mapl_ver=2.69.1-esmf-9.0.0b17",
        f"module use {HARNESS_ROOT / 'modulefiles'}",
        f"module use {_C7_ENVIRONMENT}/modules/Core",
        "module load ufs_gaeac7.oneapi",
        "module list",
        "which icx icpx ifx cmake ctest",
    ]
    # The offline probe: configure and compile as two srun steps on a C7
    # compute node (the cluster from slurm.extra_args), the test stack off;
    # then the login node re-configures with it on and builds it.
    srun = (
        "srun -A epic -q normal -p batch --clusters=c7 --nodes=1 --ntasks=1 "
        "--cpus-per-task=8 --time=10 "
    )
    probe = [line for line in build if line.startswith(srun)]
    assert [line.removeprefix(srun).split()[:2] for line in probe] == [
        ["cmake", "-S"],
        ["cmake", "--build"],
    ]
    assert "-DCECE_BUILD_TESTING=OFF" in probe[0]
    (finish,) = [line for line in build if line.startswith("cmake -S ")]
    assert "-DCECE_BUILD_TESTING=ON" in finish
    for configure in (probe[0], finish):
        assert '-DMPIEXEC_EXECUTABLE="$(command -v srun)"' in configure
    gates = [line for line in build if line.startswith("grep -Eq ")]
    assert [gate.split("'")[1] for gate in gates] == 2 * [
        "CECE dependency ESMF: system",
        "CECE dependency Kokkos: system",
        "CECE dependency yaml-cpp: system",
        _ARBORX,
    ]  # after each configure
    job = (scripts / "04-test-cece-oneapi.sbatch").read_text().splitlines()
    for directive in (
        "#SBATCH -A epic",
        "#SBATCH -q normal",
        "#SBATCH -p batch",
        "#SBATCH -n 4",
        "#SBATCH -c 8",
        "#SBATCH --time=15",
        "#SBATCH --clusters=c7",
    ):
        assert directive in job, directive
    assert "module reset" in job
    assert "module load ufs_gaeac7.oneapi" in job
    assert "export PIP_NO_INDEX=1" in job


@pytest.mark.parametrize(
    "template", sorted(p.name for p in TEMPLATES_DIR.glob("*.yaml"))
)
def test_shipped_templates_put_no_gate_on_a_toolchain(template: str) -> None:
    # A toolchain's configure_gates are added to every application of the
    # file, so each must be a line every application's configure prints.
    # None does for the shipped applications: the ArborX line is HELM's AXIS,
    # inside CECE only, and CATChem and kokkos-smoke never print it, so the
    # gate belongs to applications.cece. A gate an environment claims for one
    # application is that application's.
    raw = yaml.safe_load((TEMPLATES_DIR / template).read_text())
    for name, toolchain in (raw.get("toolchains") or {}).items():
        assert not toolchain.get("configure_gates"), f"{template}: toolchains.{name}"


def test_the_gaea_c7_template_builds_catchem_twice(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    # CATChem on C7: one checkout of the coupling branch, a compute-node
    # build with MUSICA off and a login-node build with it on, each taking
    # the environment's yaml-cpp (the C7 modulefile loads it) and checking it.
    config = load_run_config(_GAEA_C7)
    (oneapi,) = toolchain_configs(config, None)
    catchem = get_application("catchem")
    section = oneapi.applications["catchem"]
    assert (section.git_url, section.ref) == (
        "git@github.com:benkozi/CATChem.git",
        "feature/cece-coupling-proto",
    )
    compute, musica = build_configs(oneapi, catchem, None)
    c, m = compute.applications["catchem"], musica.applications["catchem"]
    assert (c.build_name, c.build_node) == ("compute", BuildNode.COMPUTE)
    assert (m.build_name, m.build_node) == ("musica", BuildNode.LOGIN)
    for variant, musica_flag in ((c, "OFF"), (m, "ON")):
        flags = variant.build_flags()
        assert f"-DCATCHEM_BUILD_MUSICA={musica_flag}" in flags
        assert "-DCATCHEM_ENABLE_KOKKOS=ON" in flags
        assert "-DCATCHEM_USE_SYSTEM_YAML_CPP=ON" in flags
        assert (
            flags[-1] == "-DCMAKE_CXX_FLAGS=-fp-model=precise"
        )  # the toolchain's, last
        assert variant.configure_gates == []  # ArborX is CECE's gate
        assert (variant.tests.enabled, variant.tests.ntasks, variant.tests.cpus) == (
            True,
            1,
            8,
        )
        # CATChem's tests reach no network; OMP_PROC_BIND set (unpinned) so
        # Kokkos's OpenMP backend does not warn in every test.
        assert variant.tests.env == {"OMP_PROC_BIND": "false"}

    run_bash = mocker.patch("cli.commands.run.run_bash")
    root = tmp_path / "root"
    assert (
        main(
            [
                "run",
                f"--config-file={_GAEA_C7}",
                f"--root-dir={root}",
                "--application=all",
                "--stage=source",
                "--stage=build",
                "--stage=test",
                "--dry-run",
            ]
        )
        == 0
    )
    run_bash.assert_not_called()
    scripts = root / "scripts"
    assert sorted(p.name for p in scripts.glob("*catchem*")) == [
        "01-source-catchem-oneapi.sh",
        "02-build-catchem-oneapi-compute.sh",
        "02-build-catchem-oneapi-musica.sh",
        "04-test-catchem-oneapi-compute.sbatch",
        "04-test-catchem-oneapi-compute.sh",
        "04-test-catchem-oneapi-musica.sbatch",
        "04-test-catchem-oneapi-musica.sh",
    ]
    compute_build = (scripts / "02-build-catchem-oneapi-compute.sh").read_text()
    srun = "srun -A epic -q normal -p batch --clusters=c7 "
    assert sum(line.startswith(srun) for line in compute_build.splitlines()) == 2
    assert "yaml-cpp not found on system, using internal version" in compute_build
    musica_build = (scripts / "02-build-catchem-oneapi-musica.sh").read_text()
    assert "srun " not in musica_build
    assert "module load ufs_gaeac7.oneapi" in musica_build
    job = (scripts / "04-test-catchem-oneapi-compute.sbatch").read_text()
    assert "#SBATCH --clusters=c7" in job
    assert "ctest --test-dir build-compute " in job
    for name in ("compute", "musica"):
        job = (scripts / f"04-test-catchem-oneapi-{name}.sbatch").read_text()
        assert "export OMP_PROC_BIND=false" in job.splitlines()
