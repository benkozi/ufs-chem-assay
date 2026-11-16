"""The Application adapter: everything the shared harness needs from "the
application under test", as one abstract base class plus the generic base
models the shared code types on.

An adapter is one registry entry (applications/registry.py). Each of its
methods exists because the shared code used to contain application-specific
logic at that point; the shared modules (enumeration, session, runtimes,
stats, plots, comparison, reporting) import only this module, never an
adapter. Adapters import the shared modules freely, so this module imports
them for typing only — and never a `cli` module: the stage enum and the
view of the run config that an adapter may read are declared here, so the
`cli` package depends on this module and not the other way round.
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from enum import StrEnum, unique
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, ClassVar, Protocol, Self

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from models.base import StrictModel
from platforms import ModuleInit, Runtime
from settings import parse_env_pairs

if TYPE_CHECKING:
    from combos import Combo, Dimension, ParameterRow
    from models.suite_config import SuiteConfig


@unique
class Stage(StrEnum):
    """The stages of `ufs-chem-assay run`, in execution order. Declared
    here rather than in cli/stages.py because the adapter interface
    (stage_lines) names it and this module imports nothing from cli."""

    SOURCE = "source"  # clone / update the application checkout
    BUILD = "build"  # configure + build the driver (and, when asked, the tests)
    TEST = "test"  # the application's own test suite, against that build
    DATA = "data"  # input data (the application's) + cartopy cache
    HARNESS = "harness"  # the pytest session, on this node; driver calls per runtime
    RESUME = "resume"  # (harness.submit_only) the session's second half: wait, evaluate


class NoDriverSuite(ValueError):
    """An application that is cloned, built and tested but never swept was
    asked for something only a driver sweep has: a suite, a configuration,
    a combination's expectations."""


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


@unique
class BuildNode(StrEnum):
    """Where an application's configure and build run (slurm runtime)."""

    LOGIN = "login"  # where the CLI runs: network access, so fetches work
    COMPUTE = "compute"  # behind srun: the offline compile probe


_SHELL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _as_exports(value: object, key: str) -> object:
    """A run-config mapping rendered as `export NAME=value` lines: the names
    must be shell identifiers, and a YAML number (kokkos_ver: 4.5,
    OMP_NUM_THREADS: 8) is as good a value as a string."""
    if not isinstance(value, dict):
        return value
    exports: dict[object, object] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not _SHELL_NAME.fullmatch(name):
            raise ValueError(
                f"{key}: {name!r} is not a shell variable name "
                "(letters, digits and underscores, not starting with a digit)"
            )
        is_number = isinstance(item, int | float) and not isinstance(item, bool)
        exports[name] = str(item) if is_number else item
    return exports


class RunConfigView(Protocol):
    """What an adapter may read of the run config (cli/run_config.py's
    RunConfig satisfies it): the effective runtime and its own checkout."""

    @property
    def runtime(self) -> Runtime:
        """The effective runtime: the harness override, else the platform's."""

    @property
    def module_init(self) -> ModuleInit:
        """How the platform clears its module environment before a
        modulefile is loaded (purge, or reset where purge strips the
        machine's own compiler environment)."""

    def checkout_dir(self, app: Application) -> Path:
        """The application's checkout: its section's clone_dir, else
        <root_dir>[/<toolchain>]/<the adapter's checkout_dirname>."""

    def mpi_configure_args(self) -> list[str]:
        """Configure arguments that bake the toolchain's MPI launcher into a
        CMake build, as shell words already quoted (one of them resolves a
        path where the configure runs); none without a toolchain or with
        FindMPI's own default."""

    def build_launcher(self, section: ApplicationRunSection, *, cpus: int) -> list[str]:
        """The command prefix of one compile step (a configure, a build) of
        `section`: empty on the login node; under build_node: compute, the
        srun that gives the step one task with `cpus` cores for the
        section's build_minutes, charged as the slurm: section says."""


