"""The harness-wide conventions: the names every module agrees on — the
files a run writes and the tools read back, the staging directory, the
environment prefixes, the default stores — as one frozen, described model
instead of module-level constants repeated where they are used. Nothing
here is configurable: a change renames something on disk or in the
environment. Domain-specific names stay beside their domain
(`baselines.models.BaselineConventions`, `benchmarks.BenchmarkConventions`).
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from models.base import StrictModel


class Conventions(StrictModel):
    """The names the harness relies on across modules, in one place."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # ── environment ──────────────────────────────────────────────────────
    env_prefix: str = Field(
        "ASSAY_", description="Prefix of every harness setting in the environment"
    )
    legacy_env_prefix: str = Field(
        "CECE_",
        description="The historical prefix, accepted as a fallback for every harness setting",
    )
    env_file: str = Field(
        ".env", description="The cwd-relative dotenv file settings are also read from"
    )

    # ── default stores ───────────────────────────────────────────────────
    default_baseline_store: str = Field(
        "s3://ufs-chem/baselines",
        description="Where baselines are published unless a setting or run config says otherwise",
    )
    default_output_store: str = Field(
        "s3://ufs-chem/assay-output",
        description="Where upload-output copies a run's output root unless told otherwise",
    )

    # ── a run's files (written by the pytest session, read back by the CLI) ─
    run_manifest: str = Field(
        "run.yaml",
        description="The run manifest at an output root; its presence is what makes a directory a run",
    )
    test_report: str = Field(
        "test-report.csv",
        description="The per-test results of a run, at the output root",
    )
    combos_csv: str = Field(
        "combos.csv",
        description="The enumerated combinations of a run, at the output root",
    )
    descriptive_stats: str = Field(
        "descriptive_stats.csv",
        description="The concatenated per-variable statistics of a run, at the output root",
    )
    stats_comparison: str = Field(
        "stats-comparison.csv",
        description="The concatenated baseline comparisons of a run, at the output root",
    )
    benchmarks: str = Field(
        "benchmarks.csv",
        description=(
            "The benchmark rows: at the output root (the run), in a combo directory "
            "(that combination, uploaded with its baseline), and committed per configuration"
        ),
    )
    upload_record: str = Field(
        "upload.yaml",
        description="Written into an output root by upload-output: when and by whom the copy was taken",
    )
    effective_config: str = Field(
        "run-config.yaml",
        description="The effective run config `run` writes beside the rendered stage scripts",
    )

    # ── transfers ────────────────────────────────────────────────────────
    staging_dirname: str = Field(
        ".staging",
        description=(
            "Where inputs, baselines, and downloaded outputs land before the final "
            "rename into place — under the destination, so the move stays on one filesystem"
        ),
    )
    digest_chunk: int = Field(
        1024 * 1024,
        gt=0,
        description="Read size for SHA-256 digests of inputs and baselines",
    )

    # ── clock ────────────────────────────────────────────────────────────
    clock_format: str = Field(
        "%Y-%m-%dT%H:%M:%SZ",
        description=(
            "strftime format of the `tic:`/`toc:` lines the launch writes around the "
            "driver (ISO-8601 UTC to the second) and benchmarks parses back"
        ),
    )

    def env_var(self, name: str, *, legacy: bool = False) -> str:
        """The environment variable of harness setting `name` (any case)."""
        prefix = self.legacy_env_prefix if legacy else self.env_prefix
        return f"{prefix}{name.upper()}"


CONVENTIONS = Conventions()
