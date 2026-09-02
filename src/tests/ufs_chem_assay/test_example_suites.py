"""Examples-as-suites (design/feat/20260724-0907-examples-as-suites.md):
the CeceConfig surface the shipped examples use, the universal
driver.log_file redirect, the ${CECE_ROOT_DIR} config_path anchor, and
the checked-in ex*-suite.yaml files. All against fabricated trees — the
external CECE checkout is never required here."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from combos import build_config, enumerate_combos
from examples import EXAMPLES_SUBDIR, example_id
from models.cece_config import Cadence, CeceConfig, DataModel
from models.suite_config import SuiteConfig, Sweep
from settings import Settings

EXAMPLE_IDS = [f"ex{n}" for n in range(1, 8)]

# Shipped example configs with no suite coverage yet: these do not run
# (see the examples-as-suites design doc). Remove an entry here when its
# example becomes runnable — the coverage test below then demands a suite.
UNCOVERED_EXAMPLES = {"advanced", "megan3"}


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


@pytest.mark.parametrize("cadence", ["hourly", "weekly", "monthly"])
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
    [("cadence", "daily"), ("cadence", "Hourly"), ("data_model", "netcdf4")],
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


# ── Grid: grid_name pattern and the either/or contract ───────────────────────


def test_grid_name_replaces_nx_ny(tmp_path: Path, cece_config_path: Path) -> None:
    content = _config_content(cece_config_path)
    grid = content["driver"]["grid"]
    del grid["nx"], grid["ny"]
    grid["grid_name"] = "F360"
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.driver.grid.grid_name == "F360"
    assert config.driver.grid.nx is None and config.driver.grid.ny is None


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


# ── An ex7-shaped config (all new fields at once) validates ──────────────────


def test_example_shaped_config_validates(
    tmp_path: Path, cece_config_path: Path
) -> None:
    content = _config_content(cece_config_path)
    content["driver"]["log_file"] = "cece.log"
    content["driver"]["amio_worker_threads"] = 2
    grid = content["driver"]["grid"]
    del grid["nx"], grid["ny"]
    grid["grid_name"] = "F360"
    stream = content["cece_data"]["streams"][0]
    stream["cadence"] = "monthly"
    stream["data_model"] = "classic"
    config = CeceConfig.from_yaml(_write_config(tmp_path, content))
    assert config.driver.grid.grid_name == "F360"
    assert config.cece_data.streams[0].cadence is Cadence.monthly


# ── build_config: driver.log_file always lands in the combo directory ────────


@pytest.mark.parametrize("base_log_file", [None, "cece.log", "/work/logs/other.log"])
def test_build_config_always_redirects_log_file(
    tmp_path: Path, cece_config_path: Path, base_log_file: str | None
) -> None:
    content = _config_content(cece_config_path)
    if base_log_file is not None:
        content["driver"]["log_file"] = base_log_file
    config_path = _write_config(tmp_path, content)
    (combo,) = enumerate_combos(Sweep(), CeceConfig.from_yaml(config_path))
    generated = build_config(
        combo, output_directory="/combo_runs/abc123", config_path=config_path
    )
    assert generated.driver.log_file == "/combo_runs/abc123/cece.log"


# ── ${CECE_ROOT_DIR} anchor + the checked-in example suites ──────────────────


@pytest.fixture()
def fake_checkout(tmp_path: Path, cece_config_path: Path) -> Path:
    """A CECE-shaped tree holding a schema-valid config at every shipped
    example's path."""
    root = tmp_path / "checkout"
    config_dir = root / "examples" / "config"
    config_dir.mkdir(parents=True)
    for eid in EXAMPLE_IDS:
        (config_dir / f"cece_config_{eid}.yaml").write_text(
            cece_config_path.read_text()
        )
    return root


