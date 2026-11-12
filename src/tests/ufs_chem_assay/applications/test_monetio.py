"""Comprehensive unit tests for the monetio application adapter."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from applications.base import Stage
from applications.monetio import (
    MonetioCaseConfig,
    MonetioConfig,
)
from applications.monetio.assertions import (
    STANDARD_DIMENSIONS,
    VOLATILE_GLOBAL_ATTRIBUTES,
    expected_output_count,
    expected_output_filenames,
    output_variable_names,
)
from applications.monetio.cli import MonetioRunSection, stage_lines
from applications.monetio.combos import (
    build_config,
    dimensions,
    effective_parameters,
)
from applications.monetio.settings import (
    CONTAINER_WORKDIR,
    ENV_PREFIX,
    MonetioSettings,
)
from applications.monetio.suite import (
    MonetioBaselineComparison,
    MonetioSuiteConfig,
    MonetioSweep,
    MonetioSweepSelector,
    selector_matches,
)
from applications.registry import get_application, load_suite
from combos import enumerate_combos, write_combos_csv
from comparison import resolve_baseline_comparisons
from platforms import Runtime
from resolution import select_suites
from selection import BUILTIN_CONFIG_DIR


@pytest.fixture
def clean_monetio_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> pytest.MonkeyPatch:
    for key in (
        "MONETIO_ROOT_DIR",
        "MONETIO_DOCKER_IMAGE",
        "MONETIO_DRIVER_PATH",
        "MONETIO_MODULEFILE",
        "ASSAY_ROOT_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


@pytest.fixture
def aeronet_base_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "aeronet_base.yaml"
    path.write_text(
        yaml.dump(
            {
                "source": "aeronet",
                "files": ["data/aeronet/aeronet-aod15-cart-site-200006.txt"],
                "output": "aeronet.nc",
                "variables": ["aod_500nm", "latitude", "longitude"],
                "kwargs": {
                    "product": "AOD15",
                    "freq": "1D",
                    "expand2d": True,
                },
                "cases": {
                    "aod15": {
                        "files": ["data/aeronet/aeronet-aod15-cart-site-200006.txt"],
                        "variables": ["aod_500nm", "latitude", "longitude"],
                        "kwargs": {"product": "AOD15", "freq": "1D", "expand2d": True},
                    },
                    "alm15-siz": {
                        "files": [
                            "data/aeronet/aeronet-alm15-siz-cart-site-200206-200305.txt"
                        ],
                        "variables": ["bin_0_050000", "latitude", "longitude"],
                        "kwargs": {
                            "product": "ALM15",
                            "inv_type": "SIZ",
                            "freq": "30D",
                            "expand2d": True,
                        },
                    },
                },
            },
            sort_keys=False,
        )
    )
    return path


# ── MonetioSettings ──────────────────────────────────────────────────────────


@pytest.mark.usefixtures("clean_monetio_env")
def test_monetio_settings_defaults_and_env(
    clean_monetio_env: pytest.MonkeyPatch,
) -> None:
    settings = MonetioSettings()
    assert settings.root_dir is None
    assert settings.docker_image == "monetio/monetio-dev"
    assert settings.driver_path == ".venv/bin/monetio"
    assert settings.modulefile is None
    assert ENV_PREFIX == "MONETIO_"
    assert str(CONTAINER_WORKDIR) == "/work"

    clean_monetio_env.setenv("MONETIO_ROOT_DIR", "/host/monetio")
    assert MonetioSettings().root_dir == Path("/host/monetio")


# ── MonetioConfig & assertions ───────────────────────────────────────────────


def test_monetio_config_round_trip_and_strictness(
    tmp_path: Path, aeronet_base_yaml: Path
) -> None:
    config = MonetioConfig.from_yaml(aeronet_base_yaml)
    assert config.source == "aeronet"
    assert config.files == ["data/aeronet/aeronet-aod15-cart-site-200006.txt"]
    assert config.output == "aeronet.nc"
    assert config.variables == ["aod_500nm", "latitude", "longitude"]
    assert config.kwargs == {"product": "AOD15", "freq": "1D", "expand2d": True}
    assert set(config.cases or {}) == {"aod15", "alm15-siz"}
    assert isinstance(config.cases["aod15"], MonetioCaseConfig)  # type: ignore[index]

    round_trip = tmp_path / "round_trip.yaml"
    config.to_yaml(round_trip)
    assert MonetioConfig.from_yaml(round_trip) == config

    with pytest.raises(ValidationError, match="extra_field"):
        MonetioConfig.model_validate(
            {
                "source": "aeronet",
                "files": ["data/aeronet/x.txt"],
                "output": "aeronet.nc",
                "extra_field": 1,
            }
        )


def test_monetio_assertions_and_fingerprint(
    tmp_path: Path, aeronet_base_yaml: Path
) -> None:
    app = get_application("monetio")
    config = MonetioConfig.from_yaml(aeronet_base_yaml)

    assert app.standard_dimensions == STANDARD_DIMENSIONS == ("time", "node")
    assert app.volatile_global_attributes == VOLATILE_GLOBAL_ATTRIBUTES == ("history",)
    assert app.expected_output_count(config) == expected_output_count(config) == 1
    assert (
        app.expected_output_filenames(config)
        == expected_output_filenames(config)
        == {"aeronet.nc"}
    )
    assert (
        app.output_variable_names(config)
        == output_variable_names(config)
        == ["aod_500nm"]
    )

    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    c = tmp_path / "c.yaml"
    config.output = "/combo_runs/01JA/aeronet.nc"
    config.to_yaml(a)
    config.output = "/work/runs/01JB/aeronet.nc"
    config.to_yaml(b)
    config.kwargs["freq"] = "6h"
    config.to_yaml(c)

    assert app.config_fingerprint(a) == app.config_fingerprint(b)
    assert app.config_fingerprint(a) != app.config_fingerprint(c)


# ── Sweeps, Combos, and Selectors ────────────────────────────────────────────


def test_monetio_sweep_enumeration_and_build_config(
    tmp_path: Path, aeronet_base_yaml: Path
) -> None:
    app = get_application("monetio")
    base = MonetioConfig.from_yaml(aeronet_base_yaml)
    sweep = MonetioSweep(
        case=["alm15-siz", "aod15"],
        freq=["6h", "1D"],
    )
    combos = enumerate_combos(app.dimensions(sweep, base))
    assert [c.name for c in combos] == [
        "monetio.case-alm15-siz__monetio.freq-1D",
        "monetio.case-alm15-siz__monetio.freq-6h",
        "monetio.case-aod15__monetio.freq-1D",
        "monetio.case-aod15__monetio.freq-6h",
    ]

    # Case preset applies its files, variables, and kwargs, then explicit freq overrides kwargs['freq']
    alm_1d = app.build_config(
        combos[0], output_directory="/combo_runs/01JA", config_path=aeronet_base_yaml
    )
    assert isinstance(alm_1d, MonetioConfig)
    assert alm_1d.files == [
        "data/aeronet/aeronet-alm15-siz-cart-site-200206-200305.txt"
    ]
    assert alm_1d.variables == ["bin_0_050000", "latitude", "longitude"]
    assert alm_1d.kwargs == {
        "product": "ALM15",
        "inv_type": "SIZ",
        "freq": "1D",
        "expand2d": True,
    }
    assert alm_1d.output == "/combo_runs/01JA/aeronet.nc"

    # Case-only sweep preserves each case's own freq
    case_only = MonetioSweep(case=["aod15", "alm15-siz"])
    case_combos = enumerate_combos(dimensions(case_only, base))
    assert [c.name for c in case_combos] == [
        "monetio.case-alm15-siz",
        "monetio.case-aod15",
    ]
    alm_default = build_config(
        case_combos[0],
        output_directory="/combo_runs/01JB",
        config_path=aeronet_base_yaml,
    )
    assert alm_default.kwargs["freq"] == "30D"

    # Effective parameters table
    rows = app.effective_parameters(case_combos[0], alm_default)
    assert ("monetio", "case", "alm15-siz", True) in rows
    assert ("monetio", "source", "aeronet", False) in rows
    assert ("monetio", "freq", "30D", False) in rows

    csv_path = tmp_path / "combos.csv"
    frame = write_combos_csv(
        [("aeronet", case_combos[0], rows)],
        run_id="01JZZZZZZZZZZZZZZZZZZZZZZZ",
        application="monetio",
        csv_path=csv_path,
    )
    assert set(frame["application"]) == {"monetio"}


def test_monetio_sweep_validation_and_empty_sweep(aeronet_base_yaml: Path) -> None:
    base = MonetioConfig.from_yaml(aeronet_base_yaml)

    # Empty sweep -> single "base" combo
    (identity,) = enumerate_combos(dimensions(MonetioSweep(), base))
    assert identity.name == "base"
    built = build_config(
        identity, output_directory="/out", config_path=aeronet_base_yaml
    )
    assert built.output == "/out/aeronet.nc"
    rows = effective_parameters(identity, built)
    assert ("monetio", "case", "", False) in rows

    # Unknown case fails against base_config.cases
    with pytest.raises(ValueError, match="unknown case 'ghost'"):
        dimensions(MonetioSweep(case=["ghost"]), base)

    # Duplicate sweep values rejected at model validation
    with pytest.raises(ValidationError, match="duplicate values"):
        MonetioSweep(case=["aod15", "aod15"])
    with pytest.raises(ValidationError, match="duplicate values"):
        MonetioSweep(freq=["1D", "1D"])

    # Case without cases in base config
    base_no_cases = base.model_copy(update={"cases": None})
    with pytest.raises(ValueError, match="unknown case 'aod15'"):
        dimensions(MonetioSweep(case=["aod15"]), base_no_cases)

    # Case with partial fields (no files/variables/kwargs) leaves base values untouched
    base_partial = base.model_copy(update={"cases": {"minimal": MonetioCaseConfig()}})
    (minimal_combo,) = enumerate_combos(
        dimensions(MonetioSweep(case=["minimal"]), base_partial)
    )
    partial_path = aeronet_base_yaml.parent / "partial.yaml"
    base_partial.to_yaml(partial_path)
    built_partial = build_config(
        minimal_combo, output_directory="/out", config_path=partial_path
    )
    assert built_partial.files == base.files
    assert built_partial.variables == base.variables


def test_monetio_suite_and_selector_matching(
    tmp_path: Path, aeronet_base_yaml: Path
) -> None:
    app = get_application("monetio")
    suite_path = tmp_path / "aeronet-suite.yaml"
    suite_path.write_text(
        f"name: aeronet\n"
        f"application: monetio\n"
        f"config_path: {aeronet_base_yaml}\n"
        f"timeout_s: 30\n"
        f"sweep:\n"
        f"  case: [aod15, alm15-siz]\n"
        f"baseline_comparisons:\n"
        f"  - sweep_selector:\n"
        f"      case: aod15\n"
        f"    ulid: 01JAAAAAAAAAAAAAAAAAAAAAAA\n"
        f"  - sweep_selector:\n"
        f"      case: alm15-siz\n"
        f"    ulid: 01JBBBBBBBBBBBBBBBBBBBBBBB\n"
    )
    suite = load_suite(suite_path)
    assert isinstance(suite, MonetioSuiteConfig)
    assert suite.application == "monetio"

    base = MonetioConfig.from_yaml(suite.config_path)
    combos = enumerate_combos(app.dimensions(suite.sweep, base))
    resolved = resolve_baseline_comparisons(app, suite.baseline_comparisons, combos)
    assert resolved["monetio.case-aod15"].ulid == "01JAAAAAAAAAAAAAAAAAAAAAAA"
    assert resolved["monetio.case-alm15-siz"].ulid == "01JBBBBBBBBBBBBBBBBBBBBBBB"

    # Selector with freq constraint when freq is not swept does not match
    assert not selector_matches(
        MonetioSweepSelector(case="aod15", freq="1D"), combos[0]
    )
    # Empty selector matches any combo
    assert selector_matches(MonetioSweepSelector(), combos[0])

    # Invalid regex rejected on MonetioSweepSelector
    with pytest.raises(ValidationError, match="regex"):
        MonetioBaselineComparison(
            sweep_selector=MonetioSweepSelector(case="["),
            ulid="01JAAAAAAAAAAAAAAAAAAAAAAA",
        )


# ── driver_arguments ─────────────────────────────────────────────────────────


def test_monetio_driver_arguments(
    tmp_path: Path, aeronet_base_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = get_application("monetio")
    config = MonetioConfig.from_yaml(aeronet_base_yaml)
    config.files = ["data/aeronet/f1.txt", "data/aeronet/f2.txt"]
    config.output = "/combo_runs/01JA/aeronet.nc"
    config.kwargs = {
        "product": "AOD15",
        "expand2d": True,
        "daily": False,
        "siteid": ["Cart_Site", "Bondville"],
        "ignored": None,
    }
    combo_yaml = tmp_path / "01JA.yaml"
    config.to_yaml(combo_yaml)

    args = app.driver_arguments(PurePosixPath(combo_yaml))
    assert args == [
        "load",
        "aeronet",
        "-f",
        "data/aeronet/f1.txt",
        "-f",
        "data/aeronet/f2.txt",
        "-o",
        "/combo_runs/01JA/aeronet.nc",
        "-k",
        "product=AOD15",
        "-k",
        "expand2d=true",
        "-k",
        "daily=false",
        "-k",
        "siteid=Cart_Site",
        "-k",
        "siteid=Bondville",
    ]

    # When driver_yaml is a relative path under MONETIO_ROOT_DIR
    checkout = tmp_path / "monetio_checkout"
    rel_dir = checkout / "artifacts" / "01JB"
    rel_dir.mkdir(parents=True)
    rel_yaml = rel_dir / "01JB.yaml"
    config.to_yaml(rel_yaml)
    monkeypatch.setenv("MONETIO_ROOT_DIR", str(checkout))
    rel_args = app.driver_arguments(PurePosixPath("artifacts/01JB/01JB.yaml"))
    assert rel_args[:2] == ["load", "aeronet"]


# ── CLI stage_lines ──────────────────────────────────────────────────────────


class _FakeRunConfigView:
    def __init__(self, runtime: Runtime, checkout: Path) -> None:
        self._runtime = runtime
        self._checkout = checkout

    @property
    def runtime(self) -> Runtime:
        return self._runtime

    def checkout_dir(self, _app: object) -> Path:
        return self._checkout


def test_monetio_stage_lines_source_build_and_data(tmp_path: Path) -> None:
    app = get_application("monetio")
    checkout = tmp_path / "monetio"
    view = _FakeRunConfigView(Runtime.NATIVE, checkout)

    section = MonetioRunSection(
        git_url="git@github.com:noaa-oar-arl/monetio.git",
        ref="feature/v1",
        update_source=False,
    )
    source_no_update = app.stage_lines(Stage.SOURCE, view, section)
    assert any("git clone" in line for line in source_no_update)
    assert any("using existing clone" in line for line in source_no_update)

    section_update = MonetioRunSection(
        git_url="git@github.com:noaa-oar-arl/monetio.git",
        ref="feature/v1",
        update_source=True,
        modulefile="monetio_ursa",
        extras=["viz", "test"],
    )
    source_update = stage_lines(Stage.SOURCE, view, section_update, checkout)
    assert any("git -C" in line and "pull --ff-only" in line for line in source_update)

    build_lines = stage_lines(Stage.BUILD, view, section_update, checkout)
    assert any("module load monetio_ursa" in line for line in build_lines)
    assert any("uv venv --allow-existing" in line for line in build_lines)
    assert any("[viz,test]" in line for line in build_lines)
    assert any("--help" in line for line in build_lines)

    # Build without extras or modulefile
    build_plain = stage_lines(Stage.BUILD, view, section, checkout)
    assert not any("module load" in line for line in build_plain)
    assert any(f"-e {checkout}" in line for line in build_plain)

    # Build under Docker runtime
    docker_view = _FakeRunConfigView(Runtime.DOCKER, checkout)
    build_docker = stage_lines(Stage.BUILD, docker_view, section, checkout)
    assert any("docker" in line for line in build_docker)

    # Data stage returns empty list (shared fetch handles inputs)
    assert stage_lines(Stage.DATA, view, section, checkout) == []

    # Shared stages raise ValueError
    with pytest.raises(ValueError, match="shared, not the adapter's"):
        stage_lines(Stage.HARNESS, view, section, checkout)


# ── Built-in AERONET suite & --dry-run ────────────────────────────────────────


def test_builtin_aeronet_suite_loads_and_enumerates() -> None:
    app = get_application("monetio")
    (suite_path,) = select_suites("aeronet-suite.yaml", [BUILTIN_CONFIG_DIR])
    suite = load_suite(suite_path, config_search_path=None, root_dir=None)
    assert isinstance(suite, MonetioSuiteConfig)
    assert suite.name == "aeronet"
    assert suite.application == "monetio"
    assert len(suite.inputs) == 2
    base = MonetioConfig.from_yaml(suite.config_path)
    combos = enumerate_combos(app.dimensions(suite.sweep, base))
    assert [c.name for c in combos] == [
        "monetio.case-alm15-siz",
        "monetio.case-aod15",
    ]


def test_aeronet_suite_dry_run_subprocess(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("CECE_", "MONETIO_", "ASSAY_"))
    }
    env["MONETIO_ROOT_DIR"] = str(tmp_path)
    env["ASSAY_PLATFORM"] = "local"
    env["ASSAY_RUNTIME"] = "native"
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "fabricated",
        ],
        cwd=tmp_path,
        check=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(repo_root / "src" / "tests" / "test_driver_combos.py"),
            "--application=monetio",
            "--suite-config=aeronet-suite.yaml",
            "--dry-run",
            "--combo-output-root=combo_runs",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    root = tmp_path / "combo_runs"
    manifest = yaml.safe_load((root / "run.yaml").read_text())
    assert manifest["application"] == "monetio"
    assert manifest["suites"][0]["application"] == "monetio"
    assert len(manifest["inputs"]) == 2

    combos = pd.read_csv(root / "combos.csv")
    assert set(combos["application"]) == {"monetio"}
    assert set(combos["name"]) == {"monetio.case-alm15-siz", "monetio.case-aod15"}
    for combo_id in combos["combo_id"]:
        assert (root / combo_id / f"{combo_id}.yaml").is_file()
