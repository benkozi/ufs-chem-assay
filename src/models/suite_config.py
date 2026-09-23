"""The generic suite model: what every suite says regardless of application.
The application-shaped parts — the sweep and the baseline sweep selectors —
are the adapter's subclasses (applications/<name>/suite.py), selected by the
suite's `application:` value through the registry's load_suite."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ConfigDict, Field, SerializeAsAny, field_validator, model_validator

from applications.base import SweepBase, SweepSelectorBase
from models.base import StrictModel
from platforms import Platform, Runtime

# General string-assertion sentinel: "don't check". A plain null cannot serve
# because null already means "assert the value is absent".
IGNORE_VALUE = "__ignore__"


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
        description="Assert NetCDF filenames match the application's naming at the expected write times; false skips the test",
    )
    validate_file_count: bool = Field(
        True,
        description="Assert the NetCDF file count matches expected_nc_file_count (or the derived count); false skips the test",
    )
    validate_dimensions: bool = Field(
        True,
        description=(
            "Assert every configured output variable carries the application's "
            "standard dimensions in every NetCDF; false skips the test"
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


def valid_regex(value: str | None) -> str | None:
    """Validator helper shared with the adapters' selector models."""
    if value is not None:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regex {value!r}: {exc}") from exc
    return value


class BaselineComparison(StrictModel):
    """One baseline comparison: a sweep-mirroring selector pairing exactly one
    combination with a baseline ULID, modeled on nccmp at comparison time.
    The selector's shape is the adapter's (its subclass narrows the type)."""

    sweep_selector: SerializeAsAny[SweepSelectorBase] = Field(
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
    """A suite: which base config, for which application, swept how, asserted
    and analysed how. Load through applications.registry.load_suite, which
    picks the adapter's subclass; the base class alone describes a sweep-less
    suite of an unspecified application."""

    name: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-]*$",
        description=(
            "Unique suite name (lowercase slug); by convention suite 'X' lives in X-suite.yaml, "
            "but this field is authoritative"
        ),
    )
    application: str = Field(
        "cece",
        description=(
            "Registry name of the application this suite tests; selects the "
            "sweep and selector schemas and the driver config model"
        ),
    )
    config_path: Path = Field(
        description="Base driver config this suite's combinations are diffs of"
    )
    analysis: Analysis = Field(
        default_factory=Analysis,
        description="Post-run analysis configuration; defaults apply when absent",
    )
    plotting: Plotting = Field(
        default_factory=Plotting,
        description="Session-end spatial plotting; defaults apply when absent",
    )
    # Sequence (covariant): the adapter's subclass narrows the entry type.
    baseline_comparisons: Sequence[BaselineComparison] = Field(
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
    # SerializeAsAny: dumped with the subclass's schema, so run.yaml records
    # the application's sweep — the base type would serialise to nothing.
    sweep: SerializeAsAny[SweepBase] = Field(
        default_factory=SweepBase,
        description=(
            "Dimensions and values defining the combination space (the "
            "application's schema); absent or empty runs the base config as the "
            "single combination"
        ),
    )

    def resolve_config_path(
        self,
        suite_path: Path,
        *,
        config_search_path: Path | None = None,
        root_dir: Path | None = None,
        root_dir_token: str,
    ) -> None:
        """Resolve config_path to an absolute host path, in place.

        A config_path starting with the literal root-dir token (the
        application's `${<PREFIX>ROOT_DIR}`) anchors on root_dir, the
        application checkout — how checked-in suites reference configs
        living in the external checkout portably. Otherwise, relative values
        resolve against the suite file's own directory, or against
        config_search_path when set (prepended verbatim, so nested and ../
        paths work), and absolute values are used as-is. A missing target
        fails here, before any driver runs.
        """
        parts = self.config_path.parts
        if parts and parts[0] == root_dir_token:
            if root_dir is None:
                variable = root_dir_token[2:-1]
                raise ValueError(
                    f"suite config_path {str(self.config_path)!r} anchors on "
                    f"{root_dir_token}, but the application checkout is not "
                    f"configured; set {variable}"
                )
            resolved = root_dir.joinpath(*parts[1:])
        elif self.config_path.is_absolute():
            resolved = self.config_path
        elif config_search_path is not None:
            resolved = config_search_path / self.config_path
        else:
            resolved = suite_path.parent / self.config_path
        resolved = resolved.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(
                f"suite config_path {str(self.config_path)!r} resolved to {resolved}, which does not exist"
            )
        self.config_path = resolved


class RunManifest(StrictModel):
    """Output-only record of one test run, written to the output root as
    run.yaml. The run_id is generated at runtime (never read from
    configuration); the suites are recorded as resolved — what actually ran,
    in selection order (a one-element list for single-suite sessions)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str  # session ULID; its timestamp encodes the run start
    application: str = Field(
        description="Registry name of the application the session ran"
    )
    # Required-but-nullable: every writer must state the SHA explicitly —
    # null is the deliberate "no checkout configured" record (checkout-less
    # dry-runs), never an accidental omission.
    application_commit: str | None = Field(
        description=(
            "HEAD commit SHA of the application checkout the session ran "
            "against; null only when no checkout is configured (a configured "
            "root without a resolvable SHA fails the session at start)"
        ),
    )
    harness_version: str = Field(
        description="Installed version of the harness package that produced the run"
    )
    harness_commit: str | None = Field(
        description=(
            "HEAD commit SHA of the harness checkout, `-dirty` when its working "
            "tree had uncommitted changes; null when the harness did not run "
            "from a git checkout"
        ),
    )
    platform: Platform = Field(
        description="Machine the session ran on (settings.platform)"
    )
    runtime: Runtime = Field(
        description="How the driver was spawned: docker, native, or slurm (settings.runtime)"
    )
    modulefile: str | None = Field(
        description="Modulefile the driver jobs loaded (slurm runtime); null otherwise"
    )
    # SerializeAsAny: each suite dumps with its adapter's schema (the sweep).
    suites: list[SerializeAsAny[SuiteConfig]]

    @field_validator("suites", mode="before")
    @classmethod
    def _dispatch_suites(cls, value: object) -> object:
        # A run.yaml read back (a report tool, a round-trip test) validates
        # each recorded suite through its adapter, like load_suite does.
        # Local import: the registry imports the adapters, which import this.
        from applications.registry import get_application

        if not isinstance(value, list):
            return value
        return [
            get_application(
                entry.get("application", "cece")
            ).suite_model.model_validate(entry)
            if isinstance(entry, dict)
            else entry
            for entry in value
        ]

    def to_yaml(self, path: Path) -> None:
        with open(path, "w") as f:
            yaml.dump(
                self.model_dump(mode="json"),
                f,
                default_flow_style=False,
                sort_keys=False,
            )
