"""A run config may extend another (`extends:`): the named file is read first
and this one deep-merged over it — mappings key by key, scalars and lists
replacing — so a person's daily file names its root, transfer directory and
selection over the shared `config/gaea-c7.yaml` without copying it. The
effective config records the merged result with no `extends`, so it reruns
alone."""

from pathlib import Path

import pytest
import yaml

from applications.base import Stage
from identity import HARNESS_ROOT
from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR, load_run_config


def _write(path: Path, data: dict[str, object]) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_a_file_extends_a_template_relative_to_the_checkout(tmp_path: Path) -> None:
    daily = _write(
        tmp_path / "c7.local.yaml",
        {
            "extends": "config/gaea-c7.yaml",
            "root_dir": str(tmp_path / "root"),
            "selected_stages": ["source", "build", "test"],
        },
    )
    config = load_run_config(daily)
    template = load_run_config(TEMPLATES_DIR / "gaea-c7.yaml")
    assert config.root_dir == tmp_path / "root"
    assert config.selected_stages == [Stage.SOURCE, Stage.BUILD, Stage.TEST]
    # Everything else is the template's.
    assert config.platform == template.platform
    assert config.toolchains == template.toolchains
    assert config.slurm == template.slurm


def test_mappings_merge_and_lists_and_scalars_replace(tmp_path: Path) -> None:
    template = TEMPLATES_DIR / "gaea-c7.yaml"
    daily = _write(
        tmp_path / "daily.yaml",
        {
            "extends": str(template),  # absolute: used as given
            "root_dir": str(tmp_path),
            "slurm": {"qos": "debug"},
            "selected_stages": ["build"],
        },
    )
    base = load_run_config(template)
    config = load_run_config(daily)
    assert base.slurm is not None
    assert config.slurm is not None
    assert config.slurm.qos == "debug"
    assert config.slurm.account == base.slurm.account
    assert config.slurm.extra_args == base.slurm.extra_args
    assert config.selected_stages == [Stage.BUILD]


def test_a_list_replaces_the_base_list(tmp_path: Path) -> None:
    base = _write(
        tmp_path / "base.yaml",
        {
            "extends": "config/gaea-c7.yaml",
            "selected_stages": ["source", "build", "test"],
        },
    )
    daily = _write(
        tmp_path / "daily.yaml",
        {"extends": str(base), "root_dir": str(tmp_path), "selected_stages": ["test"]},
    )
    assert load_run_config(daily).selected_stages == [Stage.TEST]


def test_extends_chains(tmp_path: Path) -> None:
    daily = _write(
        tmp_path / "daily.yaml",
        {
            "extends": "config/gaea-c7.yaml",
            "root_dir": str(tmp_path / "root"),
            "selected_stages": ["source", "build", "test"],
        },
    )
    musica = _write(
        tmp_path / "musica.yaml",
        {
            "extends": str(daily),
            "selected_applications": ["catchem"],
            "selected_builds": ["musica"],
            "selected_stages": ["build", "test"],
        },
    )
    config = load_run_config(musica)
    assert config.root_dir == tmp_path / "root"
    assert config.selected_applications == ["catchem"]
    assert config.selected_builds == ["musica"]
    assert config.selected_stages == [Stage.BUILD, Stage.TEST]


def test_a_cycle_is_an_error_naming_the_files(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    _write(a, {"extends": str(b)})
    _write(b, {"extends": str(a)})
    with pytest.raises(ValueError, match="cycle") as info:
        load_run_config(a)
    assert str(a) in str(info.value)
    assert str(b) in str(info.value)


def test_a_missing_base_is_an_error_naming_it(tmp_path: Path) -> None:
    daily = _write(tmp_path / "daily.yaml", {"extends": "config/no-such.yaml"})
    with pytest.raises(ValueError, match=r"no-such\.yaml") as info:
        load_run_config(daily)
    assert str(HARNESS_ROOT / "config" / "no-such.yaml") in str(info.value)
    assert str(daily) in str(info.value)


@pytest.mark.parametrize("value", [["config/gaea-c7.yaml"], 3, ""])
def test_extends_must_be_a_path(tmp_path: Path, value: object) -> None:
    daily = _write(tmp_path / "daily.yaml", {"extends": value})
    with pytest.raises(ValueError, match=r"extends: .* must name"):
        load_run_config(daily)


def test_a_base_that_is_not_a_mapping_is_an_error(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    base.write_text("- a list\n")
    daily = _write(tmp_path / "daily.yaml", {"extends": str(base)})
    with pytest.raises(ValueError, match="not a mapping"):
        load_run_config(daily)


def test_overrides_and_flags_apply_over_the_merged_file(tmp_path: Path) -> None:
    daily = _write(
        tmp_path / "daily.yaml",
        {"extends": "config/gaea-c7.yaml", "root_dir": str(tmp_path / "file")},
    )
    config = load_run_config(
        daily, root_dir=tmp_path / "flag", overrides=["slurm:qos=debug"]
    )
    assert config.root_dir == tmp_path / "flag"
    assert config.slurm is not None
    assert config.slurm.qos == "debug"


def test_the_effective_config_has_no_extends_and_loads_alone(tmp_path: Path) -> None:
    daily = _write(
        tmp_path / "daily.yaml",
        {
            "extends": "config/gaea-c7.yaml",
            "root_dir": str(tmp_path / "root"),
            "transfer_dir": str(tmp_path / "transfer"),
            "selected_stages": ["build"],
        },
    )
    config = load_run_config(daily)
    effective = tmp_path / "run-config.yaml"
    config.to_yaml(effective)
    assert "extends" not in yaml.safe_load(effective.read_text())
    again = load_run_config(effective)
    assert again.root_dir == config.root_dir
    assert again.transfer_dir == config.transfer_dir
    assert again.selected_stages == [Stage.BUILD]
    assert again.toolchains == config.toolchains


def test_transfer_dir_defaults_to_none_and_expands_the_home(tmp_path: Path) -> None:
    assert load_run_config(TEMPLATES_DIR / "gaea-c7.yaml").transfer_dir is None
    daily = _write(
        tmp_path / "daily.yaml",
        {"extends": "config/gaea-c7.yaml", "transfer_dir": "~/transfer"},
    )
    assert load_run_config(daily).transfer_dir == Path.home() / "transfer"
