"""The run config: one YAML file describing where and how a run happens."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ConfigDict, Field, field_validator, model_validator

from models.base import StrictModel
from platforms import Platform, Runtime, default_runtime, detect_platform


class CeceSection(StrictModel):
    git_url: str = Field(description="Repository cloned when clone_dir is missing")
    ref: str = Field(
        description="Branch or commit checked out (the integration workflow's ref)"
    )
    clone_dir: Path | None = Field(
        default=None,
        description=(
            "Path of the CECE checkout; null means <root_dir>/CECE. An existing "
            "checkout is used as-is unless update_source is set"
        ),
    )
    update_source: bool = Field(
        default=False,
        description=(
            "Sync an existing clone to ref: fetch, checkout, pull --ff-only, "
            "submodule update. Never destructive — a dirty or diverged clone "
            "makes git fail"
        ),
    )
    modulefile: str | None = Field(
        default=None,
        description=(
            "Name under <clone>/modulefiles loaded after `module purge`; null "
            "means no module environment (local, docker build)"
        ),
    )
    cmake_args: list[str] = Field(
        default_factory=lambda: ["-DCMAKE_BUILD_TYPE=Release"],
        description="Extra cmake configure arguments (native build only)",
    )
    build_jobs: int = Field(default=8, gt=0, description="cmake --build --parallel N")
    targets: list[str] = Field(
        default_factory=lambda: ["cece_standalone_driver"],
        description="CMake targets to build; `all` includes the unit-test stack",
    )
    run_tests: bool = Field(
        default=False,
        description="Run CECE's ctest suite under the launcher (needs the `all` target)",
    )

    @model_validator(mode="after")
    def _tests_need_all(self) -> CeceSection:
        if self.run_tests and "all" not in self.targets:
            raise ValueError(
                "run_tests needs the CECE test stack: add `all` to cece.targets"
            )
        return self


class DataSection(StrictModel):
    examples: list[str] = Field(
        default_factory=lambda: ["ex3"],
        description="Example ids whose input data download-example-data.py stages",
    )
    warm_cartopy: bool = Field(
        default=True,
        description="Fetch Natural Earth coastlines into cartopy's cache (login node)",
    )


class BaselinesSection(StrictModel):
    root_dir: Path | None = Field(
        default=None, description="CECE_BASELINE_ROOT_DIR; null leaves it unset"
    )
    enabled: bool = Field(
        default=False, description="CECE_ENABLE_BASELINE_COMPARISONS for the session"
    )


class HarnessSection(StrictModel):
    # Numbers are fine as env values (OMP_NUM_THREADS: 8); they are exported
    # as strings.
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    suite_config: str = Field(
        default="simple-maccity-suite.yaml", description="--suite-config selector"
    )
    output_root: str = Field(
        default="ufs-chem-assay-output",
        description="--combo-output-root; relative paths land under the CECE checkout",
    )
    clean_root: bool = Field(default=True, description="Pass --combo-clean-root")
    pytest_args: list[str] = Field(
        default_factory=list,
        description="Extra pytest arguments, e.g. [-x, -k, map-consd]",
    )
    runtime: Runtime | None = Field(
        default=None,
        description=(
            "Override the platform's default runtime (docker on local, slurm "
            "elsewhere): e.g. native for a session inside an salloc shell"
        ),
    )
    launcher: str = Field(
        default="",
        description="CECE_LAUNCHER: prefix for native driver runs (e.g. 'srun --ntasks=1'); ignored otherwise",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables exported before pytest",
    )
    dask_nworkers: int | None = Field(
        default=None,
        gt=0,
        description="CECE_DASK_NWORKERS; null leaves dask to size itself (cap it on a login node)",
    )
    run_timeout_s: int = Field(default=300, gt=0, description="CECE_RUN_TIMEOUT_S")


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

    @property
    def sbatch_args(self) -> str:
        """CECE_SBATCH_ARGS: one node, one task, the configured cpus."""
        return f"-A {self.account} -q {self.qos} -p {self.partition} -N 1 -n 1 -c {self.cpus}"


class UvSection(StrictModel):
    cache_dir: Path | None = Field(
        default=None, description="UV_CACHE_DIR; null means <root_dir>/uv-cache"
    )


class RunConfig(StrictModel):
    """Everything `ufs-chem-assay run` needs. Load through from_yaml, which
    resolves the platform; the model itself never guesses one."""

    platform: Platform = Field(
        description=(
            "Machine this config targets. Optional in the file: from_yaml "
            "resolves --platform > the file > hostname detection"
        )
    )
    root_dir: Path = Field(
        description="Directory holding everything the CLI creates: CECE/, logs/, scripts/, uv caches"
    )
    cece: CeceSection
    data: DataSection = Field(default_factory=DataSection)
    baselines: BaselinesSection = Field(default_factory=BaselinesSection)
    harness: HarnessSection = Field(default_factory=HarnessSection)
    slurm: SlurmSection | None = Field(
        default=None,
        description="Batch-job settings; null runs the compute stages directly",
    )
    uv: UvSection = Field(default_factory=UvSection)

    @field_validator("root_dir", mode="before")
    @classmethod
    def _expand_root(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @classmethod
    def from_yaml(cls, path: Path, platform: Platform | None = None) -> RunConfig:
        """Load a run config; the platform is the `platform` argument
        (--platform), else the file's, else hostname detection."""
        with open(path) as f:
            loaded = yaml.safe_load(f)
        if isinstance(loaded, dict):
            loaded["platform"] = platform or loaded.get("platform") or detect_platform()
        return cls.model_validate(loaded)

    @property
    def runtime(self) -> Runtime:
        return self.harness.runtime or default_runtime(self.platform)

    @property
    def clone_dir(self) -> Path:
        return self.cece.clone_dir or self.root_dir / "CECE"

    @property
    def uv_cache_dir(self) -> Path:
        return self.uv.cache_dir or self.root_dir / "uv-cache"
