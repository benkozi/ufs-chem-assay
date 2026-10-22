"""RunConfig: the one YAML file `ufs-chem-assay run` assembles a run from —
the applications map, the mirror of every setting, overrides, precedence."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from applications.base import ApplicationRunSection, ApplicationSettings
from applications.cece.cli import CeceRunSection
from applications.registry import REGISTRY, get_application
from cli.run_config import HARNESS_ROOT, HarnessSection, RunConfig
from cli.stages import SETTINGS_NOT_MIRRORED, settings_mirror_fields
from platforms import Platform, Runtime
from settings import Settings
from tests.ufs_chem_assay.run_configs import (
    REMOVE,
    TEMPLATES_DIR,
    load_run_config,
    run_config_file,
)
from tests.ufs_chem_assay.stubs import stub_application


def test_ursa_template_loads() -> None:
    config = load_run_config(TEMPLATES_DIR / "ursa.yaml")
    assert config.platform is Platform.URSA
    assert config.runtime is Runtime.SLURM
    assert config.application_names == ["cece"]
    cece = config.applications["cece"]
    assert isinstance(cece, CeceRunSection)
    assert cece.modulefile == "cece_ursa.intelllvm"
    assert config.checkout_dir(get_application("cece")) == config.root_dir / "CECE"
    assert config.slurm is not None
    assert config.slurm.account == "epic"
    assert config.slurm.sbatch_args == "-A epic -q debug -p u1-compute -N 1 -n 1 -c 8"
    assert config.harness.launcher == ""  # ignored under slurm; not set by the template
    assert config.harness.dask_nworkers == 2  # analysis runs on the login node
    assert config.uv_cache_dir == config.root_dir / "uv-cache"


def test_local_template_loads_and_runs_directly() -> None:
    config = load_run_config(TEMPLATES_DIR / "local.yaml")
    assert config.platform is Platform.LOCAL
    assert config.runtime is Runtime.DOCKER
    assert config.slurm is None
    assert config.applications["cece"].modulefile is None
    assert config.root_dir.is_absolute()  # ~ expanded


def test_templates_list_every_accepted_key() -> None:
    # The template is the documentation of the surface: every key of every
    # section appears, null where the default applies.
    import yaml

    for name in ("ursa.yaml", "local.yaml", "ursa-gcafs-anthro.yaml"):
        raw = yaml.safe_load((TEMPLATES_DIR / name).read_text())
        assert set(raw["harness"]) == set(HarnessSection.model_fields), name
        assert set(raw["applications"]["cece"]) == set(CeceRunSection.model_fields), (
            name
        )


def test_unknown_key_rejected(tmp_path: Path) -> None:
    path = run_config_file(tmp_path, overrides={"harness.suite": "typo"})
    with pytest.raises(ValidationError, match="suite"):
        load_run_config(path)


def test_unknown_application_section_rejected(tmp_path: Path) -> None:
    path = run_config_file(
        tmp_path, overrides={"applications.catchem": {"git_url": "u", "ref": "r"}}
    )
    with pytest.raises(ValidationError, match="unknown application 'catchem'"):
        load_run_config(path)


def test_empty_applications_map_rejected(tmp_path: Path) -> None:
    path = run_config_file(tmp_path, overrides={"applications": {}})
    with pytest.raises(ValidationError, match="at least one application"):
        load_run_config(path)


def test_old_top_level_cece_section_rejected(tmp_path: Path) -> None:
    # The pre-map shape (`cece:` at the top level) is a removed schema.
    path = run_config_file(tmp_path, overrides={"cece": {"git_url": "u", "ref": "r"}})
    with pytest.raises(ValidationError, match="cece"):
        load_run_config(path)


def test_platform_override_beats_file() -> None:
    config = load_run_config(TEMPLATES_DIR / "ursa.yaml", platform=Platform.LOCAL)
    assert config.platform is Platform.LOCAL
    assert config.runtime is Runtime.DOCKER


def test_platform_detected_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = run_config_file(tmp_path, overrides={"platform": REMOVE})
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "ufe04")
    assert load_run_config(path).platform is Platform.URSA
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "laptop")
    assert load_run_config(path).platform is Platform.LOCAL


def test_platform_is_required_on_direct_validation() -> None:
    # Only from_yaml resolves the platform; the model itself never guesses.
    with pytest.raises(ValidationError, match="platform"):
        RunConfig.model_validate(
            {"root_dir": "/r", "applications": {"cece": {"git_url": "u", "ref": "r"}}},
            context={"resolve_application": get_application},
        )


def test_raw_sections_need_the_resolver() -> None:
    # The registry is injected (from_yaml passes it as validation context):
    # cli.run_config never imports it. Already-built sections need no resolver.
    with pytest.raises(ValidationError, match="resolver"):
        RunConfig.model_validate(
            {
                "platform": "local",
                "root_dir": "/r",
                "applications": {"cece": {"git_url": "u", "ref": "r"}},
            }
        )
    section = CeceRunSection(git_url="u", ref="r")
    config = RunConfig.model_validate(
        {"platform": "local", "root_dir": "/r", "applications": {"cece": section}}
    )
    assert config.applications["cece"] is section


def test_harness_env_numbers_become_strings(tmp_path: Path) -> None:
    path = run_config_file(
        tmp_path,
        overrides={"harness.env": {"OMP_NUM_THREADS": 8, "FI_PROVIDER": "tcp"}},
    )
    assert load_run_config(path).harness.env == {
        "OMP_NUM_THREADS": "8",
        "FI_PROVIDER": "tcp",
    }


def test_explicit_clone_dir_is_used(tmp_path: Path) -> None:
    path = run_config_file(
        tmp_path, overrides={"applications.cece.clone_dir": "/elsewhere/CECE"}
    )
    assert load_run_config(path).checkout_dir(get_application("cece")) == Path(
        "/elsewhere/CECE"
    )


def test_run_tests_knob_is_gone_but_targets_stay(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="run_tests"):
        load_run_config(
            run_config_file(tmp_path, overrides={"applications.cece.run_tests": True})
        )
    config = load_run_config(
        run_config_file(tmp_path, overrides={"applications.cece.targets": ["all"]})
    )
    section = config.applications["cece"]
    assert isinstance(section, CeceRunSection)
    assert section.targets == ["all"]


def test_slurm_account_defaults_to_epic(tmp_path: Path) -> None:
    path = run_config_file(tmp_path, overrides={"slurm.account": REMOVE})
    config = load_run_config(path)
    assert config.slurm is not None
    assert config.slurm.account == "epic"


def test_uv_python_is_not_a_knob(tmp_path: Path) -> None:
    path = run_config_file(tmp_path, overrides={"uv.python": "3.13"})
    with pytest.raises(ValidationError, match="python"):
        load_run_config(path)


def test_data_input_search_path_defaults_empty_and_parses() -> None:
    config = load_run_config(TEMPLATES_DIR / "ursa.yaml")
    assert config.data.input_search_path == []
    config = load_run_config(
        TEMPLATES_DIR / "ursa.yaml",
        overrides=["data:input_search_path=[/scratch/x/INPUT, /scratch/y/input]"],
    )
    assert config.data.input_search_path == [
        Path("/scratch/x/INPUT"),
        Path("/scratch/y/input"),
    ]


def test_gcafs_anthro_template_bakes_in_the_use_case() -> None:
    # The static Ursa run config for the use case: no override list.
    config = load_run_config(TEMPLATES_DIR / "ursa-gcafs-anthro.yaml")
    assert config.platform is Platform.URSA
    assert config.harness.suite_config == "gcafs-anthro-suite.yaml"
    assert config.harness.run_timeout_s == 28800
    assert config.harness.env["I_MPI_FABRICS"] == "shm"
    assert config.harness.env["OMP_NUM_THREADS"] == "1"
    assert config.harness.env["OPENBLAS_DEFAULT_NUM_THREADS"] == "1"
    assert config.baselines.enabled is False
    assert config.slurm is not None
    assert (
        config.slurm.sbatch_args
        == "-A epic -q debug -p u1-compute -N 1 -n 1 -c 1 --mem=0"
    )
    assert config.slurm.srun_args_value == "--ntasks=1 --cpu-bind=cores"
    assert config.slurm.queue_wait_s == 3600
    assert config.data.input_search_path == []  # the suite's sources: cover Ursa
    # Everything else is the Ursa template's.
    ursa = load_run_config(TEMPLATES_DIR / "ursa.yaml")
    assert config.applications == ursa.applications
    assert config.harness.output_root == ursa.harness.output_root
    assert config.slurm.account == ursa.slurm.account if ursa.slurm else False


def test_slurm_geometry_defaults_and_renders() -> None:
    # The template: one task, eight cores, no extra directives, no srun
    # options beyond the task count — maccity's jobs as they were.
    config = load_run_config(TEMPLATES_DIR / "ursa.yaml")
    assert config.slurm is not None
    assert (config.slurm.ntasks, config.slurm.srun_args, config.slurm.extra_args) == (
        1,
        "",
        "",
    )
    assert config.slurm.sbatch_args == "-A epic -q debug -p u1-compute -N 1 -n 1 -c 8"
    assert config.slurm.srun_args_value == "--ntasks=1"
    # The gcafs-anthro geometry, by override: sbatch -n and srun --ntasks
    # render from the one ntasks value; extra_args is appended verbatim.
    config = load_run_config(
        TEMPLATES_DIR / "ursa.yaml",
        overrides=[
            "slurm:ntasks=4",
            "slurm:cpus=1",
            "slurm:srun_args=--cpu-bind=cores",
            "slurm:extra_args=--mem=0",
        ],
    )
    assert config.slurm is not None
    assert config.slurm.sbatch_args == (
        "-A epic -q debug -p u1-compute -N 1 -n 4 -c 1 --mem=0"
    )
    assert config.slurm.srun_args_value == "--ntasks=4 --cpu-bind=cores"
    with pytest.raises(ValidationError, match="ntasks"):
        load_run_config(TEMPLATES_DIR / "ursa.yaml", overrides=["slurm:ntasks=0"])


def test_slurm_section_describes_the_per_driver_job(tmp_path: Path) -> None:
    for gone in ("time", "submit"):
        path = run_config_file(tmp_path, overrides={f"slurm.{gone}": "x"})
        with pytest.raises(ValidationError, match=gone):
            load_run_config(path)


def test_templates_ship_without_a_root_and_derive_the_harness_parent() -> None:
    # The runbook layout: $ROOT/ufs-chem-assay beside $ROOT/CECE — so the
    # harness checkout's parent is the root, and no YAML edit is needed.
    for name in ("ursa.yaml", "local.yaml", "ursa-gcafs-anthro.yaml"):
        # no top-level root_dir line (baselines.root_dir is a different key)
        assert (
            re.search(r"^root_dir:", (TEMPLATES_DIR / name).read_text(), re.MULTILINE)
            is None
        ), name
        config = load_run_config(TEMPLATES_DIR / name)
        assert config.root_dir == HARNESS_ROOT.parent
        assert config.root_dir.is_absolute()
        assert "<" not in str(config.root_dir)
        assert (
            config.checkout_dir(get_application("cece")) == HARNESS_ROOT.parent / "CECE"
        )


def test_root_dir_precedence_flag_file_derived(tmp_path: Path) -> None:
    in_file = run_config_file(tmp_path, overrides={"root_dir": "/from/file"})
    assert load_run_config(in_file).root_dir == Path("/from/file")
    assert load_run_config(in_file, root_dir=Path("/from/flag")).root_dir == Path(
        "/from/flag"
    )
    absent = run_config_file(tmp_path)  # the template carries none
    assert load_run_config(absent).root_dir == HARNESS_ROOT.parent
    assert (
        load_run_config(absent, root_dir=Path("~/x")).root_dir
        == Path("~/x").expanduser()
    )


def test_root_dir_is_required_on_direct_validation() -> None:
    with pytest.raises(ValidationError, match="root_dir"):
        RunConfig.model_validate(
            {
                "platform": "local",
                "applications": {"cece": {"git_url": "u", "ref": "r"}},
            },
            context={"resolve_application": get_application},
        )


def test_slurm_queue_wait_defaults_and_renders() -> None:
    config = load_run_config(TEMPLATES_DIR / "ursa.yaml")
    assert config.slurm is not None
    assert config.slurm.queue_wait_s == 3600


# ── The mirror rule: the YAML surface is complete ────────────────────────────


def test_harness_section_mirrors_every_setting() -> None:
    # Every Settings field is a harness: key except the ones that live
    # elsewhere by design (top level, baselines:, slurm:, harness.env).
    mirrored = set(settings_mirror_fields())
    assert mirrored == set(Settings.model_fields) - SETTINGS_NOT_MIRRORED
    assert mirrored <= set(HarnessSection.model_fields)
    assert {
        "application",
        "platform",
        "baseline_root_dir",
        "enable_baseline_comparisons",
        "baseline_store",
        "baseline_atol",
        "baseline_rtol",
        "sbatch_args",
        "slurm_queue_wait_s",
        "srun_args",  # slurm: ntasks + srun_args
        "job_env",
        "input_search_path",  # data.input_search_path
    } == SETTINGS_NOT_MIRRORED


def test_baselines_section_tolerances_default_to_null_and_reject_negatives() -> None:
    from cli.run_config import BaselinesSection

    section = BaselinesSection()
    assert (section.atol, section.rtol) == (None, None)
    assert BaselinesSection(rtol=1e-15).rtol == 1e-15
    with pytest.raises(ValidationError, match="atol"):
        BaselinesSection(atol=-1.0)


def test_application_section_mirrors_application_settings() -> None:
    # root_dir is spelled clone_dir (its null means <root>/<checkout_dirname>).
    for app in REGISTRY.values():
        section_fields = set(app.run_section_model.model_fields)
        settings_fields = set(app.settings_model.model_fields)
        assert "clone_dir" in section_fields
        assert "root_dir" in settings_fields
        assert settings_fields - {"root_dir"} <= section_fields, app.name
        assert issubclass(app.run_section_model, ApplicationRunSection)
        assert issubclass(app.settings_model, ApplicationSettings)


def test_null_section_keys_keep_the_settings_defaults() -> None:
    config = load_run_config(TEMPLATES_DIR / "local.yaml")
    section = config.applications["cece"]
    assert section.docker_image is None
    assert section.driver_path is None


# ── Overrides ────────────────────────────────────────────────────────────────


def test_overrides_merge_before_validation(tmp_path: Path) -> None:
    path = run_config_file(tmp_path)
    config = load_run_config(
        path,
        overrides=[
            "harness:suite_config=ex3-suite.yaml",
            "harness:pytest_args=[-x, -k, base]",
            "applications:cece:ref=develop",
            "applications:cece:build_jobs=2",
            "slurm:qos=batch",
            "harness:env:OMP_NUM_THREADS=8",
            "applications:cece:clone_dir=null",
        ],
    )
    assert config.harness.suite_config == "ex3-suite.yaml"
    assert config.harness.pytest_args == ["-x", "-k", "base"]
    section = config.applications["cece"]
    assert isinstance(section, CeceRunSection)
    assert section.ref == "develop"
    assert section.build_jobs == 2
    assert section.clone_dir is None
    assert config.slurm is not None
    assert config.slurm.qos == "batch"
    assert config.harness.env["OMP_NUM_THREADS"] == "8"


def test_override_precedence_beats_named_flag_beats_file_beats_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platforms.socket.gethostname", lambda: "laptop")
    absent = run_config_file(tmp_path, overrides={"platform": REMOVE})
    assert load_run_config(absent).platform is Platform.LOCAL  # detected
    in_file = run_config_file(tmp_path, overrides={"platform": "ursa"})
    assert load_run_config(in_file).platform is Platform.URSA  # file
    assert (
        load_run_config(in_file, platform=Platform.LOCAL).platform is Platform.LOCAL
    )  # flag
    assert (
        load_run_config(
            in_file, platform=Platform.LOCAL, overrides=["platform=ursa"]
        ).platform
        is Platform.URSA
    )  # override
    assert load_run_config(
        in_file, root_dir=Path("/flag"), overrides=["root_dir=/override"]
    ).root_dir == Path("/override")


def test_override_typo_is_a_validation_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="suite"):
        load_run_config(run_config_file(tmp_path), overrides=["harness:suite=x"])


def test_override_can_add_an_application_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = stub_application()
    monkeypatch.setitem(REGISTRY, "stub", stub)
    config = load_run_config(
        run_config_file(tmp_path),
        overrides=["applications:stub:git_url=u", "applications:stub:ref=main"],
    )
    assert config.application_names == ["cece", "stub"]
    assert config.checkout_dir(stub) == config.root_dir / "STUB"
    # A required field missing on the added section fails validation.
    with pytest.raises(ValidationError, match="ref"):
        load_run_config(
            run_config_file(tmp_path), overrides=["applications:stub:git_url=u"]
        )


def test_effective_config_round_trips(tmp_path: Path) -> None:
    config = load_run_config(
        run_config_file(tmp_path), overrides=["applications:cece:ref=develop"]
    )
    out = tmp_path / "run-config.yaml"
    config.to_yaml(out)
    reloaded = load_run_config(out)
    assert reloaded == config
    section = reloaded.applications["cece"]
    assert isinstance(section, CeceRunSection)
    assert section.ref == "develop"


# ── The example keys are gone ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "dotted", ["applications.cece.examples", "harness.run_examples"]
)
def test_example_keys_are_rejected(tmp_path: Path, dotted: str) -> None:
    # Retired with the example machinery: the selected suites say what to
    # fetch, and there is no --run-examples to pass. extra="forbid" catches a
    # stale run config loudly.
    path = run_config_file(
        tmp_path,
        overrides={dotted: ["ex3"] if "examples" in dotted.split(".")[-1] else True},
    )
    with pytest.raises(ValidationError, match=dotted.split(".")[-1]):
        load_run_config(path)


# ── Publishing baselines ──────────────────────────────────────────────────────


def test_baselines_store_defaults_and_publish_keys_default_off() -> None:
    for name in ("ursa.yaml", "local.yaml", "ursa-gcafs-anthro.yaml"):
        config = load_run_config(TEMPLATES_DIR / name)
        assert config.baselines.store == "s3://ufs-chem/baselines", name
        assert config.harness.publish_baselines is False, name
        assert config.harness.no_suite_update is False, name


def test_no_suite_update_requires_publish_baselines(tmp_path: Path) -> None:
    path = run_config_file(tmp_path, overrides={"harness.no_suite_update": True})
    with pytest.raises(ValidationError, match=r"no_suite_update.*publish_baselines"):
        load_run_config(path)
    path = run_config_file(
        tmp_path,
        overrides={"harness.no_suite_update": True, "harness.publish_baselines": True},
    )
    assert load_run_config(path).harness.no_suite_update is True


def test_malformed_values_fail_validation(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="root_dir"):
        load_run_config(run_config_file(tmp_path, overrides={"root_dir": 7}))
    with pytest.raises(ValidationError, match="applications"):
        load_run_config(run_config_file(tmp_path, overrides={"applications": "cece"}))
    not_a_mapping = tmp_path / "list.yaml"
    not_a_mapping.write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="not a mapping"):
        load_run_config(not_a_mapping)
