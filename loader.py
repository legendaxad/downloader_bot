"""Application initialisation: wires settings, Redis, MySQL, services and handlers."""

from __future__ import annotations

import asyncio
import contextlib

from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from config import Settings, get_settings
from handlers.callbacks import (
    handle_cancel_callback,
    handle_dismiss_callback,
    handle_quality_callback,
    handle_stale_callback,
)
from handlers.commands import (
    about_command,
    cancel_command,
    handle_language_callback,
    handle_menu_callback,
    help_command,
    language_command,
    start_command,
)
from handlers.deps import DEPS_KEY, BotDeps
from handlers.errors import error_handler
from handlers.messages import handle_message
from keyboards.buttons import CANCEL_PATTERN, DISMISS_PATTERN, LANGUAGE_PATTERN, MENU_PATTERN, QUALITY_PATTERN
from services.database import Database, DownloadRepository, UserRepository
from services.downloader import VideoDownloader
from services.queue import TaskQueue
from services.redis import create_redis
from services.users import UserService
from utils.i18n import DEFAULT_LANGUAGE, LANGUAGES, t
from utils.logger import logger

COMMAND_NAMES = ("start", "help", "language", "cancel", "about")


def create_database(settings: Settings) -> Database | None:
    if not settings.mysql_enabled:
        logger.warning("MYSQL_HOST is not set: user data and download history will NOT be saved to MySQL")
        return None
    return Database(host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
                    password=settings.mysql_password, database=settings.mysql_database)


def create_application(settings: Settings | None = None) -> Application:
    settings = settings or get_settings()
    builder = (
        Application.builder()
        .token(settings.require_bot_token())
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(30)
        .concurrent_updates(True)  # one user's slow metadata fetch must not block others
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
    )
    if settings.telegram_api_base_url:
        builder = builder.base_url(settings.telegram_api_base_url)
    application = builder.build()
    application.bot_data["settings"] = settings

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler(["language", "lang"], language_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_quality_callback, pattern=QUALITY_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_dismiss_callback, pattern=DISMISS_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_cancel_callback, pattern=CANCEL_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_language_callback, pattern=LANGUAGE_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_menu_callback, pattern=MENU_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_stale_callback))  # legacy/unknown buttons
    application.add_error_handler(error_handler)
    return application


async def _post_init(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    redis_client = create_redis(settings.redis_url)
    await redis_client.ping()

    database = create_database(settings)
    if database is not None:
        await database.connect()

    queue = TaskQueue(
        redis_client,
        task_ttl=settings.task_ttl_seconds,
        session_ttl=settings.session_ttl_seconds,
        heartbeat_ttl=settings.stale_task_seconds,
        max_active_per_user=settings.max_active_tasks_per_user,
        history=DownloadRepository(database) if database else None,
    )
    application.bot_data[DEPS_KEY] = BotDeps(
        settings=settings,
        queue=queue,
        downloader=VideoDownloader(
            max_filesize=settings.max_upload_bytes,
            max_duration=settings.max_duration_seconds,
            js_runtimes=settings.ytdlp_js_runtimes,
        ),
        metadata_slots=asyncio.Semaphore(settings.metadata_concurrency),
        users=UserService(redis_client, UserRepository(database) if database else None),
    )
    application.bot_data["redis"] = redis_client
    application.bot_data["database"] = database
    application.bot_data["heartbeat"] = asyncio.create_task(_heartbeat(queue, settings.worker_name))
    await _set_commands(application)
    logger.info("Bot initialised")


async def _set_commands(application: Application) -> None:
    """Command menu in every supported language (Telegram picks by the user's app language)."""
    for lang in LANGUAGES:
        commands = [BotCommand(name, t(f"cmd.{name}", lang)) for name in COMMAND_NAMES]
        with contextlib.suppress(Exception):
            if lang == DEFAULT_LANGUAGE:
                await application.bot.set_my_commands(commands)
            else:
                await application.bot.set_my_commands(commands, language_code=lang)


async def _post_shutdown(application: Application) -> None:
    heartbeat = application.bot_data.get("heartbeat")
    if heartbeat:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    redis_client = application.bot_data.get("redis")
    if redis_client is not None:
        await redis_client.aclose()
    database = application.bot_data.get("database")
    if database is not None:
        await database.close()
    logger.info("Bot shut down cleanly")


async def _heartbeat(queue: TaskQueue, name: str) -> None:
    while True:
        try:
            await queue.component_heartbeat("bot", name, ttl=90)
        except Exception as error:
            logger.warning("Bot heartbeat failed: %s", error)
        await asyncio.sleep(30)
