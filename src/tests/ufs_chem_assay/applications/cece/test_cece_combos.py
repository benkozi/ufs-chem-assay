from pathlib import Path

import pytest
import yaml

from applications.cece.combos import build_config, dimensions, effective_parameters
from applications.cece.config import (
    CeceConfig,
    Mapalgo,
    Operation,
    OutputField,
    Taxmode,
    Vdist,
    VdistMethod,
)
from applications.cece.suite import (
    CeceDataSweep,
    CeceSweep,
    SpeciesEntrySweep,
    StreamSweep,
)
from combos import Combo, enumerate_combos, write_combos_csv


def _enumerate(sweep: CeceSweep, base_config: CeceConfig) -> list[Combo]:
    return enumerate_combos(dimensions(sweep, base_config))


@pytest.fixture()
def base_config(cece_config_path: Path) -> CeceConfig:
    return CeceConfig.from_yaml(cece_config_path)


@pytest.fixture()
def two_stream_config_path(tmp_path: Path, cece_config_path: Path) -> Path:
    """The maccity base config plus a second stream named AUXDATA."""
    content = yaml.safe_load(cece_config_path.read_text())
    second = dict(content["cece_data"]["streams"][0])
    second["name"] = "AUXDATA"
    content["cece_data"]["streams"].append(second)
    path = tmp_path / "two-stream.yaml"
    path.write_text(yaml.dump(content))
    return path


def _maccity_sweep() -> CeceSweep:
    return CeceSweep(
        cece_data=CeceDataSweep(
            streams=[
                StreamSweep(
                    name="MACCITY",
                    mapalgo=[Mapalgo.bilinear, Mapalgo.consd, Mapalgo.passthrough],
                )
            ]
        )
    )


def test_maccity_sweep_enumerates_three_qualified_combos(
    base_config: CeceConfig,
) -> None:
    combos = _enumerate(_maccity_sweep(), base_config)
    assert [combo.name for combo in combos] == [
        "MACCITY.map-bilinear",
        "MACCITY.map-consd",
        "MACCITY.map-passthrough",
    ]


def test_combo_ids_are_runtime_ulids(base_config: CeceConfig) -> None:
    # Ids are minted per enumeration (runtime-only, like run_id): unique
    # within a run, different across enumerations — no content semantics.
    # combos.csv carries the parameter mapping; joins use suite + name.
    first = _enumerate(_maccity_sweep(), base_config)
    second = _enumerate(_maccity_sweep(), base_config)
    all_ids = [combo.combo_id for combo in first + second]
    assert len(set(all_ids)) == len(all_ids)
    for combo_id in all_ids:
        assert len(combo_id) == 26  # ULID canonical text form
        assert combo_id == combo_id.upper()


def test_normalization_declaration_order_never_matters(base_config: CeceConfig) -> None:
    # Same sweep, values reversed and species/stream blocks declared in a
    # different order, must enumerate byte-identical names and ids.
    tidy = CeceSweep(
        species={
            "co": [SpeciesEntrySweep(operation=[Operation.add, Operation.replace])]
        },
        cece_data=CeceDataSweep(
            streams=[
                StreamSweep(name="MACCITY", mapalgo=[Mapalgo.bilinear, Mapalgo.consd])
            ]
        ),
    )
    shuffled = CeceSweep(
        cece_data=CeceDataSweep(
            streams=[
                StreamSweep(name="MACCITY", mapalgo=[Mapalgo.consd, Mapalgo.bilinear])
            ]
        ),
        species={
            "co": [SpeciesEntrySweep(operation=[Operation.replace, Operation.add])]
        },
    )
    tidy_combos = _enumerate(tidy, base_config)
    shuffled_combos = _enumerate(shuffled, base_config)
    assert [combo.name for combo in tidy_combos] == [
        combo.name for combo in shuffled_combos
    ]
    # Canonical order: species targets before stream targets.
    assert tidy_combos[0].name == "co.op-add__MACCITY.map-bilinear"


def test_empty_sweep_enumerates_identity_combo(base_config: CeceConfig) -> None:
    # A sweep attaching no dimensions runs the base config as the single
    # combination (sweep-less suites; examples-as-suites design).
    (combo,) = _enumerate(CeceSweep(), base_config)
    assert combo.values == ()
    assert combo.name == "base"
    assert len(combo.combo_id) == 26  # runtime ULID, like every combo


