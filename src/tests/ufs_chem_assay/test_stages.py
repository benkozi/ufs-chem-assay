"""Stage rendering: the shell the CLI writes for each run-config template,
per application. Golden-line assertions — the runbook (docs/ursa-runbook.md)
and these scripts must stay recognisably the same commands."""

import os
from pathlib import Path

import pytest

from applications.base import Application, Stage
from applications.registry import REGISTRY, get_application
from cli.run_config import HARNESS_ROOT, RunConfig
from cli.stages import application_output_root, render_stage
from platforms import Platform
from tests.ufs_chem_assay.run_configs import (
    TEMPLATES_DIR,
    load_run_config,
    run_config_file,
)
from tests.ufs_chem_assay.stubs import stub_application

_CECE = get_application("cece")

# The shipped template's root_dir is a placeholder with shell-special
# characters (`/scratch3/<project>/<user>/…`, quoted when rendered); the
# golden lines below use a plain root instead.
_URSA_ROOT = Path("/scratch/ursa-run")


@pytest.fixture
def ursa(tmp_path: Path) -> RunConfig:
    return load_run_config(run_config_file(tmp_path, root_dir=_URSA_ROOT))


@pytest.fixture
def local(tmp_path: Path) -> RunConfig:
    return load_run_config(
        run_config_file(tmp_path, "local.yaml", root_dir=tmp_path / "runs")
    )


def _ursa(tmp_path: Path, overrides: dict[str, object]) -> RunConfig:
    """The Ursa template with dotted-key overrides, at the runbook root."""
    return load_run_config(
        run_config_file(tmp_path, overrides=overrides, root_dir=_URSA_ROOT)
    )


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Application:
    app = stub_application()
    monkeypatch.setitem(REGISTRY, "stub", app)
    return app


def test_harness_root_is_the_repository() -> None:
    assert (HARNESS_ROOT / "pyproject.toml").is_file()
    assert (HARNESS_ROOT / "src" / "tests" / "test_driver_combos.py").is_file()


def test_every_stage_script_starts_with_shebang_and_strict_mode(
    ursa: RunConfig,
) -> None:
    for stage in Stage:
        script = render_stage(stage, ursa, _CECE)
        assert script.name == f"{stage.value}-cece"
        lines = script.text.splitlines()
        assert lines[:2] == ["#!/bin/bash", "set -euo pipefail"]
        assert f'echo ">>> stage: {stage.value} (cece)"' in lines


def test_source_stage_clones_when_missing_and_guards_submodules(
    ursa: RunConfig,
) -> None:
    script = render_stage(Stage.SOURCE, ursa, _CECE)
    clone = str(ursa.checkout_dir(_CECE))
    assert (
        "git clone --recurse-submodules --branch develop "
        f"git@github.com:ufs-community/CECE.git {clone}"
    ) in script.text
    assert f"{clone}/extern/helm/libs" in script.text
    assert "log -1 --oneline" in script.text
    assert "pull --ff-only" not in script.text  # as-is without update_source


def test_source_stage_update_source_fast_forwards(tmp_path: Path) -> None:
    config = _ursa(tmp_path, {"applications.cece.update_source": True})
    script = render_stage(Stage.SOURCE, config, _CECE)
    assert "git -C" in script.text
    assert "fetch origin" in script.text
    assert "checkout develop" in script.text
    assert "pull --ff-only origin develop" in script.text
    assert "submodule update --init --recursive" in script.text


def test_native_build_stage_loads_modules_and_gates_configure(ursa: RunConfig) -> None:
    script = render_stage(Stage.BUILD, ursa, _CECE)
    clone = str(ursa.checkout_dir(_CECE))
    lines = script.text.splitlines()
    assert "module purge" in lines
    assert f"module use {clone}/modulefiles" in lines
    assert "module load cece_ursa.intelllvm" in lines
    assert "module list" in lines
    assert any(
        line.startswith(f"cmake -S {clone} -B {clone}/build -DCMAKE_BUILD_TYPE=Release")
        and "tee" in line
        and "configure.log" in line
        for line in lines
    )
    assert any("Found MPI" in line for line in lines)
    assert (
        f"cmake --build {clone}/build --target cece_standalone_driver --parallel 8"
        in lines
    )


def test_local_build_stage_delegates_to_cece_container_script(local: RunConfig) -> None:
    script = render_stage(Stage.BUILD, local, _CECE)
    assert "module" not in script.text
    assert "cmake" not in script.text
    assert (
        f"python3 {local.checkout_dir(_CECE)}/scripts/build-and-test-container.py "
        "--no-test --target cece_standalone_driver --jobs 4"
    ) in script.text


