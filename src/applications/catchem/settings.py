"""CATChem's settings: the CATCHEM_* namespace."""

from typing import ClassVar

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from applications.base import ApplicationSettings


class CatchemSettings(ApplicationSettings):
    env_prefix: ClassVar[str] = "CATCHEM_"

    model_config = SettingsConfigDict(env_prefix=env_prefix)

    docker_image: str = Field(
        "", description="Unused: CATChem builds natively; there is no image for it"
    )
    driver_path: str = Field(
        "", description="Unused: CATChem has no driver the harness sweeps"
    )
