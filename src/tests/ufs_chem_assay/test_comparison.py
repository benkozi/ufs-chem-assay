from pathlib import Path
from typing import Literal

import numpy as np
import pytest
import xarray as xr
import yaml
from pydantic import ValidationError

from combos import Combo, enumerate_combos
from comparison import (
    BaselineComparisonResult,
    VariableComparison,
    compare_with_baseline,
    concatenate_comparison_csvs,
    resolve_baseline_comparisons,
    write_comparison_csv,
)
from models.cece_config import CeceConfig
from models.suite_config import (
    BaselineComparison,
    CeceDataSweep,
    CeceDataSweepSelector,
    SpeciesEntrySweep,
    SpeciesEntrySweepSelector,
    StreamSweep,
    StreamSweepSelector,
    SuiteConfig,
    Sweep,
    SweepSelector,
)


def _write_nc(
    path: Path,
    values: np.ndarray,
    var: str = "co",
    var_attrs: dict[str, str] | None = None,
    global_attrs: dict[str, str] | None = None,
    fmt: Literal["NETCDF4", "NETCDF3_CLASSIC"] = "NETCDF4",
    extra_var: bool = False,
) -> None:
    dataset = xr.Dataset({var: (("time", "lat", "lon"), values)})
    if extra_var:
        dataset["surprise"] = (("time", "lat", "lon"), values)
    dataset[var].attrs.update(
        var_attrs if var_attrs is not None else {"units": "kg m-2 s-1"}
    )
    dataset.attrs.update(
        global_attrs if global_attrs is not None else {"title": "CECE test"}
    )
    encoding = {str(name): {"_FillValue": None} for name in dataset.data_vars}
    dataset.to_netcdf(path, format=fmt, engine="netcdf4", encoding=encoding)


def _values() -> np.ndarray:
    rng = np.random.default_rng(11)
    return rng.random((2, 3, 4))


@pytest.fixture()
def pair_dirs(tmp_path: Path) -> tuple[Path, Path]:
    realization = tmp_path / "realization"
    baseline = tmp_path / "baseline"
    realization.mkdir()
    baseline.mkdir()
    return realization, baseline


def _compare(
    realization: Path, baseline: Path, atol: float = 0.0
) -> BaselineComparisonResult:
    return compare_with_baseline(
        realization,
        baseline,
        atol=atol,
        run_id="01JTESTRUN",
        suite="simple-maccity",
        combo="MACCITY.map-consd",
        combo_id="deadbeefdeadbeef",
        baseline_ulid="01JTESTBASELINE",
    )


def test_identical_pair_passes_bit_for_bit(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values)
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)

    assert result.passed
    assert result.file_names_match
    assert result.files[0].passed
    assert result.files[0].variables[0].data_match


def test_perturbed_value_respects_absolute_tolerance(
    pair_dirs: tuple[Path, Path],
) -> None:
    realization, baseline = pair_dirs
    values = _values()
    perturbed = values.copy()
    perturbed[0, 0, 0] += 1e-6
    _write_nc(realization / "cece_a.nc", perturbed)
    _write_nc(baseline / "cece_a.nc", values)

    assert not _compare(realization, baseline, atol=0.0).passed  # bit-for-bit
    assert _compare(realization, baseline, atol=1e-5).passed  # covering atol
    assert not _compare(realization, baseline, atol=1e-7).passed  # tighter atol

    # n_mismatched respects atol: the one perturbed element at 0.0/1e-7,
    # none at the covering tolerance (data_match <=> n_mismatched == 0).
    def _variable(result: BaselineComparisonResult) -> VariableComparison:
        (variable,) = [v for f in result.files for v in f.variables]
        return variable

    assert _variable(_compare(realization, baseline, atol=0.0)).n_mismatched == 1
    assert _variable(_compare(realization, baseline, atol=1e-5)).n_mismatched == 0
    assert _variable(_compare(realization, baseline, atol=1e-7)).n_mismatched == 1

    result = _compare(realization, baseline, atol=0.0)
    (variable,) = [v for f in result.files for v in f.variables]
    assert variable.max_abs_diff == pytest.approx(1e-6, rel=1e-3)


