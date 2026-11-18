"""Telegram-independent video downloader built on yt-dlp.

All methods are synchronous (yt-dlp is blocking); async callers must run them
in a thread (``asyncio.to_thread``). Nothing here knows about Telegram or Redis.

Security: only yt-dlp's built-in site extractors are enabled (the *generic*
extractor is disabled), so a crafted URL cannot make the server fetch arbitrary
hosts (SSRF). No cookies or credentials are ever supplied, so private/login-only
content is reported as restricted rather than bypassed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadCancelled, GeoRestrictedError, UnsupportedError

from utils.exceptions import (
    ContentRestrictedError,
    DownloaderError,
    DownloadFailedError,
    FileTooLargeError,
    FormatNotAvailableError,
    InvalidVideoError,
    TaskCancelledError,
    UnsupportedURLError,
    VideoTooLongError,
    VideoUnavailableError,
)
from utils.logger import logger

# Extractor name prefixes we accept (defence in depth on top of URL validation).
ALLOWED_EXTRACTOR_PREFIXES = ("youtube", "tiktok", "vmtiktok", "instagram", "twitter")
MIN_HEIGHT = 144
MAX_QUALITY_OPTIONS = 8
SOURCE_STEM = "source"

ProgressCallback = Callable[[int | None, int, int | None], None]
"""Called as ``progress(percent_or_None, downloaded_bytes, total_bytes_or_None)``."""


@dataclass(frozen=True)
class QualityOption:
    height: int  # value passed back to ``download``; 0 = best available
    label: str
    filesize: int | None = None


@dataclass(frozen=True)
class VideoMetadata:
    id: str
    title: str
    duration: int | None
    thumbnail: str | None
    uploader: str | None
    webpage_url: str
    extractor: str
    qualities: list[QualityOption] = field(default_factory=list)
    too_large_qualities: list[QualityOption] = field(default_factory=list)


class _Aborted(DownloadCancelled):
    """Raised from the progress hook; yt-dlp re-raises DownloadCancelled unwrapped."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _YtdlpLogger:
    """Route yt-dlp output to our logger at debug level (keeps stdout clean)."""

    def debug(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        logger.debug("yt-dlp warning: %s", msg)

    def error(self, msg: str) -> None:
        logger.debug("yt-dlp error: %s", msg)


# --------------------------------------------------------------------- errors
_UNSUPPORTED = ("unsupported url", "no suitable extractor", "is not a valid url")
_FORMAT = ("requested format is not available", "no video formats found")
_NO_VIDEO = ("no video could be found", "there's no video in this", "no media found")
_UNAVAILABLE = (
    "private video", "video unavailable", "this video is unavailable", "has been removed",
    "was deleted", "has been deleted", "does not exist", "not found", "http error 404",
    "this post is unavailable", "account is private", "no longer available", "terminated",
)
_RESTRICTED = (
    "sign in", "log in", "login", "logged-in", "cookies", "age-restricted", "age restricted",
    "inappropriate for some users", "confirm your age", "drm", "geo", "not available in your country",
    "members-only", "members only", "premium", "requires authentication", "authentication",
    "protected", "nsfw", "sensitive content", "subscriber",
)


def map_ytdlp_error(error: BaseException) -> DownloaderError:
    """Translate a yt-dlp exception into a meaningful application exception."""
    if isinstance(error, DownloaderError):
        return error
    if isinstance(error, UnsupportedError):
        return UnsupportedURLError(str(error))
    if isinstance(error, GeoRestrictedError):
        return ContentRestrictedError(str(error))

    message = str(error).lower()
    # Order matters: "unavailable" messages sometimes mention "sign in".
    for needles, exc in (
        (_UNSUPPORTED, UnsupportedURLError),
        (_FORMAT, FormatNotAvailableError),
        (_NO_VIDEO, InvalidVideoError),
        (_RESTRICTED, ContentRestrictedError),
        (_UNAVAILABLE, VideoUnavailableError),
    ):
        if any(needle in message for needle in needles):
            return exc(str(error))
    return DownloadFailedError(str(error))


# --------------------------------------------------------------------- helpers
def build_format_options(quality: int) -> dict[str, Any]:
    """Format selection: best video+audio, merged; prefer the chosen height, H.264, AAC, MP4.

    ``height:N`` sorts formats so the largest height <= N wins (falling back to
    the closest larger one if nothing smaller exists), which gracefully handles
    formats that disappear between metadata extraction and download.
    """
    sort = ["vcodec:h264", "acodec:aac", "ext:mp4:m4a"]
    if quality > 0:
        sort.insert(0, f"height:{quality}")
    return {
        "format": "bv*+ba/b",
        "format_sort": sort,
        "merge_output_format": "mp4/mkv",
    }


def _label(height: int, width: int | None) -> str:
    # Vertical videos (Shorts, TikTok) report e.g. 1080x1920; users know that as "1080p".
    short_side = min(height, width) if width else height
    return f"{short_side}p"


def collect_qualities(info: dict[str, Any]) -> list[QualityOption]:
    """Return one option per distinct video height, highest first, with size estimates."""
    formats = info.get("formats") or []
    duration = info.get("duration") or 0

    def size_of(fmt: dict[str, Any]) -> int | None:
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        if not size and fmt.get("tbr") and duration:
            size = int(fmt["tbr"] * 1000 / 8 * duration)
        return int(size) if size else None

    audio_sizes = [
        size_of(f) for f in formats
        if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")
    ]
    best_audio = max((s for s in audio_sizes if s), default=0)

    by_height: dict[int, QualityOption] = {}
    for fmt in formats:
        height = fmt.get("height")
        if not height or height < MIN_HEIGHT or fmt.get("vcodec") == "none":
            continue
        size = size_of(fmt)
        if size and fmt.get("acodec") in (None, "none"):
            size += best_audio
        current = by_height.get(height)
        # Keep the smallest size estimate per height: the H.264-preferring sort
        # usually picks a smaller stream than VP9/AV1 "best".
        if current is None or (size and (current.filesize is None or size < current.filesize)):
            by_height[height] = QualityOption(height, _label(height, fmt.get("width")), size)

    options = sorted(by_height.values(), key=lambda o: o.height, reverse=True)
    # Collapse duplicate labels (e.g. 1080x1920 and 1920x1080 variants).
    seen: set[str] = set()
    unique = [o for o in options if not (o.label in seen or seen.add(o.label))]
    return unique[:MAX_QUALITY_OPTIONS]


# --------------------------------------------------------------------- service
class VideoDownloader:
    def __init__(
        self,
        *,
        max_filesize: int | None = None,
        max_duration: int | None = None,
        js_runtimes: tuple[str, ...] = (),
        socket_timeout: int = 20,
        ydl_factory: Callable[[dict[str, Any]], Any] = yt_dlp.YoutubeDL,
    ) -> None:
        self.max_filesize = max_filesize
        self.max_duration = max_duration
        self.js_runtimes = js_runtimes
        self.socket_timeout = socket_timeout
        self._ydl_factory = ydl_factory

    # ---------------------------------------------------------------- options
    def _base_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "playlist_items": "1",
            "allowed_extractors": ["default", "-generic"],
            "socket_timeout": self.socket_timeout,
            "retries": 3,
            "fragment_retries": 3,
            "logger": _YtdlpLogger(),
        }
        if self.js_runtimes:
            options["js_runtimes"] = {name: {} for name in self.js_runtimes}
        return options

    def _extract(self, url: str, options: dict[str, Any], download: bool) -> dict[str, Any]:
        try:
            with self._ydl_factory(options) as ydl:
                info = ydl.extract_info(url, download=download)
        except _Aborted:
            raise
        except Exception as error:  # yt-dlp raises many types; normalise them all
            raise map_ytdlp_error(error) from error

        if not info:
            raise InvalidVideoError("yt-dlp returned no information")
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise InvalidVideoError("playlist without entries")
            info = entries[0]

        extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
        if not extractor.startswith(ALLOWED_EXTRACTOR_PREFIXES):
            raise UnsupportedURLError(f"extractor not allowed: {extractor}")
        return info

    # ---------------------------------------------------------------- public
    def extract_metadata(self, url: str) -> VideoMetadata:
        options = self._base_options() | {"skip_download": True}
        info = self._extract(url, options, download=False)

        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise InvalidVideoError("live stream", key="err.live")

        duration = info.get("duration")
        duration = int(duration) if duration else None
        if self.max_duration and duration and duration > self.max_duration:
            raise VideoTooLongError(f"duration {duration}s > {self.max_duration}s")

        qualities = collect_qualities(info)
        if not qualities:
            if not info.get("formats") and not info.get("url"):
                raise InvalidVideoError("no formats")
            qualities = [QualityOption(0, "Best available", info.get("filesize") or info.get("filesize_approx"))]

        too_large: list[QualityOption] = []
        if self.max_filesize:
            limit = self.max_filesize * 1.05  # estimates are approximate
            too_large = [q for q in qualities if q.filesize and q.filesize > limit]
            qualities = [q for q in qualities if q not in too_large]

        return VideoMetadata(
            id=str(info.get("id") or ""),
            title=str(info.get("title") or info.get("id") or "Video")[:200],
            duration=duration,
            thumbnail=info.get("thumbnail"),
            uploader=info.get("uploader"),
            webpage_url=info.get("webpage_url") or url,
            extractor=str(info.get("extractor_key") or ""),
            qualities=qualities,
            too_large_qualities=too_large,
        )

    def download(
        self,
        url: str,
        quality: int,
        dest_dir: Path,
        *,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Path:
        """Download ``url`` at (up to) ``quality`` px height into ``dest_dir``.

        File names are fixed (``source.<ext>``) — never derived from remote titles —
        so there is no path traversal or filename-injection surface.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        tracker = _ProgressTracker(progress, cancel_event, self.max_filesize)
        options = self._base_options() | build_format_options(quality) | {
            "outtmpl": f"{SOURCE_STEM}.%(ext)s",
            "paths": {"home": str(dest_dir), "temp": str(dest_dir)},
            "overwrites": True,
            "progress_hooks": [tracker.hook],
        }
        if self.max_filesize:
            options["max_filesize"] = self.max_filesize

        logger.info("Download started", extra={"quality": quality})
        try:
            info = self._extract(url, options, download=True)
        except _Aborted as aborted:
            if aborted.reason == "too_large":
                raise FileTooLargeError("download exceeded size limit") from aborted
            raise TaskCancelledError("download cancelled") from aborted

        if cancel_event is not None and cancel_event.is_set():
            raise TaskCancelledError("download cancelled")

        path = self._find_output(info, dest_dir)
        if path is None:
            if tracker.exceeded_known_limit:
                raise FileTooLargeError("max_filesize reached")
            raise DownloadFailedError("download finished without an output file")

        if self.max_filesize and path.stat().st_size > self.max_filesize:
            raise FileTooLargeError(f"file is {path.stat().st_size} bytes")
        return path

    @staticmethod
    def _find_output(info: dict[str, Any], dest_dir: Path) -> Path | None:
        for item in info.get("requested_downloads") or []:
            candidate = item.get("filepath")
            if candidate and Path(candidate).is_file():
                return Path(candidate)
        candidates = [
            p for p in dest_dir.glob(f"{SOURCE_STEM}.*")
            if p.is_file() and p.suffix not in {".part", ".ytdl", ".temp"} and ".f" not in p.stem[len(SOURCE_STEM):]
        ]
        return max(candidates, key=lambda p: p.stat().st_size) if candidates else None


class _ProgressTracker:
    """Aggregates per-stream yt-dlp progress into one overall percentage."""

    def __init__(
        self,
        callback: ProgressCallback | None,
        cancel_event: threading.Event | None,
        max_filesize: int | None,
    ) -> None:
        self.callback = callback
        self.cancel_event = cancel_event
        self.max_filesize = max_filesize
        self.finished: dict[str, int] = {}
        self.last_percent = -1
        self.exceeded_known_limit = False

    def hook(self, data: dict[str, Any]) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise _Aborted("cancelled")

        status = data.get("status")
        key = str(data.get("filename") or data.get("tmpfilename") or "")
        downloaded = int(data.get("downloaded_bytes") or 0)
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        total = int(total) if total else None

        if status == "finished":
            self.finished[key] = downloaded or (total or 0)
            return
        if status != "downloading":
            return

        done_before = sum(size for name, size in self.finished.items() if name != key)
        expected_total = self._expected_total(data)
        if self.max_filesize and total and done_before + total > self.max_filesize * 1.05:
            self.exceeded_known_limit = True
            raise _Aborted("too_large")
        if self.max_filesize and done_before + downloaded > self.max_filesize * 1.05:
            self.exceeded_known_limit = True
            raise _Aborted("too_large")

        if self.callback is None:
            return
        percent: int | None = None
        if expected_total:
            percent = int((done_before + downloaded) * 100 / expected_total)
        elif total:
            percent = int(downloaded * 100 / total)
        if percent is not None:
            percent = max(self.last_percent, min(percent, 99))
            self.last_percent = percent
        self.callback(percent, done_before + downloaded, expected_total or total)

    @staticmethod
    def _expected_total(data: dict[str, Any]) -> int | None:
        info = data.get("info_dict") or {}
        requested = info.get("requested_formats")
        if not requested:
            return None
        sizes = [f.get("filesize") or f.get("filesize_approx") for f in requested]
        if not all(sizes):
            return None
        return int(sum(sizes))
