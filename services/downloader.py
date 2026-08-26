from pathlib import Path
from utils.logger import logger
import yt_dlp
from yt_dlp.utils import DownloadError

from utils.exceptions import (
    DownloadFailedError,
    FormatNotAvailableError,
    VideoUnavailableError,
)


DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)


class VideoDownloader:

    def download(
        self,
        url: str,
        quality: int,
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

                # When video + audio are merged,
                # yt-dlp creates an MP4 file.
                if filepath.suffix != ".mp4":
                    filepath = filepath.with_suffix(".mp4")

                logger.info(
                    "Download completed | file=%s",
                    filepath,
                )

                return filepath

        except DownloadError as error:
            logger.error(
                "Download failed | url=%s | error=%s",
                url,
                error,
            )
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