"""RunConfig: the whole file as one model — loading with the platform and
root resolved and overrides merged (`from_yaml`), the derived values
(runtime, cache, checkouts), the sessions resolved, the effective
configuration written back."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

import yaml
from pydantic import (
    Field,
    PrivateAttr,
    SerializeAsAny,
    ValidationInfo,
    field_validator,
    model_validator,
)

from applications.base import (
    Application,
    ApplicationRunSection,
    BuildNode,
    MpiLauncher,
    Stage,
    ToolchainSection,
)
from cli.override import apply_overrides
from cli.run_config.sections import (
    BaselinesSection,
    DataSection,
    HarnessSection,
    OutputsSection,
    SlurmSection,
    UvSection,
)
from cli.run_config.sessions import (
    SESSION_NAME,
    ResolvedSession,
    SessionOverlay,
    overlaid,
)
from identity import HARNESS_ROOT
from models.base import StrictModel
from models.conventions import CONVENTIONS
from platforms import (
    ModuleInit,
    Platform,
    Runtime,
    default_runtime,
    detect_platform,
    module_init,
)

# name -> adapter, or ValueError naming the registered ones: the registry's
# get_application. Injected by the caller (cli/main.py, the tests) because
# this module must never import the registry: the registry imports the
# adapters, which import this module (design/fix/20261016-1547-codeql-dependabot-alerts).
ResolveApplication = Callable[[object], Application]


class RunConfig(StrictModel):
    """Everything `ufs-chem-assay run` needs. Load through from_yaml, which
    resolves the platform and root and merges overrides; the model itself
    never guesses."""

    platform: Platform = Field(
        description=(
            "Machine this config targets. Optional in the file: from_yaml "
            "resolves --platform > the file > hostname detection"
        )
    )
    root_dir: Path = Field(
        description=(
            "Directory holding everything the CLI creates: the checkouts, logs/, "
            "scripts/, uv caches. Optional in the file: from_yaml resolves "
            "--root-dir > the file > the harness checkout's parent directory"
        )
    )
    transfer_dir: Path | None = Field(
        default=None,
        description=(
            "Where `ufs-chem-assay transfer` copies this root's evidence, as "
            "<transfer_dir>/<name>, then commits and pushes it: a directory "
            "inside a git repository; `~` is expanded. Per person, so it belongs "
            "in a personal file that extends the shared template; unset, "
            "`transfer` refuses"
        ),
    )
    # SerializeAsAny: each section dumps with its adapter's schema.
    applications: dict[str, SerializeAsAny[ApplicationRunSection]] = Field(
        description=(
            "One section per application in the run, keyed by registry name "
            "(cece, ...); a comprehensive run lists several, each getting its "
            "own stages and pytest session"
        )
    )
    data: DataSection = Field(default_factory=DataSection)
    baselines: BaselinesSection = Field(default_factory=BaselinesSection)
    outputs: OutputsSection = Field(default_factory=OutputsSection)
    harness: HarnessSection = Field(default_factory=HarnessSection)
    slurm: SlurmSection | None = Field(
        default=None,
        description="Batch-job settings; null runs the driver directly",
    )
    sessions: dict[str, SessionOverlay] = Field(
        default_factory=dict,
        description=(
            "Named sessions, each one pytest session under <output_root>/<name>, "
            "as overlays on harness:/slurm:/baselines:; empty means one nameless "
            "session with today's paths. Select with `run --session NAME`"
        ),
    )
    uv: UvSection = Field(default_factory=UvSection)
    toolchains: dict[str, ToolchainSection] = Field(
        default_factory=dict,
        description=(
            "Named compiler suites, each overlaid on every application section "
            "(its module environment whole; its configure arguments, gates and "
            "tests environment added) and given its own checkouts, builds and "
            "results under <root_dir>/<name>. Select with `run --toolchain NAME "
            "...` or `all`; empty means one unnamed build with today's paths"
        ),
    )
    selected_toolchains: list[str] = Field(
        default_factory=list,
        description=(
            "The toolchains this run selects (`run --toolchain` wins); empty "
            "means every toolchain. `run` records what it ran here in the "
            "effective config, so `watch` and `report` cover the same ones and "
            "the effective config reruns the same run"
        ),
    )
    selected_applications: list[str] = Field(
        default_factory=list,
        description=(
            "The applications this run selects (`run --application` wins); "
            "empty means every application. `run` records what it ran here in "
            "the effective config, so `watch` and `report` cover the same ones "
            "and the effective config reruns the same run"
        ),
    )
    selected_stages: list[Stage] = Field(
        default_factory=list,
        description=(
            "The stages this run selects (`run --stage` wins); empty means "
            "every stage but resume. `run` records what it ran here in the "
            "effective config, so `watch` and `report` cover only the sessions "
            "and test runs a run started (or the stage record or a ledger shows)"
        ),
    )
    selected_builds: list[str] = Field(
        default_factory=list,
        description=(
            "The build variants this run selects (`run --build` wins); empty "
            "means every variant. `run` records what it ran here in the "
            "effective config, so `watch` and `report` cover the same ones and "
            "the effective config reruns the same run"
        ),
    )
    # The toolchain a derived config (for_toolchain) builds with; None for
    # the config as loaded. Never read from or written to a file.
    _toolchain: str | None = PrivateAttr(default=None)
    # The config as loaded, for a derived one: a variant is overlaid on the
    # section as written, before the toolchain (for_build).
    _loaded: RunConfig | None = PrivateAttr(default=None)

    @field_validator("toolchains", mode="before")
    @classmethod
    def _toolchain_names(cls, value: object) -> object:
        if isinstance(value, dict):
            for name in value:
                if (
                    not isinstance(name, str)
                    or not SESSION_NAME.match(name)
                    or name == "all"
                ):
                    raise ValueError(
                        f"toolchains: {name!r} is not a toolchain name (lower-case "
                        "letters, digits and dashes, and not 'all': it names a "
                        "directory and a script)"
                    )
        return value

    @model_validator(mode="after")
    def _toolchains_own_the_module_keys(self) -> RunConfig:
        unknown = [n for n in self.selected_toolchains if n not in self.toolchains]
        if unknown:
            raise ValueError(
                f"selected_toolchains: {unknown} not in toolchains "
                f"({list(self.toolchains)})"
            )
        if not self.toolchains:
            return self
        # What a toolchain decides may not be decided per application too:
        # that is the drift a toolchain exists to prevent. And an explicit
        # checkout would be shared by every toolchain's build.
        for name, section in self.applications.items():
            for key in section.toolchain_owned():
                if key == "clone_dir":
                    raise ValueError(
                        f"applications.{name}: under toolchains:, `clone_dir` "
                        "cannot be set: each toolchain has its own checkout, "
                        "<root_dir>/<toolchain>/<checkout>"
                    )
                raise ValueError(
                    f"applications.{name}: under toolchains:, `{key}` belongs to "
                    "the toolchain (set it under toolchains.<name>:, once for "
                    "every application)"
                )
        return self

    @field_validator("sessions", mode="before")
    @classmethod
    def _session_names(cls, value: object) -> object:
        if isinstance(value, dict):
            for name in value:
                if not isinstance(name, str) or not SESSION_NAME.match(name):
                    raise ValueError(
                        f"sessions: {name!r} is not a session name (lower-case "
                        "letters, digits and dashes: it names a directory and a script)"
                    )
        return value

    @model_validator(mode="after")
    def _sessions_resolve(self) -> RunConfig:
        # Every session resolves once at load, so a bad overlay fails
        # from_yaml, not the harness stage; the same check guards the base.
        for session in self.resolved_sessions():
            runtime = session.harness.effective_runtime(self.runtime)
            if session.harness.submit_only and runtime is not Runtime.SLURM:
                where = "" if session.name is None else f"sessions.{session.name}: "
                raise ValueError(
                    f"{where}submit_only needs the slurm runtime; "
                    f"{self.platform.value} runs {runtime.value} "
                    "(--override harness:submit_only=false to run this config here)"
                )
        return self

    @model_validator(mode="after")
    def _selected_applications_are_configured(self) -> RunConfig:
        unknown = [n for n in self.selected_applications if n not in self.applications]
        if unknown:
            raise ValueError(
                f"selected_applications: {unknown} not in applications "
                f"({list(self.applications)})"
            )
        return self

    @model_validator(mode="after")
    def _builds_resolve(self) -> RunConfig:
        # Every variant resolves once at load, so an overlay key the section
        # does not have fails from_yaml, not the build stage.
        known = {v for section in self.applications.values() for v in section.builds}
        unknown = [n for n in self.selected_builds if n not in known]
        if unknown:
            raise ValueError(
                f"selected_builds: {unknown} not among the applications' builds "
                f"({sorted(known)})"
            )
        for name, section in self.applications.items():
            for variant in section.builds:
                try:
                    section.with_build(variant)
                except ValueError as exc:
                    raise ValueError(f"applications.{name}.{exc}") from None
        return self

    def _sections_as_built(self) -> list[tuple[str, ApplicationRunSection]]:
        """Every section this config builds, labelled for an error message:
        the section itself, or each of its variants instead."""
        built: list[tuple[str, ApplicationRunSection]] = []
        for name, section in self.applications.items():
            if not section.builds:
                built.append((f"applications.{name}", section))
                continue
            built += [
                (f"applications.{name}.builds.{variant}", variant_section)
                for variant in section.builds
                for variant_section in [
                    self.for_build(name, variant).applications[name]
                ]
            ]
        return built

    @model_validator(mode="after")
    def _compute_builds_have_somewhere_to_go(self) -> RunConfig:
        # build_node: compute is an srun from a login node: it needs the
        # slurm runtime (docker builds in the container; native is already
        # inside an allocation) and the slurm: section's account to charge.
        for where, section in self._sections_as_built():
            if section.build_node is not BuildNode.COMPUTE:
                continue
            if self.runtime is not Runtime.SLURM:
                raise ValueError(
                    f"{where}: build_node: compute needs the slurm "
                    f"runtime; {self.platform.value} runs {self.runtime.value} "
                    "(there is no compute node to build on)"
                )
            if self.slurm is None:
                raise ValueError(
                    f"{where}: build_node: compute needs a slurm: "
                    "section (the account, QOS and partition of the compile srun)"
                )
        return self

    @model_validator(mode="after")
    def _sections_fit_the_runtime(self) -> RunConfig:
        # The rules a section cannot check alone (they depend on how the
        # application is built and run here): the adapter's section states
        # them, the first one fails the load.
        # Under toolchains a section is judged as each toolchain builds it
        # (a flag a rule requires may be the toolchain's).
        builds: list[tuple[str, RunConfig]] = [("", self)]
        if self.toolchains:
            builds = [
                (f"toolchains.{name}: ", self.for_toolchain(name))
                for name in self.toolchains
            ]
        for prefix, config in builds:
            for where, section in config._sections_as_built():
                problems = section.runtime_problems(self.runtime)
                if problems:
                    raise ValueError(f"{prefix}{where}: {problems[0]}")
        return self

    @field_validator("root_dir", "transfer_dir", mode="before")
    @classmethod
    def _expand_root(cls, value: object) -> object:
        if isinstance(value, str | Path):
            return Path(value).expanduser()
        return value

    @field_validator("applications", mode="before")
    @classmethod
    def _dispatch_sections(cls, value: object, info: ValidationInfo) -> object:
        # The registry dispatch for the run config: each raw section validates
        # against its adapter's model. The resolver arrives in the validation
        # context (from_yaml passes it); sections that are already models
        # pass through, so a dump can be re-validated without one.
        if not isinstance(value, dict):
            return value
        if not value:
            raise ValueError(
                "applications: at least one application section is required"
            )
        resolve = (info.context or {}).get("resolve_application")
        sections: dict[str, ApplicationRunSection] = {}
        for name, raw in value.items():
            if isinstance(raw, ApplicationRunSection):
                sections[name] = raw
                continue
            if resolve is None:
                raise ValueError(
                    "applications: validating a raw section needs the application "
                    "resolver; load through RunConfig.from_yaml"
                )
            app = resolve(name)
            sections[app.name] = app.run_section_model.model_validate(raw)
        return sections

    @classmethod
    def from_yaml(
        cls,
        path: Path,
        platform: Platform | None = None,
        root_dir: Path | None = None,
        overrides: Iterable[str] = (),
        *,
        resolve_application: ResolveApplication,
    ) -> RunConfig:
        """Load a run config. Precedence, highest first: `overrides`
        (KEY:PATH=VALUE entries, in order), the named flags (`platform`,
        `root_dir`), the file, then the derived defaults — hostname detection
        for the platform, the harness checkout's parent for the root — so
        the shipped templates run as-is from a checkout laid out like the
        runbook. `resolve_application` (the registry's get_application) turns
        each `applications:` key into its adapter, whose run-section model
        validates that section. A file may `extends:` another
        (load_extended): the merged result is what the rest applies to."""
        loaded, _ = load_extended(path)
        if platform is not None:
            loaded["platform"] = platform
        if root_dir is not None:
            loaded["root_dir"] = root_dir
        apply_overrides(overrides, loaded)
        loaded["platform"] = loaded.get("platform") or detect_platform()
        loaded["root_dir"] = loaded.get("root_dir") or HARNESS_ROOT.parent
        return cls.model_validate(
            loaded, context={"resolve_application": resolve_application}
        )

    @property
    def runtime(self) -> Runtime:
        return self.harness.runtime or default_runtime(self.platform)

    @property
    def module_init(self) -> ModuleInit:
        """How this platform clears its module environment (purge or reset)."""
        return module_init(self.platform)

    @property
    def uv_cache_dir(self) -> Path:
        return self.uv.cache_dir or self.root_dir / "uv-cache"

    @property
    def cartopy_data_dir(self) -> Path:
        """Where the plots' Natural Earth data is downloaded: under the run
        root, like everything else a run generates, unless named."""
        return self.data.cartopy_dir or self.root_dir / "cartopy"

    @property
    def application_names(self) -> list[str]:
        return list(self.applications)

    # ── toolchains ───────────────────────────────────────────────────────

    @property
    def toolchain(self) -> str | None:
        """The toolchain this config builds with: None for the config as
        loaded, the name for one derived by for_toolchain."""
        return self._toolchain

    def for_toolchain(self, name: str) -> RunConfig:
        """This config as one toolchain builds it: every application section
        with the toolchain overlaid (ApplicationRunSection.with_toolchain)
        and every path the build touches under <root_dir>/<name>. The
        result is for rendering and for locating roots — it is never
        written; the effective config is the config as loaded."""
        if name not in self.toolchains:
            raise ValueError(
                f"no toolchain {name!r} in the run config "
                f"(toolchains: {list(self.toolchains)})"
            )
        toolchain = self.toolchains[name]
        derived = self.model_copy(
            update={
                "applications": {
                    app: section.with_toolchain(toolchain)
                    for app, section in self.applications.items()
                }
            }
        )
        derived._toolchain = name
        derived._loaded = self._loaded or self
        return derived

    # ── build variants ───────────────────────────────────────────────────

    def build_names(self, app_name: str) -> list[str | None]:
        """The application's variants in the config's order; [None] — its
        one unnamed build — without a builds: map."""
        return list(self.applications[app_name].builds) or [None]

    def for_build(self, app_name: str, name: str | None) -> RunConfig:
        """This config with one application's section as its variant `name`
        builds it (None: the config itself): the overlay on the section as
        written, then this config's toolchain on top, so the toolchain's
        configure arguments still come last. The other applications are
        untouched. For rendering and locating roots; never written."""
        if name is None:
            return self
        loaded = self._loaded or self
        section = loaded.applications[app_name].with_build(name)
        if self._toolchain is not None:
            section = section.with_toolchain(self.toolchains[self._toolchain])
        derived = self.model_copy(
            update={"applications": {**self.applications, app_name: section}}
        )
        derived._toolchain = self._toolchain
        derived._loaded = loaded
        return derived

    @property
    def _build_root(self) -> Path:
        """Where a build's checkouts and tests roots live: the run root, or
        the toolchain's directory under it."""
        if self._toolchain is None:
            return self.root_dir
        return self.root_dir / self._toolchain

    def mpi_configure_args(self) -> list[str]:
        """The toolchain's MPI launcher as CMake configure arguments, shell
        words already quoted: srun as MPIEXEC_EXECUTABLE (its path resolved
        where the configure runs) with -n as the rank-count flag and the
        toolchain's preflags; nothing without a toolchain or for mpiexec,
        which is FindMPI's own default."""
        if self._toolchain is None:
            return []
        toolchain = self.toolchains[self._toolchain]
        if toolchain.mpi_launcher is not MpiLauncher.SRUN:
            return []
        args = [
            '-DMPIEXEC_EXECUTABLE="$(command -v srun)"',
            "-DMPIEXEC_NUMPROC_FLAG=-n",
        ]
        if toolchain.mpiexec_preflags:
            args.append(f"-DMPIEXEC_PREFLAGS={shlex.quote(toolchain.mpiexec_preflags)}")
        return args

    def checkout_dir(self, app: Application) -> Path:
        """The application's checkout: its section's clone_dir, else
        <root_dir>[/<toolchain>]/<the adapter's checkout_dirname>."""
        section = self.applications[app.name]
        return section.clone_dir or self._build_root / app.checkout_dirname

    def tests_root(self, app: Application) -> Path:
        """Where the application's own test runs leave their files: the
        output, the junit report, the test ledger, the benchmark rows —
        <root_dir>[/<toolchain>]/tests/<application>[/<variant>]."""
        root = self._build_root / CONVENTIONS.tests_dirname / app.name
        variant = self.applications[app.name].build_name
        return root if variant is None else root / variant

    def build_launcher(self, section: ApplicationRunSection, *, cpus: int) -> list[str]:
        """The command prefix of one compile step of `section`: empty on the
        login node; the slurm: section's srun under build_node: compute."""
        if section.build_node is not BuildNode.COMPUTE:
            return []
        assert self.slurm is not None  # validated at load
        return self.slurm.build_launcher(cpus=cpus, minutes=section.build_minutes)

    # ── sessions ─────────────────────────────────────────────────────────

    @property
    def session_names(self) -> list[str | None]:
        return [session.name for session in self.resolved_sessions()]

    def resolved_sessions(self) -> list[ResolvedSession]:
        """One entry per `sessions:` key in file order, each section built
        as the base section's values with the overlay's on top and
        re-validated (so an unknown key, a wrong type or a section rule
        fails with the usual message under `sessions.<name>.<section>:`);
        without `sessions:`, the one nameless session of the base sections."""
        if not self.sessions:
            return [
                ResolvedSession(
                    name=None,
                    harness=self.harness,
                    slurm=self.slurm,
                    baselines=self.baselines,
                )
            ]
        return [
            self._resolve_session(name, overlay)
            for name, overlay in self.sessions.items()
        ]

    def _resolve_session(self, name: str, overlay: SessionOverlay) -> ResolvedSession:
        if overlay.slurm and self.slurm is None:
            raise ValueError(
                f"session {name} overlays slurm: but the config has no slurm: section"
            )
        return ResolvedSession(
            name=name,
            harness=overlaid(self.harness, overlay.harness, f"sessions.{name}.harness"),
            slurm=(
                None
                if self.slurm is None
                else overlaid(self.slurm, overlay.slurm, f"sessions.{name}.slurm")
            ),
            baselines=overlaid(
                self.baselines, overlay.baselines, f"sessions.{name}.baselines"
            ),
        )

    def session_output_root(self, app: Application, session: ResolvedSession) -> str:
        """The session's --combo-output-root: harness.output_root as-is for
        one application and a nameless session; /<toolchain> under a
        toolchain; /<application> when several applications share the run;
        /<session> when the session is named —
        <output_root>[/<toolchain>][/<application>][/<session>]."""
        root = PurePosixPath(session.harness.output_root)
        if self._toolchain is not None:
            root = root / self._toolchain
        if len(self.applications) > 1:
            root = root / app.name
        if session.name is not None:
            root = root / session.name
        return str(root)

    def to_yaml(self, path: Path) -> None:
        """The effective configuration, as validated, keys in model order."""
        with open(path, "w") as f:
            yaml.dump(
                self.model_dump(mode="json"),
                f,
                default_flow_style=False,
                sort_keys=False,
            )


EXTENDS = "extends"


def _merged(base: dict[str, object], over: dict[str, object]) -> dict[str, object]:
    """`over` deep-merged onto `base`: mappings key by key, anything else
    (scalars, lists, null) replacing."""
    merged = dict(base)
    for key, value in over.items():
        below = merged.get(key)
        if isinstance(below, dict) and isinstance(value, dict):
            merged[key] = _merged(below, value)
        else:
            merged[key] = value
    return merged


def load_extended(
    path: Path, chain: tuple[Path, ...] = ()
) -> tuple[dict[str, object], tuple[Path, ...]]:
    """A run config file as one mapping, and the files read for it (this one
    first, then each it extends). With `extends: <file>`, that file is
    loaded first (relative paths against the harness checkout, where
    config/ is; it may extend another in turn) and this one is merged over
    it; the `extends` key itself is dropped, so the effective config is
    complete without it. A cycle, a missing base or a non-mapping is a
    ValueError naming the files."""
    path = path.resolve()
    if path in chain:
        raise ValueError(
            "run config extends cycle: " + " -> ".join(str(p) for p in (*chain, path))
        )
    if chain and not path.is_file():
        raise ValueError(f"{chain[-1]}: extends {path}, which does not exist")
    with open(path) as f:
        loaded = yaml.safe_load(f)
    if not isinstance(loaded, dict):
        raise ValueError(f"run config {path} is not a mapping")
    if EXTENDS not in loaded:
        return loaded, (path,)
    base = loaded.pop(EXTENDS)
    if not isinstance(base, str) or not base.strip():
        raise ValueError(
            f"{EXTENDS}: {path} must name one run config file path, got {base!r}"
        )
    base_path = Path(base).expanduser()
    if not base_path.is_absolute():
        base_path = HARNESS_ROOT / base_path
    below, files = load_extended(base_path, (*chain, path))
    return _merged(below, loaded), (path, *files)


def search_path_value(paths: list[Path]) -> str:
    """ASSAY_SUITE_CONFIG_SEARCH_PATH's os.pathsep-joined form."""
    return os.pathsep.join(str(path) for path in paths)