def test_data_stage_exports_settings_and_fetches_the_selected_suites_inputs(
    ursa: RunConfig,
) -> None:
    # The data stage is the harness's own `fetch` under the same exports the
    # harness stage renders (the root, the search paths), from the harness
    # checkout; nothing application-specific, no CECE tooling.
    script = render_stage(Stage.DATA, ursa, _CECE)
    lines = script.text.splitlines()
    fetch = (
        "uv run --no-sync ufs-chem-assay fetch --application=cece "
        "--suite-config=simple-maccity-suite.yaml"
    )
    assert fetch in lines
    assert f"export CECE_ROOT_DIR={ursa.checkout_dir(_CECE)}" in lines
    assert "export ASSAY_PLATFORM=ursa" in lines
    assert f"export UV_CACHE_DIR={ursa.root_dir}/uv-cache" in lines
    assert lines.index(f"cd {HARNESS_ROOT}") < lines.index(fetch)
    assert lines.index(fetch) < lines.index(
        next(line for line in lines if "natural_earth" in line)
    )
    assert "download-example-data" not in script.text
    assert "python3" not in script.text


def test_data_stage_exports_match_the_harness_stage(ursa: RunConfig) -> None:
    # One export block, rendered by both stages: whatever the harness stage
    # exports, the data stage exports too (so fetch resolves the same suites).
    harness_exports = {
        line
        for line in render_stage(Stage.HARNESS, ursa, _CECE).text.splitlines()
        if line.startswith("export ")
    }
    data_exports = {
        line
        for line in render_stage(Stage.DATA, ursa, _CECE).text.splitlines()
        if line.startswith("export ")
    }
    assert harness_exports == data_exports


def test_data_stage_without_cartopy(local: RunConfig) -> None:
    assert "natural_earth" not in render_stage(Stage.DATA, local, _CECE).text


def test_shared_data_lines_render_once_per_run(ursa: RunConfig) -> None:
    # The cartopy warm-up is harness-wide: under the first application only.
    assert "natural_earth" in render_stage(Stage.DATA, ursa, _CECE).text
    assert (
        "natural_earth"
        not in render_stage(Stage.DATA, ursa, _CECE, shared_data=False).text
    )


def test_cece_tests_is_not_a_stage_of_this_feature() -> None:
    # Running CECE's own tests is issue #9; the build stage can still build
    # the test stack (targets) for it.
    assert [stage.value for stage in Stage] == ["source", "build", "data", "harness"]


def test_harness_stage_exports_every_setting_and_runs_pytest(ursa: RunConfig) -> None:
    text = render_stage(Stage.HARNESS, ursa, _CECE).text
    for line in (
        f"export CECE_ROOT_DIR={ursa.checkout_dir(_CECE)}",
        "export CECE_MODULEFILE=cece_ursa.intelllvm",
        "export ASSAY_PLATFORM=ursa",
        "export ASSAY_RUNTIME=slurm",
        "export ASSAY_SBATCH_ARGS='-A epic -q debug -p u1-compute -N 1 -n 1 -c 8'",
        "export ASSAY_SRUN_ARGS=--ntasks=1",  # the srun inside each job
        "export ASSAY_JOB_ENV='I_MPI_FABRICS=shm FI_PROVIDER=tcp'",  # the driver jobs' env
        "export ASSAY_ENABLE_BASELINE_COMPARISONS=false",
        "export ASSAY_BASELINE_STORE=s3://ufs-chem/baselines",
        "export ASSAY_RUN_TIMEOUT_S=300",
        "export ASSAY_DASK_NWORKERS=2",
        f"export UV_CACHE_DIR={ursa.root_dir}/uv-cache",
        f"cd {HARNESS_ROOT}",
        (
            "uv run --no-sync pytest src/tests/test_driver_combos.py "
            "--application=cece --suite-config=simple-maccity-suite.yaml "
            "--combo-output-root=ufs-chem-assay-output --combo-clean-root"
        ),
    ):
        assert line in text, line
    for absent in (
        "ASSAY_BASELINE_ROOT_DIR",
        "ASSAY_BASELINE_ATOL",  # null in the template: the suite's entries apply
        "ASSAY_BASELINE_RTOL",
        "UV_PYTHON",
        "UV_OFFLINE",
        "ASSAY_LAUNCHER",
        "ASSAY_LOG_LEVEL",  # null in the template: not exported
        "ASSAY_INPUT_SEARCH_PATH",  # empty in the template: not exported
        "CECE_DOCKER_IMAGE",  # null in the template: the adapter's default
        "CECE_DRIVER_PATH",
        "SLURM_CPUS_PER_TASK",
        "CECE_PLATFORM",
        "CECE_RUNTIME",
        "--dry-run",
        "--run-examples",
        "--publish-baselines",
        "--no-suite-update",
    ):
        assert absent not in text, absent


