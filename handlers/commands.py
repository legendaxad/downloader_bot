"""/start, /help, /about, /language, /cancel and the main menu."""

from __future__ import annotations

import contextlib
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from handlers.deps import get_deps, profile_of, user_language
from keyboards.buttons import active_tasks_keyboard, admin_keyboard, language_keyboard, main_menu_keyboard
from utils.formatting import quality_label, render_status, status_text, truncate
from utils.i18n import t
from utils.validators import PLATFORM_LABELS

PLATFORMS = ", ".join(PLATFORM_LABELS.values())
EXAMPLE_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def welcome_text(name: str, lang: str) -> str:
    return t("start.welcome", lang, name=escape(name or "👋"), platforms=PLATFORMS, example=EXAMPLE_URL)


def help_text(context: ContextTypes.DEFAULT_TYPE, lang: str) -> str:
    s = get_deps(context).settings
    return t("help.text", lang, platforms=PLATFORMS, max_mb=s.max_upload_mb,
             max_minutes=s.max_duration_seconds // 60, max_active=s.max_active_tasks_per_user,
             admin=s.admin_username)


def about_text(context: ContextTypes.DEFAULT_TYPE, lang: str) -> str:
    return t("about.text", lang, admin=get_deps(context).settings.admin_username)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    deps = get_deps(context)
    await deps.users.touch(profile_of(user))

    if await deps.users.chosen_language(user.id) is None:
        # First visit: ask for the language before anything else.
        await message.reply_text(t("lang.choose"), parse_mode=ParseMode.HTML, reply_markup=language_keyboard())
        return

    lang = await deps.users.language(user.id)
    await message.reply_text(
        welcome_text(user.first_name, lang), parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=main_menu_keyboard(deps.settings.admin_username, lang),
    )


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    current = await get_deps(context).users.chosen_language(user.id)
    await message.reply_text(t("lang.choose"), parse_mode=ParseMode.HTML, reply_markup=language_keyboard(current))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    lang = await user_language(context, update.effective_user)
    await update.effective_message.reply_text(
        help_text(context, lang), parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(get_deps(context).settings.admin_username, lang),
    )


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    lang = await user_language(context, update.effective_user)
    await update.effective_message.reply_text(
        about_text(context, lang), parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(get_deps(context).settings.admin_username, lang),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    lang = await user_language(context, user)
    queue = get_deps(context).queue
    tasks = await queue.active_tasks(user.id)

    if not tasks:
        await message.reply_text(t("cancel.none", lang))
        return

    if len(tasks) == 1:
        task = tasks[0]
        result = await queue.request_cancel(task.task_id, user.id)
        if result is None:
            await message.reply_text(t("cancel.finished", lang))
            return
        if result.status.is_terminal and task.status_message_id:
            with contextlib.suppress(TelegramError):
                await context.bot.edit_message_text(
                    chat_id=task.chat_id, message_id=task.status_message_id,
                    text=render_status(task.title, task.quality, result.status, lang=lang),
                    parse_mode=ParseMode.HTML,
                )
        await message.reply_text(t("cancel.cancelling", lang, title=escape(truncate(task.title, 60))),
                                 parse_mode=ParseMode.HTML)
        return

    lines = [t("cancel.which", lang), ""]
    for task in tasks:
        lines.append(f"• {escape(truncate(task.title, 50))} — {quality_label(task.quality, lang)} "
                     f"({status_text(task.status, lang)})")
    await message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=active_tasks_keyboard([(t_.task_id, t_.title or t_.url) for t_ in tasks]),
    )


# ---------------------------------------------------------------- inline menu
async def handle_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    deps = get_deps(context)
    lang = query.data.split(":", 1)[1]
    await deps.users.set_language(query.from_user.id, lang)
    await query.answer(t("lang.changed", lang).replace("<b>", "").replace("</b>", ""))
    with contextlib.suppress(TelegramError):
        await query.edit_message_text(
            f"{t('lang.changed', lang)}\n\n{welcome_text(query.from_user.first_name, lang)}",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=main_menu_keyboard(deps.settings.admin_username, lang),
        )


async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.message:
        return
    item = query.data.split(":", 1)[1]
    lang = await user_language(context, query.from_user)
    await query.answer()
    admin = get_deps(context).settings.admin_username

    if item == "lang":
        current = await get_deps(context).users.chosen_language(query.from_user.id)
        await query.message.reply_text(t("lang.choose"), parse_mode=ParseMode.HTML,
                                       reply_markup=language_keyboard(current))
    elif item == "help":
        await query.message.reply_text(help_text(context, lang), parse_mode=ParseMode.HTML,
                                       reply_markup=admin_keyboard(admin, lang))
    elif item == "about":
        await query.message.reply_text(about_text(context, lang), parse_mode=ParseMode.HTML,
                                       reply_markup=admin_keyboard(admin, lang))
