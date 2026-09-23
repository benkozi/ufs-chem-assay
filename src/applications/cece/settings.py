"""CECE's settings: the CECE_* namespace."""

from pathlib import PurePosixPath

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from applications.base import ApplicationSettings

ENV_PREFIX = "CECE_"
# Where the checkout is bind-mounted inside cece/cece-dev: the driver's cwd,
# so relative driver and data paths resolve as they do natively.
CONTAINER_WORKDIR = PurePosixPath("/work")


class CeceSettings(ApplicationSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX)

    docker_image: str = Field(
        "cece/cece-dev",
        description="Container image the docker runtime runs the driver in (built by CECE's setup.sh)",
    )
    driver_path: str = Field(
        "./build/cece_standalone_driver",
        description="Driver executable, relative to the CECE checkout root",
    )
