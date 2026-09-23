"""Harness-wide settings: everything that is about the machine, the runtime,
and the session — never about one application. Application settings (the
checkout, image, driver, modulefile) live on each adapter's
ApplicationSettings subclass under its own prefix (CECE_*).

Environment prefix ASSAY_, with the historical CECE_ spellings accepted as
fallbacks until the second adapter lands (Phase C): ASSAY_X beats CECE_X
within a source, and the environment beats a cwd-relative .env file as
before (init kwargs > ASSAY_ env > CECE_ env > ASSAY_ .env > CECE_ .env >
field default). Frozen: constructed once at sessionstart and read-only
thereafter.
"""

import os
import shlex
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from platforms import Platform, Runtime, default_runtime, detect_platform

ENV_PREFIX = "ASSAY_"
LEGACY_ENV_PREFIX = "CECE_"  # accepted as a fallback for every harness setting
_ENV_FILE = ".env"


class Settings(BaseSettings):
    """Harness-wide, environment-derived configuration (see the module
    docstring for the prefixes and precedence)."""

    # extra="ignore": the .env file also carries the applications' keys
    # (cece_root_dir, ...), which are not this model's business.
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX, frozen=True, env_file=_ENV_FILE, extra="ignore"
    )

    application: str | None = Field(
        None,
        description=(
            "Registry name of the application this session runs (ASSAY_APPLICATION "
            "or --application, the flag winning); unset infers it from the selected "
            "suites, which must then agree"
        ),
    )
    platform: Platform = Field(
        description=(
            "Machine the harness runs on: an explicit value (ASSAY_PLATFORM or "
            "init kwarg) beats hostname detection, which falls back to local "
            "(filled in by the model validator below, never required)"
        ),
    )
    runtime: Runtime = Field(
        default=Runtime.DOCKER,
        description=(
            "How the driver is spawned: docker (the application's image), native "
            "(a host process), or slurm (one job per driver call). Defaults from "
            "the platform — docker on local, slurm elsewhere — unless "
            "ASSAY_RUNTIME says otherwise"
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
    job_env: str = Field(
        default="",
        description=(
            "slurm runtime: NAME=VALUE pairs, whitespace-separated, exported "
            "inside every driver job before the driver (e.g. 'I_MPI_FABRICS=shm "
            "FI_PROVIDER=tcp'); the login-node shell never needs them"
        ),
    )
    run_timeout_s: int = Field(
        300, gt=0, description="Caps every suite's timeout_s when smaller"
    )
    log_level: str = Field(
        "INFO", description="Harness logger level (DEBUG, INFO, ...)"
    )
    baseline_root_dir: Path | None = Field(
        None,
        description="Directory holding baselines as <root>/<ulid>/; None means the current working directory",
    )
    enable_baseline_comparisons: bool = Field(
        True,
        description="Global switch for baseline comparisons; false skips every test_baseline_comparison regardless of suite config",
    )
    dask_nworkers: int | None = Field(
        None,
        gt=0,
        description="Workers for the stats dask cluster; None sizes it to all available cores",
    )
    config_search_path: Path | None = Field(
        None,
        description=(
            "When set, prepended to a suite's relative config_path (kept whole, "
            "so nested and ../ paths work); absolute config paths are used as-is"
        ),
    )
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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Highest first. The legacy sources read the same environment and the
        # same .env file under the CECE_ prefix; a value present under both
        # spellings resolves to ASSAY_ within a source.
        return (
            init_settings,
            env_settings,
            EnvSettingsSource(settings_cls, env_prefix=LEGACY_ENV_PREFIX),
            dotenv_settings,
            DotEnvSettingsSource(
                settings_cls, env_file=_ENV_FILE, env_prefix=LEGACY_ENV_PREFIX
            ),
            file_secret_settings,
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

    @property
    def job_env_pairs(self) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for token in shlex.split(self.job_env):
            name, sep, value = token.partition("=")
            if not sep or not name:
                raise ValueError(
                    f"{ENV_PREFIX}JOB_ENV entries must be NAME=VALUE, got {token!r}"
                )
            pairs[name] = value
        return pairs

    @field_validator("suite_config_search_path", mode="before")
    @classmethod
    def _split_search_path(cls, value: object) -> object:
        if isinstance(value, str):
            return [Path(part) for part in value.split(os.pathsep) if part]
        return value
