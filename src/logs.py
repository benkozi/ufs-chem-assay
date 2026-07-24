"""Logging for the combo test runner: one namespace logger, level from settings."""

import logging

LOGGER_NAME = "combo_test_runner"

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def configure_logging(level: str) -> None:
    """Configure the namespace logger once per session (idempotent)."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
