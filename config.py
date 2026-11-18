"""Environment-driven configuration.

Settings are loaded lazily via :func:`get_settings` so that modules which do not
need Telegram credentials (downloader, video processing, tests) can be imported
without a ``BOT_TOKEN``.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(ValueError):
    """Raised when the environment configuration is invalid."""


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be an integer") from error
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    bot_token: str = ""
    redis_url: str = "redis://localhost:6379/0"

    # Telegram
    telegram_api_base_url: str = ""  # e.g. http://telegram-bot-api:8081/bot for a local Bot API server
    max_upload_mb: int = 50  # 50 MB for the public Bot API; up to 2000 with a local Bot API server

    # Limits
    max_duration_seconds: int = 3 * 60 * 60
    worker_concurrency: int = 2
    metadata_concurrency: int = 4
    max_active_tasks_per_user: int = 2
    user_requests_per_minute: int = 10

    # Timeouts / retries
    metadata_timeout_seconds: int = 60
    download_timeout_seconds: int = 30 * 60
    processing_timeout_seconds: int = 30 * 60
    upload_timeout_seconds: int = 10 * 60
    max_retries: int = 2
    retry_base_delay_seconds: int = 15
    stale_task_seconds: int = 120
    session_ttl_seconds: int = 30 * 60
    task_ttl_seconds: int = 24 * 60 * 60
    shutdown_grace_seconds: int = 45

    # Filesystem / logging
    download_dir: Path = Path("downloads")
    log_level: str = "INFO"
    log_format: str = "text"  # "text" or "json"
    log_file: str = ""

    # yt-dlp: optional JS runtimes for YouTube (e.g. "deno" or "node")
    ytdlp_js_runtimes: tuple[str, ...] = field(default_factory=tuple)

    worker_name: str = field(default_factory=lambda: f"{socket.gethostname()}-{os.getpid()}")

    # Support / admin contact (Telegram username without "@")
    admin_username: str = "legend_axad"

    # MySQL (optional; user data and download history are stored when MYSQL_HOST is set)
    mysql_host: str = ""
    mysql_port: int = 3306
    mysql_user: str = "downloader"
    mysql_password: str = ""
    mysql_database: str = "downloader_bot"

    @property
    def mysql_enabled(self) -> bool:
        return bool(self.mysql_host)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def require_bot_token(self) -> str:
        if not self.bot_token:
            raise ConfigError("BOT_TOKEN is not set (see .env.example)")
        return self.bot_token


def load_settings() -> Settings:
    log_format = _str("LOG_FORMAT", "text").lower()
    if log_format not in {"text", "json"}:
        raise ConfigError("LOG_FORMAT must be 'text' or 'json'")

    runtimes = tuple(r.strip() for r in _str("YTDLP_JS_RUNTIMES").split(",") if r.strip())

    return Settings(
        bot_token=_str("BOT_TOKEN"),
        redis_url=_str("REDIS_URL", "redis://localhost:6379/0"),
        telegram_api_base_url=_str("TELEGRAM_API_BASE_URL"),
        max_upload_mb=_int("MAX_UPLOAD_MB", 50, minimum=1),
        max_duration_seconds=_int("MAX_DURATION_SECONDS", 3 * 60 * 60, minimum=1),
        worker_concurrency=_int("WORKER_CONCURRENCY", 2, minimum=1),
        metadata_concurrency=_int("METADATA_CONCURRENCY", 4, minimum=1),
        max_active_tasks_per_user=_int("MAX_ACTIVE_TASKS_PER_USER", 2, minimum=1),
        user_requests_per_minute=_int("USER_REQUESTS_PER_MINUTE", 10, minimum=1),
        metadata_timeout_seconds=_int("METADATA_TIMEOUT_SECONDS", 60, minimum=5),
        download_timeout_seconds=_int("DOWNLOAD_TIMEOUT_SECONDS", 30 * 60, minimum=30),
        processing_timeout_seconds=_int("PROCESSING_TIMEOUT_SECONDS", 30 * 60, minimum=30),
        upload_timeout_seconds=_int("UPLOAD_TIMEOUT_SECONDS", 10 * 60, minimum=30),
        max_retries=_int("MAX_RETRIES", 2, minimum=0),
        retry_base_delay_seconds=_int("RETRY_BASE_DELAY_SECONDS", 15, minimum=1),
        stale_task_seconds=_int("STALE_TASK_SECONDS", 120, minimum=30),
        session_ttl_seconds=_int("SESSION_TTL_SECONDS", 30 * 60, minimum=60),
        task_ttl_seconds=_int("TASK_TTL_SECONDS", 24 * 60 * 60, minimum=600),
        shutdown_grace_seconds=_int("SHUTDOWN_GRACE_SECONDS", 45, minimum=0),
        download_dir=Path(_str("DOWNLOAD_DIR", "downloads")),
        log_level=_str("LOG_LEVEL", "INFO").upper(),
        log_format=log_format,
        log_file=_str("LOG_FILE"),
        ytdlp_js_runtimes=runtimes,
        worker_name=_str("WORKER_NAME") or f"{socket.gethostname()}-{os.getpid()}",
        admin_username=_str("ADMIN_USERNAME", "legend_axad").lstrip("@") or "legend_axad",
        mysql_host=_str("MYSQL_HOST"),
        mysql_port=_int("MYSQL_PORT", 3306, minimum=1),
        mysql_user=_str("MYSQL_USER", "downloader"),
        mysql_password=os.getenv("MYSQL_PASSWORD", ""),
        mysql_database=_str("MYSQL_DATABASE", "downloader_bot"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
