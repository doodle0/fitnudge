"""
Process-wide logging: everything goes to stdout.

- LOG_FORMAT=json (set in the Dockerfile): one JSON object per line with a `level` field, which
  Railway uses for severity. Writing to stderr would make Railway mark every line as an error.
- LOG_FORMAT=text (default): human-readable lines for local development.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

_LEVELS = {"DEBUG": "debug", "INFO": "info", "WARNING": "warn", "ERROR": "error", "CRITICAL": "error"}

# Third-party loggers that install their own (stderr) handlers; route them through ours instead.
_REROUTED = ("uvicorn", "uvicorn.error", "uvicorn.access", "alembic", "apscheduler")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        elif record.stack_info:
            message = f"{message}\n{self.formatStack(record.stack_info)}"
        return json.dumps(
            {
                "level": _LEVELS.get(record.levelname, "info"),
                "message": message,
                "logger": record.name,
                "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent; safe to call from main.py, Alembic's env.py, and scripts."""
    handler = logging.StreamHandler(sys.stdout)
    if os.environ.get("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    for name in _REROUTED:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.captureWarnings(True)
