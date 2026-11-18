"""Application exceptions.

Every exception carries a translation ``key`` (see ``locales/*.json``) plus
optional ``params``. ``localized(lang)`` produces a message that is safe to show
in Telegram (no stack traces, paths or secrets); ``user_message`` is the English
version used for logs and history. ``retryable`` tells the worker whether a
controlled retry makes sense.

Downloader/media errors and Telegram errors are deliberately separate
hierarchies so the downloader stays independent of Telegram.
"""

from __future__ import annotations

from typing import Any

from utils.i18n import t


class AppError(Exception):
    key = "err.generic"
    retryable = False

    def __init__(self, message: str | None = None, *, key: str | None = None,
                 params: dict[str, Any] | None = None) -> None:
        if key is not None:
            self.key = key
        self.params = params or {}
        super().__init__(message or self.user_message)

    @property
    def user_message(self) -> str:
        return t(self.key, "en", **self.params)

    def localized(self, lang: str | None) -> str:
        return t(self.key, lang, **self.params)


class TaskCancelledError(AppError):
    key = "err.cancelled"


# ---------------------------------------------------------------- validation
class InvalidURLError(AppError):
    key = "err.invalid_url"


class UnsupportedURLError(AppError):
    key = "err.unsupported_url"


# ---------------------------------------------------------------- downloader
class DownloaderError(AppError):
    """Base exception for downloader-related errors."""


class InvalidVideoError(DownloaderError):
    key = "err.invalid_video"


class VideoUnavailableError(DownloaderError):
    key = "err.unavailable"


class ContentRestrictedError(DownloaderError):
    key = "err.restricted"


class FormatNotAvailableError(DownloaderError):
    key = "err.format"


class VideoTooLongError(DownloaderError):
    key = "err.too_long"


class FileTooLargeError(DownloaderError):
    key = "err.too_large"


class DownloadFailedError(DownloaderError):
    key = "err.download_failed"
    retryable = True


class MediaProcessingError(DownloaderError):
    key = "err.processing"


# ---------------------------------------------------------------- telegram
class TelegramUploadError(AppError):
    """Telegram API failure while sending the result (separate from downloader errors)."""

    key = "err.upload"

    def __init__(
        self,
        message: str | None = None,
        *,
        retryable: bool = True,
        retry_after: float | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__(message, key=key)
        self.retryable = retryable
        self.retry_after = retry_after


# ---------------------------------------------------------------- queue / limits
class QueueError(AppError):
    key = "err.queue"
    retryable = True


class TooManyTasksError(AppError):
    key = "err.too_many"


class RateLimitedError(AppError):
    key = "err.rate_limited"
