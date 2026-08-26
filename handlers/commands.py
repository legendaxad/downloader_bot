from telegram import Update
from telegram.ext import ContextTypes


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        "👋 Hello!\n\n"
        "Send me a video URL from a supported platform."
    )
async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        "👋 Hello!\n\n"
        "How can i help you? Owner:@legend_axad."
    )
