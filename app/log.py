"""Logging setup shared by the server entrypoint and the tests."""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO, force: bool = False) -> None:
    """Configure root logging once.

    With ``force=False`` an already installed configuration (for example the
    one uvicorn sets up) is left untouched.
    """
    if logging.getLogger().handlers and not force:
        return
    logging.basicConfig(
        level=level, format=LOG_FORMAT, datefmt=DATE_FORMAT, force=force
    )
