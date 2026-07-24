import os
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-derived configuration. The CECE_ prefix is deliberately not
    runner-specific so this class can host other variable groups later.
    Frozen: constructed once at sessionstart and read-only thereafter, so
    root_dir resolution (--cece-root-dir flag over CECE_ROOT_DIR env) happens
    at exactly one point. A cwd-relative .env file supplies per-machine
    values below real environment variables (init kwargs > env > .env)."""

    model_config = SettingsConfigDict(env_prefix="CECE_", frozen=True, env_file=".env")

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

    @field_validator("suite_config_search_path", mode="before")
    @classmethod
    def _split_search_path(cls, value: object) -> object:
        if isinstance(value, str):
            return [Path(part) for part in value.split(os.pathsep) if part]
        return value
