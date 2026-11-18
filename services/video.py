"""FFmpeg-based video processing for Telegram delivery.

Strategy (avoid unnecessary transcoding):

* H.264 (yuv420p) + AAC/no audio  → stream copy into MP4 with +faststart (cheap remux)
* H.264 (yuv420p) + other audio   → copy video, re-encode audio to AAC
* anything else                   → full transcode to H.264/yuv420p + AAC

All subprocesses use argument lists (no shell) and are killable for
cancellation/timeouts.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from utils.exceptions import MediaProcessingError, TaskCancelledError
from utils.logger import logger

ProcessingProgress = Callable[[int | None], None]

TELEGRAM_VIDEO_CODECS = {"h264"}
TELEGRAM_AUDIO_CODECS = {"aac"}
TELEGRAM_PIX_FMTS = {"yuv420p", "yuvj420p"}
THUMB_MAX_SIDE = 320


class ProcessingMode(StrEnum):
    REMUX = "remux"
    AUDIO_ONLY = "audio_transcode"
    TRANSCODE = "transcode"


@dataclass(frozen=True)
class MediaInfo:
    container: str
    video_codec: str | None
    audio_codec: str | None
    pix_fmt: str | None
    width: int | None
    height: int | None
    duration: float | None


@dataclass(frozen=True)
class ProcessedVideo:
    path: Path
    width: int | None
    height: int | None
    duration: int | None
    thumbnail: Path | None
    mode: ProcessingMode


def choose_mode(info: MediaInfo) -> ProcessingMode:
    video_ok = info.video_codec in TELEGRAM_VIDEO_CODECS and (info.pix_fmt in TELEGRAM_PIX_FMTS)
    if not video_ok:
        return ProcessingMode.TRANSCODE
    if info.audio_codec is None or info.audio_codec in TELEGRAM_AUDIO_CODECS:
        return ProcessingMode.REMUX
    return ProcessingMode.AUDIO_ONLY


def build_ffmpeg_command(src: Path, dst: Path, mode: ProcessingMode, ffmpeg: str = "ffmpeg") -> list[str]:
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-progress", "pipe:1", "-nostats",
        "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-sn", "-dn", "-map_metadata", "-1",
    ]
    if mode is ProcessingMode.REMUX:
        cmd += ["-c", "copy"]
    elif mode is ProcessingMode.AUDIO_ONLY:
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            # libx264 + yuv420p requires even dimensions.
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:a", "aac", "-b:a", "128k",
        ]
    cmd += ["-movflags", "+faststart", "-f", "mp4", str(dst)]
    return cmd


class VideoProcessor:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", timeout: float = 1800) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.timeout = timeout

    @staticmethod
    def available() -> bool:
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

    async def probe(self, path: Path) -> MediaInfo:
        cmd = [
            self.ffprobe, "-v", "error", "-print_format", "json",
            "-show_entries", "format=format_name,duration",
            "-show_entries", "stream=codec_type,codec_name,pix_fmt,width,height,duration",
            str(path),
        ]
        code, stdout, stderr = await _run(cmd, timeout=60)
        if code != 0:
            raise MediaProcessingError(f"ffprobe failed: {stderr[-500:]}")
        try:
            return parse_probe(json.loads(stdout))
        except (ValueError, KeyError) as error:
            raise MediaProcessingError("invalid ffprobe output") from error

    async def prepare_for_telegram(
        self,
        src: Path,
        out_dir: Path,
        *,
        progress: ProcessingProgress | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ProcessedVideo:
        info = await self.probe(src)
        if info.video_codec is None:
            raise MediaProcessingError("no video stream", key="err.no_video_stream")

        mode = choose_mode(info)
        dst = out_dir / "telegram.mp4"
        logger.info("Processing media", extra={"status": mode.value})
        await self._run_ffmpeg(build_ffmpeg_command(src, dst, mode, self.ffmpeg), info.duration, progress, cancel_event)

        if not dst.is_file() or dst.stat().st_size == 0:
            raise MediaProcessingError("ffmpeg produced no output")

        out_info = await self.probe(dst) if mode is ProcessingMode.TRANSCODE else info
        duration = out_info.duration or info.duration
        thumbnail = await self.make_thumbnail(dst, out_dir / "thumb.jpg", duration)
        return ProcessedVideo(
            path=dst,
            width=out_info.width,
            height=out_info.height,
            duration=int(round(duration)) if duration else None,
            thumbnail=thumbnail,
            mode=mode,
        )

    async def make_thumbnail(self, src: Path, dst: Path, duration: float | None) -> Path | None:
        """Telegram thumbnails: JPEG, <= 320px per side, < 200 KB. Best effort."""
        seek = min(1.0, duration / 2) if duration else 0
        cmd = [
            self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-ss", f"{seek:.2f}", "-i", str(src), "-frames:v", "1",
            "-vf", f"scale='min({THUMB_MAX_SIDE},iw)':'min({THUMB_MAX_SIDE},ih)':force_original_aspect_ratio=decrease",
            "-q:v", "5", str(dst),
        ]
        try:
            code, _, stderr = await _run(cmd, timeout=60)
        except MediaProcessingError:
            return None
        if code != 0 or not dst.is_file() or dst.stat().st_size > 200 * 1024:
            logger.debug("Thumbnail generation skipped: %s", stderr[-200:])
            return None
        return dst

    async def _run_ffmpeg(
        self,
        cmd: list[str],
        duration: float | None,
        progress: ProcessingProgress | None,
        cancel_event: threading.Event | None,
    ) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise MediaProcessingError(f"{cmd[0]} not installed") from error
        stderr_task = asyncio.create_task(proc.stderr.read())  # type: ignore[union-attr]

        async def read_progress() -> None:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                key, _, value = raw.decode(errors="ignore").strip().partition("=")
                if key == "out_time_us" and progress and duration:
                    try:
                        percent = int(int(value) / 1_000_000 * 100 / duration)
                    except ValueError:
                        continue
                    progress(max(0, min(percent, 99)))

        async def watch_cancel() -> None:
            while cancel_event is not None:
                if cancel_event.is_set():
                    return
                await asyncio.sleep(0.5)
            await asyncio.Event().wait()  # no cancel event → wait forever

        reader = asyncio.create_task(read_progress())
        waiter = asyncio.create_task(proc.wait())
        canceller = asyncio.create_task(watch_cancel())
        try:
            done, _ = await asyncio.wait({waiter, canceller}, timeout=self.timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if waiter not in done:
                _kill(proc)
                await proc.wait()
                if canceller in done:
                    raise TaskCancelledError("processing cancelled")
                raise MediaProcessingError("ffmpeg timed out")
        except asyncio.CancelledError:
            _kill(proc)
            await proc.wait()
            raise
        finally:
            canceller.cancel()
            reader.cancel()
            await asyncio.gather(reader, canceller, return_exceptions=True)

        stderr = (await stderr_task).decode(errors="ignore")
        if proc.returncode != 0:
            raise MediaProcessingError(f"ffmpeg exited with {proc.returncode}: {stderr[-500:]}")
        if progress:
            progress(100)


def parse_probe(data: dict[str, Any]) -> MediaInfo:
    fmt = data.get("format") or {}
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

    def num(value: Any) -> float | None:
        try:
            return float(value) if value not in (None, "N/A") else None
        except (TypeError, ValueError):
            return None

    duration = num(fmt.get("duration")) or (num(video.get("duration")) if video else None)
    return MediaInfo(
        container=str(fmt.get("format_name", "")),
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        pix_fmt=video.get("pix_fmt") if video else None,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        duration=duration,
    )


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _run(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise MediaProcessingError(f"{cmd[0]} not installed") from error
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as error:
        _kill(proc)
        await proc.wait()
        raise MediaProcessingError(f"{cmd[0]} timed out") from error
    except asyncio.CancelledError:
        _kill(proc)
        await proc.wait()
        raise
    return proc.returncode or 0, stdout.decode(errors="ignore"), stderr.decode(errors="ignore")
