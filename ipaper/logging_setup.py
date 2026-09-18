"""Application logging that renders human-readable time in UTC+8.

Absolute instants elsewhere stay UTC (see :mod:`ipaper.timeutil`); this module
only affects how log lines are rendered. Protocol-level timestamps (HTTP Date,
external platform metadata, Docker engine times) are untouched.
"""
from __future__ import annotations

import logging
import sys

from .timeutil import APP_TZ_NAME, AppTimeFormatter

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S%z"

_LOGGER_NAMES = ("", "gunicorn.error", "gunicorn.access", "ipaper")


class AppFormatter(AppTimeFormatter, logging.Formatter):
    """``logging.Formatter`` that prints ``asctime`` in UTC+8."""

    def __init__(self, fmt: str = _LOG_FORMAT, datefmt: str = _DATE_FORMAT):
        logging.Formatter.__init__(self, fmt=fmt, datefmt=datefmt)


def install_app_logging(level: int | None = None) -> logging.Logger:
    """Install the UTC+8 formatter on the root logger and Gunicorn loggers.

    Safe to call more than once; existing handlers keep their streams and only
    their formatter is replaced.
    """
    formatter = AppFormatter()
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        root.addHandler(handler)
    for handler in root.handlers:
        handler.setFormatter(formatter)
    if level is not None:
        root.setLevel(level)

    for name in _LOGGER_NAMES:
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            handler.setFormatter(formatter)

    logging.getLogger("ipaper").info(
        "log time zone set to %s", APP_TZ_NAME
    )
    return root