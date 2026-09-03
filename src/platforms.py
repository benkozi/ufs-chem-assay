"""Where the harness runs (platform) and how the driver is launched (runtime).

The platform is a per-machine fact — detected from the hostname, overridable
through CECE_PLATFORM — and the runtime derives from it: docker locally,
a native host process everywhere else (RDHPC machines have no docker).
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


@unique
class Runtime(StrEnum):
    """How a driver invocation is spawned."""

    DOCKER = "docker"  # docker run against the cece/cece-dev image
    NATIVE = "native"  # a host process, optionally behind a launcher (srun)


# Hostname patterns (fullmatch) per platform. Ursa login nodes are ufe01-04;
# the compute-node form is a first-run item recorded in the design doc.
_HOSTNAME_PATTERNS: dict[Platform, re.Pattern[str]] = {
    Platform.URSA: re.compile(r"ufe\d+(\..*)?"),
}


def detect_platform(hostname: str | None = None) -> Platform:
    """The platform whose pattern matches the hostname; LOCAL otherwise."""
    name = socket.gethostname() if hostname is None else hostname
    for platform, pattern in _HOSTNAME_PATTERNS.items():
        if pattern.fullmatch(name):
            return platform
    return Platform.LOCAL


def default_runtime(platform: Platform) -> Runtime:
    """docker on a laptop, native anywhere else."""
    return Runtime.DOCKER if platform is Platform.LOCAL else Runtime.NATIVE
