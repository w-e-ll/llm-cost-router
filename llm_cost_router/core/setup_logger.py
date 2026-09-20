"""Application-only logging: rotating files and stdout, with safe event fields."""

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock

request_id = ContextVar("request_id", default="-")
run_id = ContextVar("run_id", default="-")
_lock = Lock()


class EventFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "pid": record.process,
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id.get(),
            "run_id": run_id.get(),
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if os.getenv("LOG_FORMAT", "text") == "json":
            return json.dumps(entry, default=str)
        fields = " ".join(
            f"{key}={json.dumps(value, default=str)}"
            for key, value in entry.items()
            if key not in ("timestamp", "pid", "level", "logger", "event")
        )
        return (
            f"{entry['timestamp']} {record.process:5d} {record.levelname:5s} "
            f"{record.name} {entry['event']} {fields}"
        )


def setup_logger():
    """Idempotent across Streamlit reruns; do not alter framework/root handlers."""
    logger = logging.getLogger("llm_cost_router")
    with _lock:
        if any(getattr(h, "router_handler", False) for h in logger.handlers):
            return logger
        handlers = [logging.StreamHandler(sys.stdout)]
        if filename := os.getenv("LOG_FILE"):
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    filename, maxBytes=20 * 1024 * 1024, backupCount=10, encoding="utf-8"
                )
            )
        for handler in handlers:
            handler.router_handler = True
            handler.setFormatter(EventFormatter())
            logger.addHandler(handler)
        logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        logger.propagate = False
    return logger


def event(logger, name, *, level=logging.INFO, **fields):
    """Call sites supply operational metadata only, never bodies, keys or exceptions."""
    logger.log(level, name, extra={"fields": fields})
