"""The run config: one YAML file describing where and how a run happens —
every harness setting, every application's section, every pytest option —
with command-line overrides merged in before validation.

A run config describes one or several *sessions*: `sessions:` is a map of
session name to an overlay on the file's harness:/slurm:/baselines:
sections, one level deep (an overlaid key replaces the base value whole).
Each session is one pytest session with its own output root
(<output_root>/<session>) and job geometry. No `sessions:` means one
implicit, nameless session with today's paths and script names.

The package: `sections` (the file's sections as models), `sessions` (the
overlay, the resolved session and the overlay rule) and `config`
(RunConfig: loading, overrides, the derived values). Everything a caller
needs is re-exported here."""

from __future__ import annotations

from cli.run_config.config import ResolveApplication, RunConfig, search_path_value
from cli.run_config.sections import (
    BaselinesSection,
    DataSection,
    HarnessSection,
    OutputsSection,
    SlurmSection,
    UvSection,
)
from cli.run_config.sessions import (
    SESSION_NAME,
    ResolvedSession,
    SessionOverlay,
    overlaid,
)
from identity import HARNESS_ROOT

__all__ = [
    "HARNESS_ROOT",
    "SESSION_NAME",
    "BaselinesSection",
    "DataSection",
    "HarnessSection",
    "OutputsSection",
    "ResolveApplication",
    "ResolvedSession",
    "RunConfig",
    "SessionOverlay",
    "SlurmSection",
    "UvSection",
    "overlaid",
    "search_path_value",
]
