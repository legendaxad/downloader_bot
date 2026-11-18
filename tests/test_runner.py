"""Worker runtime: concurrency, resilience to Redis errors, graceful shutdown."""

from __future__ import annotations

import asyncio

from redis.exceptions import ConnectionError as RedisConnectionError

from models.task import TaskStatus
from workers.runner import Worker


class RecordingProcessor:
    def __init__(self, queue, delay=0.05):
        self.queue, self.delay = queue, delay
        self.active = 0
        self.max_active = 0
        self.done: list[str] = []

    async def run(self, task):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            await self.queue.finish(task, TaskStatus.COMPLETED)
            self.done.append(task.task_id)
        finally:
            self.active -= 1


class NullMessenger:
    async def edit_status(self, *args, **kwargs):
        pass

    async def send_video(self, *args, **kwargs):
        pass


def make_worker(queue, processor, tmp_path, **kw):
    return Worker(queue, processor, NullMessenger(), name="test", concurrency=kw.pop("concurrency", 2),
                  download_dir=tmp_path, max_retries=1, stale_after=60, shutdown_grace=kw.pop("grace", 1),
                  maintenance_interval=0.05, claim_timeout=0.05, **kw)


async def wait_for(predicate, timeout=3):
    async def loop():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(loop(), timeout)


async def test_processes_tasks_concurrently_within_limit(queue, make_session, tmp_path):
    for user in range(1, 6):
        await queue.submit(await make_session(user_id=user), 720)
    processor = RecordingProcessor(queue)
    worker = make_worker(queue, processor, tmp_path, concurrency=2)
    run = asyncio.create_task(worker.run())
    await wait_for(lambda: len(processor.done) == 5)
    worker.stop()
    await asyncio.wait_for(run, 3)
    assert processor.max_active == 2


async def test_graceful_shutdown_waits_for_running_tasks(queue, make_session, tmp_path):
    await queue.submit(await make_session(), 720)
    processor = RecordingProcessor(queue, delay=0.3)
    worker = make_worker(queue, processor, tmp_path)
    run = asyncio.create_task(worker.run())
    await wait_for(lambda: processor.active == 1)
    worker.stop()
    await asyncio.wait_for(run, 3)
    assert len(processor.done) == 1


async def test_redis_errors_do_not_crash_worker(queue, make_session, tmp_path, monkeypatch):
    calls = {"n": 0}
    real_claim = queue.claim

    async def flaky_claim(timeout=5):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RedisConnectionError("down")
        return await real_claim(timeout)

    monkeypatch.setattr(queue, "claim", flaky_claim)
    await queue.submit(await make_session(), 720)
    processor = RecordingProcessor(queue)
    worker = make_worker(queue, processor, tmp_path)
    run = asyncio.create_task(worker.run())
    await wait_for(lambda: len(processor.done) == 1)
    worker.stop()
    await asyncio.wait_for(run, 3)


async def test_crashing_processor_does_not_kill_worker(queue, make_session, tmp_path):
    class Boom(RecordingProcessor):
        async def run(self, task):
            if not self.done:
                self.done.append("boom")
                raise RuntimeError("bug")
            await super().run(task)

    await queue.submit(await make_session(user_id=1), 720)
    await queue.submit(await make_session(user_id=2), 720)
    processor = Boom(queue)
    worker = make_worker(queue, processor, tmp_path, concurrency=1)
    run = asyncio.create_task(worker.run())
    await wait_for(lambda: len(processor.done) == 2)
    worker.stop()
    await asyncio.wait_for(run, 3)


async def test_maintenance_promotes_retries_and_writes_heartbeat(queue, make_session, tmp_path, redis_client):
    await queue.submit(await make_session(), 720)
    await queue.schedule_retry(await queue.claim(0.1), delay=0)
    processor = RecordingProcessor(queue)
    worker = make_worker(queue, processor, tmp_path)
    run = asyncio.create_task(worker.run())
    await wait_for(lambda: len(processor.done) == 1)
    assert await redis_client.exists("dl:alive:worker:test")
    worker.stop()
    await asyncio.wait_for(run, 3)
