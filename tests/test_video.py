"""FFmpeg processing tests. Pure logic is always tested; real encodes need ffmpeg."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from services.video import MediaInfo, ProcessingMode, VideoProcessor, build_ffmpeg_command, choose_mode, parse_probe
from utils.exceptions import MediaProcessingError, TaskCancelledError

needs_ffmpeg = pytest.mark.skipif(not VideoProcessor.available(), reason="ffmpeg not installed")


def info(v="h264", a="aac", pix="yuv420p"):
    return MediaInfo("mov,mp4", v, a, pix, 640, 360, 5.0)


@pytest.mark.parametrize("media,mode", [
    (info(), ProcessingMode.REMUX),
    (info(a=None), ProcessingMode.REMUX),
    (info(a="opus"), ProcessingMode.AUDIO_ONLY),
    (info(v="vp9", a="opus"), ProcessingMode.TRANSCODE),
    (info(v="h264", pix="yuv444p"), ProcessingMode.TRANSCODE),
    (info(v="av1"), ProcessingMode.TRANSCODE),
])
def test_choose_mode(media, mode):
    assert choose_mode(media) is mode


def test_ffmpeg_commands_are_safe_arg_lists():
    src, dst = Path("in; rm -rf /.mkv"), Path("out.mp4")
    cmd = build_ffmpeg_command(src, dst, ProcessingMode.TRANSCODE)
    assert str(src) in cmd  # passed as a single argv element, never through a shell
    joined = " ".join(cmd)
    for flag in ("libx264", "yuv420p", "+faststart", "aac"):
        assert flag in joined
    remux = build_ffmpeg_command(src, dst, ProcessingMode.REMUX)
    assert "copy" in remux and "libx264" not in remux and "+faststart" in remux


def test_parse_probe():
    data = {"format": {"format_name": "matroska,webm", "duration": "12.5"},
            "streams": [{"codec_type": "video", "codec_name": "vp9", "pix_fmt": "yuv420p", "width": 1280,
                         "height": 720}, {"codec_type": "audio", "codec_name": "opus"}]}
    parsed = parse_probe(data)
    assert (parsed.video_codec, parsed.audio_codec, parsed.width, parsed.duration) == ("vp9", "opus", 1280, 12.5)


def make_clip(path: Path, vcodec: str, acodec: str, size: str = "320x240", seconds: int = 2) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds),
         "-c:v", vcodec, "-pix_fmt", "yuv420p", "-c:a", acodec, str(path)],
        check=True,
    )
    return path


async def codecs(path: Path) -> tuple[str | None, str | None, str | None]:
    probed = await VideoProcessor().probe(path)
    return probed.video_codec, probed.audio_codec, probed.pix_fmt


@needs_ffmpeg
async def test_compatible_file_is_only_remuxed(tmp_path):
    src = make_clip(tmp_path / "src.mkv", "libx264", "aac")
    seen: list[int | None] = []
    result = await VideoProcessor().prepare_for_telegram(src, tmp_path, progress=seen.append)
    assert result.mode is ProcessingMode.REMUX
    assert await codecs(result.path) == ("h264", "aac", "yuv420p")
    assert result.thumbnail and result.thumbnail.stat().st_size < 200 * 1024
    assert result.duration == 2 and seen[-1] == 100
    # +faststart puts the moov atom before mdat
    head = result.path.read_bytes()[:4096]
    assert head.find(b"moov") != -1 and (head.find(b"mdat") == -1 or head.find(b"moov") < head.find(b"mdat"))


@needs_ffmpeg
async def test_h264_with_other_audio_transcodes_audio_only(tmp_path):
    src = make_clip(tmp_path / "src.mkv", "libx264", "libopus")
    result = await VideoProcessor().prepare_for_telegram(src, tmp_path)
    assert result.mode is ProcessingMode.AUDIO_ONLY
    assert await codecs(result.path) == ("h264", "aac", "yuv420p")


@needs_ffmpeg
async def test_incompatible_file_is_transcoded_with_even_dimensions(tmp_path):
    src = make_clip(tmp_path / "src.mkv", "mpeg4", "libopus", size="321x241")
    result = await VideoProcessor().prepare_for_telegram(src, tmp_path)
    assert result.mode is ProcessingMode.TRANSCODE
    assert await codecs(result.path) == ("h264", "aac", "yuv420p")
    assert result.width % 2 == 0 and result.height % 2 == 0


@needs_ffmpeg
async def test_processing_can_be_cancelled(tmp_path):
    src = make_clip(tmp_path / "src.mkv", "mpeg4", "aac", size="640x480", seconds=20)
    ev = threading.Event()
    ev.set()
    with pytest.raises(TaskCancelledError):
        await VideoProcessor().prepare_for_telegram(src, tmp_path, cancel_event=ev)


@needs_ffmpeg
async def test_corrupt_input_raises_media_error(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(MediaProcessingError):
        await VideoProcessor().prepare_for_telegram(bad, tmp_path)


async def test_missing_ffmpeg_binary(tmp_path):
    with pytest.raises(MediaProcessingError):
        await VideoProcessor(ffprobe="definitely-not-ffprobe").probe(tmp_path / "x.mp4")
