"""The run config: one YAML file describing where and how a run happens —
every harness setting, every application's section, every pytest option —
with command-line overrides merged in before validation."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import ConfigDict, Field, SerializeAsAny, field_validator

from applications.base import ApplicationRunSection
from cli.override import apply_overrides
from identity import HARNESS_ROOT
from models.base import StrictModel
from platforms import Platform, Runtime, default_runtime, detect_platform

__all__ = ["HARNESS_ROOT", "RunConfig"]


class DataSection(StrictModel):
    warm_cartopy: bool = Field(
        default=True,
        description="Fetch Natural Earth coastlines into cartopy's cache (login node)",
    )


class BaselinesSection(StrictModel):
    root_dir: Path | None = Field(
        default=None, description="ASSAY_BASELINE_ROOT_DIR; null leaves it unset"
    )
    enabled: bool = Field(
        default=False, description="ASSAY_ENABLE_BASELINE_COMPARISONS for the session"
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
    run_examples: bool = Field(
        default=False,
        description="Pass --run-examples (the application's shipped examples)",
    )
    pytest_args: list[str] = Field(
        default_factory=list,
        description="Extra pytest arguments, e.g. [-x, -k, map-consd]",
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


class SlurmSection(StrictModel):
    """The per-driver Slurm job (slurm runtime): one `sbatch --wait` per
    driver call, from the login node where pytest runs. The time limit is
    the suite timeout, not configured here."""

    account: str = Field(
        default="epic", description="sbatch -A (the Slurm project to charge)"
    )
    qos: str = Field(default="batch", description="sbatch -q")
    partition: str = Field(default="u1-compute", description="sbatch -p")
    cpus: int = Field(default=8, gt=0, description="sbatch -c for each driver job")
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

    @property
    def sbatch_args(self) -> str:
        """ASSAY_SBATCH_ARGS: one node, one task, the configured cpus."""
        return f"-A {self.account} -q {self.qos} -p {self.partition} -N 1 -n 1 -c {self.cpus}"


class UvSection(StrictModel):
    cache_dir: Path | None = Field(
        default=None, description="UV_CACHE_DIR; null means <root_dir>/uv-cache"
    )


class RunConfig(StrictModel):
    """Everything `ufs-chem-assay run` needs. Load through from_yaml, which
    resolves the platform and root and merges overrides; the model itself
    never guesses."""

    platform: Platform = Field(
        description=(
            "Machine this config targets. Optional in the file: from_yaml "
            "resolves --platform > the file > hostname detection"
        )
    )
    root_dir: Path = Field(
        description=(
            "Directory holding everything the CLI creates: the checkouts, logs/, "
            "scripts/, uv caches. Optional in the file: from_yaml resolves "
            "--root-dir > the file > the harness checkout's parent directory"
        )
    )
    # SerializeAsAny: each section dumps with its adapter's schema.
    applications: dict[str, SerializeAsAny[ApplicationRunSection]] = Field(
        description=(
            "One section per application in the run, keyed by registry name "
            "(cece, ...); a comprehensive run lists several, each getting its "
            "own stages and pytest session"
        )
    )
    data: DataSection = Field(default_factory=DataSection)
    baselines: BaselinesSection = Field(default_factory=BaselinesSection)
    harness: HarnessSection = Field(default_factory=HarnessSection)
    slurm: SlurmSection | None = Field(
        default=None,
        description="Batch-job settings; null runs the driver directly",
    )
    uv: UvSection = Field(default_factory=UvSection)

    @field_validator("root_dir", mode="before")
    @classmethod
    def _expand_root(cls, value: object) -> object:
        if isinstance(value, (str, Path)):
            return Path(value).expanduser()
        return value

    @field_validator("applications", mode="before")
    @classmethod
    def _dispatch_sections(cls, value: object) -> object:
        # The registry dispatch for the run config: each section validates
        # against its adapter's model. Local import: the registry imports the
        # adapters, which import this module.
        from applications.registry import get_application

        if not isinstance(value, dict):
            return value
        if not value:
            raise ValueError(
                "applications: at least one application section is required"
            )
        sections: dict[str, ApplicationRunSection] = {}
        for name, raw in value.items():
            app = get_application(name)
            sections[app.name] = (
                raw
                if isinstance(raw, ApplicationRunSection)
                else app.run_section_model.model_validate(raw)
            )
        return sections

    @classmethod
    def from_yaml(
        cls,
        path: Path,
        platform: Platform | None = None,
        root_dir: Path | None = None,
        overrides: Iterable[str] = (),
    ) -> RunConfig:
        """Load a run config. Precedence, highest first: `overrides`
        (KEY:PATH=VALUE entries, in order), the named flags (`platform`,
        `root_dir`), the file, then the derived defaults — hostname detection
        for the platform, the harness checkout's parent for the root — so
        the shipped templates run as-is from a checkout laid out like the
        runbook."""
        with open(path) as f:
            loaded = yaml.safe_load(f)
        if not isinstance(loaded, dict):
            raise ValueError(f"run config {path} is not a mapping")
        if platform is not None:
            loaded["platform"] = platform
        if root_dir is not None:
            loaded["root_dir"] = root_dir
        apply_overrides(overrides, loaded)
        loaded["platform"] = loaded.get("platform") or detect_platform()
        loaded["root_dir"] = loaded.get("root_dir") or HARNESS_ROOT.parent
        return cls.model_validate(loaded)

    @property
    def runtime(self) -> Runtime:
        return self.harness.runtime or default_runtime(self.platform)

    @property
    def uv_cache_dir(self) -> Path:
        return self.uv.cache_dir or self.root_dir / "uv-cache"

    @property
    def application_names(self) -> list[str]:
        return list(self.applications)

    def checkout_dir(self, name: str) -> Path:
        """The application's checkout: its section's clone_dir, else
        <root_dir>/<the adapter's checkout_dirname>."""
        from applications.registry import get_application

        section = self.applications[name]
        return (
            section.clone_dir or self.root_dir / get_application(name).checkout_dirname
        )

    def to_yaml(self, path: Path) -> None:
        """The effective configuration, as validated, keys in model order."""
        with open(path, "w") as f:
            yaml.dump(
                self.model_dump(mode="json"),
                f,
                default_flow_style=False,
                sort_keys=False,
            )


def search_path_value(paths: list[Path]) -> str:
    """ASSAY_SUITE_CONFIG_SEARCH_PATH's os.pathsep-joined form."""
    return os.pathsep.join(str(path) for path in paths)
