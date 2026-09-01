from pathlib import Path
import asyncio

import yt_dlp
from yt_dlp.utils import DownloadError

from services.queue import DownloadQueue

from utils.exceptions import (
    DownloadFailedError,
    FormatNotAvailableError,
    VideoUnavailableError,
)

from utils.logger import logger


DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)


class VideoDownloader:

    def __init__(
        self,
        queue: DownloadQueue | None = None,
    ) -> None:
        self.queue = queue or DownloadQueue()

    def download(
        self,
        url: str,
        quality: int,
        *,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> Path:

        options = {
            "format": (
                f"bestvideo[height<={quality}]"
                f"[vcodec^=avc1]"
                f"+bestaudio[acodec^=mp4a]/"

                f"bestvideo[height<={quality}]"
                f"[vcodec^=avc1]"
                f"+bestaudio/"

                f"best[height<={quality}]"
            ),

            "outtmpl": str(
                DOWNLOAD_DIR / "%(id)s_%(height)sp.%(ext)s"
            ),

            "merge_output_format": "mp4",

            "noplaylist": True,

            "progress_hooks": [
                self._progress_hook(
                    job_id,
                    loop,
                )
            ],
        }

        try:
            logger.info(
                "Download started | quality=%sp | url=%s",
                quality,
                url,
            )

            with yt_dlp.YoutubeDL(options) as ydl:

                info = ydl.extract_info(
                    url,
                    download=True,
                )

                filepath = Path(
                    ydl.prepare_filename(info)
                )

                # After merging, yt-dlp may change
                # the extension/container to mp4.
                if not filepath.exists():

                    merged_path = filepath.with_suffix(
                        ".mp4"
                    )

                    if merged_path.exists():
                        filepath = merged_path

                logger.info(
                    "Download completed | file=%s",
                    filepath,
                )

                return filepath

        except DownloadError as error:

            message = str(error).lower()

            if (
                "requested format is not available"
                in message
            ):
                raise FormatNotAvailableError(
                    "Requested quality is not available."
                ) from error

            if (
                "private video" in message
                or "video unavailable" in message
                or "this video is unavailable" in message
            ):
                raise VideoUnavailableError(
                    "Video is unavailable."
                ) from error

            raise DownloadFailedError(
                "Video download failed."
            ) from error

    def _progress_hook(
        self,
        job_id: str | None,
        loop: asyncio.AbstractEventLoop | None,
    ):
        def hook(data: dict) -> None:

            if not job_id or loop is None:
                return

            status = data.get("status")

            if status == "downloading":

                total_bytes = (
                    data.get("total_bytes")
                    or data.get("total_bytes_estimate")
                )

                downloaded_bytes = (
                    data.get("downloaded_bytes", 0)
                )

                if not total_bytes:
                    return

                progress = int(
                    downloaded_bytes
                    / total_bytes
                    * 100
                )

                progress = max(
                    0,
                    min(progress, 100),
                )

                asyncio.run_coroutine_threadsafe(
                    self.queue.set_progress(
                        job_id,
                        progress,
                    ),
                    loop,
                )

            elif status == "finished":

                asyncio.run_coroutine_threadsafe(
                    self.queue.set_progress(
                        job_id,
                        100,
                    ),
                    loop,
                )

        return hook