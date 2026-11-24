"""Run-config templates for the `ufs-chem-assay run` tests: load a checked-in
template, tweak it, write it to tmp_path, load it with the real registry."""

import copy
import re
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
            # A copy: a later dotted override descends into this value, and
            # must not edit the caller's (often module-level) object.
            target[name] = copy.deepcopy(value)
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


def uncommented_sessions(template: Path) -> str:
    """The template's text with its commented `applications:` or `sessions:`
    example entries enabled: inside those blocks a line commented at the
    entry's indentation (`  # key: ...`) loses its `# `. The shipped example
    must stay a valid overlay/section, and this is how the tests load it."""
    commented_key = re.compile(r"^  # (?: {2})*[A-Za-z0-9_.*-]+:(?:\s|$)")
    lines = template.read_text().splitlines(keepends=True)
    out: list[str] = []
    in_block = False
    for line in lines:
        if not line.startswith((" ", "#", "\n")):
            in_block = line.startswith(("sessions:", "applications:"))
        if in_block and commented_key.match(line):
            line = "  " + line[4:]
        out.append(line)
    return "".join(out)


# Two compiler suites for the toolchain tests: what a kokkos run config
# declares. The Ursa template's own modulefile comes off the application
# section, since under a toolchains: map the module keys are the toolchain's.
TOOLCHAINS: dict[str, dict[str, object]] = {
    "oneapi": {
        "modulefile": "ufs_ursa.oneapi",
        "modulepath": ["modulefiles", "/envs/oneapi/modules/Core"],
        "module_env": {"kokkos_ver": "4.5.01"},
        "toolchain_tools": ["mpiicx", "mpiicpx", "mpiifx", "cmake", "ctest"],
        "cmake_args": ["-DCMAKE_CXX_FLAGS=-fp-model=precise"],
        "mpi_launcher": "mpiexec",
        "tests_env": {"I_MPI_HYDRA_BOOTSTRAP": "fork"},
        "configure_gates": ["AXIS: ArborX .* found via find_package"],
    },
    "gnu": {
        "modulefile": "ufs_ursa.gnu",
        "modulepath": ["modulefiles", "/envs/gcc/modules/Core"],
        "toolchain_tools": ["mpicc", "mpic++", "mpifort", "cmake", "ctest"],
        "mpi_launcher": "srun",
        "tests_env": {"OMPI_MCA_plm": "isolated"},
        "configure_gates": ["AXIS: ArborX .* found via find_package"],
    },
}
TOOLCHAIN_OVERRIDES: dict[str, object] = {
    "toolchains": TOOLCHAINS,
    "applications.cece.modulefile": None,
}
