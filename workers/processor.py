"""Processes a single claimed task: download → process → upload, with lifecycle,
progress reporting, cancellation, controlled retries and guaranteed cleanup."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Protocol, TypeVar

from keyboards.buttons import admin_keyboard, cancel_keyboard
from models.task import Task, TaskStatus
from services.queue import TaskQueue
from services.video import ProcessedVideo
from utils.exceptions import (
    AppError,
    DownloadFailedError,
    FileTooLargeError,
    TaskCancelledError,
)
from utils.files import remove_dir, task_workdir
from utils.formatting import fmt_size, render_status
from utils.i18n import t
from utils.logger import logger

T = TypeVar("T")


class Downloader(Protocol):
    def download(self, url: str, quality: int, dest_dir: Path, *, progress: Any = None,
                 cancel_event: threading.Event | None = None) -> Path: ...


class Processor(Protocol):
    async def prepare_for_telegram(self, src: Path, out_dir: Path, *, progress: Any = None,
                                   cancel_event: threading.Event | None = None) -> ProcessedVideo: ...


class Messenger(Protocol):
    async def send_video(self, chat_id: int, file_path: Path, **kwargs: Any) -> None: ...
    async def edit_status(self, chat_id: int, message_id: int, text: str, markup: Any = None) -> None: ...


@dataclass
class ProcessorConfig:
    download_dir: Path
    max_upload_bytes: int
    max_retries: int = 2
    retry_base_delay: float = 15
    download_timeout: float = 1800
    upload_timeout: float = 600
    progress_interval: float = 3.0  # min seconds between Telegram edits
    poll_interval: float = 1.0  # heartbeat / cancel polling
    worker_name: str = "worker"
    admin_username: str = "legend_axad"


@dataclass
class _Progress:
    status: TaskStatus = TaskStatus.QUEUED
    percent: int | None = None
    detail: str = ""
    shown: tuple[Any, ...] = field(default_factory=tuple)
    shown_at: float = 0.0


class TaskProcessor:
    def __init__(self, queue: TaskQueue, downloader: Downloader, video: Processor, messenger: Messenger,
                 config: ProcessorConfig) -> None:
        self.queue = queue
        self.downloader = downloader
        self.video = video
        self.messenger = messenger
        self.cfg = config

    # ------------------------------------------------------------------ main
    async def run(self, task: Task) -> None:
        log = {"task_id": task.task_id, "user_id": task.user_id, "worker": self.cfg.worker_name,
               "platform": task.platform, "attempt": task.attempts}
        cancel = threading.Event()
        progress = _Progress()
        workdir = task_workdir(self.cfg.download_dir, task.task_id)
        monitor = asyncio.create_task(self._monitor(task, progress, cancel))
        timings: dict[str, float] = {}
        started = time.monotonic()
        logger.info("Task started", extra=log)

        try:
            await self._set(task, progress, TaskStatus.FETCHING_METADATA)
            workdir.mkdir(parents=True, exist_ok=True)

            t0 = time.monotonic()
            source = await self._download(task, workdir, progress, cancel)
            timings["download_s"] = round(time.monotonic() - t0, 2)
            self._check_cancel(cancel)

            await self._set(task, progress, TaskStatus.PROCESSING, percent=0)
            t0 = time.monotonic()
            processed = await self.video.prepare_for_telegram(
                source, workdir, progress=lambda p: self._progress(progress, TaskStatus.PROCESSING, p),
                cancel_event=cancel,
            )
            timings["processing_s"] = round(time.monotonic() - t0, 2)
            if processed.path != source:
                source.unlink(missing_ok=True)  # free disk early
            self._check_cancel(cancel)

            size = processed.path.stat().st_size
            if size > self.cfg.max_upload_bytes:
                raise FileTooLargeError(
                    f"{size} bytes > {self.cfg.max_upload_bytes}", key="err.file_too_large_size",
                    params={"size": fmt_size(size), "limit": fmt_size(self.cfg.max_upload_bytes)},
                )

            await self._set(task, progress, TaskStatus.UPLOADING)
            t0 = time.monotonic()
            await self._race_cancel(self._upload(task, processed), cancel, self.cfg.upload_timeout)
            timings["upload_s"] = round(time.monotonic() - t0, 2)

            await self.queue.finish(task, TaskStatus.COMPLETED, file_size=size)
            await self._show_final(task, self._text(task, TaskStatus.COMPLETED))
            logger.info("Task completed", extra=log | timings | {
                "size_bytes": size, "duration_s": round(time.monotonic() - started, 2), "status": processed.mode})

        except TaskCancelledError:
            await self.queue.finish(task, TaskStatus.CANCELLED)
            await self._show_final(task, self._text(task, TaskStatus.CANCELLED))
            logger.info("Task cancelled", extra=log | timings)

        except asyncio.CancelledError:
            # Worker shutdown: put the task back so another worker resumes it.
            cancel.set()
            logger.warning("Task interrupted by shutdown; re-queued", extra=log)
            await asyncio.shield(self._requeue_on_shutdown(task))
            raise

        except AppError as error:
            await self._handle_error(task, error, log | timings)

        except Exception as error:  # unexpected bug: never crash the worker
            logger.exception("Unexpected task error", extra=log | timings)
            await self._fail(task, AppError(str(error)), log)

        finally:
            cancel.set()
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            await asyncio.to_thread(remove_dir, workdir)

    # ------------------------------------------------------------------ stages
    async def _download(self, task: Task, workdir: Path, progress: _Progress, cancel: threading.Event) -> Path:
        def on_progress(percent: int | None, downloaded: int, total: int | None) -> None:
            detail = f"{fmt_size(downloaded)} / {fmt_size(total)}" if total else fmt_size(downloaded)
            self._progress(progress, TaskStatus.DOWNLOADING, percent, detail)

        future = asyncio.ensure_future(asyncio.to_thread(
            self.downloader.download, task.url, task.quality, workdir,
            progress=on_progress, cancel_event=cancel,
        ))
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.cfg.download_timeout)
        except TimeoutError:
            cancel.set()  # thread stops at the next progress hook
            await asyncio.gather(future, return_exceptions=True)
            raise DownloadFailedError("download timed out") from None
        except asyncio.CancelledError:
            cancel.set()
            await asyncio.gather(future, return_exceptions=True)
            raise

    async def _upload(self, task: Task, processed: ProcessedVideo) -> None:
        await self.messenger.send_video(
            task.chat_id, processed.path,
            caption=f"🎬 {escape(task.title)}",
            width=processed.width, height=processed.height, duration=processed.duration,
            thumbnail=processed.thumbnail, reply_to_message_id=task.status_message_id or None,
        )

    async def _race_cancel(self, coro: Awaitable[T], cancel: threading.Event, timeout: float) -> T:
        work = asyncio.ensure_future(coro)

        async def watch() -> None:
            while not cancel.is_set():
                await asyncio.sleep(0.5)

        watcher = asyncio.ensure_future(watch())
        try:
            done, _ = await asyncio.wait({work, watcher}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            watcher.cancel()
        if work in done:
            return work.result()
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        if cancel.is_set():
            raise TaskCancelledError("cancelled during upload")
        raise DownloadFailedError("upload timed out")

    # ------------------------------------------------------------------ errors
    async def _handle_error(self, task: Task, error: AppError, log: dict[str, Any]) -> None:
        detail = f"{type(error).__name__}: {error}"
        cancelled = await self._safe(self.queue.is_cancel_requested(task.task_id), default=False)
        if cancelled:
            await self.queue.finish(task, TaskStatus.CANCELLED)
            await self._show_final(task, self._text(task, TaskStatus.CANCELLED))
            return

        if error.retryable and task.attempts < self.cfg.max_retries:
            retry_after = getattr(error, "retry_after", None)
            delay = retry_after or self.cfg.retry_base_delay * (2 ** task.attempts)
            logger.warning("Transient failure; retrying", extra=log | {
                "error": detail[:500], "error_type": type(error).__name__})
            await self.queue.schedule_retry(task, delay)
            await self.messenger.edit_status(
                task.chat_id, task.status_message_id,
                self._text(task, TaskStatus.QUEUED, detail=t(
                    "status.retrying", task.lang, seconds=int(delay),
                    attempt=task.attempts + 1, total=self.cfg.max_retries + 1)),
                cancel_keyboard(task.task_id, task.lang),
            )
            return
        await self._fail(task, error, log)

    async def _fail(self, task: Task, error: AppError, log: dict[str, Any]) -> None:
        detail = f"{type(error).__name__}: {error}"
        logger.error("Task failed", extra=log | {"error": detail[:500], "error_type": type(error).__name__})
        try:
            await self.queue.finish(task, TaskStatus.FAILED, error=error.user_message, error_detail=detail)
        except Exception:
            logger.exception("Could not record task failure", extra=log)
        detail_text = f"{error.localized(task.lang)}\n\n{t('status.contact', task.lang, admin=self.cfg.admin_username)}"
        await self._safe(self.messenger.edit_status(
            task.chat_id, task.status_message_id, self._text(task, TaskStatus.FAILED, detail=detail_text),
            admin_keyboard(self.cfg.admin_username, task.lang)))

    async def _requeue_on_shutdown(self, task: Task) -> None:
        try:
            await self.queue.requeue(task.task_id)
            await self.messenger.edit_status(
                task.chat_id, task.status_message_id,
                self._text(task, TaskStatus.QUEUED, detail=t("status.restarting", task.lang)),
                cancel_keyboard(task.task_id, task.lang),
            )
        except Exception:
            logger.exception("Requeue on shutdown failed; stale recovery will pick it up",
                             extra={"task_id": task.task_id})

    # ------------------------------------------------------------------ progress
    @staticmethod
    def _check_cancel(cancel: threading.Event) -> None:
        if cancel.is_set():
            raise TaskCancelledError("cancelled")

    @staticmethod
    def _progress(state: _Progress, status: TaskStatus, percent: int | None, detail: str = "") -> None:
        # Called from worker threads; plain attribute writes are atomic in CPython.
        state.status, state.percent, state.detail = status, percent, detail

    async def _set(self, task: Task, state: _Progress, status: TaskStatus, percent: int | None = None) -> None:
        self._progress(state, status, percent)
        task.status = status
        await self.queue.set_status(task.task_id, status, progress=percent or 0)
        await self._render(task, state, force=True)

    async def _render(self, task: Task, state: _Progress, *, force: bool = False) -> None:
        # Bucket percentages to 5% steps so edits only happen on visible change.
        bucket = None if state.percent is None else state.percent // 5 * 5
        key = (state.status, bucket)
        now = time.monotonic()
        if key == state.shown:
            return
        stage_changed = not state.shown or state.shown[0] != state.status
        # Stage changes are shown immediately; percentage updates are throttled.
        if not (force or stage_changed) and now - state.shown_at < self.cfg.progress_interval:
            return
        state.shown, state.shown_at = key, now
        await self.messenger.edit_status(
            task.chat_id, task.status_message_id,
            render_status(task.title, task.quality, state.status, bucket, state.detail, task.lang),
            cancel_keyboard(task.task_id, task.lang),
        )

    async def _monitor(self, task: Task, state: _Progress, cancel: threading.Event) -> None:
        """Heartbeat, cancellation polling and throttled progress updates."""
        last_saved: tuple[Any, ...] = ()
        while not cancel.is_set():
            try:
                await self.queue.heartbeat(task.task_id)
                if await self.queue.is_cancel_requested(task.task_id):
                    logger.info("Cancellation requested", extra={"task_id": task.task_id})
                    cancel.set()
                    return
                if (state.status, state.percent) != last_saved:
                    last_saved = (state.status, state.percent)
                    await self.queue.set_status(task.task_id, state.status, progress=state.percent or 0)
                await self._render(task, state)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # Redis/Telegram hiccup: keep going
                logger.warning("Monitor error: %s", error, extra={"task_id": task.task_id})
            await asyncio.sleep(self.cfg.poll_interval)

    @staticmethod
    def _text(task: Task, status: TaskStatus, detail: str = "") -> str:
        return render_status(task.title, task.quality, status, detail=detail, lang=task.lang)

    async def _show_final(self, task: Task, text: str) -> None:
        await self._safe(self.messenger.edit_status(task.chat_id, task.status_message_id, text, None))

    @staticmethod
    async def _safe(coro: Awaitable[T], default: Any = None) -> Any:
        try:
            return await coro
        except Exception as error:
            logger.warning("Ignored error: %s", error)
            return default

