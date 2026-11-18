"""Background worker entry point: ``python worker.py``.

Run as many worker processes as the host can handle; each processes up to
WORKER_CONCURRENCY tasks at once.
"""

from __future__ import annotations

import asyncio
import signal

from config import get_settings
from loader import create_database
from services.database import DownloadRepository
from services.downloader import VideoDownloader
from services.queue import TaskQueue
from services.redis import create_redis
from services.telegram import TelegramService, create_bot
from services.video import VideoProcessor
from utils.logger import logger, setup_logging
from workers.processor import ProcessorConfig, TaskProcessor
from workers.runner import Worker


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format, settings.log_file)
    if not VideoProcessor.available():
        raise SystemExit("ffmpeg/ffprobe not found on PATH")

    redis_client = create_redis(settings.redis_url)
    database = create_database(settings)
    if database is not None:
        await database.connect()
    bot = create_bot(settings.require_bot_token(), base_url=settings.telegram_api_base_url,
                     upload_timeout=settings.upload_timeout_seconds)
    queue = TaskQueue(
        redis_client,
        task_ttl=settings.task_ttl_seconds,
        session_ttl=settings.session_ttl_seconds,
        heartbeat_ttl=settings.stale_task_seconds,
        max_active_per_user=settings.max_active_tasks_per_user,
        history=DownloadRepository(database) if database else None,
    )
    messenger = TelegramService(bot)
    downloader = VideoDownloader(
        max_filesize=settings.max_upload_bytes,
        max_duration=settings.max_duration_seconds,
        js_runtimes=settings.ytdlp_js_runtimes,
    )
    processor = TaskProcessor(
        queue, downloader, VideoProcessor(timeout=settings.processing_timeout_seconds), messenger,
        ProcessorConfig(
            download_dir=settings.download_dir,
            max_upload_bytes=settings.max_upload_bytes,
            max_retries=settings.max_retries,
            retry_base_delay=settings.retry_base_delay_seconds,
            download_timeout=settings.download_timeout_seconds,
            upload_timeout=settings.upload_timeout_seconds,
            worker_name=settings.worker_name,
            admin_username=settings.admin_username,
        ),
    )
    worker = Worker(
        queue, processor, messenger,
        name=settings.worker_name,
        concurrency=settings.worker_concurrency,
        download_dir=settings.download_dir,
        max_retries=settings.max_retries,
        stale_after=settings.stale_task_seconds,
        shutdown_grace=settings.shutdown_grace_seconds,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)

    try:
        await bot.initialize()
        await worker.run()
    finally:
        await bot.shutdown()
        await redis_client.aclose()
        if database is not None:
            await database.close()
        logger.info("Worker resources released")


if __name__ == "__main__":
    asyncio.run(main())
