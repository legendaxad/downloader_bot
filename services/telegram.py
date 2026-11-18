"""Telegram messaging/upload helpers used by the worker.

All Telegram API errors during upload are mapped to ``TelegramUploadError``
(kept separate from downloader exceptions) with a retryable flag.
"""

from __future__ import annotations

from pathlib import Path

from telegram import Bot, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.request import HTTPXRequest

from utils.exceptions import TelegramUploadError
from utils.formatting import truncate
from utils.logger import logger


def create_bot(token: str, *, base_url: str = "", upload_timeout: float = 600) -> Bot:
    request = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=30,
        read_timeout=upload_timeout,
        write_timeout=upload_timeout,
        pool_timeout=30,
        media_write_timeout=upload_timeout,
    )
    kwargs = {"base_url": base_url} if base_url else {}
    return Bot(token=token, request=request, **kwargs)


def map_telegram_error(error: TelegramError) -> TelegramUploadError:
    if isinstance(error, RetryAfter):
        retry_after = error.retry_after
        seconds = retry_after.total_seconds() if hasattr(retry_after, "total_seconds") else float(retry_after)
        return TelegramUploadError(str(error), retryable=True, retry_after=seconds)
    if isinstance(error, Forbidden):
        return TelegramUploadError(str(error), retryable=False, key="err.bot_blocked")
    if isinstance(error, BadRequest):
        message = str(error).lower()
        if "too large" in message or "entity too large" in message or "file is too big" in message:
            return TelegramUploadError(str(error), retryable=False, key="err.tg_too_large")
        return TelegramUploadError(str(error), retryable=False)
    if isinstance(error, (TimedOut, NetworkError)):
        return TelegramUploadError(str(error), retryable=True)
    return TelegramUploadError(str(error), retryable=False)


class TelegramService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_video(
        self,
        chat_id: int,
        file_path: Path,
        *,
        caption: str = "",
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
        thumbnail: Path | None = None,
        reply_to_message_id: int | None = None,
    ) -> None:
        logger.info("Uploading video", extra={"chat_id": chat_id, "size_bytes": file_path.stat().st_size})
        try:
            with file_path.open("rb") as video:
                thumb = thumbnail.open("rb") if thumbnail and thumbnail.is_file() else None
                try:
                    await self.bot.send_video(
                        chat_id=chat_id,
                        video=video,
                        caption=truncate(caption, 1000) or None,
                        parse_mode=ParseMode.HTML,
                        width=width,
                        height=height,
                        duration=duration,
                        thumbnail=thumb,
                        supports_streaming=True,
                        reply_to_message_id=reply_to_message_id or None,
                        allow_sending_without_reply=True,
                    )
                finally:
                    if thumb:
                        thumb.close()
        except TelegramError as error:
            raise map_telegram_error(error) from error

    async def edit_status(
        self, chat_id: int, message_id: int, text: str, markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Best-effort status edit; never raises (status updates must not fail a task)."""
        if not message_id:
            return
        try:
            await self.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text,
                parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True,
            )
        except BadRequest as error:
            if "not modified" not in str(error).lower():
                logger.debug("Status edit failed: %s", error)
        except TelegramError as error:
            logger.debug("Status edit failed: %s", error)

    async def send_text(self, chat_id: int, text: str) -> None:
        try:
            await self.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        except TelegramError as error:
            logger.warning("Failed to send message: %s", error, extra={"chat_id": chat_id})
