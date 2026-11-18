"""Telegram bot entry point: ``python bot.py``."""

from __future__ import annotations

from telegram import Update

from config import get_settings
from loader import create_application
from utils.logger import logger, setup_logging


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format, settings.log_file)
    application = create_application(settings)
    logger.info("Bot is starting…")
    # run_polling installs SIGINT/SIGTERM handlers and shuts down gracefully.
    application.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY], drop_pending_updates=False)


if __name__ == "__main__":
    main()
