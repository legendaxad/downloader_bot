"""Logging setup: text or JSON output, contextual fields, secret redaction.

Usage::

    from utils.logger import logger
    logger.info("Download finished", extra={"task_id": tid, "duration_s": 3.2})
"""

from __future__ import annotations

import json
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "downloader_bot"

# Fields passed via ``extra=`` that are rendered by the formatters.
CONTEXT_FIELDS = (
    "task_id", "user_id", "chat_id", "worker", "platform", "status", "quality",
    "attempt", "duration_s", "download_s", "processing_s", "upload_s", "size_bytes",
    "error", "error_type",
)

_TOKEN_RE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")
_REDIS_PASSWORD_RE = re.compile(r"(rediss?://[^:/@\s]*:)[^@\s]+@")


def redact(text: str) -> str:
    text = _TOKEN_RE.sub("<redacted-token>", text)
    return _REDIS_PASSWORD_RE.sub(r"\1<redacted>@", text)


class RedactingFilter(logging.Filter):
    """Removes bot tokens and Redis passwords from every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s | %(levelname)s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        ctx = " ".join(
            f"{key}={getattr(record, key)}" for key in CONTEXT_FIELDS if hasattr(record, key)
        )
        if ctx:
            first, sep, rest = base.partition("\n")
            base = f"{first} | {ctx}{sep}{rest}"
        return redact(base)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in CONTEXT_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, ensure_ascii=False))


def setup_logging(level: str = "INFO", fmt: str = "text", log_file: str = "") -> None:
    """Configure the root logger once. Safe to call multiple times."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_downloader_bot", False):
            root.removeHandler(handler)

    formatter: logging.Formatter = JsonFormatter() if fmt == "json" else TextFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5, encoding="utf-8"))

    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        handler._downloader_bot = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    root.setLevel(level)
    # httpx logs full request URLs, which contain the bot token.
    for noisy in ("httpx", "httpcore", "telegram.ext.Updater", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = logging.getLogger(LOGGER_NAME)
