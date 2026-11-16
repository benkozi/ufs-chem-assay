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


def selected_applications(
    config: RunConfig, requested: list[str] | None
) -> list[Application]:
    """The configured applications, in the config's order, narrowed to the
    requested ones ('all' selects every configured application; tokens may be
    space-separated); naming an unconfigured application is a ValueError."""
    if requested is None:
        return [get_application(name) for name in config.application_names]
    tokens = [token for item in requested for token in item.split()]
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
