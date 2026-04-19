"""Logging setup for Jarvis. Call setup_logging() once at startup."""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # Suppress noisy third-party loggers at INFO level.
    for noisy in ("httpx", "httpcore", "urllib3", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
