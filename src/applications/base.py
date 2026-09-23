"""The Application adapter: everything the shared harness needs from "the
application under test", as one abstract base class plus the generic base
models the shared code types on.

An adapter is one registry entry (applications/registry.py). Each of its
methods exists because the shared code used to contain application-specific
logic at that point; the shared modules (enumeration, session, runtimes,
stats, plots, comparison, reporting) import only this module, never an
adapter. Adapters import the shared modules freely, so this module imports
them for typing only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, ClassVar, Self

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from models.base import StrictModel

if TYPE_CHECKING:
    from cli.run_config import RunConfig
    from cli.stages import Stage
    from combos import Combo, Dimension, ParameterRow
    from examples import DownloadResult
    from models.suite_config import SuiteConfig
    from settings import Settings


class ApplicationSettings(BaseSettings):
    """An application's environment-derived settings: its checkout, image,
    driver, and modulefile, under the adapter's own prefix (the subclass sets
    env_prefix and the defaults; nothing else). Frozen, .env-aware, and
    tolerant of the other prefixes' keys in the same .env file."""

    model_config = SettingsConfigDict(frozen=True, env_file=".env", extra="ignore")

    root_dir: Path | None = Field(
        None,
        description=(
            "Host path of the application checkout (mounted at the adapter's "
            "container_workdir under docker); required to execute the driver. "
            "Unset means not configured — never a guessed path."
        ),
    )
    docker_image: str = Field(
        description="Container image the docker runtime runs the driver in"
    )
    driver_path: str = Field(
        description="Driver executable, relative to the checkout root"
    )
    modulefile: str | None = Field(
        None,
        description=(
            "Modulefile under <checkout>/modulefiles that each rendered job "
            "script loads before the driver (slurm runtime); recorded in run.yaml"
        ),
    )

    def commit_sha(self) -> str | None:
        """HEAD commit SHA of the application checkout, for run.yaml.

        None when no checkout is configured (root_dir unset). A session
        always runs against a checked-out application, so a configured root
        whose SHA cannot be determined (not a git repository, no commits,
        git missing or failing) is a fatal misconfiguration: ValueError,
        converted to a usage error at sessionstart before any work runs."""
        import subprocess  # local: keeps the module import-light

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
                f"cannot determine the application commit for {self.root_dir}: {exc}"
            ) from exc
        sha = completed.stdout.strip()
        if completed.returncode != 0 or not sha:
            detail = completed.stderr.strip() or "git produced no output"
            raise ValueError(
                f"cannot determine the application commit for {self.root_dir}: git "
                f"rev-parse HEAD failed ({detail}); the application root must be a "
                "git checkout"
            )
        return sha


class DriverConfig(StrictModel, ABC):
    """The driver's configuration file as a strict pydantic model. Every
    generated config is built as an instance and written only via to_yaml,
    so the driver never receives anything that did not pass validation."""

    @classmethod
    @abstractmethod
    def from_yaml(cls, path: Path) -> Self: ...

    @abstractmethod
    def to_yaml(self, path: Path) -> None: ...


class SweepBase(StrictModel):
    """Base of every application's sweep schema; the empty sweep — the base
    config as the single combination — is what the base class alone means."""


class SweepSelectorBase(StrictModel):
    """Base of every application's baseline sweep-selector schema."""


class ApplicationRunSection(StrictModel):
    """An application's section of the run config (`applications.<name>:`):
    where its checkout comes from, plus the mirror of ApplicationSettings —
    null means "not exported; the setting's default applies"."""

    git_url: str = Field(description="Repository cloned when clone_dir is missing")
    ref: str = Field(description="Branch or commit checked out")
    clone_dir: Path | None = Field(
        default=None,
        description=(
            "Path of the application checkout (its ROOT_DIR setting); null means "
            "<root_dir>/<the adapter's checkout_dirname>. An existing checkout is "
            "used as-is unless update_source is set"
        ),
    )
    update_source: bool = Field(
        default=False,
        description=(
            "false: an existing clone is used as-is, no git command touches it. "
            "true: git fetch, checkout ref, pull --ff-only, then submodule update "
            "--init --recursive. Never destructive — a dirty or diverged clone "
            "makes git fail. A missing clone is always cloned --recurse-submodules"
        ),
    )
    modulefile: str | None = Field(
        default=None,
        description=(
            "Name under <clone>/modulefiles loaded after `module purge` (the "
            "MODULEFILE setting); null means no module environment"
        ),
    )
    docker_image: str | None = Field(
        default=None,
        description="The DOCKER_IMAGE setting; null keeps the adapter's default",
    )
    driver_path: str | None = Field(
        default=None,
        description="The DRIVER_PATH setting; null keeps the adapter's default",
    )


