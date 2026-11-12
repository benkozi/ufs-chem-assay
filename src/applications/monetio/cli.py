"""monetio's side of `ufs-chem-assay run`: its run-config section and the
source and build stage bodies (clone the repository and install it in an
isolated `.venv` inside the checkout via `uv`)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from applications.base import ApplicationRunSection, RunConfigView, Stage
from cli.stages import LMOD_INIT, q
from platforms import Runtime


class MonetioRunSection(ApplicationRunSection):
    extras: list[str] = Field(
        default_factory=list,
        description="Optional pyproject extras installed into the checkout's `.venv`",
    )


def _as_monetio(section: ApplicationRunSection) -> MonetioRunSection:
    assert isinstance(section, MonetioRunSection), type(section)
    return section


def _module_block(checkout: Path, section: MonetioRunSection) -> list[str]:
    assert section.modulefile is not None
    return [
        LMOD_INIT,
        "module purge",
        f"module use {q(checkout)}/modulefiles",
        f"module load {q(section.modulefile)}",
        "module list",
    ]


def _source(checkout: Path, section: MonetioRunSection) -> list[str]:
    clone = q(checkout)
    ref = q(section.ref)
    lines = [
        f"if [ ! -d {clone}/.git ]; then",
        f"  git clone --branch {ref} {q(section.git_url)} {clone}",
    ]
    if section.update_source:
        lines += [
            "else",
            f"  git -C {clone} fetch origin",
            f"  git -C {clone} checkout {ref}",
            f"  git -C {clone} pull --ff-only origin {ref}",
        ]
    else:
        lines += ["else", f'  echo "using existing clone at {clone} as-is"']
    lines += [
        "fi",
        f"git -C {clone} log -1 --oneline",
    ]
    return lines


def _build(
    config: RunConfigView, checkout: Path, section: MonetioRunSection
) -> list[str]:
    if config.runtime is Runtime.DOCKER:
        image = section.docker_image or "monetio/monetio-dev"
        return [f"docker image inspect {q(image)} >/dev/null"]
    venv = checkout / ".venv"
    python_bin = venv / "bin" / "python"
    monetio_bin = venv / "bin" / "monetio"
    target = (
        f"{checkout}[{','.join(section.extras)}]" if section.extras else str(checkout)
    )
    modules = _module_block(checkout, section) if section.modulefile is not None else []
    return [
        *modules,
        f"uv venv --allow-existing {q(venv)}",
        f"uv pip install --python {q(python_bin)} -e {q(target)}",
        f"{q(monetio_bin)} --help >/dev/null",
    ]


def stage_lines(
    stage: Stage,
    config: RunConfigView,
    section: ApplicationRunSection,
    checkout: Path,
) -> list[str]:
    monetio = _as_monetio(section)
    if stage is Stage.SOURCE:
        return _source(checkout, monetio)
    if stage is Stage.BUILD:
        return _build(config, checkout, monetio)
    if stage is Stage.DATA:
        return []
    raise ValueError(f"the {stage.value} stage is shared, not the adapter's")
