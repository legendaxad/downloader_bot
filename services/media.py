from pathlib import Path
import json
import subprocess
from utils.exceptions import MediaProcessingError
from utils.logger import logger
class MediaService:

    def inspect(self, file_path: Path) -> dict:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=format_name",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "json",
            str(file_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
        )

        return json.loads(result.stdout)

    def is_telegram_compatible(
        self,
        file_path: Path,
    ) -> bool:
        info = self.inspect(file_path)

        format_name = info.get(
            "format",
            {},
        ).get(
            "format_name",
            "",
        )

        streams = info.get("streams", [])

        video_codec = None
        audio_codec = None

        for stream in streams:
            if stream.get("codec_type") == "video":
                video_codec = stream.get("codec_name")

            elif stream.get("codec_type") == "audio":
                audio_codec = stream.get("codec_name")

        return (
            "mp4" in format_name.split(",")
            and video_codec == "h264"
            and audio_codec == "aac"
        )

    def remux(
        self,
        input_path: str | Path,
    ) -> Path:
        input_path = Path(input_path)

        output_path = input_path.with_name(
            f"{input_path.stem}_telegram.mp4"
        )

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        subprocess.run(
            command,
            check=True,
        )

        return output_path

    def transcode(
        self,
        input_path: str | Path,
    ) -> Path:
        input_path = Path(input_path)

        output_path = input_path.with_name(
            f"{input_path.stem}_telegram.mp4"
        )

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),

            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",

            "-c:a",
            "aac",
            "-b:a",
            "128k",

            "-pix_fmt",
            "yuv420p",

            "-movflags",
            "+faststart",

            str(output_path),
        ]

        try:
            subprocess.run(
                command,
                check=True,
            )

        except subprocess.CalledProcessError as error:
            raise MediaProcessingError(
                "Failed to process video."
            ) from error

        return output_path

    def prepare_for_telegram(
        self,
        file_path: str | Path,
    ) -> Path:
        file_path = Path(file_path)

        if self.is_telegram_compatible(file_path):
            logger.info(
                "Media already Telegram compatible | file=%s",
                file_path,
            )

            return self.remux(file_path)

        logger.info(
            "Media requires transcoding | file=%s",
            file_path,
        )

        return self.transcode(file_path)