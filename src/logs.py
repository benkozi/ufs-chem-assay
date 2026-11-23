"""Logging for the harness: one namespace logger, level from settings, and
the command that is logging (`run`, `watch`, `report`, ... or `pytest`) on
every line, so a captured log says which part of the harness wrote it."""

import logging

from identity import HARNESS_NAME

LOGGER_NAME = HARNESS_NAME

_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(command)s]: %(message)s"

# The process's command: one per process (the CLI's subcommand, or the
# pytest session), set before anything is logged.
_command = "-"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def set_command(command: str) -> None:
    """Name the command this process runs; every later line carries it."""
    global _command
    _command = command


def current_command() -> str:
    return _command


class _CommandFilter(logging.Filter):
    """Stamp the process's command on every record the handler emits (a
    handler filter: it sees the records of every logger in the namespace)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.command = _command
        return True


def configure_logging(level: str, command: str | None = None) -> None:
    """Configure the namespace logger once per session (idempotent); name
    the command when given."""
    if command is not None:
        set_command(command)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.addFilter(_CommandFilter())
        logger.addHandler(handler)
