"""Run-config templates for the `ufs-chem-assay run` tests: load a checked-in
template, tweak it, write it to tmp_path, load it with the real registry."""

from collections.abc import Iterable
from pathlib import Path

import yaml

from applications.registry import get_application
from cli.run_config import RunConfig
from platforms import Platform

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "config"

# Sentinel for run_config_file overrides: delete the key instead of setting it.
REMOVE = object()


def run_config_file(
    tmp_path: Path,
    template: str = "ursa.yaml",
    overrides: dict[str, object] | None = None,
    root_dir: Path | None = None,
) -> Path:
    """A run config derived from a checked-in template: `overrides` keys are
    dotted paths (`section.key`, `applications.cece.ref`, or a top-level
    key), REMOVE deletes one, and `root_dir` replaces the template's (None
    keeps it). Written to tmp_path."""
    data = yaml.safe_load((TEMPLATES_DIR / template).read_text())
    assert isinstance(data, dict)
    if root_dir is not None:
        data["root_dir"] = str(root_dir)
    for dotted, value in (overrides or {}).items():
        *parents, name = dotted.split(".")
        target = data
        for parent in parents:
            target = target.setdefault(parent, {})
        if value is REMOVE:
            del target[name]
        else:
            target[name] = value
    path = tmp_path / f"run-{template}"
    path.write_text(yaml.safe_dump(data, sort_keys=False))  # keep env order
    return path


def load_run_config(
    path: Path,
    platform: Platform | None = None,
    root_dir: Path | None = None,
    overrides: Iterable[str] = (),
) -> RunConfig:
    """RunConfig.from_yaml with the registry's get_application resolving the
    adapters, as `ufs-chem-assay run` does (cli/main.py)."""
    return RunConfig.from_yaml(
        path,
        platform=platform,
        root_dir=root_dir,
        overrides=overrides,
        resolve_application=get_application,
    )
