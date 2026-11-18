"""Global error handler: log details, show users a safe localized message + admin contact."""

from __future__ import annotations

from redis.exceptions import RedisError
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from handlers.deps import get_deps
from keyboards.buttons import admin_keyboard
from utils.i18n import t
from utils.logger import logger


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    user = update.effective_user if isinstance(update, Update) else None
    logger.error("Unhandled error in handler", exc_info=error, extra={"user_id": user.id if user else None})

    if not isinstance(update, Update):
        return
    lang = "en"
    try:  # language lookup needs Redis, which may be the thing that failed
        lang = await get_deps(context).users.language(user.id) if user else "en"
    except Exception:
        pass
    key = "err.queue" if isinstance(error, (RedisError, OSError)) else "err.generic"
    text = f"❌ {t(key, lang)}"
    try:
        if update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
        elif update.effective_message:
            admin = get_deps(context).settings.admin_username
            await update.effective_message.reply_text(text, reply_markup=admin_keyboard(admin, lang))
    except TelegramError:
        pass
