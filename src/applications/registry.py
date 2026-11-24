"""The application registry: name -> adapter, and the loaders that dispatch
on a document's `application:` value.

A plain dict, as the spike decided; entry-point discovery would replace the
dict with a loader and change nothing else. Lives beside — not in —
applications/__init__.py so that importing applications.base (which the
shared models do) never imports an adapter (which import the shared models).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from applications.base import Application, Stage
from applications.catchem.application import CatchemApplication
from applications.cece.application import CeceApplication
from applications.kokkos_smoke.application import KokkosSmokeApplication
from applications.monetio.application import MonetioApplication
from models.suite_config import SuiteConfig

# The applications with a driver the harness sweeps (cece, monetio), then
# the ones it only builds and tests (catchem, kokkos-smoke): each adapter's
# `stages` says which.
REGISTRY: dict[str, Application] = {
    app.name: app
    for app in (
        CeceApplication(),
        MonetioApplication(),
        CatchemApplication(),
        KokkosSmokeApplication(),
    )
}


def get_application(name: object) -> Application:
    """The registered adapter, or ValueError naming the registered ones."""
    if isinstance(name, str) and name in REGISTRY:
        return REGISTRY[name]
    raise ValueError(
        f"unknown application {name!r}; registered applications: {sorted(REGISTRY)}"
    )


def driver_application(name: object) -> Application:
    """The registered adapter of an application that has a driver suite —
    what a suite file, a pytest session or `fetch` may name. NoDriverSuite
    for one that is only built and tested; ValueError for an unknown name."""
    app = get_application(name)
    if Stage.HARNESS not in app.stages:
        raise app.no_driver_suite()
    return app


def driver_application_names() -> list[str]:
    """The registered applications a session can run, sorted."""
    return sorted(name for name, app in REGISTRY.items() if Stage.HARNESS in app.stages)


def _application_key(raw: object) -> object:
    default = SuiteConfig.model_fields["application"].default
    if isinstance(raw, dict):
        return raw.get("application", default)
    return default


def peek_application(path: Path) -> str:
    """The application a suite file names (default: cece), without loading
    the suite — the session needs it before the application's settings
    (and so the root the suite's config_path may anchor on) exist."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return driver_application(_application_key(raw)).name


def load_suite(
    path: Path,
    *,
    config_search_path: Path | None = None,
    root_dir: Path | None = None,
) -> SuiteConfig:
    """Load a suite file through the adapter its `application:` names
    (default cece) and resolve its config_path (see
    SuiteConfig.resolve_config_path)."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    app = driver_application(_application_key(raw))
    suite = app.suite_model.model_validate(raw)
    suite.resolve_config_path(
        path,
        config_search_path=config_search_path,
        root_dir=root_dir,
        root_dir_token=app.root_dir_token,
    )
    return suite
