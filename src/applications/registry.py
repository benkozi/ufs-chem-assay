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

from applications.base import Application
from applications.cece.application import CeceApplication
from models.suite_config import SuiteConfig

REGISTRY: dict[str, Application] = {app.name: app for app in (CeceApplication(),)}
DEFAULT_APPLICATION = "cece"


def get_application(name: object) -> Application:
    """The registered adapter, or ValueError naming the registered ones."""
    if isinstance(name, str) and name in REGISTRY:
        return REGISTRY[name]
    raise ValueError(
        f"unknown application {name!r}; registered applications: {sorted(REGISTRY)}"
    )


def _application_key(raw: object) -> object:
    if isinstance(raw, dict):
        return raw.get("application", DEFAULT_APPLICATION)
    return DEFAULT_APPLICATION


def peek_application(path: Path) -> str:
    """The application a suite file names (default: cece), without loading
    the suite — the session needs it before the application's settings
    (and so the root the suite's config_path may anchor on) exist."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return get_application(_application_key(raw)).name


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
    app = get_application(_application_key(raw))
    suite = app.suite_model.model_validate(raw)
    suite.resolve_config_path(
        path,
        config_search_path=config_search_path,
        root_dir=root_dir,
        root_dir_token=app.root_dir_token,
    )
    return suite
