from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.ext import CallbackQueryHandler
from handlers.callbacks import handle_quality_callback
from config import BOT_TOKEN
from handlers.commands import start_command,help_command
from handlers.messages import handle_message


def create_application() -> Application:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(300)
        .pool_timeout(30)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )
    application.add_handler(
        CommandHandler("help", help_command)
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_quality_callback,
            pattern=r"^quality:\d+$",
        )
    )

    return application