class ApplicationSettings(BaseSettings):
    """An application's environment-derived settings: its checkout, image,
    driver, and module environment, under the adapter's own prefix (the
    subclass sets env_prefix and the defaults; nothing else). Frozen,
    .env-aware, and tolerant of the other prefixes' keys in the same .env
    file."""

    model_config = SettingsConfigDict(frozen=True, env_file=".env", extra="ignore")

    env_prefix: ClassVar[str] = ""  # the subclass's namespace ("CECE_")
    container_workdir: ClassVar[PurePosixPath] = PurePosixPath("/work")

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
            "Modulefile each rendered job script loads before the driver (slurm "
            "runtime), found through modulepath; recorded in run.yaml"
        ),
    )
    # NoDecode: pydantic-settings would otherwise JSON-decode the env value
    # for a complex type; the validator splits on os.pathsep instead.
    modulepath: Annotated[list[Path], NoDecode] = Field(
        default_factory=list,
        description=(
            "Directories each job script `module use`s before loading the "
            "modulefile, in order, os.pathsep-separated in the environment; "
            "empty means <checkout>/modulefiles"
        ),
    )
    module_env: str = Field(
        default="",
        description=(
            "NAME=VALUE pairs, whitespace-separated, exported in each job script "
            "before `module load` — what the modulefile reads (version pins such "
            "as 'kokkos_ver=4.5.01', an environment root)"
        ),
    )

    @field_validator("modulepath", mode="before")
    @classmethod
    def _split_modulepath(cls, value: object) -> object:
        if isinstance(value, str):
            return [Path(part) for part in value.split(os.pathsep) if part]
        return value

    @property
    def module_env_pairs(self) -> dict[str, str]:
        """module_env as a mapping, in the order written; ValueError naming
        the variable for an entry that is not NAME=VALUE."""
        return parse_env_pairs(self.module_env, variable=f"{self.env_prefix}MODULE_ENV")

    def resolved_modulepath(self) -> list[Path]:
        """The directories a job script `module use`s: modulepath as given,
        else the checkout's own modulefiles (none without a checkout)."""
        if self.modulepath:
            return list(self.modulepath)
        return [] if self.root_dir is None else [self.root_dir / "modulefiles"]

    def build_type(self) -> str | None:
        """CMAKE_BUILD_TYPE of the checkout's build directory
        (<root_dir>/build/CMakeCache.txt), for run.yaml and the benchmark
        rows. None without a checkout, without a cache (not a CMake build,
        or not built yet), or when the cache leaves it empty."""
        if self.root_dir is None:
            return None
        cache = self.root_dir / "build" / "CMakeCache.txt"
        if not cache.is_file():
            return None
        match = re.search(
            r"^CMAKE_BUILD_TYPE:[A-Z]+=(.*)$",
            cache.read_text(errors="replace"),
            re.MULTILINE,
        )
        return (match.group(1).strip() or None) if match else None

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
                check=False,
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
    def from_yaml(cls, path: Path) -> Self:
        """Parse and validate the driver's YAML at path."""

    @abstractmethod
    def to_yaml(self, path: Path) -> None:
        """Write the validated config as the driver's YAML at path."""


class SweepBase(StrictModel):
    """Base of every application's sweep schema; the empty sweep — the base
    config as the single combination — is what the base class alone means."""


class SweepSelectorBase(StrictModel):
    """Base of every application's baseline sweep-selector schema."""


@unique
class MpiLauncher(StrEnum):
    """What launches the ranks of an application's MPI tests."""

    MPIEXEC = "mpiexec"  # the MPI's own launcher (CMake FindMPI's default)
    SRUN = "srun"  # Slurm: every MPI test is its own job step


