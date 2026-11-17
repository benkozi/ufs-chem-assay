"""Stage rendering: the shell the CLI writes for each run-config template,
per application. Golden-line assertions — the runbook (docs/ursa-runbook.md)
and these scripts must stay recognisably the same commands."""

import os
import shlex
from pathlib import Path

import pytest

from applications.base import Application, Stage
from applications.registry import REGISTRY, get_application
from cli.run_config import HARNESS_ROOT, ResolvedSession, RunConfig
from cli.stages import SCRIPT_INDEX, render_stage
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


def _first(config: RunConfig) -> ResolvedSession:
    """The config's first session (its only one without sessions:)."""
    return config.resolved_sessions()[0]


def _session(config: RunConfig, name: str) -> ResolvedSession:
    return next(s for s in config.resolved_sessions() if s.name == name)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Application:
    app = stub_application()
    monkeypatch.setitem(REGISTRY, "stub", app)
    return app


def test_harness_root_is_the_repository() -> None:
    assert (HARNESS_ROOT / "pyproject.toml").is_file()
    assert (HARNESS_ROOT / "src" / "tests" / "test_driver_combos.py").is_file()


def test_every_stage_script_starts_with_shebang_and_strict_mode(
    ursa: RunConfig, local: RunConfig
) -> None:
    # The per-session stages (harness, resume) carry the session in their
    # name and banner; the shared ones do not. A nameless session (local)
    # keeps the bare names.
    per_session = {Stage.HARNESS, Stage.RESUME}
    for stage in Stage:
        if stage is Stage.TEST:
            continue  # rendered only with tests.enabled (below)
        script = render_stage(stage, ursa, _CECE, _session(ursa, "simple-maccity"))
        suffix = "-simple-maccity" if stage in per_session else ""
        assert script.name == f"{stage.value}-cece{suffix}"
        lines = script.text.splitlines()
        assert lines[:2] == ["#!/bin/bash", "set -euo pipefail"]
        who = "cece/simple-maccity" if stage in per_session else "cece"
        assert f'echo ">>> stage: {stage.value} ({who})"' in lines
    for stage in Stage:
        if stage in {Stage.RESUME, Stage.TEST}:
            continue  # not rendered without submit_only / tests.enabled (below)
        script = render_stage(stage, local, _CECE)
        assert script.name == f"{stage.value}-cece"
        assert f'echo ">>> stage: {stage.value} (cece)"' in script.text.splitlines()


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
    # cartopy's Natural Earth data goes under the run root, and the warm-up
    # — the harness's own, so it honours that directory — runs after it.
    cartopy_dir = f"export ASSAY_CARTOPY_DATA_DIR={ursa.root_dir}/cartopy"
    warm_up = (
        "uv run --no-sync python -c "
        "'from plotting import warm_natural_earth; warm_natural_earth()'"
    )
    assert lines.index(cartopy_dir) < lines.index(warm_up)
    assert lines.index(f"cd {HARNESS_ROOT}") < lines.index(fetch)
    assert lines.index(fetch) < lines.index(
        next(line for line in lines if "natural_earth" in line)
    )
    assert "download-example-data" not in script.text
    assert "python3" not in script.text
    # One fetch block per session, under that session's exports: the
    # gcafs-anthro session stages its own suite's inputs with its own
    # timeout cap exported; the cartopy warm-up renders once, last.
    gcafs_fetch = fetch.replace("simple-maccity", "gcafs-anthro")
    assert gcafs_fetch in lines
    assert lines.index(fetch) < lines.index(gcafs_fetch)
    assert "export ASSAY_RUN_TIMEOUT_S=300" in lines
    assert "export ASSAY_RUN_TIMEOUT_S=28800" in lines
    assert lines.index("export ASSAY_RUN_TIMEOUT_S=28800") < lines.index(gcafs_fetch)
    assert sum("natural_earth" in line for line in lines) == 1
    assert lines.index(gcafs_fetch) < lines.index(
        next(line for line in lines if "natural_earth" in line)
    )
    # Narrowed to some sessions, the data stage fetches only theirs.
    only = render_stage(
        Stage.DATA, ursa, _CECE, sessions=[_session(ursa, "gcafs-anthro")]
    ).text
    assert gcafs_fetch in only
    assert fetch not in only


def test_data_stage_exports_match_the_harness_stage(ursa: RunConfig) -> None:
    # One export block per session, rendered by both stages: whatever a
    # session's harness stage exports, its data block exports too (so fetch
    # resolves the same suites under the same settings).
    data_exports = {
        line
        for line in render_stage(Stage.DATA, ursa, _CECE).text.splitlines()
        if line.startswith("export ")
    }
    union: set[str] = set()
    for session in ursa.resolved_sessions():
        harness_exports = {
            line
            for line in render_stage(
                Stage.HARNESS, ursa, _CECE, session
            ).text.splitlines()
            if line.startswith("export ")
        }
        assert harness_exports <= data_exports
        union |= harness_exports
    assert union == data_exports


