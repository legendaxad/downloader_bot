"""Worker task lifecycle: success, permanent/transient failures, retries, cancel, cleanup."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from models.task import TaskStatus
from services.queue import DELAYED, PENDING, PROCESSING
from services.video import ProcessedVideo, ProcessingMode
from utils.exceptions import (
    DownloadFailedError,
    MediaProcessingError,
    TaskCancelledError,
    TelegramUploadError,
    VideoUnavailableError,
)
from workers.processor import ProcessorConfig, TaskProcessor


class FakeDownloader:
    def __init__(self, error: Exception | None = None, size: int = 1000, block: threading.Event | None = None,
                 delay: float = 0.0):
        self.error, self.size, self.block, self.delay = error, size, block, delay
        self.calls = 0

    def download(self, url, quality, dest_dir: Path, *, progress=None, cancel_event=None) -> Path:
        self.calls += 1
        if progress:
            progress(50, 500, 1000)
        time.sleep(self.delay)
        if self.block is not None:
            self.block.set()  # signal "download in progress"
            while not cancel_event.is_set():
                cancel_event.wait(0.01)
            raise TaskCancelledError("cancelled")
        if self.error:
            raise self.error
        path = dest_dir / "source.mkv"
        path.write_bytes(b"\0" * self.size)
        return path


class FakeVideo:
    def __init__(self, error: Exception | None = None):
        self.error = error

    async def prepare_for_telegram(self, src: Path, out_dir: Path, *, progress=None, cancel_event=None):
        if self.error:
            raise self.error
        out = out_dir / "telegram.mp4"
        out.write_bytes(src.read_bytes())
        if progress:
            progress(100)
        return ProcessedVideo(out, 640, 360, 10, None, ProcessingMode.REMUX)


class FakeMessenger:
    def __init__(self, upload_error: Exception | None = None):
        self.upload_error = upload_error
        self.sent: list[Path] = []
        self.edits: list[str] = []

    async def send_video(self, chat_id, file_path: Path, **kwargs):
        assert file_path.exists()
        if self.upload_error:
            raise self.upload_error
        self.sent.append(file_path)

    async def edit_status(self, chat_id, message_id, text, markup=None):
        self.edits.append(text)


@pytest.fixture
def config(tmp_path):
    return ProcessorConfig(download_dir=tmp_path, max_upload_bytes=10_000, max_retries=2, retry_base_delay=5,
                           progress_interval=0, poll_interval=0.01)


async def claimed(queue, make_session):
    await queue.submit(await make_session(), 720, status_message_id=9)
    return await queue.claim(0.1)


async def test_successful_task(queue, make_session, config, redis_client):
    task = await claimed(queue, make_session)
    messenger = FakeMessenger()
    await TaskProcessor(queue, FakeDownloader(delay=0.2), FakeVideo(), messenger, config).run(task)

    stored = await queue.get_task(task.task_id)
    assert stored.status is TaskStatus.COMPLETED
    assert len(messenger.sent) == 1
    assert any("Downloading — 50%" in e for e in messenger.edits)
    assert any("Uploading" in e for e in messenger.edits)
    assert "Done" in messenger.edits[-1]
    assert not (config.download_dir / task.task_id).exists()  # temp files removed
    assert await redis_client.llen(PROCESSING) == 0


async def test_permanent_error_fails_without_retry(queue, make_session, config, redis_client):
    task = await claimed(queue, make_session)
    messenger = FakeMessenger()
    downloader = FakeDownloader(error=VideoUnavailableError("gone"))
    await TaskProcessor(queue, downloader, FakeVideo(), messenger, config).run(task)

    stored = await queue.get_task(task.task_id)
    assert stored.status is TaskStatus.FAILED
    assert stored.error == VideoUnavailableError().user_message
    assert "gone" in stored.error_detail  # technical detail kept for ops, not shown to user
    assert "VideoUnavailableError" not in messenger.edits[-1]
    assert await redis_client.zcard(DELAYED) == 0
    assert not (config.download_dir / task.task_id).exists()


async def test_transient_error_schedules_retry_with_backoff(queue, make_session, config, redis_client):
    task = await claimed(queue, make_session)
    messenger = FakeMessenger()
    await TaskProcessor(queue, FakeDownloader(error=DownloadFailedError("reset")), FakeVideo(), messenger,
                        config).run(task)

    stored = await queue.get_task(task.task_id)
    assert stored.status is TaskStatus.QUEUED and stored.attempts == 1
    assert await redis_client.zcard(DELAYED) == 1
    assert "retrying in 5s" in messenger.edits[-1]
    assert await redis_client.llen(PROCESSING) == 0


async def test_retries_are_bounded(queue, make_session, config):
    task = await claimed(queue, make_session)
    processor = TaskProcessor(queue, FakeDownloader(error=DownloadFailedError("x")), FakeVideo(),
                              FakeMessenger(), config)
    for _ in range(config.max_retries):
        await processor.run(task)
        await queue.r.zadd(DELAYED, {task.task_id: 0})
        await queue.promote_due()
        task = await queue.claim(0.1)
    await processor.run(task)
    stored = await queue.get_task(task.task_id)
    assert stored.status is TaskStatus.FAILED and stored.attempts == config.max_retries


async def test_telegram_retry_after_is_respected(queue, make_session, config, redis_client):
    task = await claimed(queue, make_session)
    messenger = FakeMessenger(upload_error=TelegramUploadError("flood", retryable=True, retry_after=42))
    await TaskProcessor(queue, FakeDownloader(), FakeVideo(), messenger, config).run(task)
    score = await redis_client.zscore(DELAYED, task.task_id)
    assert score is not None
    assert "retrying in 42s" in messenger.edits[-1]


async def test_non_retryable_telegram_error_fails(queue, make_session, config):
    task = await claimed(queue, make_session)
    error = TelegramUploadError("blocked", retryable=False, key="err.bot_blocked")
    await TaskProcessor(queue, FakeDownloader(), FakeVideo(), FakeMessenger(upload_error=error), config).run(task)
    assert (await queue.get_task(task.task_id)).status is TaskStatus.FAILED


async def test_processing_error_fails_and_cleans_up(queue, make_session, config):
    task = await claimed(queue, make_session)
    await TaskProcessor(queue, FakeDownloader(), FakeVideo(error=MediaProcessingError("bad")), FakeMessenger(),
                        config).run(task)
    assert (await queue.get_task(task.task_id)).status is TaskStatus.FAILED
    assert not (config.download_dir / task.task_id).exists()


async def test_file_over_upload_limit_fails_permanently(queue, make_session, config, redis_client):
    task = await claimed(queue, make_session)
    messenger = FakeMessenger()
    await TaskProcessor(queue, FakeDownloader(size=20_000), FakeVideo(), messenger, config).run(task)
    stored = await queue.get_task(task.task_id)
    assert stored.status is TaskStatus.FAILED and "lower quality" in stored.error
    assert not messenger.sent and await redis_client.zcard(DELAYED) == 0


async def test_user_cancellation_stops_download(queue, make_session, config):
    task = await claimed(queue, make_session)
    started = threading.Event()
    messenger = FakeMessenger()
    processor = TaskProcessor(queue, FakeDownloader(block=started), FakeVideo(), messenger, config)
    job = asyncio.create_task(processor.run(task))
    await asyncio.to_thread(started.wait, 2)

    await queue.request_cancel(task.task_id, user_id=task.user_id)
    await asyncio.wait_for(job, 2)

    assert (await queue.get_task(task.task_id)).status is TaskStatus.CANCELLED
    assert "Cancelled" in messenger.edits[-1]
    assert not messenger.sent
    assert not (config.download_dir / task.task_id).exists()


async def test_shutdown_requeues_running_task(queue, make_session, config, redis_client):
    task = await claimed(queue, make_session)
    started = threading.Event()
    processor = TaskProcessor(queue, FakeDownloader(block=started), FakeVideo(), FakeMessenger(), config)
    job = asyncio.create_task(processor.run(task))
    await asyncio.to_thread(started.wait, 2)

    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert await redis_client.lrange(PENDING, 0, -1) == [task.task_id]
    assert await redis_client.llen(PROCESSING) == 0
    assert (await queue.get_task(task.task_id)).status is TaskStatus.QUEUED
    assert not (config.download_dir / task.task_id).exists()


async def test_download_timeout_is_transient(queue, make_session, config):
    task = await claimed(queue, make_session)
    config.download_timeout = 0.05
    started = threading.Event()
    await TaskProcessor(queue, FakeDownloader(block=started), FakeVideo(), FakeMessenger(), config).run(task)
    stored = await queue.get_task(task.task_id)
    assert stored.status is TaskStatus.QUEUED and stored.attempts == 1  # retry scheduled
