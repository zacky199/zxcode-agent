"""Structured logging for ZX Agent.

Supports two formats:

* **console** - human-readable line logs (default)
* **json** - structured JSON lines for machine parsing / log aggregators

Every key subsystem should create its own logger via :func:`setup_logger`
and use structured ``extra`` fields where useful.
"""
import json
import logging
import sys
from pathlib import Path
from typing import Optional


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def __init__(self, fmt: Optional[dict] = None):
        super().__init__()
        self._fields = fmt or {
            "ts": "asctime",
            "level": "levelname",
            "logger": "name",
            "message": "message",
        }

    def format(self, record: logging.LogRecord) -> str:
        payload = {}
        for key, attr in self._fields.items():
            if attr == "message":
                value = record.getMessage()
            else:
                value = getattr(record, attr, None)
            if value is not None:
                payload[key] = value
        for key, value in getattr(record, "fields", {}).items():
            payload.setdefault(key, value)
        payload.setdefault("asctime", self.formatTime(record, "%Y-%m-%d %H:%M:%S"))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Compact human-readable format with optional structured extras."""

    def format(self, record: logging.LogRecord) -> str:
        line = self.formatTime(record, "%H:%M:%S")
        fields = getattr(record, "fields", None)
        if fields:
            extras = " ".join(f"{k}={v}" for k, v in fields.items())
            return f"[{line}] [{record.levelname}] {record.name}: {record.getMessage()} ({extras})"
        return f"[{line}] [{record.levelname}] {record.name}: {record.getMessage()}"


def setup_logger(name: str = "zx_agent",
                 log_file: Optional[Path] = None,
                 level: int = logging.INFO,
                 json_logs: bool = False,
                 use_stderr: bool = True) -> logging.Logger:
    """Build (or return an existing) configured logger.

    Args:
        name: logger name (module-scoped).
        log_file: optional file path to persist logs (parent dirs created).
        level: minimum level.
        json_logs: if True, file handler emits JSON lines; console stays human.
        use_stderr: route console output to stderr (keeps stdout clean for the TUI).
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    stream = sys.stderr if use_stderr else sys.stdout
    stream_handler = logging.StreamHandler(stream)
    stream_handler.setFormatter(ConsoleFormatter())
    logger.addHandler(stream_handler)

    if log_file:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter() if json_logs else ConsoleFormatter())
        logger.addHandler(file_handler)

    return logger


def structured(level: int, message: str, **fields):
    """Emit a structured log record. Usage: ``structured(logging.INFO, "msg", key=val)``."""
    record = logging.LogRecord("zx", level, "", 0, message, (), None)
    record.fields = fields
    logging.getLogger("zx").handle(record)