def test_data_stage_fetches_once_for_sessions_that_fetch_the_same(
    tmp_path: Path,
) -> None:
    # Two sessions with the same selector under the same exports stage the
    # same inputs: one fetch block, not two.
    config = _ursa(
        tmp_path, {"sessions": {"a": {}, "b": {}, "c": {"slurm": {"qos": "x"}}}}
    )
    lines = render_stage(Stage.DATA, config, _CECE).text.splitlines()
    fetches = [line for line in lines if "ufs-chem-assay fetch" in line]
    assert len(fetches) == 2  # a and b share one; c's exports differ (-q x)
    assert sum(line.startswith("cd ") for line in lines) == 2


def test_data_stage_without_cartopy(local: RunConfig) -> None:
    assert "natural_earth" not in render_stage(Stage.DATA, local, _CECE).text


def test_shared_data_lines_render_once_per_run(ursa: RunConfig) -> None:
    # The cartopy warm-up is harness-wide: under the first application only.
    assert "natural_earth" in render_stage(Stage.DATA, ursa, _CECE).text
    assert (
        "natural_earth"
        not in render_stage(Stage.DATA, ursa, _CECE, shared_data=False).text
    )


def test_test_stage_is_slot_04_between_build_and_data() -> None:
    # The application's own tests (issue #9) run against the build, before
    # the driver sessions: the slot the script numbering always held.
    assert [stage.value for stage in Stage] == [
        "source",
        "build",
        "test",
        "data",
        "harness",
        "resume",
    ]
    assert [SCRIPT_INDEX[stage] for stage in Stage] == [1, 2, 4, 3, 5, 6]


def test_harness_stage_exports_every_setting_and_runs_pytest(ursa: RunConfig) -> None:
    script = render_stage(Stage.HARNESS, ursa, _CECE, _session(ursa, "simple-maccity"))
    assert script.name == "harness-cece-simple-maccity"
    text = script.text
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
        "export ASSAY_OUTPUT_STORE=s3://ufs-chem/assay-output",
        "export ASSAY_RUN_TIMEOUT_S=300",
        "export ASSAY_SLURM_QUEUE_WAIT_S=3600",
        "export ASSAY_SLURM_POLL_S=30",  # the asynchronous wait's cadence
        "export ASSAY_DASK_NWORKERS=2",
        f"export ASSAY_CARTOPY_DATA_DIR={ursa.root_dir}/cartopy",  # the plots' map data
        f"export UV_CACHE_DIR={ursa.root_dir}/uv-cache",
        f"cd {HARNESS_ROOT}",
        (
            "uv run --no-sync pytest src/tests/test_driver_combos.py "
            "--application=cece --suite-config=simple-maccity-suite.yaml "
            "--combo-output-root=ufs-chem-assay-output/simple-maccity "
            "--combo-clean-root --submit-only"
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
        "--upload-output",  # off by default everywhere
    ):
        assert absent not in text, absent
    assert "--record-benchmarks" in text  # the Ursa template keeps every run


def test_harness_stage_renders_the_upload_flag_when_asked(tmp_path: Path) -> None:
    config = _ursa(
        tmp_path, {"harness.upload_output": True, "outputs.store": "/scratch/outputs"}
    )
    text = render_stage(Stage.HARNESS, config, _CECE, _first(config)).text
    assert "--upload-output" in text
    assert "export ASSAY_OUTPUT_STORE=/scratch/outputs" in text


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
    text = render_stage(Stage.HARNESS, config, _CECE, _first(config)).text
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
            "harness.submit_only": False,  # slurm only
        },
    )
    text = render_stage(Stage.HARNESS, config, _CECE, _first(config)).text
    assert "export ASSAY_RUNTIME=native" in text
    assert "export ASSAY_LAUNCHER='srun --ntasks=1'" in text
    assert "ASSAY_SBATCH_ARGS" not in text


def test_local_harness_stage_has_no_slurm_or_offline_bits(local: RunConfig) -> None:
    assert (
        "--record-benchmarks"
        not in render_stage(Stage.HARNESS, local, _CECE, _first(local)).text
    )
    text = render_stage(Stage.HARNESS, local, _CECE, _first(local)).text
    assert "export ASSAY_PLATFORM=local" in text
    assert "export ASSAY_RUNTIME=docker" in text
    assert "ASSAY_LAUNCHER" not in text
    assert "UV_OFFLINE" not in text
    assert "SLURM" not in text
    assert "module" not in text


def test_platform_override_changes_runtime_exports() -> None:
    config = load_run_config(
        TEMPLATES_DIR / "ursa.yaml",
        platform=Platform.LOCAL,
        overrides=["harness:submit_only=false"],
    )
    text = render_stage(Stage.HARNESS, config, _CECE, _first(config)).text
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
        text = render_stage(stage, config, _CECE, _first(config)).text
        assert f"export ASSAY_INPUT_SEARCH_PATH={os.pathsep.join(dirs)}" in text


