"""Worker runtime: claims tasks with bounded concurrency, runs maintenance
(retry promotion, stale-task recovery, heartbeats, disk cleanup) and shuts
down gracefully."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from redis.exceptions import RedisError

from models.task import Task
from services.queue import TaskQueue
from utils.files import cleanup_stale_dirs
from utils.formatting import render_status
from utils.i18n import t
from utils.logger import logger
from workers.processor import Messenger, TaskProcessor


class Worker:
    def __init__(
        self,
        queue: TaskQueue,
        processor: TaskProcessor,
        messenger: Messenger,
        *,
        name: str,
        concurrency: int,
        download_dir: Path,
        max_retries: int,
        stale_after: float,
        shutdown_grace: float = 45,
        stale_dir_age: float = 3 * 3600,
        maintenance_interval: float = 10,
        claim_timeout: float = 5,
    ) -> None:
        self.queue = queue
        self.processor = processor
        self.messenger = messenger
        self.name = name
        self.concurrency = concurrency
        self.download_dir = download_dir
        self.max_retries = max_retries
        self.stale_after = stale_after
        self.shutdown_grace = shutdown_grace
        self.stale_dir_age = stale_dir_age
        self.maintenance_interval = maintenance_interval
        self.claim_timeout = claim_timeout
        self._stopping = asyncio.Event()
        self._running: set[asyncio.Task[None]] = set()

    def stop(self) -> None:
        if not self._stopping.is_set():
            logger.info("Worker stop requested", extra={"worker": self.name})
            self._stopping.set()

    async def run(self) -> None:
        logger.info("Worker started", extra={"worker": self.name})
        self.download_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(cleanup_stale_dirs, self.download_dir, self.stale_dir_age)
        maintenance = asyncio.create_task(self._maintenance_loop())
        slots = asyncio.Semaphore(self.concurrency)
        backoff = 1.0
        try:
            while not self._stopping.is_set():
                if not await self._acquire_or_stop(slots):
                    break
                try:
                    task = await self.queue.claim(timeout=self.claim_timeout)
                    backoff = 1.0
                except (RedisError, OSError) as error:
                    slots.release()
                    logger.error("Redis unavailable while claiming: %s", error, extra={"worker": self.name})
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue
                if task is None:
                    slots.release()
                    continue
                job = asyncio.create_task(self._run_one(task), name=f"task-{task.task_id}")
                self._running.add(job)
                job.add_done_callback(lambda t: (self._running.discard(t), slots.release()))
        finally:
            await self._drain()
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)
            logger.info("Worker stopped", extra={"worker": self.name})

    async def _acquire_or_stop(self, slots: asyncio.Semaphore) -> bool:
        """Wait for a free slot; return False (holding nothing) if stop was requested."""
        acquire = asyncio.ensure_future(slots.acquire())
        stop = asyncio.ensure_future(self._stopping.wait())
        await asyncio.wait({acquire, stop}, return_when=asyncio.FIRST_COMPLETED)
        stop.cancel()
        if not acquire.done():
            acquire.cancel()
            await asyncio.gather(acquire, return_exceptions=True)
            return False
        if self._stopping.is_set():
            slots.release()
            return False
        return True

    async def _run_one(self, task: Task) -> None:
        try:
            await self.processor.run(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Processor already handles task errors; this guards against Redis
            # failures while recording results. Stale recovery re-queues it.
            logger.exception("Task crashed", extra={"task_id": task.task_id, "worker": self.name})

    async def _drain(self) -> None:
        if not self._running:
            return
        logger.info("Waiting for %d running task(s)", len(self._running), extra={"worker": self.name})
        _, pending = await asyncio.wait(set(self._running), timeout=self.shutdown_grace)
        for job in pending:
            job.cancel()  # processor re-queues the task
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _maintenance_loop(self) -> None:
        ticks = 0
        while True:
            try:
                await self.queue.worker_heartbeat(self.name, ttl=max(30, int(self.maintenance_interval * 6)))
                await self.queue.promote_due()
                for task in await self.queue.recover_stale(self.max_retries, self.stale_after):
                    await self.messenger.edit_status(
                        task.chat_id, task.status_message_id,
                        render_status(task.title, task.quality, task.status,
                                      detail=t("err.stuck", task.lang), lang=task.lang),
                    )
                ticks += 1
                if ticks % 360 == 0:  # ~hourly with the default interval
                    await asyncio.to_thread(cleanup_stale_dirs, self.download_dir, self.stale_dir_age)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Maintenance error: %s", error, extra={"worker": self.name})
            await asyncio.sleep(self.maintenance_interval)

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), seconds)
