"""Reliable Redis task queue.

Keys (all prefixed ``dl:``)::

    task:<id>              hash   task state (models.Task)
    queue:pending          list   task ids waiting for a worker (LPUSH in, BLMOVE out)
    queue:processing       list   task ids claimed by a worker (removed on finish)
    queue:delayed          zset   task ids waiting for a retry (score = ready timestamp)
    hb:<id>                str    per-task heartbeat (TTL); missing → task is stale
    user:<uid>:active      set    active task ids per user (limits + /cancel)
    dedupe:<uid>:<hash>    str    active task id for (user, url, quality)
    session:<sid>          hash   quality-selection session (url, qualities, task_id)
    rate:<uid>:<minute>    int    per-user request counter
    worker:<name>          str    worker liveness (health checks)

Claiming uses BLMOVE pending → processing, so a crashed worker never loses a
task: its heartbeat expires and :meth:`TaskQueue.recover_stale` re-queues it.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Protocol

import redis.asyncio as redis

from models.task import TERMINAL_STATUSES, Task, TaskStatus
from utils.exceptions import RateLimitedError, TooManyTasksError
from utils.logger import logger

PREFIX = "dl:"
PENDING = f"{PREFIX}queue:pending"
PROCESSING = f"{PREFIX}queue:processing"
DELAYED = f"{PREFIX}queue:delayed"


def _task_key(task_id: str) -> str:
    return f"{PREFIX}task:{task_id}"


def _hb_key(task_id: str) -> str:
    return f"{PREFIX}hb:{task_id}"


def _active_key(user_id: int) -> str:
    return f"{PREFIX}user:{user_id}:active"


def _dedupe_key(user_id: int, url: str, quality: int) -> str:
    digest = hashlib.sha256(f"{url}|{quality}".encode()).hexdigest()[:24]
    return f"{PREFIX}dedupe:{user_id}:{digest}"


def _session_key(session_id: str) -> str:
    return f"{PREFIX}session:{session_id}"


class History(Protocol):
    """Optional persistent history (e.g. MySQL) notified on every task state change."""

    async def record(self, task: Task, file_size: int | None = None) -> None: ...


@dataclass(frozen=True)
class Session:
    session_id: str
    user_id: int
    chat_id: int
    url: str
    platform: str
    title: str
    qualities: list[int]
    task_id: str | None


@dataclass(frozen=True)
class SubmitResult:
    task: Task
    created: bool  # False → an equivalent task already existed (repeat press / duplicate)


class TaskQueue:
    def __init__(
        self,
        client: redis.Redis,
        *,
        task_ttl: int = 24 * 3600,
        session_ttl: int = 1800,
        heartbeat_ttl: int = 120,
        max_active_per_user: int = 2,
        history: History | None = None,
    ) -> None:
        self.r = client
        self.history = history
        self.task_ttl = task_ttl
        self.session_ttl = session_ttl
        self.heartbeat_ttl = heartbeat_ttl
        self.max_active_per_user = max_active_per_user

    # ------------------------------------------------------------ sessions
    async def create_session(
        self, *, user_id: int, chat_id: int, url: str, platform: str, title: str, qualities: list[int],
    ) -> str:
        session_id = secrets.token_hex(6)  # short: callback_data is limited to 64 bytes
        key = _session_key(session_id)
        await self.r.hset(key, mapping={
            "user_id": user_id, "chat_id": chat_id, "url": url, "platform": platform,
            "title": title, "qualities": json.dumps(qualities),
        })
        await self.r.expire(key, self.session_ttl)
        return session_id

    async def get_session(self, session_id: str) -> Session | None:
        data = await self.r.hgetall(_session_key(session_id))
        if not data:
            return None
        return Session(
            session_id=session_id,
            user_id=int(data["user_id"]),
            chat_id=int(data["chat_id"]),
            url=data["url"],
            platform=data.get("platform", ""),
            title=data.get("title", ""),
            qualities=[int(q) for q in json.loads(data.get("qualities", "[]"))],
            task_id=data.get("task_id") or None,
        )

    # ------------------------------------------------------------ limits
    async def check_rate_limit(self, user_id: int, per_minute: int) -> None:
        key = f"{PREFIX}rate:{user_id}:{int(time.time() // 60)}"
        count = await self.r.incr(key)
        if count == 1:
            await self.r.expire(key, 70)
        if count > per_minute:
            raise RateLimitedError(f"user {user_id} exceeded {per_minute}/min")

    async def active_tasks(self, user_id: int) -> list[Task]:
        ids = await self.r.smembers(_active_key(user_id))
        tasks: list[Task] = []
        for task_id in ids:
            task = await self.get_task(task_id)
            if task is None or task.status in TERMINAL_STATUSES:
                await self.r.srem(_active_key(user_id), task_id)  # self-healing
                continue
            tasks.append(task)
        return sorted(tasks, key=lambda t: t.created_at)

    # ------------------------------------------------------------ submit
    async def submit(self, session: Session, quality: int, status_message_id: int = 0,
                     lang: str = "en") -> SubmitResult:
        """Create and enqueue a task for a selection session (idempotent per session)."""
        if session.task_id and (existing := await self.get_task(session.task_id)):
            return SubmitResult(existing, created=False)

        dedupe_key = _dedupe_key(session.user_id, session.url, quality)
        if (dup_id := await self.r.get(dedupe_key)) and (dup := await self.get_task(dup_id)):
            if dup.status not in TERMINAL_STATUSES:
                return SubmitResult(dup, created=False)

        if len(await self.active_tasks(session.user_id)) >= self.max_active_per_user:
            raise TooManyTasksError(f"user {session.user_id} at active-task limit")

        task = Task(
            user_id=session.user_id, chat_id=session.chat_id, url=session.url,
            platform=session.platform, quality=quality, title=session.title,
            status_message_id=status_message_id, lang=lang,
        )
        # Guard against concurrent double-presses on the same keyboard.
        if not await self.r.hsetnx(_session_key(session.session_id), "task_id", task.task_id):
            winner_id = await self.r.hget(_session_key(session.session_id), "task_id")
            if winner_id and (winner := await self.get_task(winner_id)):
                return SubmitResult(winner, created=False)

        task.status = TaskStatus.QUEUED
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.hset(_task_key(task.task_id), mapping=task.to_redis())
            pipe.expire(_task_key(task.task_id), self.task_ttl)
            pipe.sadd(_active_key(task.user_id), task.task_id)
            pipe.expire(_active_key(task.user_id), self.task_ttl)
            pipe.set(dedupe_key, task.task_id, ex=self.task_ttl)
            pipe.lpush(PENDING, task.task_id)
            await pipe.execute()
        logger.info("Task queued", extra={"task_id": task.task_id, "user_id": task.user_id,
                                          "platform": task.platform, "quality": quality})
        await self._record(task)
        return SubmitResult(task, created=True)

    # ------------------------------------------------------------ task state
    async def get_task(self, task_id: str) -> Task | None:
        data = await self.r.hgetall(_task_key(task_id))
        if not data:
            return None
        try:
            return Task.from_redis(data)
        except (KeyError, ValueError):
            logger.warning("Corrupt task record", extra={"task_id": task_id})
            return None

    async def update(self, task_id: str, **fields: Any) -> None:
        fields["updated_at"] = time.time()
        mapping = {k: (str(v) if not isinstance(v, bool) else ("1" if v else "0")) for k, v in fields.items()}
        await self.r.hset(_task_key(task_id), mapping=mapping)

    async def set_status(self, task_id: str, status: TaskStatus, progress: int | None = None) -> None:
        fields: dict[str, Any] = {"status": status.value}
        if progress is not None:
            fields["progress"] = progress
        await self.update(task_id, **fields)

    async def queue_position(self, task_id: str) -> int | None:
        """1-based position in the pending queue (1 = next to be picked)."""
        ids = await self.r.lrange(PENDING, 0, -1)
        if task_id not in ids:
            return None
        return len(ids) - ids.index(task_id)  # BLMOVE takes from the right

    # ------------------------------------------------------------ worker side
    async def claim(self, timeout: float = 5) -> Task | None:
        task_id = await self.r.blmove(PENDING, PROCESSING, timeout, "RIGHT", "LEFT")
        if task_id is None:
            return None
        await self.r.set(_hb_key(task_id), "1", ex=self.heartbeat_ttl)
        task = await self.get_task(task_id)
        if task is None:
            await self._release(task_id)
            return None
        if task.cancel_requested or task.status in TERMINAL_STATUSES:
            if task.status not in TERMINAL_STATUSES:
                await self.finish(task, TaskStatus.CANCELLED)
            else:
                await self._release(task_id)
            return None
        return task

    async def heartbeat(self, task_id: str) -> None:
        await self.r.set(_hb_key(task_id), "1", ex=self.heartbeat_ttl)

    async def is_cancel_requested(self, task_id: str) -> bool:
        return await self.r.hget(_task_key(task_id), "cancel_requested") == "1"

    async def finish(
        self, task: Task, status: TaskStatus, *, error: str = "", error_detail: str = "",
        file_size: int | None = None,
    ) -> None:
        now = time.time()
        task.status, task.error, task.completed_at = status, error, now
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.hset(_task_key(task.task_id), mapping={
                "status": status.value, "error": error, "error_detail": error_detail[:1000],
                "completed_at": now, "updated_at": now,
                **({"progress": 100} if status is TaskStatus.COMPLETED else {}),
            })
            pipe.expire(_task_key(task.task_id), self.task_ttl)
            pipe.lrem(PROCESSING, 0, task.task_id)
            pipe.lrem(PENDING, 0, task.task_id)
            pipe.zrem(DELAYED, task.task_id)
            pipe.srem(_active_key(task.user_id), task.task_id)
            pipe.delete(_dedupe_key(task.user_id, task.url, task.quality))
            pipe.delete(_hb_key(task.task_id))
            await pipe.execute()
        await self._record(task, file_size)

    async def _record(self, task: Task, file_size: int | None = None) -> None:
        if self.history is None:
            return
        try:
            await self.history.record(task, file_size)
        except Exception as error:  # history must never break the queue
            logger.warning("Recording history failed: %s", error, extra={"task_id": task.task_id})

    async def schedule_retry(self, task: Task, delay: float) -> None:
        task.attempts += 1
        task.status = TaskStatus.QUEUED
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.hset(_task_key(task.task_id), mapping={
                "attempts": task.attempts, "status": TaskStatus.QUEUED.value,
                "progress": 0, "updated_at": time.time(),
            })
            pipe.lrem(PROCESSING, 0, task.task_id)
            pipe.zadd(DELAYED, {task.task_id: time.time() + delay})
            pipe.delete(_hb_key(task.task_id))
            await pipe.execute()
        await self._record(task)

    async def requeue(self, task_id: str) -> None:
        """Put a claimed task back at the front of the queue (graceful shutdown)."""
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.lrem(PROCESSING, 0, task_id)
            pipe.rpush(PENDING, task_id)
            pipe.hset(_task_key(task_id), mapping={"status": TaskStatus.QUEUED.value, "progress": 0,
                                                   "updated_at": time.time()})
            pipe.delete(_hb_key(task_id))
            await pipe.execute()

    async def promote_due(self) -> int:
        """Move retry tasks whose delay elapsed back into the pending queue."""
        moved = 0
        for task_id in await self.r.zrangebyscore(DELAYED, "-inf", time.time()):
            if await self.r.zrem(DELAYED, task_id):  # only one worker wins
                await self.r.lpush(PENDING, task_id)
                moved += 1
        return moved

    async def recover_stale(self, max_retries: int, stale_after: float) -> list[Task]:
        """Re-queue (or fail) tasks whose worker stopped heart-beating.

        Returns tasks that were permanently failed so the caller can notify users.
        """
        failed: list[Task] = []
        cutoff = time.time() - stale_after
        for task_id in await self.r.lrange(PROCESSING, 0, -1):
            if await self.r.exists(_hb_key(task_id)):
                continue
            task = await self.get_task(task_id)
            if task is not None and task.updated_at > cutoff:
                continue  # just claimed; heartbeat not written yet
            if not await self.r.lrem(PROCESSING, 0, task_id):
                continue  # another worker handled it
            if task is None:
                continue
            if task.cancel_requested:
                await self.finish(task, TaskStatus.CANCELLED)
            elif task.attempts < max_retries:
                logger.warning("Recovering stale task", extra={"task_id": task_id, "attempt": task.attempts + 1})
                await self.update(task_id, attempts=task.attempts + 1, status=TaskStatus.QUEUED.value, progress=0)
                await self.r.lpush(PENDING, task_id)
            else:
                logger.error("Stale task exceeded retries", extra={"task_id": task_id})
                await self.finish(task, TaskStatus.FAILED, error="The download got stuck and was stopped.",
                                  error_detail="stale: worker heartbeat lost")
                failed.append(task)
        return failed

    async def _release(self, task_id: str) -> None:
        await self.r.lrem(PROCESSING, 0, task_id)
        await self.r.delete(_hb_key(task_id))

    # ------------------------------------------------------------ cancellation
    async def request_cancel(self, task_id: str, user_id: int) -> Task | None:
        """Request cancellation. Returns the task (status CANCELLED if it was still waiting).

        Returns None if the task doesn't exist, belongs to someone else, or already finished.
        """
        task = await self.get_task(task_id)
        if task is None or task.user_id != user_id or task.status in TERMINAL_STATUSES:
            return None
        await self.update(task_id, cancel_requested=True)
        task.cancel_requested = True
        removed = await self.r.lrem(PENDING, 0, task_id) + await self.r.zrem(DELAYED, task_id)
        if removed:
            await self.finish(task, TaskStatus.CANCELLED)
        return task

    # ------------------------------------------------------------ monitoring
    async def component_heartbeat(self, component: str, name: str, ttl: int = 60) -> None:
        """Liveness marker used by health checks (component = "worker" or "bot")."""
        await self.r.set(f"{PREFIX}alive:{component}:{name}", str(time.time()), ex=ttl)

    async def worker_heartbeat(self, name: str, ttl: int = 60) -> None:
        await self.component_heartbeat("worker", name, ttl)

    async def stats(self) -> dict[str, int]:
        return {
            "pending": await self.r.llen(PENDING),
            "processing": await self.r.llen(PROCESSING),
            "delayed": await self.r.zcard(DELAYED),
        }