def test_ursa_template_renders_gcafs_anthro_as_shipped(tmp_path: Path) -> None:
    config = load_run_config(run_config_file(tmp_path, root_dir=_URSA_ROOT))
    script = render_stage(
        Stage.HARNESS, config, _CECE, _session(config, "gcafs-anthro")
    )
    assert script.name == "harness-cece-gcafs-anthro"
    text = script.text
    for line in (
        "export ASSAY_SBATCH_ARGS='-A epic -q debug -p u1-compute -N 1 -n 1 -c 1 --mem=0'",
        "export ASSAY_SRUN_ARGS='--ntasks=1 --cpu-bind=cores'",
        "export ASSAY_RUN_TIMEOUT_S=28800",
        "export ASSAY_SLURM_QUEUE_WAIT_S=3600",
        "--suite-config=gcafs-anthro-suite.yaml",
        "--combo-output-root=ufs-chem-assay-output/gcafs-anthro --combo-clean-root --submit-only",
        "--record-benchmarks",
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


def test_resume_stage_reopens_the_sessions_root(tmp_path: Path) -> None:
    # The resume session is the ordinary session minus submission: the same
    # exports, --resume on the same root, every session flag the harness line
    # has except --combo-clean-root, --submit-only and --dry-run.
    config = _ursa(
        tmp_path,
        {
            "harness.pytest_dry_run": True,
            "harness.upload_output": True,
            "harness.pytest_args": ["-k", "map-consd"],
        },
    )
    session = _session(config, "gcafs-anthro")
    harness = render_stage(Stage.HARNESS, config, _CECE, session).text
    resume = render_stage(Stage.RESUME, config, _CECE, session)
    assert resume.name == "resume-cece-gcafs-anthro"
    exports = lambda text: [l for l in text.splitlines() if l.startswith("export ")]
    assert exports(resume.text) == exports(harness)
    harness_line = next(l for l in harness.splitlines() if "pytest src/tests" in l)
    resume_line = next(l for l in resume.text.splitlines() if "pytest src/tests" in l)
    assert (
        "--combo-clean-root --dry-run --submit-only --record-benchmarks "
        "--upload-output -k map-consd"
    ) in harness_line
    assert (
        "--application=cece --suite-config=gcafs-anthro-suite.yaml --resume "
        "--combo-output-root=ufs-chem-assay-output/gcafs-anthro --record-benchmarks "
        "--upload-output -k map-consd"
    ) in resume_line
    for absent in ("--combo-clean-root", "--submit-only", "--dry-run"):
        assert absent not in resume_line, absent
    assert 'echo ">>> stage: resume (cece/gcafs-anthro)"' in resume.text


def test_resume_is_rendered_only_with_submit_only(local: RunConfig) -> None:
    with pytest.raises(ValueError, match="submit_only"):
        render_stage(Stage.RESUME, local, _CECE)


def test_harness_stage_needs_a_session_when_the_config_names_them(
    ursa: RunConfig, local: RunConfig
) -> None:
    with pytest.raises(ValueError, match="simple-maccity, gcafs-anthro"):
        render_stage(Stage.HARNESS, ursa, _CECE)
    # A config without sessions: the one nameless session is implied.
    script = render_stage(Stage.HARNESS, local, _CECE)
    assert script.name == "harness-cece"
    assert (
        "--combo-output-root=ufs-chem-assay-output/cece --combo-clean-root"
        in script.text
    )
    assert "--submit-only" not in script.text


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
    text = render_stage(Stage.HARNESS, geometry, _CECE, _first(geometry)).text
    assert "export ASSAY_SRUN_ARGS='--ntasks=4 --cpu-bind=cores'" in text
    assert (
        "export ASSAY_SBATCH_ARGS='-A epic -q debug -p u1-compute -N 1 -n 4 -c 8 --mem=0'"
        in text
    )
    assert (
        "ASSAY_SRUN_ARGS"
        not in render_stage(Stage.HARNESS, local, _CECE, _first(local)).text
    )


def test_harness_stage_exports_the_queue_allowance_under_slurm_only(
    ursa: RunConfig, local: RunConfig, tmp_path: Path
) -> None:
    assert (
        "export ASSAY_SLURM_QUEUE_WAIT_S=3600"
        in render_stage(Stage.HARNESS, ursa, _CECE, _first(ursa)).text
    )
    assert (
        "ASSAY_SLURM_QUEUE_WAIT_S"
        not in render_stage(Stage.HARNESS, local, _CECE, _first(local)).text
    )
    long_wait = _ursa(tmp_path, {"slurm.queue_wait_s": 28800})
    assert (
        "export ASSAY_SLURM_QUEUE_WAIT_S=28800"
        in render_stage(Stage.HARNESS, long_wait, _CECE, _first(long_wait)).text
    )


def test_regex_suite_selector_survives_quoting(tmp_path: Path) -> None:
    # Several suites in one session: the selector is a regex, every match runs.
    config = _ursa(tmp_path, {"harness.suite_config": "ex[0-9]-suite.yaml"})
    text = render_stage(Stage.HARNESS, config, _CECE, _first(config)).text
    assert "'--suite-config=ex[0-9]-suite.yaml'" in text


# ── Several applications in one run ──────────────────────────────────────────


def test_output_root_is_suffixed_with_the_application_and_the_session(
    tmp_path: Path, stub: Application, local: RunConfig
) -> None:
    assert (
        local.session_output_root(_CECE, _first(local)) == "ufs-chem-assay-output/cece"
    )
    two = _ursa(tmp_path, {"applications.stub": {"git_url": "u", "ref": "main"}})
    simple = _session(two, "simple-maccity")
    assert (
        two.session_output_root(_CECE, simple)
        == "ufs-chem-assay-output/cece/simple-maccity"
    )
    assert (
        two.session_output_root(stub, simple)
        == "ufs-chem-assay-output/stub/simple-maccity"
    )
    text = render_stage(Stage.HARNESS, two, stub, simple).text
    assert "--application=stub" in text
    assert "--combo-output-root=ufs-chem-assay-output/stub/simple-maccity" in text
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
    text = render_stage(Stage.HARNESS, config, _CECE, _first(config)).text
    assert "export ASSAY_BASELINE_STORE=s3://other/prefix" in text
    assert "export ASSAY_BASELINE_ROOT_DIR=/scratch/baselines" in text
    assert (
        "--combo-clean-root --submit-only --publish-baselines --no-suite-update" in text
    )


def test_harness_stage_exports_the_tolerance_overrides(tmp_path: Path) -> None:
    config = _ursa(tmp_path, {"baselines.atol": 1e-24, "baselines.rtol": 1e-15})
    text = render_stage(Stage.HARNESS, config, _CECE, _first(config)).text
    assert "export ASSAY_BASELINE_ATOL=1e-24" in text
    assert "export ASSAY_BASELINE_RTOL=1e-15" in text
    assert "ASSAY_BASELINE_RTOL" in render_stage(Stage.DATA, config, _CECE).text


def test_env_value_renders_an_enum_by_its_value() -> None:
    from cli.stages import _env_value
    from platforms import Runtime

    assert _env_value(Runtime.SLURM) == "slurm"
    assert _env_value(None) is None


def test_session_runtime_override_exports_per_session_runtime(tmp_path: Path) -> None:
    config = load_run_config(
        run_config_file(
            tmp_path,
            "local.yaml",
            root_dir=tmp_path / "runs",
            overrides={
                "sessions": {
                    "docker-s": {},
                    "native-s": {"harness": {"runtime": "native"}},
                }
            },
        )
    )
    docker_text = render_stage(
        Stage.HARNESS, config, _CECE, _session(config, "docker-s")
    ).text
    native_text = render_stage(
        Stage.HARNESS, config, _CECE, _session(config, "native-s")
    ).text
    assert "export ASSAY_RUNTIME=docker" in docker_text
    assert "export ASSAY_RUNTIME=native" in native_text


# ── The module environment: paths, exports, probes, gates ────────────────────

_KOKKOS_ENV: dict[str, object] = {
    "applications.cece.modulefile": "ufs_ursa.oneapi",
    "applications.cece.modulepath": ["modulefiles", "/envs/ue-oneapi/modules/Core"],
    "applications.cece.module_env": {"kokkos_ver": "4.5.01", "arborx_ver": "2.0"},
}


def test_default_module_environment_is_the_checkouts_modulefiles(
    ursa: RunConfig,
) -> None:
    # Nothing but `modulefile` set: purge, the clone's own modulefiles, the
    # compiler-variable probe — exactly what the runbook does by hand.
    lines = render_stage(Stage.BUILD, ursa, _CECE).text.splitlines()
    clone = str(ursa.checkout_dir(_CECE))
    start = lines.index("module purge")
    assert lines[start : start + 4] == [
        "module purge",
        f"module use {clone}/modulefiles",
        "module load cece_ursa.intelllvm",
        "module list",
    ]
    assert "which cmake ${CC:-} ${CXX:-} ${FC:-}" in lines
    assert not any("configure gate failed" in line for line in lines)


def test_build_stage_uses_the_configured_module_environment(tmp_path: Path) -> None:
    config = _ursa(tmp_path, _KOKKOS_ENV)
    lines = render_stage(Stage.BUILD, config, _CECE).text.splitlines()
    clone = str(config.checkout_dir(_CECE))
    start = lines.index("module purge")
    assert lines[start : start + 7] == [
        "module purge",
        "export kokkos_ver=4.5.01",  # before the load: the modulefile reads it
        "export arborx_ver=2.0",
        f"module use {HARNESS_ROOT}/modulefiles",  # relative: the harness checkout
        "module use /envs/ue-oneapi/modules/Core",  # absolute: as written
        "module load ufs_ursa.oneapi",
        "module list",
    ]
    assert f"module use {clone}/modulefiles" not in lines  # replaced, not added


def test_build_stage_probes_the_configured_toolchain_tools(tmp_path: Path) -> None:
    tools = ["mpiicx", "mpiicpx", "mpiifx", "cmake", "ctest"]
    config = _ursa(tmp_path, {"applications.cece.toolchain_tools": tools})
    lines = render_stage(Stage.BUILD, config, _CECE).text.splitlines()
    assert "which mpiicx mpiicpx mpiifx cmake ctest" in lines
    assert "which cmake ${CC:-} ${CXX:-} ${FC:-}" not in lines
    assert lines.index("module list") < lines.index(
        "which mpiicx mpiicpx mpiifx cmake ctest"
    )


def test_build_stage_gates_the_configure_log_on_the_configured_claims(
    tmp_path: Path,
) -> None:
    gates = [
        "CECE dependency Kokkos: system",
        "AXIS: ArborX .* found via find_package",
    ]
    config = _ursa(tmp_path, {"applications.cece.configure_gates": gates})
    lines = render_stage(Stage.BUILD, config, _CECE).text.splitlines()
    log = f"{config.checkout_dir(_CECE)}/build/configure.log"
    build = next(i for i, line in enumerate(lines) if line.startswith("cmake --build"))
    for gate in gates:
        failing = (
            f"grep -Eq {shlex.quote(gate)} {log} || "
            f"{{ echo {shlex.quote('configure gate failed: ' + gate)} >&2; exit 1; }}"
        )
        showing = f"grep -E {shlex.quote(gate)} {log}"  # the matched line, in the log
        assert lines.index(failing) + 1 == lines.index(showing)
        assert lines.index(showing) < build  # a failed gate never builds
    # The adapter's own gates stay ahead of the configured ones.
    mpi = next(i for i, line in enumerate(lines) if "Found MPI" in line)
    assert mpi < lines.index(f"grep -E {shlex.quote(gates[0])} {log}")


def test_gaea_resets_modules_where_ursa_purges(tmp_path: Path) -> None:
    config = _ursa(tmp_path, {"platform": "gaea-c7"})
    assert config.platform is Platform.GAEA_C7
    build = render_stage(Stage.BUILD, config, _CECE).text.splitlines()
    assert "module reset" in build
    assert "module purge" not in build
    # The harness venv's shell is cleaned the same way: never a purge on Gaea.
    for stage in (Stage.DATA, Stage.HARNESS):
        lines = render_stage(
            stage, config, _CECE, _session(config, "simple-maccity")
        ).text.splitlines()
        assert "module reset" in lines
        assert "module purge" not in lines
        assert lines.index("module reset") + 1 == lines.index("unset PYTHONPATH")
    assert "export ASSAY_PLATFORM=gaea-c7" in lines


def test_harness_stage_exports_the_module_environment_for_the_driver_jobs(
    ursa: RunConfig, tmp_path: Path
) -> None:
    # The driver jobs must load what the build loaded: the pytest session
    # reads the paths and exports from the application's settings.
    config = _ursa(tmp_path, _KOKKOS_ENV)
    for stage in (Stage.DATA, Stage.HARNESS):
        text = render_stage(stage, config, _CECE, _first(config)).text
        assert "export CECE_MODULEFILE=ufs_ursa.oneapi" in text
        assert (
            f"export CECE_MODULEPATH={HARNESS_ROOT}/modulefiles"
            f"{os.pathsep}/envs/ue-oneapi/modules/Core"
        ) in text
        assert "export CECE_MODULE_ENV='kokkos_ver=4.5.01 arborx_ver=2.0'" in text
    # Defaults are not exported (the settings' own defaults apply), and the
    # build-only keys never are.
    text = render_stage(Stage.HARNESS, ursa, _CECE, _first(ursa)).text
    for absent in (
        "CECE_MODULEPATH",
        "CECE_MODULE_ENV",
        "CECE_TOOLCHAIN_TOOLS",
        "CECE_CONFIGURE_GATES",
    ):
        assert absent not in text, absent


# ── Build placement: the login node, or a compute node behind srun ───────────

# The Ursa template's slurm: section as the compile step's srun (one node, one
# task, build_jobs cpus, build_minutes).
_BUILD_SRUN = (
    "srun -A epic -q debug -p u1-compute --nodes=1 --ntasks=1 "
    "--cpus-per-task=8 --time=10"
)
_TESTED_CECE: dict[str, object] = {
    "applications.cece.cmake_args": ["-DCMAKE_BUILD_TYPE=Debug", "-DENABLE_ESMF=ON"],
    "applications.cece.targets": ["all"],
    "applications.cece.tests": {"enabled": True},
}


def _starting(lines: list[str], prefix: str) -> list[int]:
    return [index for index, line in enumerate(lines) if line.startswith(prefix)]


def test_login_build_is_the_default_and_says_so(ursa: RunConfig) -> None:
    lines = render_stage(Stage.BUILD, ursa, _CECE).text.splitlines()
    assert lines[3] == "echo 'build node: login'"  # first, after the banner
    assert not any("srun" in line for line in lines)
    # One configure with the cmake_args as written: no test-stack flag added.
    assert len(_starting(lines, "cmake -S")) == 1
    assert "CECE_BUILD_TESTING" not in "\n".join(lines)


def test_compute_build_probes_offline_behind_srun(tmp_path: Path) -> None:
    config = _ursa(tmp_path, {"applications.cece.build_node": "compute"})
    lines = render_stage(Stage.BUILD, config, _CECE).text.splitlines()
    clone = str(config.checkout_dir(_CECE))
    assert (
        lines[3] == f"echo {shlex.quote('build node: compute (' + _BUILD_SRUN + ')')}"
    )
    # Configure and build are two srun steps (two allocations), never one
    # spanning both; the configure has the test stack off, because GTest and
    # RapidCheck are fetched at configure time and compute nodes have no network.
    configure, build = _starting(lines, "srun ")
    assert lines[configure] == (
        f"{_BUILD_SRUN} cmake -S {clone} -B {clone}/build -DCMAKE_BUILD_TYPE=Release "
        f"-DCECE_BUILD_TESTING=OFF 2>&1 | tee {clone}/build/configure.log"
    )
    assert lines[build] == (
        f"{_BUILD_SRUN} cmake --build {clone}/build "
        "--target cece_standalone_driver --parallel 8"
    )
    note = lines[configure - 1]
    assert note.startswith("echo ")
    assert "-DCECE_BUILD_TESTING=OFF" in note
    assert "no network" in note
    # Modules and the probe run in the login shell (srun inherits them); the
    # gates sit between the two steps.
    assert lines.index("module list") < configure
    assert _starting(lines, "which ")[0] < configure
    mpi = next(i for i, line in enumerate(lines) if "Found MPI" in line)
    assert configure < mpi < build
    # Tests are off: the stage ends with the probe — no login-node finish.
    assert build == len(lines) - 1
    assert len([line for line in lines if "cmake -S" in line]) == 1


def test_compute_build_launcher_follows_the_section_and_the_slurm_keys(
    tmp_path: Path,
) -> None:
    config = _ursa(
        tmp_path,
        {
            "applications.cece.build_node": "compute",
            "applications.cece.build_minutes": 25,
            "applications.cece.build_jobs": 4,
            "slurm.account": "bil-fire3",
            "slurm.qos": "normal",
            "slurm.partition": "batch",
            "slurm.extra_args": "--clusters=c6",
        },
    )
    lines = render_stage(Stage.BUILD, config, _CECE).text.splitlines()
    launcher = (
        "srun -A bil-fire3 -q normal -p batch --clusters=c6 --nodes=1 --ntasks=1 "
        "--cpus-per-task=4 --time=25"
    )
    assert len(_starting(lines, f"{launcher} cmake ")) == 2
    assert lines[-1].endswith("--parallel 4")


def test_compute_build_finishes_on_the_login_node_when_tests_are_enabled(
    tmp_path: Path,
) -> None:
    config = _ursa(
        tmp_path, {"applications.cece.build_node": "compute", **_TESTED_CECE}
    )
    lines = render_stage(Stage.BUILD, config, _CECE).text.splitlines()
    clone = str(config.checkout_dir(_CECE))
    args = "-DCMAKE_BUILD_TYPE=Debug -DENABLE_ESMF=ON"
    where = f"build node: compute ({_BUILD_SRUN}), then login (tests)"
    assert lines[3] == f"echo {shlex.quote(where)}"
    # Phase 1, the compute-node probe; phase 2, the login-node finish: the
    # same build directory re-configured with the test stack on, the gates
    # again, the build reusing the compute-built objects.
    probe_configure, probe_build = _starting(lines, "srun ")
    finish_configure, finish_build = _starting(lines, "cmake ")
    assert probe_configure < probe_build < finish_configure < finish_build
    assert lines[probe_configure] == (
        f"{_BUILD_SRUN} cmake -S {clone} -B {clone}/build {args} "
        f"-DCECE_BUILD_TESTING=OFF 2>&1 | tee {clone}/build/configure.log"
    )
    assert lines[finish_configure] == (
        f"cmake -S {clone} -B {clone}/build {args} "
        f"-DCECE_BUILD_TESTING=ON 2>&1 | tee {clone}/build/configure.log"
    )
    assert lines[probe_build] == (
        f"{_BUILD_SRUN} cmake --build {clone}/build --target all --parallel 8"
    )
    assert lines[finish_build] == (
        f"cmake --build {clone}/build --target all --parallel 8"
    )
    assert finish_build == len(lines) - 1
    assert "-DCECE_BUILD_TESTING=ON" in lines[finish_configure - 1]  # the note
    # Every gate is checked after each configure.
    for needle in ("Found MPI", "netcdf", "CECE dependency ESMF: system"):
        gates = [
            i for i, line in enumerate(lines) if needle in line and "grep -" in line
        ]
        assert any(probe_configure < i < probe_build for i in gates), needle
        assert any(finish_configure < i < finish_build for i in gates), needle


def test_login_build_with_tests_is_one_configure(tmp_path: Path) -> None:
    # On the login node the one configure is the build: the test stack is
    # what cmake_args and targets say, nothing is turned off or back on.
    config = _ursa(tmp_path, _TESTED_CECE)
    lines = render_stage(Stage.BUILD, config, _CECE).text.splitlines()
    assert lines[3] == "echo 'build node: login'"
    assert len(_starting(lines, "cmake -S")) == 1
    assert "CECE_BUILD_TESTING" not in "\n".join(lines)
    assert lines[-1].endswith("--target all --parallel 8")


def test_esmf_builds_gate_on_the_systems_esmf(ursa: RunConfig, tmp_path: Path) -> None:
    # The adapter's own gate: with ESMF on, the configure must have found
    # the module environment's ESMF. Ahead of the run config's gates.
    gate = "CECE dependency ESMF: system"
    assert gate not in render_stage(Stage.BUILD, ursa, _CECE).text
    config = _ursa(
        tmp_path,
        {
            "applications.cece.cmake_args": ["-DENABLE_ESMF=ON"],
            "applications.cece.configure_gates": ["CECE dependency Kokkos: system"],
        },
    )
    lines = render_stage(Stage.BUILD, config, _CECE).text.splitlines()
    log = f"{config.checkout_dir(_CECE)}/build/configure.log"
    failing = (
        f"grep -Eq {shlex.quote(gate)} {log} || "
        f"{{ echo {shlex.quote('configure gate failed: ' + gate)} >&2; exit 1; }}"
    )
    kokkos = f"grep -E {shlex.quote('CECE dependency Kokkos: system')} {log}"
    assert lines.index(failing) < lines.index(kokkos)


def test_docker_build_has_no_build_node(local: RunConfig) -> None:
    # The container build script owns the whole build: no node to name.
    assert "build node" not in render_stage(Stage.BUILD, local, _CECE).text


# ── The test stage: the application's own tests, against the build ───────────

_TESTS: dict[str, object] = {
    "enabled": True,
    "ntasks": 4,
    "cpus": 8,
    "minutes": 5,
    "env": {"I_MPI_HYDRA_BOOTSTRAP": "fork"},
}
_NO_TESTS_BUILT = (
    "the build stage builds the test stack when applications.cece.tests.enabled is true"
)


def _tests_on(tmp_path: Path, **overrides: object) -> RunConfig:
    return _ursa(
        tmp_path,
        {**_TESTED_CECE, "applications.cece.tests": {**_TESTS}, **overrides},
    )


def test_test_stage_is_rendered_only_with_tests_enabled(
    ursa: RunConfig, stub: Application, tmp_path: Path
) -> None:
    with pytest.raises(
        ValueError, match=r"applications\.cece\.tests\.enabled is false"
    ):
        render_stage(Stage.TEST, ursa, _CECE)
    config = _ursa(
        tmp_path,
        {"applications.stub": {"git_url": "u", "ref": "m", "tests": {"enabled": True}}},
    )
    with pytest.raises(ValueError, match="stub has no test suite of its own"):
        render_stage(Stage.TEST, config, stub)


def test_slurm_test_stage_guards_then_submits_through_the_harness(
    tmp_path: Path,
) -> None:
    config = _tests_on(tmp_path)
    script = render_stage(Stage.TEST, config, _CECE)
    assert script.name == "test-cece"
    lines = script.text.splitlines()
    clone = config.checkout_dir(_CECE)
    assert lines[:3] == [
        "#!/bin/bash",
        "set -euo pipefail",
        'echo ">>> stage: test (cece)"',
    ]
    # The guard: the test stage never compiles, so the test stack must be there.
    assert lines[3].startswith(f"[ -f {clone}/build/CTestTestfile.cmake ] || {{ echo ")
    assert _NO_TESTS_BUILT in lines[3]
    assert lines[3].endswith(">&2; exit 1; }")
    # The submit is the harness's own command, in the harness's clean
    # environment, from the harness checkout — as the data stage calls fetch.
    assert lines[-1] == (
        "uv run --no-sync ufs-chem-assay submit-tests "
        f"--root-dir {config.root_dir} --application cece"
    )
    assert lines[-2] == f"cd {HARNESS_ROOT}"
    assert lines.index("module purge") + 1 == lines.index("unset PYTHONPATH")
    assert f"export UV_CACHE_DIR={config.root_dir}/uv-cache" in lines
    # Nothing compiles, nothing runs here: the tests are the job's.
    for absent in ("cmake -", "ctest ", "sbatch ", "srun ", "module load"):
        assert absent not in script.text, absent


def test_slurm_test_job_runs_ctest_directly_on_the_node(tmp_path: Path) -> None:
    config = _tests_on(tmp_path, **{"slurm.extra_args": "--clusters=c6"})
    script = render_stage(Stage.TEST, config, _CECE)
    assert list(script.attachments) == [".sbatch"]
    lines = script.attachments[".sbatch"].splitlines()
    clone = config.checkout_dir(_CECE)
    tests_root = config.root_dir / "tests" / "cece"
    assert lines[0] == "#!/bin/bash"
    directives = [line for line in lines if line.startswith("#SBATCH")]
    assert directives == [
        "#SBATCH --job-name=ufs-chem-assay-tests-cece",
        f"#SBATCH --output={tests_root}/tests-%j.out",  # named by the job id
        f"#SBATCH --chdir={clone}",
        "#SBATCH --time=5",
        "#SBATCH -A epic",
        "#SBATCH -q debug",
        "#SBATCH -p u1-compute",
        "#SBATCH -N 1",
        "#SBATCH -n 4",  # tests.ntasks: the suite's largest rank count
        "#SBATCH -c 8",
        "#SBATCH --clusters=c6",
    ]
    body = lines[len(directives) + 1 :]
    assert body[0] == "set -euo pipefail"
    # The module environment the build loaded, then the tests' own exports.
    load = body.index("module load cece_ursa.intelllvm")
    assert body[load - 2 : load] == ["module purge", f"module use {clone}/modulefiles"]
    assert load < body.index("export I_MPI_HYDRA_BOOTSTRAP=fork")
    # ctest is the launcher: it runs on the node, never behind srun (each
    # MPI test is its own step or fork), with the junit named by the job id.
    command = (
        "ctest --test-dir build --output-on-failure "
        f'--output-junit {tests_root}/tests-"$SLURM_JOB_ID".xml'
    )
    # A failing suite must still close its clock and report its memory: the
    # status is held, not left to `set -e`, and is the job's exit.
    launch = body.index(f"{command} || status=$?")
    assert body[launch - 2] == 'echo "tic: $(date -u +%Y-%m-%dT%H:%M:%SZ)"'
    assert body[launch - 1] == "status=0"
    assert body[launch + 1] == 'echo "toc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"'
    assert body[-2].startswith("if command -v report-mem")
    assert body[-1] == 'exit "$status"'
    assert not any(line.startswith("srun") for line in body)
    assert "cmake" not in script.attachments[".sbatch"]


def test_test_command_takes_the_filter_and_the_parallelism(tmp_path: Path) -> None:
    tests = {**_TESTS, "filter": "^Cece(Axis|Core)", "parallel": 4}
    config = _tests_on(tmp_path, **{"applications.cece.tests": tests})
    job = render_stage(Stage.TEST, config, _CECE).attachments[".sbatch"]
    assert " -R '^Cece(Axis|Core)' -j 4" in job
    assert job.count("ctest ") == 1


def test_native_test_stage_runs_ctest_in_place_and_records_it(tmp_path: Path) -> None:
    # Inside an allocation: the launcher prefix, the module environment, the
    # run teed to the tests root, then the harness records the verdict.
    config = _tests_on(
        tmp_path,
        **{
            "harness.runtime": "native",
            "harness.submit_only": False,
            "harness.launcher": "srun --ntasks=1 --cpus-per-task=8",
        },
    )
    script = render_stage(Stage.TEST, config, _CECE)
    assert script.attachments == {}
    lines = script.text.splitlines()
    clone = config.checkout_dir(_CECE)
    tests_root = config.root_dir / "tests" / "cece"
    out = f"{tests_root}/tests.out"
    assert lines[3].startswith(f"[ -f {clone}/build/CTestTestfile.cmake ]")
    run = lines.index(
        "srun --ntasks=1 --cpus-per-task=8 ctest --test-dir build --output-on-failure "
        f"--output-junit {tests_root}/tests.xml 2>&1 | tee -a {out} || status=$?"
    )
    assert lines.index("module load cece_ursa.intelllvm") < run
    assert lines.index("export I_MPI_HYDRA_BOOTSTRAP=fork") < run
    assert lines.index(f"mkdir -p {tests_root}") < lines.index(f"cd {clone}") < run
    assert lines[run - 2] == "status=0"
    assert lines[run - 1] == f'echo "tic: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee {out}'
    assert lines[run + 1] == (
        f'echo "toc: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a {out}'
    )
    # Then the harness venv, clean of the modules, records the run; the
    # stage's exit is the tests'.
    assert run < lines.index("unset PYTHONPATH") < lines.index(f"cd {HARNESS_ROOT}")
    assert lines[-2] == (
        "uv run --no-sync ufs-chem-assay evaluate-tests "
        f'--root-dir {config.root_dir} --application cece --exit-status "$status"'
    )
    assert lines[-1] == 'exit "$status"'
    assert "sbatch" not in script.text
    assert "submit-tests" not in script.text


def test_docker_test_stage_runs_ctest_in_the_applications_image(
    tmp_path: Path,
) -> None:
    config = load_run_config(
        run_config_file(
            tmp_path,
            "local.yaml",
            overrides={
                "applications.cece.targets": ["all"],
                "applications.cece.tests": {"enabled": True},
            },
            root_dir=tmp_path / "runs",
        )
    )
    script = render_stage(Stage.TEST, config, _CECE)
    assert script.attachments == {}
    lines = script.text.splitlines()
    clone = config.checkout_dir(_CECE)
    tests_root = config.root_dir / "tests" / "cece"
    run = next(i for i, line in enumerate(lines) if line.startswith("docker run"))
    assert lines[run] == (
        f"docker run --rm -v {clone}:/work -v {tests_root}:/assay-tests -w /work "
        "-e OMPI_ALLOW_RUN_AS_ROOT=1 -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 cece/cece-dev "
        "ctest --test-dir build --output-on-failure "
        f"--output-junit /assay-tests/tests.xml 2>&1 | tee -a {tests_root}/tests.out "
        "|| status=$?"
    )
    assert "module" not in script.text
    assert lines[-2].startswith("uv run --no-sync ufs-chem-assay evaluate-tests ")
    assert lines[-1] == 'exit "$status"'
