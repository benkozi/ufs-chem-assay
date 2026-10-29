"""The generic suite model: what every suite says regardless of application.
The application-shaped parts — the sweep and the baseline sweep selectors —
are the adapter's subclasses (applications/<name>/suite.py), selected by the
suite's `application:` value through the registry's load_suite."""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum, unique
from pathlib import Path, PurePosixPath

import yaml
from pydantic import ConfigDict, Field, SerializeAsAny, field_validator, model_validator

from applications.base import SweepBase, SweepSelectorBase
from models.base import StrictModel
from platforms import Platform, Runtime

# General string-assertion sentinel: "don't check". A plain null cannot serve
# because null already means "assert the value is absent".
IGNORE_VALUE = "__ignore__"

# An S3 *object* URI: bucket naming rules (lowercase, digits, dots, hyphens,
# 3-63 chars, no edge dot/hyphen) plus a non-empty key that does not end in a
# slash — a prefix is not an input.
S3_OBJECT_URI_PATTERN = r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/(?:[^/\s]+/)*[^/\s]+$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class InputFile(StrictModel):
    """One input the suite's base configuration reads, expected in the
    application checkout's data directory: an S3 object, fetched by
    `ufs-chem-assay fetch` with one filtered `aws s3 sync` of its parent
    prefix — or, with no url, a file placed there by hand (not in any
    bucket yet) that `fetch` only ever verifies by digest."""

    url: str | None = Field(
        None,
        pattern=S3_OBJECT_URI_PATTERN,
        description=(
            "S3 object URI of the file (s3://bucket/key), fetched with one "
            "filtered `aws s3 sync` of the key's parent prefix; null declares an "
            "input with no remote source yet — placed at dst by hand, verified "
            "by sha256, never downloaded"
        ),
    )
    public: bool = Field(
        False,
        description=(
            "True: the bucket allows anonymous reads and the fetch passes "
            "--no-sign-request (no credentials needed); False: the AWS CLI's own "
            "credential chain applies"
        ),
    )
    dst: PurePosixPath = Field(
        description=(
            "Destination relative to the checkout's data directory (the "
            "adapter's data_dirname); no absolute paths, no '..' or '.' parts"
        ),
    )
    sha256: str | None = Field(
        None,
        pattern=SHA256_PATTERN,
        description=(
            "Expected SHA-256 (lowercase hex); a present file with this digest is "
            "skipped, a mismatch is re-fetched, a download is verified before it "
            "replaces anything; null skips present non-empty files unverified"
        ),
    )
    sources: dict[Platform, PurePosixPath] | None = Field(
        None,
        description=(
            "Where the file is known to live, per platform (absolute paths): on "
            "that platform `fetch` links an absent source-less input from here "
            "before consulting the input search path, and a missing declared "
            "file is a named failure; only with url null"
        ),
    )

    @field_validator("dst", mode="after")
    @classmethod
    def _relative_and_clean(cls, value: PurePosixPath) -> PurePosixPath:
        parts = value.parts
        if not parts or value.is_absolute():
            raise ValueError(
                f"dst must be a non-empty relative path, got {str(value)!r}"
            )
        if any(part in ("..", ".") for part in parts):
            raise ValueError(
                f"dst must not contain '.' or '..' parts, got {str(value)!r}"
            )
        return value

    @field_validator("sources", mode="after")
    @classmethod
    def _sources_are_absolute(
        cls, value: dict[Platform, PurePosixPath] | None
    ) -> dict[Platform, PurePosixPath] | None:
        if value is not None:
            for platform, path in value.items():
                if not path.is_absolute():
                    raise ValueError(
                        f"sources: the {platform.value} path must be absolute, got {str(path)!r}"
                    )
        return value

    @model_validator(mode="after")
    def _source_less_needs_a_digest(self) -> InputFile:
        if self.url is None:
            if self.sha256 is None:
                raise ValueError(
                    f"inputs: {str(self.dst)!r} has no url (no remote source) and "
                    "must declare sha256 — without both, any non-empty file would pass"
                )
            if self.public:
                raise ValueError(
                    f"inputs: {str(self.dst)!r} has no url, so public has no meaning"
                )
        elif self.sources:
            raise ValueError(
                f"inputs: {str(self.dst)!r} has a url, so sources has no meaning "
                "(a URL input is fetched, never linked)"
            )
        return self

    def _url(self) -> str:
        if self.url is None:
            raise ValueError(f"input {str(self.dst)!r} has no remote source")
        return self.url

    @property
    def parent_prefix(self) -> str:
        """`s3://bucket/a/b/file.nc` -> `s3://bucket/a/b/` (the sync source)."""
        return self._url().rsplit("/", 1)[0] + "/"

    @property
    def filename(self) -> str:
        """The key's last component (the sync's include filter)."""
        return self._url().rsplit("/", 1)[1]


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
        description="Absolute data tolerance; with rtol 0 too, the comparison is bit-for-bit",
    )
    rtol: float = Field(
        0.0,
        ge=0,
        description=(
            "Relative data tolerance, scaled by the baseline's magnitude: an element "
            "passes when |real - baseline| <= atol + rtol * |baseline| (numpy "
            "style); ~2.2e-16 is one float64 ULP"
        ),
    )
    plot: bool = Field(
        True,
        description="Render bias plots + GIF for this comparison at session end",
    )


@unique
class ColorScale(StrEnum):
    """Overview-map color scales (plotting.scale)."""

    log = "log"  # LogNorm; zeros (and negatives) masked grey — the default
    symlog = "symlog"  # SymLogNorm; zeros on the colorbar inside the linear core
    linear = "linear"  # no transform: today's exact min/max


