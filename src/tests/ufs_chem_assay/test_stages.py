"""Stage rendering: the shell the CLI writes for each run-config template.
Golden-line assertions — the runbook (docs/ursa-runbook.md) and these scripts
must stay recognisably the same commands."""

from pathlib import Path

import pytest

from cli.run_config import RunConfig
from cli.stages import HARNESS_ROOT, Stage, render_stage
from platforms import Platform
from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR, run_config_file


# The shipped template's root_dir is a placeholder with shell-special
# characters (`/scratch3/<project>/<user>/…`, quoted when rendered); the
# golden lines below use a plain root instead.
_URSA_ROOT = Path("/scratch/ursa-run")


@pytest.fixture()
def ursa(tmp_path: Path) -> RunConfig:
    return RunConfig.from_yaml(run_config_file(tmp_path, root_dir=_URSA_ROOT))


@pytest.fixture()
def local(tmp_path: Path) -> RunConfig:
    return RunConfig.from_yaml(
        run_config_file(tmp_path, "local.yaml", root_dir=tmp_path / "runs")
    )


@pytest.fixture()
def ursa_with_tests(tmp_path: Path) -> RunConfig:
    return RunConfig.from_yaml(
        run_config_file(
            tmp_path,
            overrides={"cece.run_tests": True, "cece.targets": ["all"]},
            root_dir=_URSA_ROOT,
        )
    )


def test_harness_root_is_the_repository() -> None:
    assert (HARNESS_ROOT / "pyproject.toml").is_file()
    assert (HARNESS_ROOT / "src" / "tests" / "test_driver_combos.py").is_file()


def test_every_stage_script_starts_with_shebang_and_strict_mode(
    ursa: RunConfig,
) -> None:
    for stage in Stage:
        lines = render_stage(stage, ursa).text.splitlines()
        assert lines[:2] == ["#!/bin/bash", "set -euo pipefail"]
        assert f'echo ">>> stage: {stage.value}"' in lines


def test_source_stage_clones_when_missing_and_guards_submodules(
    ursa: RunConfig,
) -> None:
    script = render_stage(Stage.SOURCE, ursa)
    clone = str(ursa.clone_dir)
    assert (
        "git clone --recurse-submodules --branch fix/all-examples-pass "
        f"git@github.com:benkozi/CECE.git {clone}"
    ) in script.text
    assert f"{clone}/extern/helm/libs" in script.text
    assert "log -1 --oneline" in script.text
    assert "pull --ff-only" not in script.text  # as-is without update_source


def test_source_stage_update_source_fast_forwards(tmp_path: Path) -> None:
    config = RunConfig.from_yaml(
        run_config_file(
            tmp_path, overrides={"cece.update_source": True}, root_dir=_URSA_ROOT
        )
    )
    script = render_stage(Stage.SOURCE, config)
    assert "git -C" in script.text and "fetch origin" in script.text
    assert "checkout fix/all-examples-pass" in script.text
    assert "pull --ff-only origin fix/all-examples-pass" in script.text
    assert "submodule update --init --recursive" in script.text


def test_native_build_stage_loads_modules_and_gates_configure(ursa: RunConfig) -> None:
    script = render_stage(Stage.BUILD, ursa)
    clone = str(ursa.clone_dir)
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
    script = render_stage(Stage.BUILD, local)
    assert "module" not in script.text
    assert "cmake" not in script.text
    assert (
        f"python3 {local.clone_dir}/scripts/build-and-test-container.py --no-test "
        "--target cece_standalone_driver --jobs 4"
    ) in script.text


def test_data_stage_downloads_examples_and_warms_cartopy(ursa: RunConfig) -> None:
    # CECE's examples tooling needs Python >= 3.11; after `module purge` the
    # only python3 on Ursa is the OS one, so the harness venv's runs it.
    script = render_stage(Stage.DATA, ursa)
    lines = script.text.splitlines()
    download = (
        f"uv run --no-sync python {ursa.clone_dir}/examples/download-example-data.py "
        f"--example ex3 --dst-dir {ursa.clone_dir}/data"
    )
    assert download in lines
    assert lines.index(f"cd {HARNESS_ROOT}") < lines.index(download)
    assert "python3" not in script.text
    assert "natural_earth" in script.text


