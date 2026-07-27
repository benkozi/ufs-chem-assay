from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Callable, TypeVar

import yaml
from pydantic import ConfigDict, Field, field_validator, model_validator

from models.base import StrictModel
from models.cece_config import (
    Category,
    Mapalgo,
    Operation,
    Taxmode,
    Tintalgo,
    VdistMethod,
)


# General string-assertion sentinel: "don't check". A plain null cannot serve
# because null already means "assert the value is absent".
IGNORE_VALUE = "__ignore__"

# config_path prefix anchoring a checked-in suite on the external CECE
# checkout (settings.root_dir); a literal token, not environment expansion.
_ROOT_DIR_TOKEN = "${CECE_ROOT_DIR}"


_SweptEnum = TypeVar("_SweptEnum", bound=StrEnum)


def _unique_values(values: list[_SweptEnum] | None) -> list[_SweptEnum] | None:
    """Duplicate sweep values would enumerate two combinations with the same
    id and directory — rejected loudly rather than deduped."""
    if values is not None and len(values) != len(set(values)):
        raise ValueError("duplicate values in sweep list")
    return values


def _expand_enum_regex(enum_cls: type[StrEnum]) -> Callable[[object], object]:
    """Sweep values accept a regex string in place of a value list: expanded
    (fullmatch) against the enum's values into the sorted matching list at
    load time, so ".*" always means every value — including ones added after
    the suite was written — and run.yaml records the expanded list. A regex
    matching nothing, like an invalid one, fails the load."""

    def expand(value: object) -> object:
        if isinstance(value, str):
            try:
                pattern = re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regex {value!r}: {exc}") from exc
            matched = sorted(
                member.value for member in enum_cls if pattern.fullmatch(member.value)
            )
            if not matched:
                raise ValueError(
                    f"regex {value!r} matches no {enum_cls.__name__} value "
                    f"(values: {sorted(member.value for member in enum_cls)})"
                )
            return matched
        return value

    return expand


class StreamSweep(StrictModel):
    """Swept dimensions attached to one stream, selected by name."""

    name: str = Field(description="Stream in the base config this sweep attaches to")
    taxmode: list[Taxmode] | None = Field(None, min_length=1)
    tintalgo: list[Tintalgo] | None = Field(None, min_length=1)
    mapalgo: list[Mapalgo] | None = Field(None, min_length=1)

    _expand_taxmode = field_validator("taxmode", mode="before")(
        _expand_enum_regex(Taxmode)
    )
    _expand_tintalgo = field_validator("tintalgo", mode="before")(
        _expand_enum_regex(Tintalgo)
    )
    _expand_mapalgo = field_validator("mapalgo", mode="before")(
        _expand_enum_regex(Mapalgo)
    )
    _unique = field_validator("taxmode", "tintalgo", "mapalgo")(_unique_values)


class CeceDataSweep(StrictModel):
    streams: list[StreamSweep] = Field(min_length=1)

    @field_validator("streams")
    @classmethod
    def _unique_stream_names(cls, streams: list[StreamSweep]) -> list[StreamSweep]:
        names = [stream.name for stream in streams]
        if len(names) != len(set(names)):
            raise ValueError("duplicate stream names in sweep")
        return streams


class SpeciesEntrySweep(StrictModel):
    """Swept dimensions attached to one species entry, selected by list
    position (sweep list index i -> species.<name>[i]); {} skips an entry."""

    operation: list[Operation] | None = Field(None, min_length=1)
    category: list[Category] | None = Field(None, min_length=1)
    vdist_method: list[VdistMethod] | None = Field(None, min_length=1)

    _expand_operation = field_validator("operation", mode="before")(
        _expand_enum_regex(Operation)
    )
    _expand_category = field_validator("category", mode="before")(
        _expand_enum_regex(Category)
    )
    _expand_vdist_method = field_validator("vdist_method", mode="before")(
        _expand_enum_regex(VdistMethod)
    )
    _unique = field_validator("operation", "category", "vdist_method")(_unique_values)


class Sweep(StrictModel):
    """Sweeps mirror the driver-config structure and attach to named streams
    or positional species entries. The combination space is the cartesian
    product of every attached value list."""

    cece_data: CeceDataSweep | None = None
    species: dict[str, list[SpeciesEntrySweep]] | None = None


