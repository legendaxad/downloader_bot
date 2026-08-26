from pathlib import Path

from telegram import Message


class TelegramService:

    async def send_video(
        self,
        message: Message,
        file_path: Path,
    ) -> None:
        await message.reply_video(
            video=file_path,
        )