@unique
class BiasColorScale(StrEnum):
    """Bias-map color scales (plotting.bias_scale)."""

    linear = "linear"  # symmetric at the exact max |diff| — the default
    symlog = "symlog"  # SymLogNorm, symmetric, linthresh by the decade rule


class Plotting(StrictModel):
    """Spatial-plot rendering at session end. The shared color scale derives
    from the descriptive statistics, so plotting requires the stats step.
    The maximum is always the exact suite-wide maximum; under log/symlog the
    floor is a rule (decades below the maximum, never below the suite-wide
    smallest positive value) or an explicit value."""

    enabled: bool = Field(
        True, description="Render spatial plots per NetCDF into <combo>/plots/"
    )
    gif_enabled: bool = Field(
        True, description="Assemble the per-variable plots into an animated GIF"
    )
    scale: ColorScale = Field(
        ColorScale.log,
        description=(
            "Overview-map color scale: log (zeros masked grey, the default), "
            "symlog (zeros on the colorbar), or linear (no transform)"
        ),
    )
    decades: int | None = Field(
        5,
        ge=1,
        description=(
            "log/symlog: the floor is the suite-wide max / 10**decades, never "
            "below the suite-wide smallest positive value; null = that smallest "
            "positive value exactly"
        ),
    )
    floor: float | None = Field(
        None,
        gt=0,
        description=(
            "log/symlog: an explicit floor in data units; overrides decades "
            "(ignored under linear)"
        ),
    )
    bias_scale: BiasColorScale = Field(
        BiasColorScale.linear,
        description=(
            "Bias-map color scale: linear (symmetric at the exact max |diff|, the "
            "default) or symlog"
        ),
    )
    bias_decades: int = Field(
        5,
        ge=1,
        description="bias symlog: linthresh = max |diff| / 10**bias_decades",
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
        description=(
            "Base driver configuration this suite's combinations are diffs of: a "
            "file, or a directory for an application whose configuration spans "
            "several files"
        )
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
    inputs: list[InputFile] = Field(
        default_factory=list,
        description=(
            "Input files the base configuration reads, staged by `ufs-chem-assay "
            "fetch` and required present at session start (dry runs excepted)"
        ),
    )

    @field_validator("inputs", mode="after")
    @classmethod
    def _unique_dst(cls, value: list[InputFile]) -> list[InputFile]:
        seen: set[PurePosixPath] = set()
        for entry in value:
            if entry.dst in seen:
                raise ValueError(f"inputs: dst {str(entry.dst)!r} is declared twice")
            seen.add(entry.dst)
        return value

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
        application checkout — how a suite references a config living in the
        external checkout portably. Otherwise, relative values resolve
        against the suite file's own directory, or against
        config_search_path when set (prepended verbatim, so nested and ../
        paths work), and absolute values are used as-is. The target may be a
        file or, for an application whose configuration spans several
        files, a directory — the adapter's loader decides the shape; a
        missing target fails here, before any driver runs.
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
        if not resolved.exists():
            raise FileNotFoundError(
                f"suite config_path {str(self.config_path)!r} resolved to {resolved}, which does not exist"
            )
        self.config_path = resolved


class RecordedInput(StrictModel):
    """One declared input as found at session start (run.yaml): the session
    records presence and size, never a digest — hashing is `fetch`'s job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite: str = Field(description="Suite that declares the input")
    url: str | None = Field(
        description="The input's S3 object URI; null for one with no remote source"
    )
    path: Path = Field(description="Host path the input is staged at")
    sha256: str | None = Field(description="The declared digest; null when none")
    bytes: int | None = Field(
        description="Size of the file present at session start; null when absent"
    )


class RecordedBaseline(StrictModel):
    """One pinned baseline as the session found or staged it at start
    (run.yaml): local, synced from the store, would-sync (dry run), or
    failed with the reason."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite: str = Field(description="Suite that pins the baseline")
    ulid: str = Field(description="The baseline's ULID")
    source: str = Field(description="Where it is synced from: <store>/<ulid>")
    path: Path = Field(description="Where the session reads it: <baseline root>/<ulid>")
    state: str = Field(
        description="local, synced, would-sync, unpublished (placeholder ULID), or failed"
    )
    detail: str = Field(description="Why: present, synced, or the error")


class LaunchRecord(StrictModel):
    """How the driver jobs were launched (run.yaml, slurm runtime): the
    sbatch directives, the srun options, and the environment exported inside
    each job — the rank count, binding, and thread settings that produced
    the output. Copied into a published baseline's manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sbatch_args: str = Field(description="The ASSAY_SBATCH_ARGS directives, verbatim")
    srun_args: str = Field(description="The ASSAY_SRUN_ARGS options, verbatim")
    job_env: dict[str, str] = Field(
        description="NAME -> VALUE exported inside every driver job (ASSAY_JOB_ENV)"
    )


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
            "against; null when no checkout is configured, or under --dry-run "
            "when the configured root yields none (a real run fails at "
            "collection instead)"
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
    launch: LaunchRecord | None = Field(
        default=None,
        description=(
            "How the driver jobs were launched (slurm runtime: directives, srun "
            "options, job environment); null under docker and native"
        ),
    )
    # SerializeAsAny: each suite dumps with its adapter's schema (the sweep).
    suites: list[SerializeAsAny[SuiteConfig]]
    inputs: list[RecordedInput] = Field(
        default_factory=list,
        description="Every selected suite's declared inputs as found at session start",
    )
    baselines: list[RecordedBaseline] = Field(
        default_factory=list,
        description=(
            "Every pinned baseline as found or staged at session start (empty "
            "when comparisons are disabled)"
        ),
    )

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
