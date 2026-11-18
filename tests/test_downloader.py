"""Downloader service tests with a fake yt-dlp (no network)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError, GeoRestrictedError, UnsupportedError

from services.downloader import (
    VideoDownloader,
    build_format_options,
    collect_qualities,
    map_ytdlp_error,
)
from utils.exceptions import (
    ContentRestrictedError,
    DownloadFailedError,
    FileTooLargeError,
    FormatNotAvailableError,
    InvalidVideoError,
    TaskCancelledError,
    UnsupportedURLError,
    VideoTooLongError,
    VideoUnavailableError,
)

FORMATS = [
    {"format_id": "a", "vcodec": "none", "acodec": "mp4a.40.2", "filesize": 1_000_000},
    {"format_id": "v360", "vcodec": "avc1", "acodec": "none", "height": 360, "width": 640, "filesize": 3_000_000},
    {"format_id": "v720", "vcodec": "avc1", "acodec": "none", "height": 720, "width": 1280, "filesize": 9_000_000},
    {"format_id": "v720vp9", "vcodec": "vp9", "acodec": "none", "height": 720, "width": 1280, "filesize": 7_000_000},
    {"format_id": "v1080", "vcodec": "avc1", "acodec": "none", "height": 1080, "width": 1920,
     "filesize": 200_000_000},
    {"format_id": "tiny", "vcodec": "avc1", "acodec": "none", "height": 72, "width": 128},
    {"format_id": "sb", "vcodec": "none", "acodec": "none", "height": 90},
]

INFO = {"id": "vid1", "title": "Hello", "duration": 60, "thumbnail": "https://i/t.jpg", "uploader": "me",
        "webpage_url": "https://youtu.be/vid1", "extractor_key": "Youtube", "formats": FORMATS}


class FakeYDL:
    """Minimal stand-in for yt_dlp.YoutubeDL."""

    def __init__(self, info=None, error=None, on_download=None):
        self.info, self.error, self.on_download = info, error, on_download
        self.params = None

    def __call__(self, params):
        self.params = params
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if self.error:
            raise self.error
        if download and self.on_download:
            return self.on_download(self.params)
        return dict(self.info)


def test_format_options_prefer_h264_aac_and_height():
    opts = build_format_options(720)
    assert opts["format"] == "bv*+ba/b"
    assert opts["format_sort"][0] == "height:720"
    assert "vcodec:h264" in opts["format_sort"] and "acodec:aac" in opts["format_sort"]
    assert "height" not in " ".join(build_format_options(0)["format_sort"])


def test_collect_qualities_dedupes_and_estimates_size():
    options = collect_qualities(INFO)
    assert [o.height for o in options] == [1080, 720, 360]
    q720 = next(o for o in options if o.height == 720)
    assert q720.filesize == 7_000_000 + 1_000_000  # smallest video at that height + audio
    assert q720.label == "720p"


def test_vertical_video_label_uses_short_side():
    info = {"duration": 10, "formats": [{"vcodec": "avc1", "acodec": "mp4a", "height": 1920, "width": 1080}]}
    assert collect_qualities(info)[0].label == "1080p"
    assert collect_qualities(info)[0].height == 1920


def test_extract_metadata_hides_too_large_qualities():
    d = VideoDownloader(max_filesize=50_000_000, ydl_factory=FakeYDL(INFO))
    meta = d.extract_metadata("https://youtu.be/vid1")
    assert meta.title == "Hello" and meta.duration == 60
    assert [q.height for q in meta.qualities] == [720, 360]
    assert [q.height for q in meta.too_large_qualities] == [1080]


def test_extract_metadata_options_disable_generic_extractor():
    fake = FakeYDL(INFO)
    VideoDownloader(ydl_factory=fake).extract_metadata("https://youtu.be/vid1")
    assert fake.params["allowed_extractors"] == ["default", "-generic"]
    assert fake.params["noplaylist"] is True
    assert "cookiefile" not in fake.params and "username" not in fake.params


def test_extract_metadata_best_available_when_no_heights():
    info = dict(INFO, formats=[{"format_id": "x", "url": "u", "vcodec": "h264", "acodec": "aac"}])
    meta = VideoDownloader(ydl_factory=FakeYDL(info)).extract_metadata("u")
    assert [(q.height, q.label) for q in meta.qualities] == [(0, "Best available")]


def test_extract_metadata_picks_first_playlist_entry():
    info = {"_type": "playlist", "entries": [dict(INFO, extractor_key="Twitter")]}
    meta = VideoDownloader(ydl_factory=FakeYDL(info)).extract_metadata("https://x.com/a/status/1")
    assert meta.id == "vid1"


def test_extract_metadata_rejects_long_live_and_foreign_extractor():
    with pytest.raises(VideoTooLongError):
        VideoDownloader(max_duration=30, ydl_factory=FakeYDL(INFO)).extract_metadata("u")
    with pytest.raises(InvalidVideoError):
        VideoDownloader(ydl_factory=FakeYDL(dict(INFO, is_live=True))).extract_metadata("u")
    with pytest.raises(UnsupportedURLError):
        VideoDownloader(ydl_factory=FakeYDL(dict(INFO, extractor_key="Generic"))).extract_metadata("u")


@pytest.mark.parametrize("error,expected", [
    (DownloadError("ERROR: [youtube] x: Private video. Sign in if you've been granted access"),
     ContentRestrictedError),
    (DownloadError("ERROR: [youtube] x: Video unavailable"), VideoUnavailableError),
    (DownloadError("ERROR: This video has been removed by the uploader"), VideoUnavailableError),
    (DownloadError("ERROR: Requested format is not available"), FormatNotAvailableError),
    (DownloadError("ERROR: Sign in to confirm your age"), ContentRestrictedError),
    (DownloadError("ERROR: This video is DRM protected"), ContentRestrictedError),
    (DownloadError("ERROR: No video could be found in this tweet"), InvalidVideoError),
    (DownloadError("ERROR: Unsupported URL: https://foo"), UnsupportedURLError),
    (DownloadError("ERROR: unable to download: Connection reset by peer"), DownloadFailedError),
    (UnsupportedError("https://foo"), UnsupportedURLError),
    (GeoRestrictedError("not available"), ContentRestrictedError),
])
def test_error_mapping(error, expected):
    mapped = map_ytdlp_error(error)
    assert isinstance(mapped, expected)
    assert "Traceback" not in mapped.user_message


def test_only_transient_errors_are_retryable():
    assert DownloadFailedError().retryable
    assert not VideoUnavailableError().retryable
    assert not ContentRestrictedError().retryable
    assert not FileTooLargeError().retryable


def test_metadata_maps_errors():
    d = VideoDownloader(ydl_factory=FakeYDL(error=DownloadError("ERROR: Video unavailable")))
    with pytest.raises(VideoUnavailableError):
        d.extract_metadata("u")


# ------------------------------------------------------------------ download
def _writing_download(size: int, hooks_data: list[dict] | None = None):
    def run(params):
        out = Path(params["paths"]["home"]) / "source.mp4"
        for data in hooks_data or []:
            for hook in params["progress_hooks"]:
                hook(data)
        out.write_bytes(b"\0" * size)
        return {"requested_downloads": [{"filepath": str(out)}], "extractor_key": "Youtube"}
    return run


def test_download_returns_file_with_fixed_name_and_reports_progress(tmp_path):
    requested = [{"filesize": 600}, {"filesize": 400}]
    hooks = [
        {"status": "downloading", "filename": "v", "downloaded_bytes": 300, "total_bytes": 600,
         "info_dict": {"requested_formats": requested}},
        {"status": "finished", "filename": "v", "downloaded_bytes": 600, "total_bytes": 600},
        {"status": "downloading", "filename": "a", "downloaded_bytes": 200, "total_bytes": 400,
         "info_dict": {"requested_formats": requested}},
    ]
    seen = []
    fake = FakeYDL(on_download=_writing_download(1000, hooks))
    path = VideoDownloader(ydl_factory=fake).download(
        "https://youtu.be/x", 720, tmp_path, progress=lambda p, d, t: seen.append((p, d, t)))
    assert path == tmp_path / "source.mp4"
    assert seen == [(30, 300, 1000), (80, 800, 1000)]
    assert fake.params["outtmpl"] == "source.%(ext)s"  # never derived from the remote title


def test_download_cancel_via_hook(tmp_path):
    ev = threading.Event()
    ev.set()
    fake = FakeYDL(on_download=_writing_download(10, [{"status": "downloading", "downloaded_bytes": 1}]))
    with pytest.raises(TaskCancelledError):
        VideoDownloader(ydl_factory=fake).download("u", 360, tmp_path, cancel_event=ev)


def test_download_aborts_when_too_large(tmp_path):
    hooks = [{"status": "downloading", "filename": "v", "downloaded_bytes": 10, "total_bytes": 5000}]
    fake = FakeYDL(on_download=_writing_download(10, hooks))
    with pytest.raises(FileTooLargeError):
        VideoDownloader(max_filesize=1000, ydl_factory=fake).download("u", 360, tmp_path)


def test_download_rejects_oversized_output(tmp_path):
    fake = FakeYDL(on_download=_writing_download(2000))
    with pytest.raises(FileTooLargeError):
        VideoDownloader(max_filesize=1000, ydl_factory=fake).download("u", 360, tmp_path)


def test_download_missing_output_is_failure(tmp_path):
    fake = FakeYDL(on_download=lambda params: {"extractor_key": "Youtube"})
    with pytest.raises(DownloadFailedError):
        VideoDownloader(ydl_factory=fake).download("u", 360, tmp_path)


def test_download_format_unavailable(tmp_path):
    fake = FakeYDL(error=DownloadError("ERROR: Requested format is not available"))
    with pytest.raises(FormatNotAvailableError):
        VideoDownloader(ydl_factory=fake).download("u", 360, tmp_path)


@pytest.mark.network
def test_live_youtube_metadata():
    meta = VideoDownloader(js_runtimes=("node",)).extract_metadata("https://www.youtube.com/watch?v=jNQXAC9IVRw")
    assert meta.qualities
