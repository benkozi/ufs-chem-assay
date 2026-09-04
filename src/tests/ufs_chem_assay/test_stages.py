"""Stage rendering: the shell the CLI writes for each run-config template.
Golden-line assertions — the runbook (docs/ursa-runbook.md) and these scripts
must stay recognisably the same commands."""

from pathlib import Path

import pytest

from cli.run_config import RunConfig
from cli.stages import (
    COMPUTE_STAGES,
    HARNESS_ROOT,
    Stage,
    render_batch,
    render_stage,
)
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


def test_compute_stages_are_the_last_two() -> None:
    assert COMPUTE_STAGES == (Stage.CECE_TESTS, Stage.HARNESS)


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


def test_cece_tests_stage_runs_ctest_under_launcher(ursa_with_tests: RunConfig) -> None:
    script = render_stage(Stage.CECE_TESTS, ursa_with_tests)
    assert (
        f"srun --ntasks=1 ctest --test-dir {ursa_with_tests.clone_dir}/build "
        "--output-on-failure"
    ) in script.text


def test_harness_stage_exports_every_setting_and_runs_pytest(ursa: RunConfig) -> None:
    text = render_stage(Stage.HARNESS, ursa).text
    for line in (
        f"export CECE_ROOT_DIR={ursa.clone_dir}",
        "export CECE_PLATFORM=ursa",
        "export CECE_RUNTIME=native",
        "export CECE_LAUNCHER='srun --ntasks=1'",
        "export CECE_ENABLE_BASELINE_COMPARISONS=false",
        "export CECE_RUN_TIMEOUT_S=300",
        'export CECE_DASK_NWORKERS="${SLURM_CPUS_PER_TASK}"',
        f"export UV_CACHE_DIR={ursa.root_dir}/uv-cache",
        "export UV_OFFLINE=1",
        "export I_MPI_FABRICS=shm",
        "export FI_PROVIDER=tcp",
        f"cd {HARNESS_ROOT}",
        "uv run --no-sync pytest src/tests/test_driver_combos.py "
        "--suite-config=simple-maccity-suite.yaml "
        "--combo-output-root=ufs-chem-assay-output --combo-clean-root",
    ):
        assert line in text, line
    assert "CECE_BASELINE_ROOT_DIR" not in text
    assert "UV_PYTHON" not in text


def test_local_harness_stage_has_no_slurm_or_offline_bits(local: RunConfig) -> None:
    text = render_stage(Stage.HARNESS, local).text
    assert "export CECE_PLATFORM=local" in text
    assert "export CECE_RUNTIME=docker" in text
    assert "CECE_LAUNCHER" not in text
    assert "UV_OFFLINE" not in text
    assert "SLURM" not in text
    assert "module" not in text


def test_batch_script_wraps_compute_stages(ursa_with_tests: RunConfig) -> None:
    config = ursa_with_tests
    batch = render_batch(config, [Stage.CECE_TESTS, Stage.HARNESS])
    lines = batch.text.splitlines()
    assert lines[0] == "#!/bin/bash"
    assert "#SBATCH -A epic -q debug -p u1-compute -N 1 -n 1 -c 8 -t 00:30:00" in lines
    assert f"#SBATCH -J ufs-chem-assay -o {config.root_dir}/logs/slurm-%j.out" in lines
    assert lines.count("module load cece_ursa.intelllvm") == 1  # one preamble
    assert lines.count("set -euo pipefail") == 1
    ctest = next(
        i for i, line in enumerate(lines) if line.startswith("srun --ntasks=1 ctest")
    )
    assert lines.index("module load cece_ursa.intelllvm") < ctest
    assert (
        lines.index('echo ">>> stage: cece-tests"')
        < ctest
        < lines.index('echo ">>> stage: harness"')
    )
    assert any(line.startswith("uv run --no-sync pytest") for line in lines)
    # Stand-alone and batch renderings share the same stage body.
    body = render_stage(Stage.HARNESS, config).text.splitlines()
    pytest_line = next(
        line for line in body if line.startswith("uv run --no-sync pytest")
    )
    assert pytest_line in lines


def test_batch_needs_slurm(local: RunConfig) -> None:
    with pytest.raises(ValueError, match="slurm"):
        render_batch(local, [Stage.HARNESS])


def test_batch_rejects_login_stages(ursa: RunConfig) -> None:
    with pytest.raises(ValueError, match="login"):
        render_batch(ursa, [Stage.BUILD])


def test_platform_override_changes_runtime_exports() -> None:
    config = RunConfig.from_yaml(TEMPLATES_DIR / "ursa.yaml", platform=Platform.LOCAL)
    text = render_stage(Stage.HARNESS, config).text
    assert "export CECE_PLATFORM=local" in text
    assert "export CECE_RUNTIME=docker" in text
