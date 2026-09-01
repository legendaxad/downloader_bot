from pathlib import Path

from telegram import Bot
from telegram.error import TelegramError

from utils.exceptions import TelegramUploadError
from utils.logger import logger


class TelegramService:

    async def send_video(
        self,
        bot: Bot,
        chat_id: int,
        file_path: Path,
    ) -> None:

        try:
            logger.info(
                "Uploading video to Telegram | "
                "chat_id=%s | file=%s",
                chat_id,
                file_path,
            )

            with file_path.open("rb") as video:
                await bot.send_video(
                    chat_id=chat_id,
                    video=video,
                )

            logger.info(
                "Video uploaded successfully | "
                "chat_id=%s | file=%s",
                chat_id,
                file_path,
            )

        except TelegramError as error:
            logger.exception(
                "Telegram upload failed | "
                "chat_id=%s | file=%s",
                chat_id,
                file_path,
            )

            raise TelegramUploadError(
                "Failed to upload video to Telegram."
            ) from error