class ToolchainSection(StrictModel):
    """One compiler suite of a run config (`toolchains.<name>:`): everything
    that must change together when the compiler does — the module
    environment, the configure flags, the MPI launcher, the tests'
    environment — so no application can pair one suite's modulefile with
    another's flags. `--toolchain NAME` selects it; it is overlaid on every
    application section and names a path segment of its own."""

    modulefile: str = Field(description="The module every application loads")
    modulepath: list[Path] = Field(
        default_factory=list,
        description=(
            "Directories `module use`d before the load, as for an application "
            "section: relative entries are under the harness checkout "
            "(`modulefiles`), absolute ones are used as written (the "
            "environment's modules/Core)"
        ),
    )
    module_env: dict[str, str] = Field(
        default_factory=dict,
        description="Variables exported before `module load` (version pins)",
    )
    toolchain_tools: list[str] = Field(
        default_factory=list,
        description="Commands the build stage locates with `which` after the load",
    )
    cmake_args: list[str] = Field(
        default_factory=list,
        description=(
            "Configure arguments appended to every CMake application's own "
            "(a compiler-specific flag such as -DCMAKE_CXX_FLAGS=-fp-model=precise)"
        ),
    )
    mpi_launcher: MpiLauncher = Field(
        default=MpiLauncher.MPIEXEC,
        description=(
            "What launches the MPI tests' ranks: mpiexec (FindMPI's default, "
            "forking inside the allocation) or srun (baked into every CMake "
            "configure as MPIEXEC_EXECUTABLE, so each MPI test is a Slurm step)"
        ),
    )
    mpiexec_preflags: str | None = Field(
        default=None,
        description=(
            "mpi_launcher: srun only: flags placed before the rank count "
            "(MPIEXEC_PREFLAGS), e.g. --mpi=pmi2"
        ),
    )
    tests_env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Variables merged into every application's tests.env (the "
            "toolchain's value wins): what this MPI needs to launch tests"
        ),
    )
    configure_gates: list[str] = Field(
        default_factory=list,
        description=(
            "Configure-log patterns added to every application's own: what "
            "this environment claims to supply"
        ),
    )

    @field_validator("module_env", "tests_env", mode="before")
    @classmethod
    def _maps_are_exports(cls, value: object, info: ValidationInfo) -> object:
        return _as_exports(value, str(info.field_name))


# The keys of an application section that a toolchain owns: under a
# toolchains: map they may not be set on the section itself.
_TOOLCHAIN_KEYS = ("modulefile", "modulepath", "module_env", "toolchain_tools")


class TestsSection(StrictModel):
    """An application's own test suite (`applications.<name>.tests:`): what
    the application's developers run — ctest for a CMake project — as
    opposed to the driver sweeps the harness's pytest sessions run."""

    __test__: ClassVar[bool] = False  # a model, not a pytest class

    enabled: bool = Field(
        default=False,
        description=(
            "true: the build stage builds the application's test stack (after a "
            "compute-node build, in a login-node finish) and the test stage runs "
            "it; false: the driver alone is built and there is no test stage"
        ),
    )
    filter: str | None = Field(
        default=None,
        description="Run only the tests matching this regex (ctest -R); null: all",
    )
    parallel: int | None = Field(
        default=None,
        gt=0,
        description="Tests run at once (ctest -j); null: one at a time",
    )
    ntasks: int = Field(
        default=1,
        gt=0,
        description=(
            "slurm runtime: the test job's task count (sbatch -n) — the largest "
            "MPI rank count any test asks for"
        ),
    )
    cpus: int = Field(
        default=8,
        gt=0,
        description="slurm runtime: the test job's cores per task (sbatch -c)",
    )
    minutes: int = Field(
        default=5,
        gt=0,
        description="slurm runtime: the test job's time limit, in minutes",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Variables exported before the tests run (inside the job under slurm), "
            "e.g. an MPI bootstrap hint; numbers are exported as strings"
        ),
    )

    @field_validator("env", mode="before")
    @classmethod
    def _env_as_exports(cls, value: object) -> object:
        return _as_exports(value, "tests.env")


