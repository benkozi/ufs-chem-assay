"""Sessions: the `sessions:` overlay as written, the session as the stages
render it (its name and its three effective sections), and the overlay
rule — base values with the overlay's on top, re-validated as the section."""

from __future__ import annotations

import re
from typing import TypeVar

from pydantic import Field

from cli.run_config.sections import BaselinesSection, HarnessSection, SlurmSection
from models.base import StrictModel

# Session names become directory and script names: lower-case, digits, dashes.
SESSION_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class SessionOverlay(StrictModel):
    """One `sessions:` entry: what this session changes in the harness:,
    slurm: and baselines: sections, one level deep — an overlaid key
    replaces the base value whole (`env` is replaced, not merged). The other
    sections (applications:, data:, outputs:, uv:) are run-wide: the
    checkout, the inputs, the stores and the cache are the same whichever
    suite runs."""

    harness: dict[str, object] = Field(
        default_factory=dict,
        description="harness: keys this session overrides",
    )
    slurm: dict[str, object] = Field(
        default_factory=dict,
        description="slurm: keys this session overrides; needs a slurm: section in the file",
    )
    baselines: dict[str, object] = Field(
        default_factory=dict,
        description="baselines: keys this session overrides",
    )


class ResolvedSession(StrictModel):
    """A session as the stages render it: its name (None for the implicit
    session of a config without sessions:) and its three effective sections."""

    name: str | None = Field(
        description="The sessions: key; None for the nameless implicit session"
    )
    harness: HarnessSection = Field(description="The effective harness: section")
    slurm: SlurmSection | None = Field(
        description="The effective slurm: section; None when the file has none"
    )
    baselines: BaselinesSection = Field(description="The effective baselines: section")


_Section = TypeVar("_Section", bound=StrictModel)


def overlaid(base: _Section, patch: dict[str, object], where: str) -> _Section:
    """The base section's values with the overlay's on top, re-validated as
    the section — so an unknown key, a wrong type or the section's own
    rule fails with the usual message, prefixed by the session's path."""
    try:
        return type(base).model_validate(base.model_dump() | patch)
    except ValueError as exc:  # ValidationError is a ValueError
        raise ValueError(f"{where}: {exc}") from exc