def test_root_token_resolves_against_root_dir(
    tmp_path: Path, fake_checkout: Path
) -> None:
    suite_file = tmp_path / "token-suite.yaml"
    suite_file.write_text(
        "name: token-suite\n"
        "config_path: ${CECE_ROOT_DIR}/examples/config/cece_config_ex3.yaml\n"
        "timeout_s: 5\n"
    )
    suite = SuiteConfig.from_yaml(suite_file, root_dir=fake_checkout)
    assert (
        suite.config_path
        == (fake_checkout / "examples" / "config" / "cece_config_ex3.yaml").resolve()
    )


def test_root_token_without_root_dir_errors(tmp_path: Path) -> None:
    suite_file = tmp_path / "token-suite.yaml"
    suite_file.write_text(
        "name: token-suite\n"
        "config_path: ${CECE_ROOT_DIR}/examples/config/cece_config_ex3.yaml\n"
        "timeout_s: 5\n"
    )
    with pytest.raises(ValueError, match="CECE_ROOT_DIR"):
        SuiteConfig.from_yaml(suite_file)


def test_root_token_beats_config_search_path(
    tmp_path: Path, fake_checkout: Path
) -> None:
    # The token is an explicit anchor; the search path (which would break on
    # it anyway) must not be consulted.
    suite_file = tmp_path / "token-suite.yaml"
    suite_file.write_text(
        "name: token-suite\n"
        "config_path: ${CECE_ROOT_DIR}/examples/config/cece_config_ex3.yaml\n"
        "timeout_s: 5\n"
    )
    suite = SuiteConfig.from_yaml(
        suite_file, config_search_path=tmp_path / "elsewhere", root_dir=fake_checkout
    )
    assert suite.config_path.is_file()


def test_plain_relative_config_path_ignores_root_dir(
    suite_path: Path, fake_checkout: Path
) -> None:
    # Existing suites resolve exactly as before even when a root is known.
    suite = SuiteConfig.from_yaml(suite_path, root_dir=fake_checkout)
    assert (
        suite.config_path
        == (suite_path.parent / ".." / "cece" / "simple-maccity.yaml").resolve()
    )


def test_every_runnable_example_has_a_suite_file(suite_dir: Path) -> None:
    """Coverage guard against the real checkout: every shipped
    examples/config/cece_config_*.yaml outside UNCOVERED_EXAMPLES has a
    checked-in <id>-suite.yaml — a new example arriving in CECE fails here
    until it gains a suite. Skips when no checkout is configured (the only
    test in this module touching one)."""
    root_dir = Settings().root_dir
    if root_dir is None or not root_dir.is_dir():
        pytest.skip("CECE checkout not configured; set CECE_ROOT_DIR")
    configs = sorted((root_dir / EXAMPLES_SUBDIR).glob("cece_config_*.yaml"))
    assert configs, f"no example configs under {root_dir / EXAMPLES_SUBDIR}"
    runnable = {example_id(path) for path in configs} - UNCOVERED_EXAMPLES
    missing = sorted(
        eid for eid in runnable if not (suite_dir / f"{eid}-suite.yaml").is_file()
    )
    assert not missing, f"examples without a checked-in suite file: {missing}"


@pytest.mark.parametrize("eid", EXAMPLE_IDS)
def test_checked_in_example_suite_loads(
    suite_dir: Path, fake_checkout: Path, eid: str
) -> None:
    """Every shipped example has a checked-in suite file: name matches the
    example id, config_path anchors on ${CECE_ROOT_DIR}, no sweep (the
    single base combination)."""
    suite_file = suite_dir / f"{eid}-suite.yaml"
    assert suite_file.is_file(), f"missing {suite_file}"
    suite = SuiteConfig.from_yaml(suite_file, root_dir=fake_checkout)
    assert suite.name == eid
    assert suite.config_path.name == f"cece_config_{eid}.yaml"
    base_config = CeceConfig.from_yaml(suite.config_path)
    combos = enumerate_combos(suite.sweep, base_config)
    assert [combo.name for combo in combos] == ["base"]
