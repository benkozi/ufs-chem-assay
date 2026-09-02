"""Logging for the harness: one namespace logger, level from settings."""

import logging

# The harness name: the one place code names it (logger namespace, plot
# footer). Kept identical to the repository name for consistency.
HARNESS_NAME = "ufs-chem-assay"
LOGGER_NAME = HARNESS_NAME

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
