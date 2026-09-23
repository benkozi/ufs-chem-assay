"""CECE's baseline sweep selectors: the models, and their matching against
enumerated combinations through the adapter."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from applications.cece.config import CeceConfig, Mapalgo, Operation, Taxmode
from applications.cece.suite import (
    CeceBaselineComparison,
    CeceDataSweep,
    CeceDataSweepSelector,
    CeceSweep,
    CeceSweepSelector,
    SpeciesEntrySweep,
    SpeciesEntrySweepSelector,
    StreamSweep,
    StreamSweepSelector,
)
from applications.registry import get_application, load_suite
from combos import Combo, enumerate_combos
from comparison import resolve_baseline_comparisons

_APP = get_application("cece")


def _combos(sweep: CeceSweep, base: CeceConfig) -> list[Combo]:
    return enumerate_combos(_APP.dimensions(sweep, base))


def _stream_selector(
    ulid: str, atol: float = 0.0, name: str = "MACC.*", **fields: str
) -> CeceBaselineComparison:
    return CeceBaselineComparison(
        sweep_selector=CeceSweepSelector(
            cece_data=CeceDataSweepSelector(
                streams=[StreamSweepSelector(name=name, **fields)]
            )
        ),
        ulid=ulid,
        atol=atol,
    )


@pytest.fixture()
def maccity_combos(cece_config_path: Path) -> list[Combo]:
    base = CeceConfig.from_yaml(cece_config_path)
    sweep = CeceSweep(
        cece_data=CeceDataSweep(
            streams=[
                StreamSweep(name="MACCITY", mapalgo=[Mapalgo.bilinear, Mapalgo.consd])
            ]
        )
    )
    return _combos(sweep, base)


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
    resolved = resolve_baseline_comparisons(_APP, entries, maccity_combos)
    assert resolved["MACCITY.map-bilinear"].ulid == "01AAA"
    assert resolved["MACCITY.map-consd"].ulid == "01BBB"


def test_selector_regexes_are_fullmatch_anchored(maccity_combos: list[Combo]) -> None:
    # "CITY" must not substring-match MACCITY: the selector matches nothing.
    with pytest.raises(ValueError, match="matches no combination"):
        resolve_baseline_comparisons(
            _APP,
            [_stream_selector("01AAA", name="CITY", mapalgo="bilinear")],
            maccity_combos,
        )


def test_selector_matching_zero_combinations_rejected(
    maccity_combos: list[Combo],
) -> None:
    with pytest.raises(ValueError, match="matches no combination"):
        resolve_baseline_comparisons(
            _APP, [_stream_selector("01AAA", mapalgo="redist")], maccity_combos
        )


def test_ambiguous_selector_rejected_naming_matches(
    maccity_combos: list[Combo],
) -> None:
    # The added-dimension insulation scenario: a selector unique under one
    # dimension becomes ambiguous when the sweep grows — surfaced, not guessed.
    with pytest.raises(ValueError) as excinfo:
        resolve_baseline_comparisons(_APP, [_stream_selector("01AAA")], maccity_combos)
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
        resolve_baseline_comparisons(_APP, entries, maccity_combos)


def test_selector_scopes_fields_to_the_named_stream(
    tmp_path: Path, cece_config_path: Path
) -> None:
    # With two swept streams, a block's fields pin to the name-matched stream
    # only: AUXDATA.tax-extend must not satisfy a MACCITY-scoped taxmode.
    content = yaml.safe_load(cece_config_path.read_text())
    second = dict(content["cece_data"]["streams"][0])
    second["name"] = "AUXDATA"
    content["cece_data"]["streams"].append(second)
    config_file = tmp_path / "two-stream.yaml"
    config_file.write_text(yaml.dump(content))
    base = CeceConfig.from_yaml(config_file)

    sweep = CeceSweep(
        cece_data=CeceDataSweep(
            streams=[
                StreamSweep(name="MACCITY", mapalgo=[Mapalgo.bilinear, Mapalgo.consd]),
                StreamSweep(name="AUXDATA", taxmode=[Taxmode.cycle, Taxmode.extend]),
            ]
        )
    )
    combos = _combos(sweep, base)  # 4 combinations

    entry = CeceBaselineComparison(
        sweep_selector=CeceSweepSelector(
            cece_data=CeceDataSweepSelector(
                streams=[
                    StreamSweepSelector(name="MACCITY", mapalgo="bilinear"),
                    StreamSweepSelector(name="AUXDATA", taxmode="extend"),
                ]
            )
        ),
        ulid="01AAA",
    )
    resolved = resolve_baseline_comparisons(_APP, [entry], combos)
    assert list(resolved) == ["AUXDATA.tax-extend__MACCITY.map-bilinear"]

    # taxmode scoped to MACCITY matches nothing: taxmode was swept on AUXDATA.
    mis_scoped = CeceBaselineComparison(
        sweep_selector=CeceSweepSelector(
            cece_data=CeceDataSweepSelector(
                streams=[StreamSweepSelector(name="MACCITY", taxmode="extend")]
            )
        ),
        ulid="01BBB",
    )
    with pytest.raises(ValueError, match="matches no combination"):
        resolve_baseline_comparisons(_APP, [mis_scoped], combos)


def test_species_selector_matches_by_key_regex_and_entry_position(
    cece_config_path: Path,
) -> None:
    base = CeceConfig.from_yaml(cece_config_path)
    sweep = CeceSweep(
        species={
            "co": [SpeciesEntrySweep(operation=[Operation.add, Operation.replace])]
        },
        cece_data=CeceDataSweep(
            streams=[StreamSweep(name="MACCITY", mapalgo=[Mapalgo.consd])]
        ),
    )
    combos = _combos(sweep, base)  # co.op-add__..., co.op-replace__...

    entry = CeceBaselineComparison(
        sweep_selector=CeceSweepSelector(
            species={"c.*": [SpeciesEntrySweepSelector(operation="add")]}
        ),
        ulid="01AAA",
    )
    resolved = resolve_baseline_comparisons(_APP, [entry], combos)
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
    suite = load_suite(suite_file)
    (entry,) = suite.baseline_comparisons
    assert isinstance(entry, CeceBaselineComparison)
    assert entry.ulid == "01JZZZZZZZZZZZZZZZZZZZZZZZ"
    assert entry.atol == 0.001
    assert entry.sweep_selector.cece_data is not None
    assert entry.sweep_selector.cece_data.streams[0].mapalgo == "consd"
