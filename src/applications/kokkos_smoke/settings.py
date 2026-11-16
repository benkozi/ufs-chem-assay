"""The kokkos-smoke adapter's settings: the KOKKOS_SMOKE_* namespace."""

from typing import ClassVar

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from applications.base import ApplicationSettings


class KokkosSmokeSettings(ApplicationSettings):
    env_prefix: ClassVar[str] = "KOKKOS_SMOKE_"

    model_config = SettingsConfigDict(env_prefix=env_prefix)

    docker_image: str = Field(
        "", description="Unused: the smoke project builds natively; there is no image"
    )
    driver_path: str = Field(
        "./build/smoke",
        description="The smoke executable, relative to the adapter's build root",
    )

    def commit_sha(self) -> str | None:
        """None: the project ships with the harness, so its version is the
        harness's own commit — there is no checkout of its own to read."""
        return None