def test_nan_position_mismatch_fails_even_under_tolerance(
    pair_dirs: tuple[Path, Path],
) -> None:
    realization, baseline = pair_dirs
    values = _values()
    with_nan = values.copy()
    with_nan[0, 1, 1] = np.nan
    _write_nc(realization / "cece_a.nc", values)
    _write_nc(baseline / "cece_a.nc", with_nan)

    assert not _compare(realization, baseline, atol=1.0).passed


def test_changed_variable_attribute_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values, var_attrs={"units": "mol mol-1"})
    _write_nc(baseline / "cece_a.nc", values, var_attrs={"units": "kg m-2 s-1"})

    result = _compare(realization, baseline, atol=1.0)  # attributes are always exact
    assert not result.passed
    assert not result.files[0].variables[0].attributes_match


def test_changed_global_attribute_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values, global_attrs={"title": "changed"})
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)
    assert not result.passed
    assert not result.files[0].global_attributes_match


def test_dimension_size_change_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    _write_nc(realization / "cece_a.nc", _values()[:, :2, :])  # lat 2 vs 3
    _write_nc(baseline / "cece_a.nc", _values())

    result = _compare(realization, baseline)
    assert not result.passed
    assert not result.files[0].dimensions_match


def test_variable_added_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values, extra_var=True)
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)
    assert not result.passed
    assert not result.files[0].variables_match


def test_file_set_mismatch_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values)
    _write_nc(baseline / "cece_a.nc", values)
    _write_nc(baseline / "cece_b.nc", values)  # baseline has an extra file

    result = _compare(realization, baseline)
    assert not result.passed
    assert not result.file_names_match


def test_format_mismatch_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values, fmt="NETCDF3_CLASSIC")
    _write_nc(baseline / "cece_a.nc", values, fmt="NETCDF4")

    result = _compare(realization, baseline)
    assert not result.passed
    assert not result.files[0].format_match


def test_difference_statistics_match_numpy(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    rng = np.random.default_rng(7)
    perturbed = values + rng.normal(0, 1e-3, values.shape)
    _write_nc(realization / "cece_a.nc", perturbed)
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)
    (variable,) = [v for f in result.files for v in f.variables]
    diff = perturbed - values
    assert variable.rmse == pytest.approx(float(np.sqrt(np.nanmean(diff**2))))
    assert variable.n_evaluated == int(np.sum(~np.isnan(diff)))
    assert variable.n_mismatched == int(np.sum(diff != 0))
    assert variable.diff_sum == pytest.approx(float(np.nansum(diff)))
    assert variable.diff_mean == pytest.approx(float(np.nanmean(diff)))
    assert variable.diff_std == pytest.approx(float(np.nanstd(diff)))
    assert variable.diff_min == pytest.approx(float(np.nanmin(diff)))
    assert variable.diff_max == pytest.approx(float(np.nanmax(diff)))
    assert variable.diff_median == pytest.approx(float(np.nanmedian(diff)))


def test_identical_pair_has_zero_difference_statistics(
    pair_dirs: tuple[Path, Path],
) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values)
    _write_nc(baseline / "cece_a.nc", values)

    (variable,) = [
        v for f in _compare(realization, baseline).files for v in f.variables
    ]
    assert variable.rmse == 0.0
    assert variable.diff_min == 0.0
    assert variable.diff_max == 0.0
    assert variable.n_mismatched == 0  # data_match <=> n_mismatched == 0


def test_comparison_csv_one_row_per_file_variable(
    pair_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values + 1.0)
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)
    csv_path = tmp_path / "x-stats-comparison.csv"
    frame = write_comparison_csv(result, csv_path)

    assert csv_path.is_file()
    assert len(frame) == 1  # one file, one variable
    row = frame.iloc[0]
    assert (row["run_id"], row["suite"], row["combo"]) == (
        "01JTESTRUN",
        "simple-maccity",
        "MACCITY.map-consd",
    )
    assert row["baseline_ulid"] == "01JTESTBASELINE"
    assert (row["file"], row["variable"]) == ("cece_a.nc", "co")
    assert not row["data_match"]
    assert row["rmse"] == pytest.approx(1.0)
    assert row["n_evaluated"] == 24  # 2 x 3 x 4 elements
    assert row["n_mismatched"] == 24  # every element shifted by 1.0
    assert not row["passed"]


