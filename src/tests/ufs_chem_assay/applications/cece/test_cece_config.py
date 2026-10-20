"""CeceConfig's newer driver fields — cadence, time_label, data_model,
log_file, the AMIO input knobs, grid_name, nz — and build_config's universal
log_file redirect, against fabricated configs derived from the checked-in
maccity base config. The external CECE checkout is never required here."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from applications.cece.combos import build_config
from applications.cece.config import Cadence, CeceConfig, DataModel, TimeLabel
from applications.cece.suite import CeceSweep
from applications.registry import get_application
from combos import enumerate_combos

_APP = get_application("cece")


# Any deliberately: these tests mutate the raw YAML tree before validation;
# typing the open driver schema here would re-implement the model under test.
def _config_content(cece_config_path: Path) -> dict[str, Any]:
    content: dict[str, Any] = yaml.safe_load(cece_config_path.read_text())
    return content


def _write_config(tmp_path: Path, content: dict[str, Any]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(content))
    return path


# ── Stream: cadence and data_model ───────────────────────────────────────────


@pytest.mark.parametrize(
    "cadence", ["hourly", "daily", "weekly", "monthly", "series", "stepwise"]
)
def test_stream_cadence_parses(
    tmp_path: Path, cece_config_path: Path, cadence: str
) -> None:
    content = _config_content(cece_config_path)
    content["cece_data"]["streams"][0]["cadence"] = cadence
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.cece_data.streams[0].cadence is Cadence(cadence)


@pytest.mark.parametrize("data_model", ["classic", "enhanced", "auto"])
def test_stream_data_model_parses(
    tmp_path: Path, cece_config_path: Path, data_model: str
) -> None:
    content = _config_content(cece_config_path)
    content["cece_data"]["streams"][0]["data_model"] = data_model
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.cece_data.streams[0].data_model is DataModel(data_model)


def test_stream_cadence_and_data_model_default_none(
    cece_config_path: Path,
) -> None:
    config = CeceConfig.from_yaml(cece_config_path)
    assert config.cece_data.streams[0].cadence is None
    assert config.cece_data.streams[0].data_model is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cadence", "yearly"),
        ("cadence", "Hourly"),
        ("cadence", "step"),  # the driver's alias; canonical values only
        ("time_label", "Start"),
        ("time_label", "middle"),
        ("data_model", "netcdf4"),
    ],
)
def test_non_canonical_stream_values_rejected(
    tmp_path: Path, cece_config_path: Path, field: str, value: str
) -> None:
    # The driver lowercases and silently falls back for unknown data_model
    # values; the model accepts canonical (lowercase) values only.
    content = _config_content(cece_config_path)
    content["cece_data"]["streams"][0][field] = value
    with pytest.raises(ValidationError, match=field):
        CeceConfig.from_yaml(_write_config(tmp_path, content))


# ── Driver: log_file and amio_worker_threads ─────────────────────────────────


def test_driver_log_file_and_worker_threads_parse(
    tmp_path: Path, cece_config_path: Path
) -> None:
    content = _config_content(cece_config_path)
    content["driver"]["log_file"] = "cece.log"
    content["driver"]["amio_worker_threads"] = 2
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.driver.log_file == "cece.log"
    assert config.driver.amio_worker_threads == 2


@pytest.mark.parametrize("threads", [0, -1])
def test_amio_worker_threads_below_one_rejected(
    tmp_path: Path, cece_config_path: Path, threads: int
) -> None:
    # The driver warns and silently runs values < 1 as 1; the model refuses
    # the silent correction.
    content = _config_content(cece_config_path)
    content["driver"]["amio_worker_threads"] = threads
    with pytest.raises(ValidationError, match="amio_worker_threads"):
        CeceConfig.from_yaml(_write_config(tmp_path, content))


# ── Stream: time_label ───────────────────────────────────────────────────────


@pytest.mark.parametrize("label", ["auto", "start", "center", "end"])
def test_stream_time_label_parses(
    tmp_path: Path, cece_config_path: Path, label: str
) -> None:
    content = _config_content(cece_config_path)
    content["cece_data"]["streams"][0]["time_label"] = label
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.cece_data.streams[0].time_label is TimeLabel(label)


def test_stream_time_label_defaults_none(cece_config_path: Path) -> None:
    config = CeceConfig.from_yaml(cece_config_path)
    assert config.cece_data.streams[0].time_label is None


# ── Driver: the AMIO input knobs the driver reads, and the two it does not ──

_AMIO_INPUT_KNOBS = {
    "amio_staging_buffer_count": 32,
    "amio_staging_buffer_capacity_bytes": 67108864,
    "amio_prefetch_depth": 1,
}


def test_driver_amio_input_knobs_parse(tmp_path: Path, cece_config_path: Path) -> None:
    content = _config_content(cece_config_path)
    content["driver"].update(_AMIO_INPUT_KNOBS)
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.driver.amio_staging_buffer_count == 32
    assert config.driver.amio_staging_buffer_capacity_bytes == 67108864
    assert config.driver.amio_prefetch_depth == 1


def test_driver_amio_input_knobs_default_none(cece_config_path: Path) -> None:
    config = CeceConfig.from_yaml(cece_config_path)
    assert config.driver.amio_staging_buffer_count is None
    assert config.driver.amio_staging_buffer_capacity_bytes is None
    assert config.driver.amio_prefetch_depth is None


@pytest.mark.parametrize("knob", sorted(_AMIO_INPUT_KNOBS))
@pytest.mark.parametrize("value", [0, -1])
def test_driver_amio_input_knobs_below_one_rejected(
    tmp_path: Path, cece_config_path: Path, knob: str, value: int
) -> None:
    # The driver rejects values < 1 for these three (unlike
    # amio_worker_threads, which it silently corrects); the model agrees.
    content = _config_content(cece_config_path)
    content["driver"][knob] = value
    with pytest.raises(ValidationError, match=knob):
        CeceConfig.from_yaml(_write_config(tmp_path, content))


@pytest.mark.parametrize(
    ("key", "value"), [("amio_read_timeout_s", 300), ("amio_staging_timeout_ms", 60000)]
)
def test_driver_keys_the_driver_never_reads_are_rejected(
    tmp_path: Path, cece_config_path: Path, key: str, value: int
) -> None:
    # Deliberately absent from Driver: CECE develop reads neither at the
    # driver level (the AMIO manifest hard-codes 120 s / 30000 ms), so a
    # config carrying them would document settings with no effect
    # (design/feat/20261019-1447-global-anthro-use-case, TODO 1).
    content = _config_content(cece_config_path)
    content["driver"][key] = value
    with pytest.raises(ValidationError, match=key):
        CeceConfig.from_yaml(_write_config(tmp_path, content))


# ── Grid: nz ─────────────────────────────────────────────────────────────────


def test_grid_nz_parses_and_defaults_none(
    tmp_path: Path, cece_config_path: Path
) -> None:
    assert CeceConfig.from_yaml(cece_config_path).driver.grid.nz is None
    content = _config_content(cece_config_path)
    content["driver"]["grid"]["nz"] = 72
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.driver.grid.nz == 72


@pytest.mark.parametrize("nz", [0, -3])
def test_grid_nz_below_one_rejected(
    tmp_path: Path, cece_config_path: Path, nz: int
) -> None:
    content = _config_content(cece_config_path)
    content["driver"]["grid"]["nz"] = nz
    with pytest.raises(ValidationError, match="nz"):
        CeceConfig.from_yaml(_write_config(tmp_path, content))


# ── Round trip: the new fields survive to_yaml/from_yaml; absent stays absent


def test_new_fields_round_trip_and_absent_fields_are_not_dumped(
    tmp_path: Path, cece_config_path: Path
) -> None:
    content = _config_content(cece_config_path)
    content["driver"].update(_AMIO_INPUT_KNOBS)
    content["driver"]["grid"]["nz"] = 1
    content["cece_data"]["streams"][0]["cadence"] = "series"
    content["cece_data"]["streams"][0]["time_label"] = "start"
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    dumped = tmp_path / "dumped.yaml"
    config.to_yaml(dumped)
    assert CeceConfig.from_yaml(dumped) == config
    raw = yaml.safe_load(dumped.read_text())
    assert raw["driver"]["grid"]["nz"] == 1
    assert raw["cece_data"]["streams"][0]["time_label"] == "start"
    # maccity itself dumps without any of the new keys (exclude_none).
    plain = tmp_path / "plain.yaml"
    CeceConfig.from_yaml(cece_config_path).to_yaml(plain)
    plain_raw = yaml.safe_load(plain.read_text())
    assert "nz" not in plain_raw["driver"]["grid"]
    assert "amio_prefetch_depth" not in plain_raw["driver"]
    assert "time_label" not in plain_raw["cece_data"]["streams"][0]


# ── Grid: grid_name pattern and the either/or contract ───────────────────────


def test_grid_name_replaces_nx_ny(tmp_path: Path, cece_config_path: Path) -> None:
    content = _config_content(cece_config_path)
    grid = content["driver"]["grid"]
    del grid["nx"], grid["ny"]
    grid["grid_name"] = "F360"
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.driver.grid.grid_name == "F360"
    assert config.driver.grid.nx is None
    assert config.driver.grid.ny is None


def test_grid_name_alongside_dims_parses(
    tmp_path: Path, cece_config_path: Path
) -> None:
    # Both given is legal; the driver validates the dimensions against the
    # name at startup (mismatch is its error to raise).
    content = _config_content(cece_config_path)
    content["driver"]["grid"]["grid_name"] = "R90"
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.driver.grid.grid_name == "R90"
    assert config.driver.grid.nx is not None


@pytest.mark.parametrize("name", ["G218", "grid218", "f360", "F0", "F", "X99"])
def test_non_target_grid_names_rejected(
    tmp_path: Path, cece_config_path: Path, name: str
) -> None:
    # Only families F and R are structured CECE target grids (main.cpp);
    # lowercase, zero, and other registry families are rejected here.
    content = _config_content(cece_config_path)
    content["driver"]["grid"]["grid_name"] = name
    with pytest.raises(ValidationError, match="grid_name"):
        CeceConfig.from_yaml(_write_config(tmp_path, content))


def test_grid_requires_name_or_both_dims(
    tmp_path: Path, cece_config_path: Path
) -> None:
    content = _config_content(cece_config_path)
    del content["driver"]["grid"]["nx"]
    del content["driver"]["grid"]["ny"]
    with pytest.raises(ValidationError, match="grid_name"):
        CeceConfig.from_yaml(_write_config(tmp_path, content))


def test_grid_partial_dims_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    content = _config_content(cece_config_path)
    del content["driver"]["grid"]["ny"]
    with pytest.raises(ValidationError, match="grid_name"):
        CeceConfig.from_yaml(_write_config(tmp_path, content))


# ── Every newer field at once validates ──────────────────


def test_all_newer_fields_at_once_validate(
    tmp_path: Path, cece_config_path: Path
) -> None:
    content = _config_content(cece_config_path)
    content["driver"]["log_file"] = "cece.log"
    content["driver"]["amio_worker_threads"] = 2
    grid = content["driver"]["grid"]
    del grid["nx"], grid["ny"]
    grid["grid_name"] = "F360"
    grid["nz"] = 1
    content["driver"]["amio_staging_buffer_count"] = 32
    content["driver"]["amio_staging_buffer_capacity_bytes"] = 67108864
    content["driver"]["amio_prefetch_depth"] = 1
    stream = content["cece_data"]["streams"][0]
    stream["cadence"] = "monthly"
    stream["time_label"] = "start"
    stream["data_model"] = "classic"
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.driver.grid.grid_name == "F360"
    assert config.cece_data.streams[0].cadence is Cadence.monthly
    assert config.cece_data.streams[0].time_label is TimeLabel.start


# ── build_config: driver.log_file always lands in the combo directory ────────


@pytest.mark.parametrize("base_log_file", [None, "cece.log", "/work/logs/other.log"])
def test_build_config_always_redirects_log_file(
    tmp_path: Path, cece_config_path: Path, base_log_file: str | None
) -> None:
    content = _config_content(cece_config_path)
    if base_log_file is not None:
        content["driver"]["log_file"] = base_log_file
    config_path = _write_config(tmp_path, content)
    (combo,) = enumerate_combos(
        _APP.dimensions(CeceSweep(), CeceConfig.from_yaml(config_path))
    )
    generated = build_config(
        combo, output_directory="/combo_runs/abc123", config_path=config_path
    )
    assert generated.driver.log_file == "/combo_runs/abc123/cece.log"