def test_data_stage_without_cartopy(local: RunConfig) -> None:
    assert "natural_earth" not in render_stage(Stage.DATA, local).text


def test_cece_tests_stage_submits_its_own_rendered_job_script(
    ursa_with_tests: RunConfig,
) -> None:
    config = ursa_with_tests
    script = render_stage(Stage.CECE_TESTS, config)
    job = f"{config.root_dir}/scripts/cece-tests.sbatch"
    assert f"sbatch --wait --parsable {job}" in script.text
    assert "cece-tests.sbatch" in script.companions
    text = script.companions["cece-tests.sbatch"]
    assert "#SBATCH -A epic" in text and "#SBATCH --time=30" in text
    assert f"#SBATCH --chdir={config.clone_dir}" in text
    assert "module load cece_ursa.intelllvm" in text
    assert text.rstrip().endswith(
        f"srun --ntasks=1 ctest --test-dir {config.clone_dir}/build --output-on-failure"
    )


def test_harness_stage_exports_every_setting_and_runs_pytest(ursa: RunConfig) -> None:
    text = render_stage(Stage.HARNESS, ursa).text
    for line in (
        f"export CECE_ROOT_DIR={ursa.clone_dir}",
        "export CECE_PLATFORM=ursa",
        "export CECE_RUNTIME=slurm",
        "export CECE_SBATCH_ARGS='-A epic -q debug -p u1-compute -N 1 -n 1 -c 8'",
        "export CECE_MODULEFILE=cece_ursa.intelllvm",
        "export CECE_JOB_ENV='I_MPI_FABRICS=shm FI_PROVIDER=tcp'",  # the driver jobs' env
        "export CECE_ENABLE_BASELINE_COMPARISONS=false",
        "export CECE_RUN_TIMEOUT_S=300",
        "export CECE_DASK_NWORKERS=2",
        f"export UV_CACHE_DIR={ursa.root_dir}/uv-cache",
        f"cd {HARNESS_ROOT}",
        "uv run --no-sync pytest src/tests/test_driver_combos.py "
        "--suite-config=simple-maccity-suite.yaml "
        "--combo-output-root=ufs-chem-assay-output --combo-clean-root",
    ):
        assert line in text, line
    for absent in (
        "CECE_BASELINE_ROOT_DIR",
        "UV_PYTHON",
        "UV_OFFLINE",
        "CECE_LAUNCHER",
        "SLURM_CPUS_PER_TASK",
    ):
        assert absent not in text, absent


def test_native_harness_stage_keeps_the_launcher(tmp_path: Path) -> None:
    # Inside an salloc shell: CECE_RUNTIME=native with a plain launcher.
    config = RunConfig.from_yaml(
        run_config_file(
            tmp_path,
            overrides={
                "harness.launcher": "srun --ntasks=1",
                "harness.runtime": "native",
            },
            root_dir=_URSA_ROOT,
        )
    )
    text = render_stage(Stage.HARNESS, config).text
    assert "export CECE_RUNTIME=native" in text
    assert "export CECE_LAUNCHER='srun --ntasks=1'" in text
    assert "CECE_SBATCH_ARGS" not in text


def test_local_harness_stage_has_no_slurm_or_offline_bits(local: RunConfig) -> None:
    text = render_stage(Stage.HARNESS, local).text
    assert "export CECE_PLATFORM=local" in text
    assert "export CECE_RUNTIME=docker" in text
    assert "CECE_LAUNCHER" not in text
    assert "UV_OFFLINE" not in text
    assert "SLURM" not in text
    assert "module" not in text


def test_platform_override_changes_runtime_exports() -> None:
    config = RunConfig.from_yaml(TEMPLATES_DIR / "ursa.yaml", platform=Platform.LOCAL)
    text = render_stage(Stage.HARNESS, config).text
    assert "export CECE_PLATFORM=local" in text
    assert "export CECE_RUNTIME=docker" in text