class ExamplesSupport(ABC):
    """An application's shipped, runnable examples (--run-examples): how to
    find them, name them, stage their data, and run one."""

    @abstractmethod
    def discover(self, root_dir: Path) -> list[Path]: ...

    @abstractmethod
    def example_id(self, config_path: Path) -> str: ...

    @abstractmethod
    def download(
        self, root_dir: Path, timeout_s: int = 300
    ) -> list[DownloadResult]: ...

    @abstractmethod
    def run_command(
        self, settings: Settings, app_settings: ApplicationSettings, eid: str
    ) -> list[str]: ...


class Application(ABC):
    """One application the harness can test. Class attributes are the
    adapter's constants; the methods are the points where the shared code
    needs application knowledge. Each adapter narrows the generic base types
    it receives with isinstance once per method — a wrong `application:`
    cannot reach it, since the suite model already validated for it."""

    name: ClassVar[str]  # registry key; the `application:` value
    env_prefix: ClassVar[str]  # "CECE_": its settings namespace and root-dir token
    container_workdir: ClassVar[PurePosixPath]  # checkout mount point under docker
    default_suite: ClassVar[str]  # what a bare `--application=<name>` runs
    checkout_dirname: ClassVar[str]  # default checkout under the CLI's run root
    standard_dimensions: ClassVar[tuple[str, ...]]  # every output variable's dims
    settings_model: ClassVar[type[ApplicationSettings]]
    config_model: ClassVar[type[DriverConfig]]
    suite_model: ClassVar[type[SuiteConfig]]
    run_section_model: ClassVar[type[ApplicationRunSection]]
    examples: ClassVar[ExamplesSupport | None]  # None: ships no runnable examples

    @property
    def root_dir_token(self) -> str:
        """The literal config_path prefix that anchors a suite on the
        application checkout: `${CECE_ROOT_DIR}` — the variable's own name."""
        return "${" + self.env_prefix + "ROOT_DIR}"

    # -- enumeration and config generation (combos.py) ------------------------

    @abstractmethod
    def dimensions(
        self, sweep: SweepBase, base_config: DriverConfig
    ) -> list[tuple[Dimension, tuple[StrEnum, ...]]]:
        """The sweep's dimensions with their value lists, validated against
        the base config (unknown targets fail here, before any run)."""

    @abstractmethod
    def build_config(
        self, combo: Combo, output_directory: str, config_path: Path
    ) -> DriverConfig:
        """A fresh base config with the combo's values applied and every
        output (files, logs) pointed into the combo's own directory."""

    @abstractmethod
    def effective_parameters(
        self, combo: Combo, config: DriverConfig
    ) -> list[ParameterRow]:
        """(target, field, value, swept) for every sweepable dimension of the
        combo, read from its generated config — the combos.csv rows."""

    # -- derived assertions (assertions.py) -----------------------------------

    @abstractmethod
    def expected_output_count(self, config: DriverConfig) -> int:
        """NetCDF files a correct run of this config writes."""

    @abstractmethod
    def expected_output_filenames(self, config: DriverConfig) -> set[str]:
        """Their names."""

    @abstractmethod
    def output_variable_names(self, config: DriverConfig) -> list[str]:
        """The configured output variables, each expected to carry
        standard_dimensions in every file."""

    # -- baseline selectors (comparison.py) -----------------------------------

    @abstractmethod
    def selector_matches(self, selector: SweepSelectorBase, combo: Combo) -> bool:
        """Whether a baseline entry's sweep selector matches the combo."""

    # -- CLI stage bodies (cli/stages.py) -------------------------------------

    @abstractmethod
    def stage_lines(
        self, stage: Stage, config: RunConfig, section: ApplicationRunSection
    ) -> list[str]:
        """The shell lines of the source, build, or data stage for this
        application (the harness stage is shared)."""
