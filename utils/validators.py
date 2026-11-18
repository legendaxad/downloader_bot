"""URL validation and platform detection.

This is the first SSRF barrier: only http(s) URLs on an explicit allowlist of
platform hostnames, default ports and no embedded credentials are accepted.
The second barrier is the yt-dlp extractor allowlist in services/downloader.py.
"""

from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from utils.exceptions import InvalidURLError, UnsupportedURLError

MAX_URL_LENGTH = 2048


class Platform(StrEnum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    TWITTER = "twitter"


PLATFORM_LABELS = {
    Platform.YOUTUBE: "YouTube",
    Platform.TIKTOK: "TikTok",
    Platform.INSTAGRAM: "Instagram",
    Platform.TWITTER: "Twitter / X",
}

# Registrable domains → platform. Subdomains (www., m., vm., ...) are accepted.
PLATFORM_DOMAINS: dict[str, Platform] = {
    "youtube.com": Platform.YOUTUBE,
    "youtu.be": Platform.YOUTUBE,
    "youtube-nocookie.com": Platform.YOUTUBE,
    "tiktok.com": Platform.TIKTOK,
    "instagram.com": Platform.INSTAGRAM,
    "instagr.am": Platform.INSTAGRAM,
    "twitter.com": Platform.TWITTER,
    "x.com": Platform.TWITTER,
}

# Backwards-compatible name used by older code.
SUPPORTED_DOMAINS = set(PLATFORM_DOMAINS)

_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_url(text: str) -> str | None:
    """Return the first http(s) URL found in a free-form message."""
    if not text:
        return None
    match = _URL_IN_TEXT_RE.search(text.strip())
    if match:
        return match.group(0).rstrip(").,;!?")
    candidate = text.strip()
    # Accept bare links like "youtu.be/abc" by assuming https.
    if " " not in candidate and "." in candidate.split("/", 1)[0]:
        return f"https://{candidate}"
    return None


def _platform_for_host(hostname: str) -> Platform | None:
    hostname = hostname.lower().rstrip(".")
    for domain, platform in PLATFORM_DOMAINS.items():
        if hostname == domain or hostname.endswith(f".{domain}"):
            return platform
    return None


def validate_url(url: str) -> tuple[str, Platform]:
    """Validate and normalise ``url``.

    Returns the normalised URL and detected platform.
    Raises InvalidURLError or UnsupportedURLError.
    """
    if not url or len(url) > MAX_URL_LENGTH:
        raise InvalidURLError("empty or too long URL")

    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as error:
        raise InvalidURLError(f"unparsable URL: {error}") from error

    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise InvalidURLError("URL must use http or https and include a host")
    if parts.username or parts.password:
        raise UnsupportedURLError("credentials in URL are not allowed")
    if port not in (None, 80, 443):
        raise UnsupportedURLError("non-default ports are not allowed")

    platform = _platform_for_host(parts.hostname)
    if platform is None:
        raise UnsupportedURLError(f"host not allowed: {parts.hostname}")

    path = parts.path.lower()
    if platform is Platform.YOUTUBE and (path.startswith("/playlist") or path.startswith("/@")
                                         or path.startswith("/channel") or path.startswith("/c/")):
        raise UnsupportedURLError(
"playlist/channel URL", key="err.playlist")

    normalised = urlunsplit(("https", parts.hostname.lower(), parts.path or "/", parts.query, ""))
    return normalised, platform


def detect_platform(url: str) -> Platform | None:
    try:
        return validate_url(url)[1]
    except (InvalidURLError, UnsupportedURLError):
        return None


def is_valid_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return parts.scheme in {"http", "https"} and bool(parts.netloc)
    except ValueError:
        return False


def is_supported_platform(url: str) -> bool:
    return detect_platform(url) is not None