def test_harness_stage_exports_are_field_driven(tmp_path: Path) -> None:
    # A harness: key that mirrors a setting renders as ASSAY_<FIELD>; an
    # application key as <PREFIX><FIELD>; the pytest options as flags.
    config = _ursa(
        tmp_path,
        {
            "harness.log_level": "DEBUG",
            "harness.config_search_path": "/configs",
            "harness.suite_config_search_path": ["/suites/a", "/suites/b"],
            "harness.pytest_dry_run": True,
            "applications.cece.docker_image": "img:tag",
            "applications.cece.driver_path": "./build/other",
        },
    )
    text = render_stage(Stage.HARNESS, config, _CECE).text
    for line in (
        "export ASSAY_LOG_LEVEL=DEBUG",
        "export ASSAY_CONFIG_SEARCH_PATH=/configs",
        "export ASSAY_SUITE_CONFIG_SEARCH_PATH=/suites/a:/suites/b",
        "export CECE_DOCKER_IMAGE=img:tag",
        "export CECE_DRIVER_PATH=./build/other",
    ):
        assert line in text, line
    assert "--combo-clean-root --dry-run" in text
    assert "--run-examples" not in text


def test_native_harness_stage_keeps_the_launcher(tmp_path: Path) -> None:
    # Inside an salloc shell: ASSAY_RUNTIME=native with a plain launcher.
    config = _ursa(
        tmp_path,
        {
            "harness.launcher": "srun --ntasks=1",
            "harness.runtime": "native",
        },
    )
    text = render_stage(Stage.HARNESS, config, _CECE).text
    assert "export ASSAY_RUNTIME=native" in text
    assert "export ASSAY_LAUNCHER='srun --ntasks=1'" in text
    assert "ASSAY_SBATCH_ARGS" not in text


def test_local_harness_stage_has_no_slurm_or_offline_bits(local: RunConfig) -> None:
    text = render_stage(Stage.HARNESS, local, _CECE).text
    assert "export ASSAY_PLATFORM=local" in text
    assert "export ASSAY_RUNTIME=docker" in text
    assert "ASSAY_LAUNCHER" not in text
    assert "UV_OFFLINE" not in text
    assert "SLURM" not in text
    assert "module" not in text


def test_platform_override_changes_runtime_exports() -> None:
    config = load_run_config(TEMPLATES_DIR / "ursa.yaml", platform=Platform.LOCAL)
    text = render_stage(Stage.HARNESS, config, _CECE).text
    assert "export ASSAY_PLATFORM=local" in text
    assert "export ASSAY_RUNTIME=docker" in text


def test_data_and_harness_stages_export_the_input_search_path_when_set(
    tmp_path: Path,
) -> None:
    # Both shared stages see it: the data stage's fetch links source-less
    # inputs from it; the harness stage inherits the same exports.
    dirs = ["/scratch/x/INPUT", "/scratch/y/input"]
    config = _ursa(tmp_path, {"data.input_search_path": dirs})
    for stage in (Stage.DATA, Stage.HARNESS):
        text = render_stage(stage, config, _CECE).text
        assert f"export ASSAY_INPUT_SEARCH_PATH={os.pathsep.join(dirs)}" in text


def test_gcafs_anthro_template_renders_the_use_case_as_shipped(tmp_path: Path) -> None:
    config = load_run_config(
        run_config_file(tmp_path, "ursa-gcafs-anthro.yaml", root_dir=_URSA_ROOT)
    )
    text = render_stage(Stage.HARNESS, config, _CECE).text
    for line in (
        "export ASSAY_SBATCH_ARGS='-A epic -q debug -p u1-compute -N 1 -n 1 -c 1 --mem=0'",
        "export ASSAY_SRUN_ARGS='--ntasks=1 --cpu-bind=cores'",
        "export ASSAY_RUN_TIMEOUT_S=28800",
        "export ASSAY_SLURM_QUEUE_WAIT_S=3600",
        "--suite-config=gcafs-anthro-suite.yaml",
    ):
        assert line in text, line
    job_env = next(
        l for l in text.splitlines() if l.startswith("export ASSAY_JOB_ENV=")
    )
    for pair in (
        "I_MPI_FABRICS=shm",
        "FI_PROVIDER=tcp",
        "OMP_NUM_THREADS=1",
        "OMP_PROC_BIND=spread",
        "OMP_PLACES=threads",
        "KOKKOS_NUM_THREADS=1",
        "MKL_NUM_THREADS=1",
        "OPENBLAS_NUM_THREADS=1",
        "OPENBLAS_DEFAULT_NUM_THREADS=1",
    ):
        assert pair in job_env, pair
    assert "ASSAY_INPUT_SEARCH_PATH" not in text


