"""
Structured logs of `gcm-mcp` for Cloud Logging (docs/remote/07, "Logging"; 09, N5).

- stdout, one JSON object per line: `severity`, `message`, `logger`, `time`, and
  `sub` (a hash of the caller's `sub`) when the record is made inside an
  authenticated request. Cloud Run sends stdout to Cloud Logging, which reads
  `severity` and `message`.
- Email addresses are masked in the finished line, tracebacks included
  (`log_masking.EmailMaskingFormatter`), on every handler installed here.
- No access log: the URL of an OAuth callback carries `code` and `state`.
  `uvicorn.access` has no handler and does not propagate, and uvicorn is started
  with `access_log=False` as well.
- fastmcp logs through its own Rich handlers on stderr, which would bypass the
  formatter: they are removed and its records go to the root handler.

This module is named like the standard library one only inside the package; it
imports the standard one with an absolute import.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone

from ..log_masking import EmailMaskingFormatter
from .chat import current_sub

_SEVERITY = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


def hash_sub(sub: str) -> str:
    return hashlib.sha256(sub.encode()).hexdigest()[:16]


class JsonFormatter(EmailMaskingFormatter):
    """One JSON object per record, with addresses masked in `message`."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)  # masked, traceback included
        payload = {
            "severity": _SEVERITY.get(record.levelno, "DEFAULT"),
            "message": message,
            "logger": record.name,
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        }
        try:
            sub = current_sub()
        except Exception:
            sub = None
        if sub:
            payload["sub"] = hash_sub(sub)
        return json.dumps(payload, ensure_ascii=False)


def log_config(level: str = "INFO") -> dict:
    """A `logging.config.dictConfig` / uvicorn `log_config` dictionary."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": f"{__name__}.JsonFormatter"}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": [], "level": "INFO", "propagate": True},
            "uvicorn.error": {"handlers": [], "level": "INFO", "propagate": True},
            # Access log: never written (callback URLs carry `code` and `state`).
            "uvicorn.access": {"handlers": [], "level": "CRITICAL", "propagate": False},
        },
    }


def route_fastmcp_to_root() -> None:
    """Drop fastmcp's own handlers so its records reach the JSON handler.

    Call after fastmcp is imported and the app is built: fastmcp installs its
    handlers when it is imported or configured.
    """
    for name in ("fastmcp", "FastMCP"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.propagate = True


def setup_logging(level: str = "INFO") -> None:
    import logging.config

    logging.config.dictConfig(log_config(level))
    route_fastmcp_to_root()


__all__ = ["JsonFormatter", "hash_sub", "log_config", "route_fastmcp_to_root", "setup_logging"]
