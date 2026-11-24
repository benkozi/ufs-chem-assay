"""The Lmod block: the shell lines that put an application's module
environment in place — initialise Lmod, clear what the calling shell had
loaded, export what the modulefile reads, name the module directories, load,
list. One rendering serves the stage scripts (the build, cli/stages.py) and
the job scripts (the driver, runner.py), so a job loads exactly the
environment the build did. How to clear is a platform fact
(platforms.MODULE_INIT); everything else comes from the run config or the
application's settings.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

from platforms import ModuleInit

# Batch shells and non-login shells do not always carry the `module`
# function; guarded so the line is harmless where Lmod is absent.
LMOD_INIT = 'if [ -n "${MODULESHOME:-}" ]; then source "$MODULESHOME/init/bash"; fi'


def module_lines(
    *,
    init: ModuleInit,
    env: Mapping[str, str],
    paths: Sequence[Path],
    modulefile: str,
) -> list[str]:
    """The lines that load `modulefile`: Lmod's init, `module <init>`, one
    `export` per `env` entry (before the load — the modulefile reads them:
    version pins, an environment root), one `module use` per directory in
    `paths` (in order), `module load`, `module list`. Values and paths are
    shell-quoted."""
    return [
        LMOD_INIT,
        f"module {init.value}",
        *(f"export {name}={shlex.quote(value)}" for name, value in env.items()),
        *(f"module use {shlex.quote(str(path))}" for path in paths),
        f"module load {shlex.quote(modulefile)}",
        "module list",
    ]
