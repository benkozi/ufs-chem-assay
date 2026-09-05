"""Run-config templates for the `ufs-chem-assay run` tests: load a checked-in
template, tweak it, write it to tmp_path."""

from pathlib import Path

import yaml

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
    dotted (`section.key`, or a top-level key), REMOVE deletes one, and
    `root_dir` replaces the template's (None keeps it). Written to tmp_path."""
    data = yaml.safe_load((TEMPLATES_DIR / template).read_text())
    assert isinstance(data, dict)
    if root_dir is not None:
        data["root_dir"] = str(root_dir)
    for dotted, value in (overrides or {}).items():
        section, _, key = dotted.partition(".")
        target = data[section] if key else data
        name = key or section
        if value is REMOVE:
            del target[name]
        else:
            target[name] = value
    path = tmp_path / f"run-{template}"
    path.write_text(yaml.safe_dump(data, sort_keys=False))  # keep env order
    return path