class AttributesAssertion(StrictModel):
    """Expected attribute dictionary for a species' variable. Per-value
    semantics: a string asserts exact equality, null asserts absence, and
    IGNORE_VALUE ("__ignore__") places no constraint on the value."""

    exact: bool = Field(
        True,
        description="True: attribute dictionaries match exactly (unmentioned found attributes fail); False: expected is a subset",
    )
    expected: dict[str, str | None] = Field(
        default_factory=dict,
        description="Attribute name -> expected value (string), null (must be absent), or '__ignore__' (any value)",
    )


class SpeciesAssertions(StrictModel):
    """Per-species expectations about the NetCDF output."""

    attributes: AttributesAssertion | None = Field(
        None,
        description="Full attribute-dictionary expectation for the species' variable; None = no attribute test",
    )


class Assertions(StrictModel):
    """What each combination's output is expected to look like. Configures
    expectations only — never run behavior (fail-fast stays pytest's -x)."""

    expected_nc_file_count: int | None = Field(
        None,
        ge=0,
        description="Exact NetCDF file count per combo; None derives it from the combo config; 0 means none expected",
    )
    validate_filenames: bool = Field(
        True,
        description="Assert NetCDF filenames match filename_pattern at the expected write times; false skips the test",
    )
    validate_file_count: bool = Field(
        True,
        description="Assert the NetCDF file count matches expected_nc_file_count (or the derived count); false skips the test",
    )
    validate_dimensions: bool = Field(
        True,
        description=(
            "Assert every configured output variable carries the standard "
            "(time, lev, lat, lon) dimensions in every NetCDF; false skips "
            "the test"
        ),
    )
    species: dict[str, SpeciesAssertions] | None = Field(
        None,
        description="Per-species output expectations, keyed by species name; absent species are not checked",
    )


class Analysis(StrictModel):
    """Post-run analysis steps. Like Assertions, configures what to compute,
    never run behavior."""

    compute_descriptive_stats: bool = Field(
        True,
        description="Compute per-NetCDF descriptive statistics and write per-combo + suite-level CSVs; false skips",
    )


def _valid_regex(value: str | None) -> str | None:
    if value is not None:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regex {value!r}: {exc}") from exc
    return value


class StreamSweepSelector(StrictModel):
    """Mirror of StreamSweep with regexes at the leaves: name selects the
    stream target, sibling fields constrain that stream's swept values."""

    name: str = Field(description="Regex (fullmatch) against the stream target's name")
    taxmode: str | None = Field(
        None, description="Regex (fullmatch) against the stream's swept taxmode value"
    )
    tintalgo: str | None = Field(
        None, description="Regex (fullmatch) against the stream's swept tintalgo value"
    )
    mapalgo: str | None = Field(
        None, description="Regex (fullmatch) against the stream's swept mapalgo value"
    )

    _regex = field_validator("name", "taxmode", "tintalgo", "mapalgo")(_valid_regex)


class CeceDataSweepSelector(StrictModel):
    streams: list[StreamSweepSelector] = Field(
        min_length=1, description="Stream selector blocks; all must be satisfied"
    )


class SpeciesEntrySweepSelector(StrictModel):
    """Mirror of SpeciesEntrySweep with regexes at the leaves; list position
    selects the species entry index, as in the sweep."""

    operation: str | None = Field(
        None, description="Regex (fullmatch) against the entry's swept operation value"
    )
    category: str | None = Field(
        None, description="Regex (fullmatch) against the entry's swept category value"
    )
    vdist_method: str | None = Field(
        None,
        description="Regex (fullmatch) against the entry's swept vdist_method value",
    )

    _regex = field_validator("operation", "category", "vdist_method")(_valid_regex)


class SweepSelector(StrictModel):
    """Mirror of Sweep: a structural pattern matched against a combination's
    swept elements. Unspecified structure is unconstrained."""

    cece_data: CeceDataSweepSelector | None = Field(
        None, description="Stream selector blocks; None leaves streams unconstrained"
    )
    species: dict[str, list[SpeciesEntrySweepSelector]] | None = Field(
        None,
        description="Species-name regex -> positional entry selectors; None leaves species unconstrained",
    )

    @field_validator("species")
    @classmethod
    def _species_keys_are_regexes(
        cls, species: dict[str, list[SpeciesEntrySweepSelector]] | None
    ) -> dict[str, list[SpeciesEntrySweepSelector]] | None:
        if species is not None:
            for key in species:
                _valid_regex(key)
        return species


