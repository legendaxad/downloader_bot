"""Inline keyboard callbacks: quality selection, dismiss, cancel."""

from __future__ import annotations

from telegram import CallbackQuery, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from handlers.deps import get_deps, user_language
from keyboards.buttons import cancel_keyboard
from models.task import TaskStatus
from utils.exceptions import AppError
from utils.formatting import quality_label, render_status
from utils.i18n import t
from utils.logger import logger


async def handle_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.message:
        return
    deps = get_deps(context)
    user_id = query.from_user.id
    lang = await user_language(context, query.from_user)

    try:
        _, session_id, raw_quality = query.data.split(":")
        quality = int(raw_quality)
    except ValueError:
        await query.answer(t("cb.invalid", lang), show_alert=True)
        return

    session = await deps.queue.get_session(session_id)
    if session is None:
        await query.answer(t("cb.expired", lang), show_alert=True)
        await _remove_keyboard(query)
        return
    if session.user_id != user_id:
        await query.answer(t("cb.not_yours", lang), show_alert=True)
        return
    if quality not in session.qualities:
        await query.answer(t("cb.quality_unavailable", lang), show_alert=True)
        return
    if session.task_id:  # repeated press
        await query.answer(t("cb.already_queued", lang))
        return

    status_msg = await query.message.reply_text(
        render_status(session.title, quality, TaskStatus.QUEUED, lang=lang), parse_mode=ParseMode.HTML,
    )
    try:
        result = await deps.queue.submit(session, quality, status_message_id=status_msg.message_id, lang=lang)
    except AppError as error:
        await _safe_delete(status_msg)
        await query.answer(error.localized(lang), show_alert=True)
        return

    if not result.created:
        await _safe_delete(status_msg)
        await query.answer(t("cb.duplicate", lang))
        await _remove_keyboard(query)
        return

    position = await deps.queue.queue_position(result.task.task_id)
    detail = t("queue.position", lang, position=position) if position and position > 1 else t("queue.starting", lang)
    await status_msg.edit_text(
        render_status(session.title, quality, TaskStatus.QUEUED, detail=detail, lang=lang),
        parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard(result.task.task_id, lang),
    )
    await query.answer(t("cb.queued", lang, quality=quality_label(quality, lang)))
    await _remove_keyboard(query)


async def handle_dismiss_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    try:
        await query.message.delete()  # type: ignore[union-attr]
    except TelegramError:
        await _remove_keyboard(query)


async def handle_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    task_id = query.data.split(":", 1)[1]
    queue = get_deps(context).queue
    lang = await user_language(context, query.from_user)

    task = await queue.request_cancel(task_id, query.from_user.id)
    if task is None:
        await query.answer(t("cancel.finished", lang), show_alert=True)
        await _remove_keyboard(query)
        return

    if task.status is TaskStatus.CANCELLED:  # was still waiting in the queue
        await query.answer(t("cancel.done", lang))
        if task.status_message_id:
            await _edit(context, task.chat_id, task.status_message_id,
                        render_status(task.title, task.quality, TaskStatus.CANCELLED, lang=task.lang))
        if query.message and query.message.message_id != task.status_message_id:
            await _remove_keyboard(query)
        return

    await query.answer(t("cancel.requested", lang))
    logger.info("Cancel requested by user", extra={"task_id": task_id, "user_id": query.from_user.id})
    if query.message and query.message.message_id != task.status_message_id:
        await _remove_keyboard(query)


async def handle_stale_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        lang = await user_language(context, update.callback_query.from_user)
        await update.callback_query.answer(t("cb.expired", lang), show_alert=True)


async def _remove_keyboard(query: CallbackQuery) -> None:
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass


async def _safe_delete(message) -> None:  # noqa: ANN001
    try:
        await message.delete()
    except TelegramError:
        pass


async def _edit(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, text: str) -> None:
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                                            parse_mode=ParseMode.HTML)
    except TelegramError:
        pass
