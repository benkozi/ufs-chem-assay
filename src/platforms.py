"""Where the harness runs (platform) and how the driver is launched (runtime).

The platform is a per-machine fact — detected from the hostname, overridable
through ASSAY_PLATFORM — and the runtime derives from it: docker locally,
a Slurm job per driver call everywhere else (RDHPC machines have no docker).
Detection is a convenience, never load-bearing: run configs for a machine
set the platform explicitly.
"""

import re
import socket
from enum import StrEnum, unique


@unique
class Platform(StrEnum):
    """Machines the harness knows how to run on."""

    LOCAL = "local"
    URSA = "ursa"
    GAEA_C6 = "gaea-c6"
    GAEA_C7 = "gaea-c7"


@unique
class Runtime(StrEnum):
    """How a driver invocation is spawned."""

    DOCKER = "docker"  # docker run against the application's image
    NATIVE = "native"  # a host process, optionally behind a launcher (srun)
    SLURM = "slurm"  # one `sbatch --wait` job per driver call, from a login node


@unique
class Accounting(StrEnum):
    """How a platform reports a finished job's elapsed time, queue wait,
    peak memory, state and exit code — the benchmark columns that need the
    scheduler's help."""

    SACCT = "sacct"  # Slurm accounting, queried from the node the harness runs on


# Platforms whose accounting the harness knows how to ask for; a platform
# absent here (local) records the harness's own clock and null accounting
# columns. Gaea C6/C7 join once their sacct is confirmed to answer the same
# columns (one entry here).
ACCOUNTING: dict[Platform, Accounting] = {
    Platform.URSA: Accounting.SACCT,
}


@unique
class ModuleInit(StrEnum):
    """How a platform clears the module environment before an application's
    modulefile is loaded: the `module` subcommand."""

    PURGE = "purge"  # unload everything
    RESET = "reset"  # back to the system default set


# `module purge` strips Cray's craype/PrgEnv machinery on Gaea, which the
# compiler wrappers (cc, CC, ftn) need: reset there. A platform absent here
# purges (module_init below).
MODULE_INIT: dict[Platform, ModuleInit] = {
    Platform.URSA: ModuleInit.PURGE,
    Platform.GAEA_C6: ModuleInit.RESET,
    Platform.GAEA_C7: ModuleInit.RESET,
}


def module_init(platform: Platform) -> ModuleInit:
    """How `platform` clears its module environment; purge unless
    MODULE_INIT says otherwise."""
    return MODULE_INIT.get(platform, ModuleInit.PURGE)


# Hostname patterns (fullmatch) per platform. Ursa login nodes are ufe01-04;
# the compute-node form is a first-run item recorded in the design doc. Gaea
# login nodes are gaea<cluster digit><node digits>: C6 is gaea61-68; C7 is
# gaea7N by the same scheme, to be confirmed on a first login there. A
# hostname that matches nothing is LOCAL, and every Gaea run config names its
# platform explicitly, so a wrong pattern costs nothing but the convenience.
_HOSTNAME_PATTERNS: dict[Platform, re.Pattern[str]] = {
    Platform.URSA: re.compile(r"ufe\d+(\..*)?"),
    Platform.GAEA_C6: re.compile(r"gaea6\d+(\..*)?"),
    Platform.GAEA_C7: re.compile(r"gaea7\d+(\..*)?"),
}


def detect_platform(hostname: str | None = None) -> Platform:
    """The platform whose pattern matches the hostname; LOCAL otherwise."""
    name = socket.gethostname() if hostname is None else hostname
    for platform, pattern in _HOSTNAME_PATTERNS.items():
        if pattern.fullmatch(name):
            return platform
    return Platform.LOCAL


def default_runtime(platform: Platform) -> Runtime:
    """docker on a laptop; one Slurm job per driver call on an RDHPC machine
    (native — a direct host process — is the explicit choice inside an
    allocation)."""
    return Runtime.DOCKER if platform is Platform.LOCAL else Runtime.SLURM
