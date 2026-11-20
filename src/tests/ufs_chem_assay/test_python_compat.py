"""Source rules that keep the harness running on the oldest Python it
supports (pyproject's requires-python), whatever Python it is developed on."""

import ast
import re
import tomllib
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
_REPO = _SRC.parent


def _modules() -> list[Path]:
    return sorted(
        path
        for path in _SRC.rglob("*.py")
        if "tests" not in path.relative_to(_SRC).parts
    )


def _enum_classes(trees: dict[Path, ast.Module]) -> set[str]:
    """The names of every Enum subclass the harness defines, by the names of
    their bases: the standard ones, or another Enum of the harness."""
    found = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}
    grew = True
    while grew:
        grew = False
        for tree in trees.values():
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name not in found:
                    bases = {
                        base.id
                        if isinstance(base, ast.Name)
                        else getattr(base, "attr", "")
                        for base in node.bases
                    }
                    if bases & found:
                        found.add(node.name)
                        grew = True
    return found


def test_no_membership_test_against_an_enum_class() -> None:
    # `value in SomeEnum` with a plain value (a str from squeue, say) raises
    # TypeError before Python 3.12 and answers by value from 3.12 on. The
    # harness supports 3.11 (Gaea C7's system Python), so test membership
    # against a set of the members instead: StrEnum members hash and compare
    # as their values.
    trees = {path: ast.parse(path.read_text(), str(path)) for path in _modules()}
    enums = _enum_classes(trees)
    offenders = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for op, right in zip(node.ops, node.comparators, strict=True):
                name = (
                    right.id
                    if isinstance(right, ast.Name)
                    else getattr(right, "attr", None)
                )
                if (
                    isinstance(op, ast.In | ast.NotIn) and name in enums
                ):  # codespell:ignore
                    offenders.append(
                        f"{path.relative_to(_SRC)}:{node.lineno}: in {name}"
                    )
    assert offenders == []


def test_the_harness_python_is_pinned_for_uv() -> None:
    # .python-version picks the interpreter uv builds the harness venv with,
    # whatever else is on PATH. On an HPC machine that matters: the system
    # Python (3.11 on Gaea C7) and a module stack's Python (spack-stack
    # 2.2's python/3.13) would otherwise satisfy requires-python, and a venv
    # built on a module's Python breaks once that module is unloaded. Neither
    # satisfies the pin, so uv uses a Python it installs itself
    # (UV_PYTHON_INSTALL_DIR). The pin is the version CI already runs, so CI
    # and a laptop keep their interpreters.
    pin = (_REPO / ".python-version").read_text().strip()
    assert pin == "3.14"
    requires = tomllib.loads((_REPO / "pyproject.toml").read_text())["project"][
        "requires-python"
    ]
    assert requires == ">=3.11"  # still supported: see the Enum rule above
    dockerfile = (_REPO / "Dockerfile").read_text()
    assert f"FROM ghcr.io/astral-sh/uv:python{pin}-" in dockerfile
    integration = (_REPO / ".github" / "workflows" / "integration.yaml").read_text()
    assert f"python-version: '{pin}'" in integration
    # The runbooks let the pin choose: no per-command interpreter override.
    for runbook in sorted((_REPO / "docs").glob("*.md")):
        assert re.search(r"UV_PYTHON=\d", runbook.read_text()) is None, runbook.name
