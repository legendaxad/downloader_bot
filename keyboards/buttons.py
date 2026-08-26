from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def quality_keyboard(qualities: list[int]) -> InlineKeyboardMarkup:
    buttons = []

    for quality in qualities:
        buttons.append(
            InlineKeyboardButton(
                text=f"{quality}p",
                callback_data=f"quality:{quality}",
            )
        )

    rows = []

    for index in range(0, len(buttons), 2):
        rows.append(buttons[index:index + 2])

    return InlineKeyboardMarkup(rows)