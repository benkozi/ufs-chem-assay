"""One module per subcommand. Each exposes `register(subparsers, name)`,
which adds its argparse parser under `name`, and `handler(args) -> int`,
the subcommand itself; cli/main.py maps the `Command` enum to the modules
and dispatches. Nothing here is imported by the machinery modules
(run_config, stages, shell, sessions, run_report), only the other way."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    import argparse

# argparse's subparsers action: what `register` adds a parser to (a string
# alias: the class is generic for the type checker only).
Subparsers: TypeAlias = "argparse._SubParsersAction[argparse.ArgumentParser]"
