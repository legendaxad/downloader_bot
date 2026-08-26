class DownloaderError(Exception):
    """Base exception for downloader-related errors."""


class InvalidVideoError(DownloaderError):
    """The provided URL cannot be processed."""


class VideoUnavailableError(DownloaderError):
    """The video is unavailable or private."""


class FormatNotAvailableError(DownloaderError):
    """The requested quality/format is unavailable."""


class DownloadFailedError(DownloaderError):
    """The video download failed."""


class MediaProcessingError(DownloaderError):
    """FFmpeg/media processing failed."""


class TelegramUploadError(DownloaderError):
    """Telegram upload failed."""