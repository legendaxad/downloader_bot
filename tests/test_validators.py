import pytest

from utils.exceptions import InvalidURLError, UnsupportedURLError
from utils.validators import Platform, detect_platform, extract_url, validate_url


@pytest.mark.parametrize("url,platform", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", Platform.YOUTUBE),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", Platform.YOUTUBE),
    ("https://m.youtube.com/shorts/abc123", Platform.YOUTUBE),
    ("https://www.tiktok.com/@user/video/123", Platform.TIKTOK),
    ("https://vm.tiktok.com/ZMabc/", Platform.TIKTOK),
    ("https://www.instagram.com/reel/Cabc/", Platform.INSTAGRAM),
    ("https://twitter.com/user/status/1", Platform.TWITTER),
    ("https://x.com/user/status/1", Platform.TWITTER),
    ("HTTP://WWW.YOUTUBE.COM/watch?v=x", Platform.YOUTUBE),
])
def test_supported_urls(url, platform):
    normalised, detected = validate_url(url)
    assert detected is platform
    assert normalised.startswith("https://")


@pytest.mark.parametrize("url", [
    "https://example.com/video",
    "https://youtube.com.evil.com/watch?v=1",   # suffix trick
    "https://evilyoutube.com/watch?v=1",         # no dot boundary
    "https://user:pass@youtube.com/watch?v=1",   # credentials
    "https://youtube.com:8080/watch?v=1",        # odd port
    "https://www.youtube.com/playlist?list=PL1", # playlist
    "https://www.youtube.com/@channel",
])
def test_rejected_urls(url):
    with pytest.raises(UnsupportedURLError):
        validate_url(url)


@pytest.mark.parametrize("url", ["", "ftp://youtube.com/x", "file:///etc/passwd", "javascript:alert(1)",
                                 "https://", "https://" + "a" * 3000 + ".com"])
def test_invalid_urls(url):
    with pytest.raises((InvalidURLError, UnsupportedURLError)):
        validate_url(url)


def test_fragment_is_dropped():
    assert validate_url("https://youtu.be/abc#t=10")[0] == "https://youtu.be/abc"


def test_detect_platform_none_for_unsupported():
    assert detect_platform("https://vimeo.com/1") is None


@pytest.mark.parametrize("text,expected", [
    ("look at this https://youtu.be/abc!", "https://youtu.be/abc"),
    ("youtu.be/abc", "https://youtu.be/abc"),
    ("hello there", None),
    ("(https://x.com/a/status/1)", "https://x.com/a/status/1"),
])
def test_extract_url(text, expected):
    assert extract_url(text) == expected
