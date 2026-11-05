"""The run config's sections as models: data:, baselines:, outputs:,
harness: (the pytest session and the mirror of every harness-wide
setting), slurm: (the per-driver job) and uv:. Each is validated on its
own, so a session overlay re-validates one section with the same rules."""

from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field, model_validator

from models.base import StrictModel
from models.conventions import CONVENTIONS
from platforms import Runtime


class DataSection(StrictModel):
    warm_cartopy: bool = Field(
        default=True,
        description="Fetch Natural Earth coastlines into cartopy's cache (login node)",
    )
    input_search_path: list[Path] = Field(
        default_factory=list,
        description=(
            "ASSAY_INPUT_SEARCH_PATH: directories fetch searches recursively, by "
            "basename, for source-less inputs (url null) to link into the data "
            "directory; per-machine (this run's), never a suite's"
        ),
    )


class BaselinesSection(StrictModel):
    root_dir: Path | None = Field(
        default=None, description="ASSAY_BASELINE_ROOT_DIR; null leaves it unset"
    )
    enabled: bool = Field(
        default=False, description="ASSAY_ENABLE_BASELINE_COMPARISONS for the session"
    )
    store: str = Field(
        default=CONVENTIONS.default_baseline_store,
        description=(
            "ASSAY_BASELINE_STORE: where publish-baselines writes <store>/<ulid>/ "
            "(append-only): an s3://bucket/prefix or a local directory"
        ),
    )
    atol: float | None = Field(
        default=None,
        ge=0,
        description="ASSAY_BASELINE_ATOL: absolute tolerance over every entry; null = the entries' own",
    )
    rtol: float | None = Field(
        default=None,
        ge=0,
        description="ASSAY_BASELINE_RTOL: relative tolerance (x |baseline|) over every entry; null = the entries' own",
    )


class OutputsSection(StrictModel):
    store: str = Field(
        default=CONVENTIONS.default_output_store,
        description=(
            "ASSAY_OUTPUT_STORE: where upload-output (and --upload-output) copies a "
            "run's whole output root as <store>/<run_id>/: an s3://bucket/prefix or "
            "a local directory"
        ),
    )


class HarnessSection(StrictModel):
    """The pytest session: its options, and the mirror of every harness-wide
    setting (a null key is not exported, so the setting's default or the
    shell's value applies)."""

    # Numbers are fine as env values (OMP_NUM_THREADS: 8); they are exported
    # as strings.
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    suite_config: str = Field(
        default="simple-maccity-suite.yaml",
        description=(
            "--suite-config selector: a regex fullmatched against suite file "
            "names; every match runs in one session (e.g. 'ex[0-9]-suite.yaml')"
        ),
    )
    output_root: str = Field(
        default="ufs-chem-assay-output",
        description=(
            "--combo-output-root: relative paths land under the application "
            "checkout; absolute paths are written anywhere the runtime can "
            "(docker: under the checkout mount only). With several applications "
            "configured each session gets <output_root>/<application>. "
            "clean_root only ever removes a previous harness root (one with run.yaml)"
        ),
    )
    clean_root: bool = Field(default=True, description="Pass --combo-clean-root")
    pytest_dry_run: bool = Field(
        default=False,
        description=(
            "Pass pytest's --dry-run: generate everything, execute no driver "
            "(distinct from the CLI's --dry-run, which renders scripts and runs nothing)"
        ),
    )
    pytest_args: list[str] = Field(
        default_factory=list,
        description="Extra pytest arguments, e.g. [-x, -k, map-consd]",
    )
    publish_baselines: bool = Field(
        default=False,
        description=(
            "Pass --publish-baselines: at session end, publish every compared "
            "combination that passed as a new baseline and repoint the suites"
        ),
    )
    no_suite_update: bool = Field(
        default=False,
        description="Pass --no-suite-update (publish, but leave the suite files alone)",
    )
    record_benchmarks: bool = Field(
        default=False,
        description=(
            "Pass --record-benchmarks: at session end, append the run's benchmark "
            "rows to each suite's committed configuration benchmarks.csv (the "
            "per-run <output root>/benchmarks.csv is always written)"
        ),
    )
    upload_output: bool = Field(
        default=False,
        description=(
            "Pass --upload-output: at session end, after every other artifact, "
            "upload the whole output root to outputs.store under the run ULID "
            "(off by default; needs network where the session runs)"
        ),
    )
    submit_only: bool = Field(
        default=False,
        description=(
            "Pass --submit-only: the session submits every driver job, writes "
            "jobs.yaml at its output root and stops; `ufs-chem-assay watch` (or "
            "`run --stage resume`) evaluates it later through the rendered resume "
            "script. Slurm runtime only"
        ),
    )
    runtime: Runtime | None = Field(
        default=None,
        description=(
            "ASSAY_RUNTIME: override the platform's default runtime (docker on "
            "local, slurm elsewhere), e.g. native for a session inside an salloc shell"
        ),
    )
    launcher: str = Field(
        default="",
        description="ASSAY_LAUNCHER: prefix for native driver runs (e.g. 'srun --ntasks=1'); ignored otherwise",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Environment variables exported before pytest (docker/native) or "
            "inside every driver job as ASSAY_JOB_ENV (slurm)"
        ),
    )
    dask_nworkers: int | None = Field(
        default=None,
        gt=0,
        description="ASSAY_DASK_NWORKERS; null leaves dask to size itself (cap it on a login node)",
    )
    run_timeout_s: int = Field(default=300, gt=0, description="ASSAY_RUN_TIMEOUT_S")
    log_level: str | None = Field(
        default=None, description="ASSAY_LOG_LEVEL; null keeps the default (INFO)"
    )
    config_search_path: Path | None = Field(
        default=None,
        description="ASSAY_CONFIG_SEARCH_PATH: prepended to relative suite config_path values",
    )
    suite_config_search_path: list[Path] = Field(
        default_factory=list,
        description=(
            "ASSAY_SUITE_CONFIG_SEARCH_PATH: directories searched recursively for "
            "--suite-config selection (the built-in suite directory is always last)"
        ),
    )

    @model_validator(mode="after")
    def _no_suite_update_needs_publish(self) -> HarnessSection:
        if self.no_suite_update and not self.publish_baselines:
            raise ValueError(
                "no_suite_update has no effect without publish_baselines; set both or neither"
            )
        return self


