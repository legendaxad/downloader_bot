from pathlib import Path

from telegram import Message
from telegram.error import TelegramError

from utils.exceptions import TelegramUploadError
from utils.logger import logger


class TelegramService:

    async def send_video(
        self,
        message: Message,
        file_path: Path,
    ) -> None:
        logger.info(
            "Telegram upload started | file=%s",
            file_path,
        )

        try:
            await message.reply_video(
                video=file_path,
            )

            logger.info(
                "Telegram upload completed | file=%s",
                file_path,
            )

        except TelegramError as error:
            logger.exception(
                "Telegram upload failed | file=%s",
                file_path,
            )

            raise TelegramUploadError(
                "Failed to upload video to Telegram."
            ) from error