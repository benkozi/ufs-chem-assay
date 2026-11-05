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


def selected_applications(
    config: RunConfig, requested: list[str] | None
) -> list[Application]:
    """The configured applications, in the config's order, narrowed to the
    requested ones; naming an unconfigured application is an error."""
    if requested is not None:
        unknown = sorted(set(requested) - set(config.applications))
        if unknown:
            raise ValueError(
                f"--application {unknown} not in the run config's applications "
                f"({config.application_names})"
            )
    return [
        get_application(name)
        for name in config.application_names
        if requested is None or name in requested
    ]
