import os
import shlex
import subprocess
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from platforms import Platform, Runtime, default_runtime, detect_platform


class Settings(BaseSettings):
    """Environment-derived configuration. The CECE_ prefix is deliberately not
    runner-specific so this class can host other variable groups later.
    Frozen: constructed once at sessionstart and read-only thereafter, so
    root_dir resolution (--cece-root-dir flag over CECE_ROOT_DIR env) happens
    at exactly one point. A cwd-relative .env file supplies per-machine
    values below real environment variables (init kwargs > env > .env)."""

    model_config = SettingsConfigDict(env_prefix="CECE_", frozen=True, env_file=".env")

    platform: Platform = Field(
        description=(
            "Machine the harness runs on: an explicit value (CECE_PLATFORM or "
            "init kwarg) beats hostname detection, which falls back to local "
            "(filled in by the model validator below, never required)"
        ),
    )
    runtime: Runtime = Field(
        default=Runtime.DOCKER,
        description=(
            "How the driver is spawned: docker (the cece/cece-dev image) or "
            "native (a host process). Defaults from the platform — docker on "
            "local, native elsewhere — unless CECE_RUNTIME says otherwise"
        ),
    )
    launcher: str = Field(
        default="",
        description=(
            "Command prefix for native driver runs, word-split like a shell "
            "(e.g. 'srun --ntasks=1'); empty runs the driver directly"
        ),
    )
    sbatch_args: str = Field(
        default="",
        description=(
            "slurm runtime: sbatch options for every driver job, word-split "
            "like a shell (e.g. '-A epic -q debug -p u1-compute -N 1 -n 1 -c 8'); "
            "the time limit comes from the suite timeout"
        ),
    )
    slurm_queue_wait_s: int = Field(
        default=3600,
        gt=0,
        description=(
            "slurm runtime: seconds allowed for a driver job to wait in the "
            "queue, added to the suite timeout for the outer bound; the job's "
            "own limit is the suite timeout rounded up to minutes"
        ),
    )
    modulefile: str | None = Field(
        default=None,
        description=(
            "CECE modulefile the job script loads before the driver (slurm "
            "runtime); read by scripts/cece-modules.sh from the environment, "
            "recorded in run.yaml"
        ),
    )
    docker_image: str = "cece/cece-dev"
    root_dir: Path | None = Field(
        None,
        description=(
            "Host path of the CECE repository root, mounted at /work in the "
            "driver container; required to execute the driver. Unset means "
            "not configured — never a guessed path."
        ),
    )
    driver_path: str = "./build/cece_standalone_driver"
    run_timeout_s: int = 300
    log_level: str = "INFO"
    baseline_root_dir: Path | None = Field(
        None,
        description="Directory holding baselines as <root>/<ulid>/; None means the current working directory",
    )
    enable_baseline_comparisons: bool = Field(
        True,
        description="Global switch for baseline comparisons; false skips every test_baseline_comparison regardless of suite config",
    )
    # None -> LocalCluster sizes itself to all available cores.
    dask_nworkers: int | None = Field(None, gt=0)
    # When set, prepended to relative config paths (kept whole, so nested and
    # ../ paths work); absolute provided paths are always used as-is.
    config_search_path: Path | None = None  # applies to the suite's config_path
    # NoDecode: pydantic-settings would otherwise JSON-decode the env value
    # for a complex type; the validator splits on os.pathsep instead.
    suite_config_search_path: Annotated[list[Path], NoDecode] = Field(
        default_factory=list,
        description=(
            "Directories searched recursively for --suite-config selection, "
            "os.pathsep-separated in the environment; the built-in suite "
            "directory is always searched last"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _resolve_platform_and_runtime(cls, data: object) -> object:
        # The model is frozen, so the detected platform and the
        # platform-derived runtime are filled in before construction; explicit
        # values (env, .env, or init kwarg) are left alone. Sources are merged
        # before validation, so `data` carries the env/.env values too.
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("platform", detect_platform())
            data.setdefault("runtime", default_runtime(Platform(data["platform"])))
        return data

    @property
    def launcher_argv(self) -> list[str]:
        return shlex.split(self.launcher)

    @property
    def sbatch_argv(self) -> list[str]:
        return shlex.split(self.sbatch_args)

    @field_validator("suite_config_search_path", mode="before")
    @classmethod
    def _split_search_path(cls, value: object) -> object:
        if isinstance(value, str):
            return [Path(part) for part in value.split(os.pathsep) if part]
        return value

    def get_cece_commit_sha(self) -> str | None:
        """HEAD commit SHA of the CECE checkout, for the run.yaml record.

        None when no checkout is configured (root_dir unset). A session
        always runs against a checked-out CECE, so a configured root whose
        SHA cannot be determined (not a git repository, no commits, git
        missing or failing) is a fatal misconfiguration: ValueError,
        converted to a usage error at sessionstart before any work runs."""
        if self.root_dir is None:
            return None
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.root_dir), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(
                f"cannot determine the CECE commit for {self.root_dir}: {exc}"
            ) from exc
        sha = completed.stdout.strip()
        if completed.returncode != 0 or not sha:
            detail = completed.stderr.strip() or "git produced no output"
            raise ValueError(
                f"cannot determine the CECE commit for {self.root_dir}: git "
                f"rev-parse HEAD failed ({detail}); the CECE root must be a "
                "git checkout"
            )
        return sha
