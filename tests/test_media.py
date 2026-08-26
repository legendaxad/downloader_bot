from pathlib import Path

from services import MediaService


media_service = MediaService()

output_path = media_service.convert_to_telegram_mp4(
    Path("downloads/Wx5HvKTefh8.mp4")
)

print(output_path)