def test_comparison_csvs_concatenate_to_root(
    pair_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values)
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)
    path_a = tmp_path / "a-stats-comparison.csv"
    path_b = tmp_path / "b-stats-comparison.csv"
    write_comparison_csv(result, path_a)
    write_comparison_csv(result, path_b)

    combined = concatenate_comparison_csvs(
        [path_a, path_b], tmp_path / "stats-comparison.csv"
    )
    assert (tmp_path / "stats-comparison.csv").is_file()
    assert len(combined) == 2


def _stream_selector(
    ulid: str, atol: float = 0.0, name: str = "MACC.*", **fields: str
) -> BaselineComparison:
    return BaselineComparison(
        sweep_selector=SweepSelector(
            cece_data=CeceDataSweepSelector(
                streams=[StreamSweepSelector(name=name, **fields)]
            )
        ),
        ulid=ulid,
        atol=atol,
    )


@pytest.fixture()
def maccity_combos(cece_config_path: Path) -> list[Combo]:
    from models.cece_config import Mapalgo

    base = CeceConfig.from_yaml(cece_config_path)
    sweep = Sweep(
        cece_data=CeceDataSweep(
            streams=[
                StreamSweep(name="MACCITY", mapalgo=[Mapalgo.bilinear, Mapalgo.consd])
            ]
        )
    )
    return enumerate_combos(sweep, base)


def test_baseline_comparison_model_validation() -> None:
    entry = _stream_selector("01JZZ")
    assert entry.atol == 0.0  # bit-for-bit default; per-entry override allowed
    assert _stream_selector("01JZZ", atol=0.001).atol == 0.001
    with pytest.raises(ValidationError):
        _stream_selector("01JZZ", atol=-0.5)


def test_invalid_selector_regex_rejected_at_load() -> None:
    with pytest.raises(ValidationError, match="regex"):
        _stream_selector("01JZZ", name="MACC[")


def test_selector_resolves_single_combination(maccity_combos: list[Combo]) -> None:
    entries = [
        _stream_selector("01AAA", mapalgo="bilinear"),
        _stream_selector("01BBB", mapalgo="consd"),
    ]
    resolved = resolve_baseline_comparisons(entries, maccity_combos)
    assert resolved["MACCITY.map-bilinear"].ulid == "01AAA"
    assert resolved["MACCITY.map-consd"].ulid == "01BBB"


def test_selector_regexes_are_fullmatch_anchored(maccity_combos: list[Combo]) -> None:
    # "CITY" must not substring-match MACCITY: the selector matches nothing.
    with pytest.raises(ValueError, match="matches no combination"):
        resolve_baseline_comparisons(
            [_stream_selector("01AAA", name="CITY", mapalgo="bilinear")], maccity_combos
        )


def test_selector_matching_zero_combinations_rejected(
    maccity_combos: list[Combo],
) -> None:
    with pytest.raises(ValueError, match="matches no combination"):
        resolve_baseline_comparisons(
            [_stream_selector("01AAA", mapalgo="redist")], maccity_combos
        )


def test_ambiguous_selector_rejected_naming_matches(
    maccity_combos: list[Combo],
) -> None:
    # The added-dimension insulation scenario: a selector unique under one
    # dimension becomes ambiguous when the sweep grows — surfaced, not guessed.
    with pytest.raises(ValueError) as excinfo:
        resolve_baseline_comparisons([_stream_selector("01AAA")], maccity_combos)
    message = str(excinfo.value)
    assert "MACCITY.map-bilinear" in message
    assert "MACCITY.map-consd" in message


def test_two_selectors_claiming_one_combination_rejected(
    maccity_combos: list[Combo],
) -> None:
    entries = [
        _stream_selector("01AAA", mapalgo="bilinear"),
        _stream_selector("01BBB", mapalgo="bilin.*"),
    ]
    with pytest.raises(ValueError, match="multiple selectors"):
        resolve_baseline_comparisons(entries, maccity_combos)


