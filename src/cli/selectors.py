"""The selection helpers the subcommands share: the requested stages,
sessions and applications of a run config, in the config's order, with an
unconfigured name a ValueError."""

from __future__ import annotations

from applications.base import Application, Stage
from applications.registry import get_application
from cli.run_config import ResolvedSession, RunConfig


def selected_stages(requested: list[Stage] | None) -> list[Stage]:
    """The requested stages in canonical order; all but resume when none
    given — a plain `run` must not block on the evaluation it just deferred."""
    if requested is None:
        return [stage for stage in Stage if stage is not Stage.RESUME]
    return [stage for stage in Stage if stage in requested]


def selected_sessions(
    config: RunConfig, requested: list[str] | None
) -> list[ResolvedSession]:
    """The configured sessions, in the config's order, narrowed to the
    requested names; naming an unconfigured session is an error, as is
    naming any on a config without sessions:."""
    sessions = config.resolved_sessions()
    if requested is None:
        return sessions
    if not config.sessions:
        raise ValueError(
            f"--session {requested}: the run config has no sessions: map "
            "(it is one nameless session)"
        )
    unknown = sorted(set(requested) - set(config.sessions))
    if unknown:
        raise ValueError(
            f"--session {unknown} not in the run config's sessions "
            f"({list(config.sessions)})"
        )
    return [session for session in sessions if session.name in requested]


def toolchain_configs(
    config: RunConfig, requested: list[str] | None
) -> list[RunConfig]:
    """The config as each selected toolchain builds it, in the config's
    order. Without a toolchains: map: the config itself (naming a toolchain
    is then an error). With one: the requested names ('all' selects every
    toolchain; tokens may be space-separated); with none requested, the
    selection the last `run` recorded, else the map's only entry — several
    unrecorded ones must be chosen from."""
    names = list(config.toolchains)
    if not names:
        if requested:
            raise ValueError(
                f"--toolchain {requested}: the run config has no toolchains: map"
            )
        return [config]
    if requested is None:
        if config.selected_toolchains:
            chosen = list(config.selected_toolchains)
        elif len(names) == 1:
            chosen = names
        else:
            raise ValueError(
                f"--toolchain is required: the run config names several "
                f"({', '.join(names)}); 'all' selects every one"
            )
    else:
        tokens = [token for item in requested for token in item.split()]
        if not tokens:
            raise ValueError(
                "--toolchain requires at least one toolchain name or 'all'"
            )
        unknown = sorted(set(tokens) - {"all"} - set(names))
        if unknown:
            raise ValueError(
                f"--toolchain {unknown} not in the run config's toolchains ({names})"
            )
        chosen = names if "all" in tokens else tokens
    return [config.for_toolchain(name) for name in names if name in chosen]


def one_toolchain_config(config: RunConfig, requested: str | None) -> RunConfig:
    """The config as the one toolchain a per-build command works on builds
    it: the named one; without a name, the only one there is to choose (no
    toolchains: map, a single entry, a recorded selection of one)."""
    builds = toolchain_configs(config, None if requested is None else [requested])
    if len(builds) != 1:
        names = ", ".join(str(build.toolchain) for build in builds)
        raise ValueError(
            f"--toolchain is required: the run selected several ({names}), and "
            "this command works on one"
        )
    return builds[0]


def _tokens(requested: list[str]) -> list[str]:
    return [token for item in requested for token in item.split()]


def build_configs(
    config: RunConfig, app: Application, requested: list[str] | None
) -> list[RunConfig]:
    """The config as each selected variant of `app` builds it, in the
    config's order; [config] for an application without variants. The
    selection names variants ('all' selects every one; tokens may be
    space-separated); with none requested, the selection the last `run`
    recorded, else every variant. A name this application has no variant of
    selects nothing of it (check_builds refuses a name no application has)."""
    names = [name for name in config.build_names(app.name) if name is not None]
    if not names:
        return [config]
    if requested is None:
        chosen = list(config.selected_builds) or names
    else:
        tokens = _tokens(requested)
        chosen = names if "all" in tokens else tokens
    return [config.for_build(app.name, name) for name in names if name in chosen]


def check_builds(
    config: RunConfig, applications: list[Application], requested: list[str] | None
) -> list[str]:
    """The --build names, validated against the selected applications'
    variants: every name must be one of them ('all' always is). Returns the
    selection to record ([] for every variant)."""
    if requested is None:
        return []
    tokens = _tokens(requested)
    if not tokens:
        raise ValueError("--build requires at least one build name or 'all'")
    known = {
        name
        for app in applications
        for name in config.build_names(app.name)
        if name is not None
    }
    unknown = sorted(set(tokens) - {"all"} - known)
    if unknown:
        raise ValueError(
            f"--build {unknown} is no build of the selected applications "
            f"({', '.join(sorted(known)) or 'none has builds:'})"
        )
    return [] if "all" in tokens else [n for n in tokens if n in known]


def one_build_config(
    config: RunConfig, app: Application, requested: str | None
) -> RunConfig:
    """The config as the one variant of `app` a per-build command works on
    builds it: the named one; without a name, the only one there is (no
    builds: map, or a single variant)."""
    names = [name for name in config.build_names(app.name) if name is not None]
    if requested is None:
        if len(names) > 1:
            raise ValueError(
                f"--build is required: {app.name} has several builds "
                f"({', '.join(names)}), and this command works on one"
            )
        return config.for_build(app.name, names[0] if names else None)
    if requested not in names:
        raise ValueError(
            f"--build {requested!r} is no build of {app.name} "
            f"({', '.join(names) or 'it has no builds:'})"
        )
    return config.for_build(app.name, requested)


def selected_applications(
    config: RunConfig, requested: list[str] | None
) -> list[Application]:
    """The configured applications, in the config's order, narrowed to the
    requested ones ('all' selects every configured application; tokens may be
    space-separated); naming an unconfigured application is a ValueError.
    With none requested: the ones the last `run` recorded (an effective
    config's selected_applications), else every configured application."""
    if requested is None:
        names = config.selected_applications or config.application_names
        return [
            get_application(name) for name in config.application_names if name in names
        ]
    tokens = _tokens(requested)
    if not tokens:
        raise ValueError(
            "--application requires at least one application name or 'all'"
        )
    unknown = sorted(set(tokens) - {"all"} - set(config.applications))
    if unknown:
        raise ValueError(
            f"--application {unknown} not in the run config's applications "
            f"({config.application_names})"
        )
    if "all" in tokens:
        return [get_application(name) for name in config.application_names]
    return [
        get_application(name) for name in config.application_names if name in tokens
    ]