class SlurmSection(StrictModel):
    """The per-driver Slurm job (slurm runtime): one job per driver call,
    submitted from the login node where pytest runs and waited for by
    polling. The time limit is the suite timeout, not configured here."""

    account: str = Field(
        default="epic", description="sbatch -A (the Slurm project to charge)"
    )
    qos: str = Field(default="batch", description="sbatch -q")
    partition: str = Field(default="u1-compute", description="sbatch -p")
    ntasks: int = Field(
        default=1,
        gt=0,
        description=(
            "sbatch -n and srun --ntasks: MPI tasks per driver job (the driver "
            "decomposes latitude bands across them)"
        ),
    )
    cpus: int = Field(
        default=8, gt=0, description="sbatch -c for each driver job (cores per task)"
    )
    srun_args: str = Field(
        default="",
        description="Extra srun options for the driver launch, e.g. --cpu-bind=cores",
    )
    extra_args: str = Field(
        default="",
        description=(
            "Extra sbatch directives appended verbatim, e.g. --mem=0 or --exclusive"
        ),
    )
    queue_wait_s: int = Field(
        default=3600,
        gt=0,
        description=(
            "ASSAY_SLURM_QUEUE_WAIT_S: seconds a driver job may wait in the queue "
            "before the harness cancels it; added to the suite timeout for the "
            "outer bound (the job's own limit is the suite timeout). Raise it for "
            "the batch QOS, where waits can be hours"
        ),
    )

    poll_s: int = Field(
        default=30,
        gt=0,
        description=(
            "ASSAY_SLURM_POLL_S: seconds between squeue polls while a driver job "
            "is queued or running"
        ),
    )

    @property
    def sbatch_args(self) -> str:
        """ASSAY_SBATCH_ARGS: one node, the configured tasks and cpus per
        task, then the extra directives verbatim."""
        base = (
            f"-A {self.account} -q {self.qos} -p {self.partition} "
            f"-N 1 -n {self.ntasks} -c {self.cpus}"
        )
        return f"{base} {self.extra_args}".strip()

    @property
    def srun_args_value(self) -> str:
        """ASSAY_SRUN_ARGS: the task count (always, so sbatch -n and srun
        --ntasks agree) then the extra srun options."""
        return f"--ntasks={self.ntasks} {self.srun_args}".strip()


class UvSection(StrictModel):
    cache_dir: Path | None = Field(
        default=None, description="UV_CACHE_DIR; null means <root_dir>/uv-cache"
    )
