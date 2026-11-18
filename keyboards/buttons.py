"""Inline keyboards.

Callback data formats (Telegram limits callback_data to 64 bytes):

* ``q:<session_id>:<height>``  — quality selection (height 0 = best available)
* ``x:<session_id>``           — dismiss a selection
* ``c:<task_id>``              — cancel a task
* ``lang:<code>``              — choose interface language
* ``menu:<item>``              — main menu (help / about / lang)
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.downloader import QualityOption
from utils.formatting import fmt_size, quality_label, truncate
from utils.i18n import DEFAULT_LANGUAGE, LANGUAGES, t

QUALITY_PATTERN = r"^q:[0-9a-f]{12}:\d{1,5}$"
DISMISS_PATTERN = r"^x:[0-9a-f]{12}$"
CANCEL_PATTERN = r"^c:[0-9a-f]{32}$"
LANGUAGE_PATTERN = r"^lang:(" + "|".join(LANGUAGES) + r")$"
MENU_PATTERN = r"^menu:(help|about|lang)$"


def admin_url(admin: str) -> str:
    return f"https://t.me/{admin}"


def admin_button(admin: str, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardButton:
    return InlineKeyboardButton(t("btn.admin", lang), url=admin_url(admin))


def admin_keyboard(admin: str, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[admin_button(admin, lang)]])


def main_menu_keyboard(admin: str, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn.help", lang), callback_data="menu:help"),
         InlineKeyboardButton(t("btn.language", lang), callback_data="menu:lang")],
        [InlineKeyboardButton(t("btn.about", lang), callback_data="menu:about"),
         admin_button(admin, lang)],
    ])


def language_keyboard(current: str | None = None) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"{'✅ ' if code == current else ''}{label}", callback_data=f"lang:{code}")
        for code, label in LANGUAGES.items()
    ]
    return InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])


def quality_keyboard(session_id: str, qualities: list[QualityOption],
                     lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    buttons = []
    for option in qualities:
        label = option.label if option.height else quality_label(0, lang)
        text = label if not option.filesize else f"{label} · ~{fmt_size(option.filesize)}"
        buttons.append(InlineKeyboardButton(text=text, callback_data=f"q:{session_id}:{option.height}"))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(t("btn.close", lang), callback_data=f"x:{session_id}")])
    return InlineKeyboardMarkup(rows)


def cancel_keyboard(task_id: str, lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t("btn.cancel", lang), callback_data=f"c:{task_id}")]])


def active_tasks_keyboard(tasks: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """``tasks`` is a list of (task_id, label)."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"🚫 {truncate(label, 40)}", callback_data=f"c:{task_id}")] for task_id, label in tasks]
    )
