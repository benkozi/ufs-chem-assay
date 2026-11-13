"""monetio's settings: the MONETIO_* namespace."""

from pathlib import PurePosixPath
from typing import ClassVar

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from applications.base import ApplicationSettings


class MonetioSettings(ApplicationSettings):
    env_prefix: ClassVar[str] = "MONETIO_"
    container_workdir: ClassVar[PurePosixPath] = PurePosixPath("/work")

    model_config = SettingsConfigDict(env_prefix=env_prefix)

    docker_image: str = Field(
        "monetio/monetio-dev",
        description="Container image the docker runtime runs monetio in",
    )
    driver_path: str = Field(
        ".venv/bin/monetio",
        description="Driver executable, relative to the monetio checkout root",
    )