class BaselineComparison(StrictModel):
    """One baseline comparison: a sweep-mirroring selector pairing exactly one
    combination with a baseline ULID, modeled on nccmp at comparison time."""

    sweep_selector: SweepSelector = Field(
        description="Structural pattern selecting exactly one enumerated combination"
    )
    ulid: str = Field(description="ULID of the baseline under baseline_root_dir")
    atol: float = Field(
        0.0,
        ge=0,
        description="0 = bit-for-bit data comparison; > 0 = absolute tolerance (no scaling)",
    )
    plot: bool = Field(
        True,
        description="Render bias plots + GIF for this comparison at session end",
    )


class Plotting(StrictModel):
    """Spatial-plot rendering at session end. The shared color scale derives
    from the descriptive statistics, so plotting requires the stats step."""

    enabled: bool = Field(
        True, description="Render spatial plots per NetCDF into <combo>/plots/"
    )
    gif_enabled: bool = Field(
        True, description="Assemble the per-variable plots into an animated GIF"
    )


class SuiteConfig(StrictModel):
    name: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-]*$",
        description=(
            "Unique suite name (lowercase slug); by convention suite 'X' lives in X-suite.yaml, "
            "but this field is authoritative"
        ),
    )
    config_path: Path = Field(
        description="Base CECE driver config this suite's combinations are diffs of"
    )
    analysis: Analysis = Field(
        default_factory=Analysis,
        description="Post-run analysis configuration; defaults apply when absent",
    )
    plotting: Plotting = Field(
        default_factory=Plotting,
        description="Session-end spatial plotting; defaults apply when absent",
    )
    baseline_comparisons: list[BaselineComparison] = Field(
        default_factory=list,
        description="Per-combination baseline comparisons; empty/absent disables",
    )

    @model_validator(mode="after")
    def _plotting_requires_stats(self) -> SuiteConfig:
        if self.plotting.enabled and not self.analysis.compute_descriptive_stats:
            raise ValueError(
                "plotting.enabled requires analysis.compute_descriptive_stats: the shared "
                "color scale derives from the descriptive statistics (disable plotting to "
                "run stats-less)"
            )
        return self

    assertions: Assertions = Field(
        default_factory=Assertions,
        description="Post-run assertion expectations; defaults apply when absent",
    )
    timeout_s: int = Field(
        gt=0,
        description="Per-combination driver timeout in seconds; capped by the run_timeout_s setting",
    )
    sweep: Sweep = Field(
        default_factory=Sweep,
        description=(
            "Enum dimensions and values defining the combination space; absent "
            "or empty runs the base config as the single combination"
        ),
    )

    @classmethod
    def from_yaml(
        cls,
        path: Path,
        config_search_path: Path | None = None,
        root_dir: Path | None = None,
    ) -> SuiteConfig:
        """Load a suite file; config_path is resolved to an absolute host path.

        A config_path starting with the literal ${CECE_ROOT_DIR} token anchors
        on root_dir (the CECE checkout) — how checked-in suites reference
        configs living in the external checkout portably. Otherwise, relative
        values resolve against the suite file's own directory, or against
        config_search_path when set (prepended verbatim, so nested and ../
        paths work), and absolute values are used as-is. A missing target
        fails here, before any containers run.
        """
        with open(path) as f:
            suite = cls.model_validate(yaml.safe_load(f))
        parts = suite.config_path.parts
        if parts and parts[0] == _ROOT_DIR_TOKEN:
            if root_dir is None:
                raise ValueError(
                    f"suite config_path {str(suite.config_path)!r} anchors on "
                    f"{_ROOT_DIR_TOKEN}, but the CECE repository root is not "
                    "configured; pass --cece-root-dir or set CECE_ROOT_DIR"
                )
            resolved = root_dir.joinpath(*parts[1:])
        elif suite.config_path.is_absolute():
            resolved = suite.config_path
        elif config_search_path is not None:
            resolved = config_search_path / suite.config_path
        else:
            resolved = path.parent / suite.config_path
        resolved = resolved.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(
                f"suite config_path {str(suite.config_path)!r} resolved to {resolved}, which does not exist"
            )
        suite.config_path = resolved
        return suite


class RunManifest(StrictModel):
    """Output-only record of one test run, written to the output root as
    run.yaml. The run_id is generated at runtime (never read from
    configuration); the suites are recorded as resolved — what actually ran,
    in selection order (a one-element list for single-suite sessions)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str  # session ULID; its timestamp encodes the run start
    suites: list[SuiteConfig]

    def to_yaml(self, path: Path) -> None:
        with open(path, "w") as f:
            yaml.dump(
                self.model_dump(mode="json"),
                f,
                default_flow_style=False,
                sort_keys=False,
            )
