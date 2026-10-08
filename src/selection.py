"""Resolving a session's suites: the one sequence both entry points run —
pytest's sessionstart and the CLI's `fetch` — from the `--suite-config`
selector and the settings to the loaded suites of one application.

Selection (`resolution.select_suites`) finds the files; this module decides
the application (explicit, or inferred from the suites, which must agree),
loads each suite through its adapter with the application root for the
`${<PREFIX>ROOT_DIR}` anchor, and rejects duplicate suite names. Every
failure is a ValueError; the callers turn it into their own error type.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, InstanceOf

from applications.base import Application, ApplicationSettings
from applications.registry import (
    DEFAULT_APPLICATION,
    get_application,
    load_suite,
    peek_application,
)
from logs import get_logger
from models.suite_config import SuiteConfig
from resolution import select_suites
from settings import Settings

logger = get_logger("selection")

# Always the final --suite-config search root, so checked-in suites stay
# selectable and the bare default needs no configuration: the whole config
# tree, <application>/<configuration>/*-suite.yaml beside the configuration
# files (discovery is by the -suite.yaml rule).
BUILTIN_CONFIG_DIR = Path(__file__).resolve().parent / "tests" / "config"


class ResolvedSuites(BaseModel):
    """The session's application, its settings, and the loaded suites in
    selection order, each with the file it came from."""

    model_config = ConfigDict(frozen=True)

    application: InstanceOf[Application]
    app_settings: InstanceOf[ApplicationSettings]
    suites: list[tuple[Path, InstanceOf[SuiteConfig]]]


def _resolve_application(
    settings: Settings, suite_paths: list[Path]
) -> tuple[Application, list[Path]]:
    """The session's one application and the suites that belong to it.
    Explicit (--application / ASSAY_APPLICATION): other applications' suites
    are dropped with a log line; none left is an error. Inferred: every
    selected suite must name the same application."""
    try:
        by_path = {path: peek_application(path) for path in suite_paths}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(str(exc)) from exc
    if settings.application is not None:
        app = get_application(settings.application)
        kept = [path for path, name in by_path.items() if name == app.name]
        for path, name in by_path.items():
            if name != app.name:
                logger.info("suite %s: application %s, skipped", path.name, name)
        if not kept:
            raise ValueError(
                f"--application {app.name}: none of the selected suites is a "
                f"{app.name} suite (selected: "
                + ", ".join(f"{path.name} [{name}]" for path, name in by_path.items())
                + ")"
            )
        return app, kept
    names = sorted(set(by_path.values()))
    if len(names) > 1:
        raise ValueError(
            "the selected suites belong to several applications ("
            + ", ".join(f"{path.name} [{name}]" for path, name in by_path.items())
            + "); a session runs one application — pass --application to choose"
        )
    return get_application(names[0]), suite_paths


def resolve_suites(settings: Settings, suite_option: str | None) -> ResolvedSuites:
    """From the selector (None: the application's default suite) to the
    loaded suites of one application. ValueError on every failure: no match,
    unknown or disagreeing applications, an unloadable suite, a missing
    config_path, or two selected suites sharing a name."""
    default_app = get_application(settings.application or DEFAULT_APPLICATION)
    option = suite_option or default_app.default_suite
    suite_paths = select_suites(
        option, [*settings.suite_config_search_path, BUILTIN_CONFIG_DIR]
    )
    app, suite_paths = _resolve_application(settings, suite_paths)
    app_settings = app.settings_model()

    suites: list[tuple[Path, SuiteConfig]] = []
    seen_names: dict[str, Path] = {}
    for suite_path in suite_paths:
        try:
            suite = load_suite(
                suite_path,
                config_search_path=settings.config_search_path,
                root_dir=app_settings.root_dir,
            )
        except FileNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        if suite.name in seen_names:
            raise ValueError(
                f"suite name {suite.name!r} is defined by both "
                f"{seen_names[suite.name]} and {suite_path}; suite names join "
                "every session artifact and must be unique among the selected suites"
            )
        seen_names[suite.name] = suite_path
        suites.append((suite_path, suite))
    return ResolvedSuites(application=app, app_settings=app_settings, suites=suites)
