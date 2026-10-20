"""The baseline models: the conventions, the manifest a published baseline
carries, the plan's inputs and records, and the staging record."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, InstanceOf

from models.base import StrictModel
from models.suite_config import (
    BaselineComparison,
    LaunchRecord,
    RecordedBaseline,
    SuiteConfig,
)
from platforms import Platform, Runtime

Action = Literal["published", "would-publish", "skipped", "failed"]
BaselineState = Literal["local", "synced", "would-sync", "failed"]


class BaselineConventions(StrictModel):
    """The names the baseline machinery relies on, in one place."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: str = Field(
        "baseline.yaml",
        description="The manifest file inside every published baseline directory",
    )
    driver_test: str = Field(
        "test_driver_execution",
        description="The combo test that must have passed for a combination to be published",
    )
    comparison_test: str = Field(
        "test_baseline_comparison",
        description="The combo test the publishing gate ignores (the old baseline is expected red)",
    )
    staging_dirname: str = Field(
        ".staging",
        description="Where a baseline is synced to before it is moved into the baseline root",
    )
    digest_chunk: int = Field(
        1024 * 1024, gt=0, description="Read size for the manifest's SHA-256 digests"
    )


CONVENTIONS = BaselineConventions()


class BaselineFile(StrictModel):
    """One file of a published baseline, as it was at publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(
        description=(
            "Path relative to the baseline directory (posix; subdirectories "
            "such as plots-overview/ included)"
        )
    )
    bytes: int = Field(description="Size on disk at publication")
    sha256: str = Field(description="SHA-256 (lowercase hex) at publication")


class BaselineParameter(StrictModel):
    """One combos.csv row of the published combination: an effective
    parameter, swept or pinned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str = Field(description="Attachment target (e.g. a stream or species name)")
    field: str = Field(description="Configuration field name")
    value: str = Field(description="The value in the combination's generated config")
    swept: bool = Field(description="Whether the suite swept this dimension")


class BaselineManifest(StrictModel):
    """baseline.yaml: what a baseline is, where it came from, and what it
    holds. Output only, like run.yaml; the download side reads it back."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ulid: str = Field(
        description="The combination's runtime ULID: the store key and the directory name"
    )
    store: str = Field(
        description="Where it was published: an s3://bucket/prefix or a local directory"
    )
    application: str = Field(description="Registry name of the application")
    application_commit: str = Field(
        description="HEAD commit of the application checkout the run was built from"
    )
    suite: str = Field(
        description="Unique name of the suite the combination belongs to"
    )
    combo: str = Field(description="Canonical combination name")
    run_id: str = Field(description="Session ULID of the run that produced it")
    harness_version: str = Field(
        description="Harness package version that produced the run"
    )
    harness_commit: str | None = Field(
        description="Harness checkout HEAD (-dirty when edited); null outside a checkout"
    )
    platform: Platform = Field(description="Machine the run happened on")
    runtime: Runtime = Field(description="How the driver was spawned")
    launch: LaunchRecord | None = Field(
        default=None,
        description=(
            "The run's job launch (ranks, srun options, job environment) under "
            "the slurm runtime; null otherwise"
        ),
    )
    published_at: datetime = Field(description="Publication time, UTC, timezone-aware")
    published_by: str = Field(description="<user>@<hostname> that published it")
    superseded_ulid: str | None = Field(
        description=(
            "The ULID the suite entry pinned when this baseline was published "
            "(whether or not the suite was repointed); null when it had none"
        )
    )
    parameters: list[BaselineParameter] = Field(
        description="The combination's effective-parameter rows (combos.csv)"
    )
    files: list[BaselineFile] = Field(
        description=(
            "Every file under the combination directory (this manifest excepted), "
            "sorted by path"
        )
    )


class SuiteInput(BaseModel):
    """One selected suite as the run executed it: the file (the edit
    target), the loaded suite, its resolved entries, and the run's ids."""

    model_config = ConfigDict(frozen=True)

    path: Path = Field(description="The suite file")
    suite: InstanceOf[SuiteConfig] = Field(description="The suite as loaded")
    baselines: dict[str, InstanceOf[BaselineComparison]] = Field(
        description="Combination name -> baseline_comparisons entry (the session's resolution)"
    )
    combo_ids: dict[str, str] = Field(
        description="Combination name -> the run's combo_id (the directory name)"
    )


class PublishRecord(StrictModel):
    """What publication did, will do, or would not do for one entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite: str = Field(description="Unique suite name")
    suite_path: Path = Field(description="The suite file the entry lives in")
    combo: str = Field(description="Canonical combination name the entry selects")
    ulid: str = Field(description="The new baseline's ULID (the run's combo_id)")
    previous_ulid: str = Field(
        description="The ULID the entry pins before this publish"
    )
    source_dir: Path = Field(description="The combination's output directory")
    store: str = Field(description="The s3://bucket/prefix or directory published to")
    action: Action = Field(
        description=(
            "published: in the store and (unless disabled) the suite repointed; "
            "would-publish: the plan, or a dry run's outcome; skipped: the gate, "
            "already published, or nothing to compare; failed: see detail"
        )
    )
    detail: str = Field(
        description="Why: the gate's verdict, the probe's finding, the error"
    )
    suite_updated: bool = Field(description="Whether the suite file was rewritten")

    @property
    def local_store(self) -> Path | None:
        """The store as a directory when it is one; None for an S3 prefix."""
        return None if self.store.startswith("s3://") else Path(self.store)

    @property
    def destination(self) -> str:
        if self.local_store is not None:
            return str(self.local_store / self.ulid)
        return f"{self.store}/{self.ulid}/"

    def with_outcome(
        self, action: Action, detail: str, *, suite_updated: bool = False
    ) -> PublishRecord:
        return self.model_copy(
            update={"action": action, "detail": detail, "suite_updated": suite_updated}
        )


class StagedBaseline(StrictModel):
    """What ensure_baselines found or did for one pinned ULID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite: str = Field(description="Suite that pins the baseline")
    ulid: str = Field(description="The baseline's ULID")
    source: str = Field(description="Where it is synced from: <store>/<ulid>")
    path: Path = Field(description="Where the session reads it: <baseline root>/<ulid>")
    state: BaselineState = Field(
        description=(
            "local: present, nothing done; synced: fetched from the store into "
            "place; would-sync: a dry run's plan; failed: see detail"
        )
    )
    detail: str = Field(description="Why: present, synced, or the error")

    def recorded(self) -> RecordedBaseline:
        return RecordedBaseline(**self.model_dump())
