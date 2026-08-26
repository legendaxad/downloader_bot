from pathlib import Path

import yt_dlp


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

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                url,
                download=True,
            )

            filepath = ydl.prepare_filename(info)

        return Path(filepath)