"""The gcafs-anthro configuration (issue #33): the carried v5 configuration
validates under the strict model, its suite is one sweep-less combination
with seven placed (source-less) inputs and one empty-selector baseline
entry, and a dry run of it generates everything and executes nothing."""

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

from applications.cece.combos import build_config
from applications.cece.config import Cadence, CeceConfig, TimeLabel
from applications.registry import get_application, load_suite
from combos import enumerate_combos
from comparison import resolve_baseline_comparisons

_RUNNER_ROOT = Path(__file__).resolve().parents[5]  # <repo root>/
_GCAFS_DIR = _RUNNER_ROOT / "src" / "tests" / "config" / "cece" / "gcafs-anthro"
_SUITE = _GCAFS_DIR / "gcafs-anthro-suite.yaml"
_CONFIG = _GCAFS_DIR / "gcafs-anthro.yaml"
_APP = get_application("cece")


def test_carried_configuration_is_the_v5_shape() -> None:
    config = CeceConfig.from_yaml(_CONFIG)
    assert config.driver.start_time == "2021-12-20T11:00:00"
    assert config.driver.end_time == "2021-12-21T12:00:00"
    assert (config.driver.grid.nx, config.driver.grid.ny, config.driver.grid.nz) == (
        1440,
        720,
        1,
    )
    assert config.driver.amio_staging_buffer_count == 32
    assert config.driver.amio_prefetch_depth == 1
    assert len(config.species) == 10
    assert sum(len(entries) for entries in config.species.values()) == 25
    streams = config.cece_data.streams
    assert len(streams) == 32
    # Seven files, every path cwd-relative under the configuration's data dir.
    files = {str(stream.file) for stream in streams}
    assert len(files) == 7
    assert all(path.startswith("data/gcafs-anthro/") for path in files)
    by_name = {stream.name: stream for stream in streams}
    assert by_name["CAMS_DAILY_RES"].cadence is Cadence.series
    assert by_name["CAMS_DAILY_RES"].time_label is TimeLabel.start
    assert by_name["BC_agr"].cadence is Cadence.monthly
    assert by_name["EDGAR_TODNOX"].cadence is Cadence.hourly
    assert by_name["EDGAR_TODNOX"].time_label is None
    # The SO4 shipping stream reads the SO2 file variable under another name.
    (variable,) = by_name["SO4_shp"].variables
    assert (variable.file, variable.model) == ("SO2_shp", "SO4_shp")
    assert config.output is not None
    assert config.output.fields == ["SO2", "OC", "BC"]


def test_suite_is_one_sweepless_combination_with_placed_inputs() -> None:
    suite = load_suite(_SUITE)
    assert suite.name == "gcafs-anthro"
    assert suite.timeout_s == 14400
    base = CeceConfig.from_yaml(suite.config_path)
    combos = enumerate_combos(_APP.dimensions(suite.sweep, base))
    assert [combo.name for combo in combos] == ["base"]
    assert len(suite.inputs) == 7
    assert all(entry.url is None for entry in suite.inputs)
    assert all(entry.sha256 is not None for entry in suite.inputs)
    assert all(str(entry.dst).startswith("gcafs-anthro/") for entry in suite.inputs)
    # Every stream file is a declared input (data/<dst>).
    declared = {f"data/{entry.dst}" for entry in suite.inputs}
    assert {str(stream.file) for stream in base.cece_data.streams} == declared
    (entry,) = suite.baseline_comparisons
    assert resolve_baseline_comparisons(_APP, [entry], combos) == {"base": entry}
    assert entry.rtol == 1.0e-15


def test_expected_outputs_are_twenty_five_hourly_files() -> None:
    suite = load_suite(_SUITE)
    base = CeceConfig.from_yaml(suite.config_path)
    (combo,) = enumerate_combos(_APP.dimensions(suite.sweep, base))
    config = build_config(
        combo, output_directory="/combo_runs/x", config_path=suite.config_path
    )
    assert _APP.expected_output_count(config) == 25
    names = _APP.expected_output_filenames(config)
    assert len(names) == 25
    assert {"cece_20211220_1200.nc", "cece_20211221_1200.nc"} <= names
    assert "cece_20211220_1100.nc" not in names  # t=0 is not a write
    assert _APP.output_variable_names(config) == ["SO2", "OC", "BC"]
    assert config.driver.log_file == "/combo_runs/x/cece.log"


def test_dry_run_generates_everything_and_executes_nothing(tmp_path: Path) -> None:
    # Checkout-less (no CECE_ROOT_DIR): the inputs are recorded absent, the
    # job/command is not described, nothing runs — exactly what any machine
    # without the data can do with this suite.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CECE_", "ASSAY_"))}
    env["ASSAY_PLATFORM"] = "local"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(_RUNNER_ROOT / "src" / "tests" / "test_driver_combos.py"),
            "--dry-run",
            "--suite-config=gcafs-anthro-suite.yaml",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "bt"),
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    root = next((tmp_path / "bt").rglob("run.yaml")).parent
    manifest = yaml.safe_load((root / "run.yaml").read_text())
    assert [suite["name"] for suite in manifest["suites"]] == ["gcafs-anthro"]
    assert len(manifest["inputs"]) == 7
    assert {entry["url"] for entry in manifest["inputs"]} == {None}
    assert {entry["bytes"] for entry in manifest["inputs"]} == {None}
    combos = pd.read_csv(root / "combos.csv")
    assert len(set(combos["combo_id"])) == 1
    assert len(combos) == 32 * 3 + 25 * 3  # every stream and species-entry field, pinned
    assert not combos["swept"].any()
    report = pd.read_csv(root / "test-report.csv")
    assert set(report["result"]) == {"skipped"}
    assert not list(root.rglob("*.nc"))
