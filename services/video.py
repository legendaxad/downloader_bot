import yt_dlp


class VideoService:

    def get_metadata(self, url: str) -> dict:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                url,
                download=False,
            )

        qualities = set()

        for format_info in info.get("formats", []):
            height = format_info.get("height")
            video_codec = format_info.get("vcodec")

            if not height:
                continue

            if video_codec == "none":
                continue

            if height >= 144:
                qualities.add(height)

        return {
            "id": info.get("id"),
            "title": info.get("title"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "uploader": info.get("uploader"),
            "webpage_url": info.get("webpage_url"),
            "qualities": sorted(qualities),
        }