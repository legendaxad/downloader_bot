"""URL message → validate → metadata (off the event loop) → quality selection."""

from __future__ import annotations

import asyncio
from html import escape

from telegram import Message, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from handlers.deps import BotDeps, get_deps, user_language
from keyboards.buttons import admin_keyboard, quality_keyboard
from services.downloader import VideoMetadata
from utils.exceptions import AppError, FileTooLargeError
from utils.formatting import fmt_duration, fmt_size, truncate
from utils.i18n import t
from utils.logger import logger
from utils.validators import PLATFORM_LABELS, Platform, extract_url, validate_url


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    if not message or not message.text or not user:
        return

    deps = get_deps(context)
    raw_url = extract_url(message.text)
    if raw_url is None:
        if message.chat.type == ChatType.PRIVATE:  # stay silent on chatter in groups
            lang = await user_language(context, user)
            await message.reply_text(t("url.hint", lang))
        return

    lang = await user_language(context, user)
    log = {"user_id": user.id}
    status_msg: Message | None = None
    try:
        url, platform = validate_url(raw_url)
        log["platform"] = platform.value
        await deps.queue.check_rate_limit(user.id, deps.settings.user_requests_per_minute)

        status_msg = await message.reply_text(t("meta.fetching", lang))
        metadata = await _fetch_metadata(deps, url)

        if not metadata.qualities:
            raise FileTooLargeError("all qualities exceed upload limit", key="err.all_too_large",
                                    params={"max_mb": deps.settings.max_upload_mb})

        session_id = await deps.queue.create_session(
            user_id=user.id, chat_id=message.chat_id, url=url, platform=platform.value,
            title=metadata.title, qualities=[q.height for q in metadata.qualities],
        )
        await _show_selection(message, status_msg, metadata, platform, session_id, deps.settings.max_upload_mb, lang)
        logger.info("Metadata shown", extra=log)

    except AppError as error:
        logger.info("Request rejected: %s", error, extra=log | {"error_type": type(error).__name__})
        await _reply(message, status_msg, f"❌ {error.localized(lang)}",
                     admin_keyboard(deps.settings.admin_username, lang))


async def _fetch_metadata(deps: BotDeps, url: str) -> VideoMetadata:
    """Run blocking yt-dlp extraction in a thread, bounded by a semaphore and a timeout."""
    async with deps.metadata_slots:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(deps.downloader.extract_metadata, url),
                deps.settings.metadata_timeout_seconds,
            )
        except TimeoutError:
            raise AppError("metadata timeout", key="err.metadata_timeout") from None


async def _show_selection(
    message: Message, status_msg: Message, meta: VideoMetadata, platform: Platform, session_id: str,
    max_mb: int, lang: str,
) -> None:
    lines = [f"🎬 <b>{escape(truncate(meta.title, 150))}</b>"]
    if meta.uploader:
        lines.append(f"👤 {escape(truncate(meta.uploader, 60))}")
    lines.append(f"📱 {PLATFORM_LABELS[platform]}   ⏱ {fmt_duration(meta.duration)}")
    if meta.too_large_qualities:
        hidden = ", ".join(f"{q.label} (~{fmt_size(q.filesize)})" for q in meta.too_large_qualities[:4])
        lines.append(f"\n<i>{escape(t('meta.hidden', lang, max_mb=max_mb, list=hidden))}</i>")
    lines.append(f"\n{t('meta.choose', lang)}")
    caption = "\n".join(lines)
    keyboard = quality_keyboard(session_id, meta.qualities, lang)

    if meta.thumbnail:
        try:
            await message.reply_photo(meta.thumbnail, caption=caption, parse_mode=ParseMode.HTML,
                                      reply_markup=keyboard)
            await _delete(status_msg)
            return
        except TelegramError as error:  # Telegram couldn't fetch the thumbnail URL
            logger.debug("Thumbnail send failed, falling back to text: %s", error)
    await status_msg.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=keyboard,
                               disable_web_page_preview=True)


async def _reply(message: Message, status_msg: Message | None, text: str, markup=None) -> None:  # noqa: ANN001
    try:
        if status_msg:
            await status_msg.edit_text(text, reply_markup=markup)
        else:
            await message.reply_text(text, reply_markup=markup)
    except TelegramError as error:
        logger.debug("Reply failed: %s", error)


async def _delete(msg: Message) -> None:
    try:
        await msg.delete()
    except TelegramError:
        pass