def test_selector_scopes_fields_to_the_named_stream(
    tmp_path: Path, cece_config_path: Path
) -> None:
    # With two swept streams, a block's fields pin to the name-matched stream
    # only: AUXDATA.tax-extend must not satisfy a MACCITY-scoped taxmode.
    from models.cece_config import Mapalgo, Taxmode

    content = yaml.safe_load(cece_config_path.read_text())
    second = dict(content["cece_data"]["streams"][0])
    second["name"] = "AUXDATA"
    content["cece_data"]["streams"].append(second)
    config_file = tmp_path / "two-stream.yaml"
    config_file.write_text(yaml.dump(content))
    base = CeceConfig.from_yaml(config_file)

    sweep = Sweep(
        cece_data=CeceDataSweep(
            streams=[
                StreamSweep(name="MACCITY", mapalgo=[Mapalgo.bilinear, Mapalgo.consd]),
                StreamSweep(name="AUXDATA", taxmode=[Taxmode.cycle, Taxmode.extend]),
            ]
        )
    )
    combos = enumerate_combos(sweep, base)  # 4 combinations

    entry = BaselineComparison(
        sweep_selector=SweepSelector(
            cece_data=CeceDataSweepSelector(
                streams=[
                    StreamSweepSelector(name="MACCITY", mapalgo="bilinear"),
                    StreamSweepSelector(name="AUXDATA", taxmode="extend"),
                ]
            )
        ),
        ulid="01AAA",
    )
    resolved = resolve_baseline_comparisons([entry], combos)
    assert list(resolved) == ["AUXDATA.tax-extend__MACCITY.map-bilinear"]

    # taxmode scoped to MACCITY matches nothing: taxmode was swept on AUXDATA.
    mis_scoped = BaselineComparison(
        sweep_selector=SweepSelector(
            cece_data=CeceDataSweepSelector(
                streams=[StreamSweepSelector(name="MACCITY", taxmode="extend")]
            )
        ),
        ulid="01BBB",
    )
    with pytest.raises(ValueError, match="matches no combination"):
        resolve_baseline_comparisons([mis_scoped], combos)


def test_species_selector_matches_by_key_regex_and_entry_position(
    cece_config_path: Path,
) -> None:
    from models.cece_config import Mapalgo, Operation

    base = CeceConfig.from_yaml(cece_config_path)
    sweep = Sweep(
        species={
            "co": [SpeciesEntrySweep(operation=[Operation.add, Operation.replace])]
        },
        cece_data=CeceDataSweep(
            streams=[StreamSweep(name="MACCITY", mapalgo=[Mapalgo.consd])]
        ),
    )
    combos = enumerate_combos(sweep, base)  # co.op-add__..., co.op-replace__...

    entry = BaselineComparison(
        sweep_selector=SweepSelector(
            species={"c.*": [SpeciesEntrySweepSelector(operation="add")]}
        ),
        ulid="01AAA",
    )
    resolved = resolve_baseline_comparisons([entry], combos)
    assert list(resolved) == ["co.op-add__MACCITY.map-consd"]


def test_suite_parses_baseline_comparisons_list(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "baseline-suite.yaml"
    suite_file.write_text(
        f"name: baseline-suite\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        "baseline_comparisons:\n"
        "  - sweep_selector:\n"
        "      cece_data:\n"
        "        streams:\n"
        "          - name: MACC.*\n"
        "            mapalgo: consd\n"
        "    ulid: 01JZZZZZZZZZZZZZZZZZZZZZZZ\n"
        "    atol: 0.001\n"
        "sweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    suite = SuiteConfig.from_yaml(suite_file)
    (entry,) = suite.baseline_comparisons
    assert entry.ulid == "01JZZZZZZZZZZZZZZZZZZZZZZZ"
    assert entry.atol == 0.001
    assert entry.sweep_selector.cece_data is not None
    assert entry.sweep_selector.cece_data.streams[0].mapalgo == "consd"