def test_harness_stage_exports_the_srun_geometry_under_slurm_only(
    tmp_path: Path, local: RunConfig
) -> None:
    geometry = _ursa(
        tmp_path,
        {
            "slurm.ntasks": 4,
            "slurm.srun_args": "--cpu-bind=cores",
            "slurm.extra_args": "--mem=0",
        },
    )
    text = render_stage(Stage.HARNESS, geometry, _CECE).text
    assert "export ASSAY_SRUN_ARGS='--ntasks=4 --cpu-bind=cores'" in text
    assert (
        "export ASSAY_SBATCH_ARGS='-A epic -q debug -p u1-compute -N 1 -n 4 -c 8 --mem=0'"
        in text
    )
    assert "ASSAY_SRUN_ARGS" not in render_stage(Stage.HARNESS, local, _CECE).text


def test_harness_stage_exports_the_queue_allowance_under_slurm_only(
    ursa: RunConfig, local: RunConfig, tmp_path: Path
) -> None:
    assert (
        "export ASSAY_SLURM_QUEUE_WAIT_S=3600"
        in render_stage(Stage.HARNESS, ursa, _CECE).text
    )
    assert (
        "ASSAY_SLURM_QUEUE_WAIT_S" not in render_stage(Stage.HARNESS, local, _CECE).text
    )
    long_wait = _ursa(tmp_path, {"slurm.queue_wait_s": 28800})
    assert (
        "export ASSAY_SLURM_QUEUE_WAIT_S=28800"
        in render_stage(Stage.HARNESS, long_wait, _CECE).text
    )


def test_regex_suite_selector_survives_quoting(tmp_path: Path) -> None:
    # Several suites in one session: the selector is a regex, every match runs.
    config = _ursa(tmp_path, {"harness.suite_config": "ex[0-9]-suite.yaml"})
    text = render_stage(Stage.HARNESS, config, _CECE).text
    assert "'--suite-config=ex[0-9]-suite.yaml'" in text


# ── Several applications in one run ──────────────────────────────────────────


def test_output_root_is_suffixed_only_with_several_applications(
    tmp_path: Path, stub: Application
) -> None:
    one = load_run_config(run_config_file(tmp_path, root_dir=_URSA_ROOT))
    assert application_output_root(one, _CECE) == "ufs-chem-assay-output"
    two = _ursa(tmp_path, {"applications.stub": {"git_url": "u", "ref": "main"}})
    assert application_output_root(two, _CECE) == "ufs-chem-assay-output/cece"
    assert application_output_root(two, stub) == "ufs-chem-assay-output/stub"
    text = render_stage(Stage.HARNESS, two, stub).text
    assert "--application=stub" in text
    assert "--combo-output-root=ufs-chem-assay-output/stub" in text
    assert f"export STUB_ROOT_DIR={_URSA_ROOT}/STUB" in text
    assert "CECE_ROOT_DIR" not in text
    assert (
        render_stage(Stage.SOURCE, two, stub).text.rstrip().endswith("echo stub source")
    )


def test_harness_stage_renders_the_publish_flags(tmp_path: Path) -> None:
    config = _ursa(
        tmp_path,
        {
            "harness.publish_baselines": True,
            "harness.no_suite_update": True,
            "baselines.store": "s3://other/prefix",
            "baselines.root_dir": "/scratch/baselines",
        },
    )
    text = render_stage(Stage.HARNESS, config, _CECE).text
    assert "export ASSAY_BASELINE_STORE=s3://other/prefix" in text
    assert "export ASSAY_BASELINE_ROOT_DIR=/scratch/baselines" in text
    assert "--combo-clean-root --publish-baselines --no-suite-update" in text


def test_harness_stage_exports_the_tolerance_overrides(tmp_path: Path) -> None:
    config = _ursa(tmp_path, {"baselines.atol": 1e-24, "baselines.rtol": 1e-15})
    text = render_stage(Stage.HARNESS, config, _CECE).text
    assert "export ASSAY_BASELINE_ATOL=1e-24" in text
    assert "export ASSAY_BASELINE_RTOL=1e-15" in text
    assert "ASSAY_BASELINE_RTOL" in render_stage(Stage.DATA, config, _CECE).text


def test_env_value_renders_an_enum_by_its_value() -> None:
    from cli.stages import _env_value
    from platforms import Runtime

    assert _env_value(Runtime.SLURM) == "slurm"
    assert _env_value(None) is None
