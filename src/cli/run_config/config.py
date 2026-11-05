"""RunConfig: the whole file as one model — loading with the platform and
root resolved and overrides merged (`from_yaml`), the derived values
(runtime, cache, checkouts), the sessions resolved, the effective
configuration written back."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

import yaml
from pydantic import (
    Field,
    SerializeAsAny,
    ValidationInfo,
    field_validator,
    model_validator,
)

from applications.base import Application, ApplicationRunSection
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
from platforms import Platform, Runtime, default_runtime, detect_platform

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
            if session.harness.submit_only and self.runtime is not Runtime.SLURM:
                where = "" if session.name is None else f"sessions.{session.name}: "
                raise ValueError(
                    f"{where}submit_only needs the slurm runtime; "
                    f"{self.platform.value} runs {self.runtime.value} "
                    "(--override harness:submit_only=false to run this config here)"
                )
        return self

    @field_validator("root_dir", mode="before")
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
        validates that section."""
        with open(path) as f:
            loaded = yaml.safe_load(f)
        if not isinstance(loaded, dict):
            raise ValueError(f"run config {path} is not a mapping")
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
    def uv_cache_dir(self) -> Path:
        return self.uv.cache_dir or self.root_dir / "uv-cache"

    @property
    def application_names(self) -> list[str]:
        return list(self.applications)

    def checkout_dir(self, app: Application) -> Path:
        """The application's checkout: its section's clone_dir, else
        <root_dir>/<the adapter's checkout_dirname>."""
        section = self.applications[app.name]
        return section.clone_dir or self.root_dir / app.checkout_dirname

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
        if "runtime" in overlay.harness:
            raise ValueError(
                f"sessions.{name}.harness: runtime is config-wide (the platform's "
                "runtime is one thing); it cannot be overlaid per session"
            )
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
        one application and a nameless session; /<application> when several
        applications share the run; /<session> when the session is named —
        <output_root>[/<application>][/<session>]."""
        root = PurePosixPath(session.harness.output_root)
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


def search_path_value(paths: list[Path]) -> str:
    """ASSAY_SUITE_CONFIG_SEARCH_PATH's os.pathsep-joined form."""
    return os.pathsep.join(str(path) for path in paths)