class TestPlan(StrictModel):
    """What the test stage launches for one application: the adapter's
    knowledge of its own test suite; where and how it is launched (a Slurm
    job, the image, a launcher prefix) is the shared stage's."""

    __test__: ClassVar[bool] = False  # a model, not a pytest class

    requires: str = Field(
        description=(
            "A file, relative to the checkout, that exists only when the test "
            "stack was built (build/CTestTestfile.cmake): the stage's guard — "
            "the test stage never compiles"
        )
    )
    command: str = Field(
        description=(
            "The shell command that runs the suite from the checkout root and "
            "writes its junit report where the stage asked"
        )
    )
    passed_marker: str | None = Field(
        default=None,
        description=(
            "For a test command that writes no junit report (one executable, one "
            "verdict): the text in its output that means it passed — the run then "
            "counts as one test, passed when the output holds it"
        ),
    )


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
            "Module loaded after the platform's `module purge` / `module reset` "
            "(the MODULEFILE setting), found through modulepath; null means no "
            "module environment"
        ),
    )
    modulepath: list[Path] = Field(
        default_factory=list,
        description=(
            "Directories `module use`d before the load, in order (the MODULEPATH "
            "setting): a relative entry is under the harness checkout (`modulefiles` "
            "is the harness's own UFS modulefiles), an absolute one is used as "
            "written (a spack-stack environment's modules/Core); empty means "
            "<clone>/modulefiles"
        ),
    )
    module_env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Variables exported before `module load` (the MODULE_ENV setting) — "
            "what the modulefile reads: version pins (kokkos_ver), an environment "
            "root (ASSAY_SPACK_ENV_ROOT). Names are shell identifiers; numbers are "
            "exported as strings"
        ),
    )
    toolchain_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Commands the build stage locates with `which` after the load, so the "
            "log names the compilers the configure will see (mpiicx, cmake, ...); "
            "empty keeps the adapter's default probe. Build stage only"
        ),
    )
    configure_gates: list[str] = Field(
        default_factory=list,
        description=(
            "Extended regular expressions (grep -E) the configure log must match, "
            "checked after the adapter's own gates and before the build: the "
            "environment's claims ('Using system Kokkos'). Build stage only"
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
    build_node: BuildNode = Field(
        default=BuildNode.LOGIN,
        description=(
            "Where the configure and the build run. login: where the CLI runs, "
            "with network access. compute (slurm runtime only): each behind its "
            "own srun, as an offline compile probe — the adapter turns off what "
            "would fetch at configure time, and with tests.enabled finishes the "
            "build on the login node"
        ),
    )
    build_minutes: int = Field(
        default=10,
        gt=0,
        description=(
            "build_node: compute only: the time limit, in minutes, of each "
            "compile step's srun"
        ),
    )
    tests: TestsSection = Field(
        default_factory=TestsSection,
        description="The application's own test suite; off by default",
    )

    @field_validator("module_env", mode="before")
    @classmethod
    def _module_env_as_exports(cls, value: object) -> object:
        return _as_exports(value, "module_env")

    def build_flags(self) -> list[str]:
        """The configure flags this section gives the build, for the record
        of what a test run ran against; none by default."""
        return []

    def toolchain_owned(self) -> list[str]:
        """The keys this section sets that belong to a toolchain when the
        run config has any: the module environment's, and clone_dir (each
        toolchain has its own checkout). Set means different from the
        section's default — an effective config lists every key."""
        fields = type(self).model_fields
        return [
            name
            for name in (*_TOOLCHAIN_KEYS, "clone_dir")
            if getattr(self, name)
            != fields[name].get_default(call_default_factory=True)
        ]

    def toolchain_updates(self, toolchain: ToolchainSection) -> dict[str, object]:
        """What a toolchain changes in this section: the module environment
        replaced whole, its configure gates added to the section's own (a
        union, the section's first), its tests_env merged over tests.env.
        A section with configure arguments extends this with them."""
        gates = [
            *self.configure_gates,
            *(g for g in toolchain.configure_gates if g not in self.configure_gates),
        ]
        return {
            "modulefile": toolchain.modulefile,
            "modulepath": list(toolchain.modulepath),
            "module_env": dict(toolchain.module_env),
            "toolchain_tools": list(toolchain.toolchain_tools),
            "configure_gates": gates,
            "tests": {
                **self.tests.model_dump(),
                "env": {**self.tests.env, **toolchain.tests_env},
            },
        }

    def with_toolchain(self, toolchain: ToolchainSection) -> Self:
        """This section as one toolchain builds it, re-validated."""
        return type(self).model_validate(
            {**self.model_dump(), **self.toolchain_updates(toolchain)}
        )

    def runtime_problems(self, runtime: Runtime) -> list[str]:
        """What is wrong with this section under `runtime`, one sentence
        each — the rules that need more than the section itself (the run
        config raises the first as a validation error). None by default; an
        adapter's section overrides it."""
        _ = runtime
        return []


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
    data_dirname: ClassVar[str]  # input data directory under the checkout ("data")
    standard_dimensions: ClassVar[tuple[str, ...]]  # every output variable's dims
    # Global attributes the driver stamps per run (creation time, a history
    # line): the baseline comparison requires them present but ignores their
    # values; () when none.
    volatile_global_attributes: ClassVar[tuple[str, ...]] = ()
    settings_model: ClassVar[type[ApplicationSettings]]
    config_model: ClassVar[type[DriverConfig]]
    suite_model: ClassVar[type[SuiteConfig]]
    run_section_model: ClassVar[type[ApplicationRunSection]]
    # The stages of `ufs-chem-assay run` the application takes part in:
    # every one for an application with a driver the harness sweeps; fewer
    # for one that is only built and tested (applications/nodriver.py).
    # Everything driver-shaped — a suite, a pytest session, `fetch` — asks
    # for Stage.HARNESS here first.
    stages: ClassVar[frozenset[Stage]] = frozenset(Stage)

    def no_driver_suite(self) -> NoDriverSuite:
        """The refusal an application without a driver suite gives, naming
        what it does take part in."""
        mine = [stage.value for stage in Stage if stage in self.stages]
        return NoDriverSuite(
            f"{self.name} has no driver suite: it takes part in the "
            f"{_listed(mine)} stages only"
        )

    @property
    def root_dir_token(self) -> str:
        """The literal config_path prefix that anchors a suite on the
        application checkout: `${CECE_ROOT_DIR}` — the variable's own name."""
        return "${" + self.env_prefix + "ROOT_DIR}"

    def data_dir(self, root_dir: Path) -> Path:
        """Where a suite's `inputs` are staged: <checkout>/<data_dirname>."""
        return root_dir / self.data_dirname

    def write_config(
        self, config: DriverConfig, combo_dir: Path, combo_id: str
    ) -> PurePosixPath:
        """Write a combination's generated configuration into its directory
        and return the driver's argument relative to that directory. The
        default is the single-file form, `<combo_id>.yaml`; an application
        whose configuration is a directory overrides it."""
        name = f"{combo_id}.yaml"
        config.to_yaml(combo_dir / name)
        return PurePosixPath(name)

    def generated_config_path(self, combo_dir: Path, combo_id: str) -> Path:
        """Where write_config put a combination's generated configuration —
        the same convention read back from a published baseline directory."""
        return combo_dir / f"{combo_id}.yaml"

    def driver_arguments(
        self, driver_yaml: PurePosixPath, *, host_yaml: Path | None = None
    ) -> list[str]:
        """The CLI arguments following `driver_path` for one combination.
        `driver_yaml` is the config path as the runtime sees it; `host_yaml`,
        when provided, is its host-side path so applications that inspect the
        YAML from the host (e.g. under Docker) can read it without resolving
        container paths. The default passes `driver_yaml` as a single
        positional argument; an application whose CLI takes subcommands or
        flags overrides it."""
        _ = host_yaml
        return [str(driver_yaml)]

    def config_fingerprint(self, path: Path) -> str:
        """A generated configuration with its per-run paths normalised, as a
        string two combinations can be compared by: equal fingerprints mean
        the same configuration. The default loads the config model and
        dumps it verbatim; an adapter whose generated configs carry run
        paths (output directory, log file) overrides it to neutralise them."""
        return self.config_model.from_yaml(path).model_dump_json()

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
        self, stage: Stage, config: RunConfigView, section: ApplicationRunSection
    ) -> list[str]:
        """The shell lines of the source, build, or data stage for this
        application (the test, harness and resume stages are shared)."""

    def test_plan(self, section: ApplicationRunSection, junit: str) -> TestPlan | None:
        """The application's own test suite as the test stage launches it:
        `junit` is the shell word of the junit report the command must
        write. None — the default — means the application has no test suite
        of its own, and tests.enabled is an error for it."""
        _ = section, junit
        return None
