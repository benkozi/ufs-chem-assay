"""monetio's settings: the MONETIO_* namespace."""

from pathlib import PurePosixPath

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from applications.base import ApplicationSettings

ENV_PREFIX = "MONETIO_"
CONTAINER_WORKDIR = PurePosixPath("/work")


class MonetioSettings(ApplicationSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX)

    docker_image: str = Field(
        "monetio/monetio-dev",
        description="Container image the docker runtime runs monetio in",
    )
    driver_path: str = Field(
        ".venv/bin/monetio",
        description="Driver executable, relative to the monetio checkout root",
    )