def test_identity_combo_build_config_changes_output_only(
    base_config: CeceConfig, cece_config_path: Path
) -> None:
    (combo,) = _enumerate(CeceSweep(), base_config)
    generated = build_config(
        combo, output_directory="/combo_runs/x", config_path=cece_config_path
    )
    assert generated.output is not None
    assert generated.output.directory == "/combo_runs/x"
    assert generated.driver.log_file == "/combo_runs/x/cece.log"
    # Everything else is the base config verbatim.
    assert generated.cece_data == base_config.cece_data
    assert generated.species == base_config.species


def test_field_attributes_round_trip_through_generated_configs(
    base_config: CeceConfig, cece_config_path: Path
) -> None:
    # The checked-in base config declares output attributes for co nested in
    # its fields entry; generated combo configs carry them to the driver.
    assert base_config.output is not None
    assert base_config.output.fields == [
        OutputField(
            name="co",
            attributes={
                "units": "kg m-2 s-1",
                "long_name": "carbon_monoxide_emission_flux",
            },
        )
    ]
    (combo,) = _enumerate(_single_consd_sweep(), base_config)
    generated = build_config(combo, output_directory=".", config_path=cece_config_path)
    assert generated.output is not None
    assert generated.output.fields == base_config.output.fields


def _single_consd_sweep() -> CeceSweep:
    return CeceSweep(
        cece_data=CeceDataSweep(
            streams=[StreamSweep(name="MACCITY", mapalgo=[Mapalgo.consd])]
        )
    )


def test_build_config_applies_to_named_non_first_stream(
    two_stream_config_path: Path,
) -> None:
    base = CeceConfig.from_yaml(two_stream_config_path)
    sweep = CeceSweep(
        cece_data=CeceDataSweep(
            streams=[StreamSweep(name="AUXDATA", mapalgo=[Mapalgo.nn])]
        )
    )
    (combo,) = _enumerate(sweep, base)
    assert combo.name == "AUXDATA.map-nn"

    config = build_config(
        combo, output_directory=".", config_path=two_stream_config_path
    )
    streams = {stream.name: stream for stream in config.cece_data.streams}
    assert streams["AUXDATA"].mapalgo is Mapalgo.nn
    assert streams["MACCITY"].mapalgo is Mapalgo.consd  # base value untouched


def test_stream_targets_sorted_lexicographically(two_stream_config_path: Path) -> None:
    base = CeceConfig.from_yaml(two_stream_config_path)
    sweep = CeceSweep(
        cece_data=CeceDataSweep(
            streams=[
                StreamSweep(name="MACCITY", mapalgo=[Mapalgo.consd]),
                StreamSweep(name="AUXDATA", taxmode=[Taxmode.extend]),
            ]
        )
    )
    (combo,) = _enumerate(sweep, base)
    assert combo.name == "AUXDATA.tax-extend__MACCITY.map-consd"


def test_build_config_supplies_vdist_companions_per_method(
    base_config: CeceConfig, cece_config_path: Path
) -> None:
    # Sweeping vdist_method builds the nested vdist block the driver parses,
    # with the companions each method needs for a meaningful config.
    sweep = CeceSweep(
        species={"co": [SpeciesEntrySweep(vdist_method=list(VdistMethod))]}
    )
    combos = _enumerate(sweep, base_config)
    assert [combo.name for combo in combos] == [
        "co.vd-height",
        "co.vd-pbl",
        "co.vd-pressure",
        "co.vd-range",
        "co.vd-single",
    ]

    def vdist_for(name: str) -> Vdist:
        (combo,) = [c for c in combos if c.name == name]
        entry = build_config(
            combo, output_directory=".", config_path=cece_config_path
        ).species["co"][0]
        assert entry.vdist is not None
        return entry.vdist

    height = vdist_for("co.vd-height")
    assert height.method is VdistMethod.height
    assert (height.h_start, height.h_end) == (0.0, 100.0)
    assert height.layer_start is None and height.p_start is None

    pressure = vdist_for("co.vd-pressure")
    assert pressure.method is VdistMethod.pressure
    assert (pressure.p_start, pressure.p_end) == (100000.0, 90000.0)
    assert pressure.layer_start is None and pressure.h_start is None

    layer_range = vdist_for("co.vd-range")
    assert layer_range.method is VdistMethod.range
    assert (layer_range.layer_start, layer_range.layer_end) == (0, 2)
    assert layer_range.p_start is None and layer_range.h_start is None

    single = vdist_for("co.vd-single")
    assert single.method is VdistMethod.single
    assert single.layer_start == 0
    assert single.layer_end is None

    pbl = vdist_for("co.vd-pbl")
    assert pbl.method is VdistMethod.pbl
    assert (pbl.layer_start, pbl.layer_end) == (None, None)
    assert (pbl.p_start, pbl.p_end, pbl.h_start, pbl.h_end) == (
        None,
        None,
        None,
        None,
    )


def test_unknown_stream_selector_rejected(base_config: CeceConfig) -> None:
    sweep = CeceSweep(
        cece_data=CeceDataSweep(
            streams=[StreamSweep(name="NOPE", mapalgo=[Mapalgo.consd])]
        )
    )
    with pytest.raises(ValueError, match="NOPE"):
        _enumerate(sweep, base_config)


def test_unknown_species_selector_rejected(base_config: CeceConfig) -> None:
    sweep = CeceSweep(species={"nox": [SpeciesEntrySweep(operation=[Operation.add])]})
    with pytest.raises(ValueError, match="nox"):
        _enumerate(sweep, base_config)


def test_oversized_species_entry_list_rejected(base_config: CeceConfig) -> None:
    sweep = CeceSweep(
        species={
            "co": [
                SpeciesEntrySweep(operation=[Operation.add]),
                SpeciesEntrySweep(operation=[Operation.replace]),
            ]
        }
    )
    with pytest.raises(ValueError, match="entry blocks"):
        _enumerate(sweep, base_config)


def test_write_combos_csv_is_effective_parameter_table(
    tmp_path: Path, base_config: CeceConfig, cece_config_path: Path
) -> None:
    """One row per (target, sweepable field) per combo with values from the
    generated config: swept rows carry the swept value, pinned rows the base
    value — parameters are joinable whether swept or not."""
    combos = _enumerate(_maccity_sweep(), base_config)
    entries = [
        (
            "simple-maccity",
            combo,
            effective_parameters(
                combo,
                build_config(combo, output_directory=".", config_path=cece_config_path),
            ),
        )
        for combo in combos
    ]
    csv_path = tmp_path / "combos.csv"
    frame = write_combos_csv(
        entries,
        run_id="01JZZZZZZZZZZZZZZZZZZZZZZZ",
        application="cece",
        csv_path=csv_path,
    )

    assert csv_path.is_file()
    assert list(frame.columns) == [
        "run_id",
        "combo_id",
        "application",
        "suite",
        "name",
        "target",
        "field",
        "value",
        "swept",
    ]
    assert set(frame["application"]) == {"cece"}
    # maccity: species co (3 sweepable fields) + stream MACCITY (3) per combo.
    assert len(frame) == 6 * 3
    assert set(frame["suite"]) == {"simple-maccity"}

    consd = frame[frame["combo_id"] == combos[1].combo_id]
    assert set(consd["name"]) == {"MACCITY.map-consd"}
    by_key = {
        (row["target"], row["field"]): (row["value"], row["swept"])
        for _, row in consd.iterrows()
    }
    # The swept dimension carries the swept value and the flag.
    assert by_key[("MACCITY", "mapalgo")] == ("consd", True)
    # Pinned parameters are recorded with their base-config values.
    assert by_key[("MACCITY", "taxmode")] == ("cycle", False)
    assert by_key[("MACCITY", "tintalgo")] == ("linear", False)
    assert by_key[("co", "operation")] == ("add", False)
    # Unset optionals record an empty value.
    value, swept = by_key[("co", "category")]
    assert value == "" and not swept


def test_write_combos_csv_covers_sweep_less_combos(
    tmp_path: Path, base_config: CeceConfig, cece_config_path: Path
) -> None:
    # A base combo gets its full effective parameter set — all pinned.
    (combo,) = _enumerate(CeceSweep(), base_config)
    config = build_config(combo, output_directory=".", config_path=cece_config_path)
    frame = write_combos_csv(
        [("ex-suite", combo, effective_parameters(combo, config))],
        run_id="01JZZZZZZZZZZZZZZZZZZZZZZZ",
        application="cece",
        csv_path=tmp_path / "combos.csv",
    )
    assert len(frame) == 6
    assert not frame["swept"].any()
    mapalgo = frame[(frame["target"] == "MACCITY") & (frame["field"] == "mapalgo")]
    assert mapalgo["value"].iloc[0] == "consd"  # the base-config value
