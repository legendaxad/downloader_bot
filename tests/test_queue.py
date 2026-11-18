"""Redis queue tests (fakeredis)."""

from __future__ import annotations

import time

import pytest

from models.task import TaskStatus
from services.queue import DELAYED, PENDING, PROCESSING
from utils.exceptions import RateLimitedError, TooManyTasksError


async def test_session_roundtrip_and_ttl(queue, make_session, redis_client):
    session = await make_session()
    assert session.qualities == [720, 360] and session.task_id is None
    assert 0 < await redis_client.ttl(f"dl:session:{session.session_id}") <= 600
    assert await queue.get_session("deadbeef0000") is None


async def test_submit_creates_unique_queued_task(queue, make_session, redis_client):
    session = await make_session()
    result = await queue.submit(session, 720, status_message_id=5)
    assert result.created
    task = await queue.get_task(result.task.task_id)
    assert task.status is TaskStatus.QUEUED and task.status_message_id == 5 and len(task.task_id) == 32
    assert await redis_client.lrange(PENDING, 0, -1) == [task.task_id]


async def test_repeated_press_is_idempotent(queue, make_session, redis_client):
    session = await make_session()
    first = await queue.submit(session, 720)
    # stale session object (as a concurrent second press would see it)
    second = await queue.submit(session, 360)
    assert not second.created and second.task.task_id == first.task.task_id
    assert await redis_client.llen(PENDING) == 1


async def test_duplicate_url_and_quality_returns_existing_task(queue, make_session):
    first = await queue.submit(await make_session(), 720)
    dup = await queue.submit(await make_session(), 720)
    assert not dup.created and dup.task.task_id == first.task.task_id


async def test_per_user_active_limit(queue, make_session):
    await queue.submit(await make_session(url="https://youtu.be/1"), 720)
    await queue.submit(await make_session(url="https://youtu.be/2"), 720)
    with pytest.raises(TooManyTasksError):
        await queue.submit(await make_session(url="https://youtu.be/3"), 720)
    # another user is unaffected
    assert (await queue.submit(await make_session(user_id=2, url="https://youtu.be/3"), 720)).created


async def test_claim_moves_to_processing_and_finish_cleans_up(queue, make_session, redis_client):
    created = (await queue.submit(await make_session(), 720)).task
    task = await queue.claim(timeout=0.1)
    assert task.task_id == created.task_id
    assert await redis_client.lrange(PROCESSING, 0, -1) == [task.task_id]
    assert await redis_client.exists(f"dl:hb:{task.task_id}")

    await queue.finish(task, TaskStatus.COMPLETED)
    stored = await queue.get_task(task.task_id)
    assert stored.status is TaskStatus.COMPLETED and stored.progress == 100 and stored.completed_at > 0
    assert await redis_client.llen(PROCESSING) == 0
    assert await queue.active_tasks(task.user_id) == []
    # dedupe released → same URL can be requested again
    assert (await queue.submit(await make_session(), 720)).created


async def test_claim_returns_none_when_empty(queue):
    assert await queue.claim(timeout=0.1) is None


async def test_fifo_order_and_positions(queue, make_session):
    a = (await queue.submit(await make_session(user_id=1), 720)).task
    b = (await queue.submit(await make_session(user_id=2), 720)).task
    assert await queue.queue_position(a.task_id) == 1
    assert await queue.queue_position(b.task_id) == 2
    assert (await queue.claim(0.1)).task_id == a.task_id


async def test_retry_is_delayed_then_promoted(queue, make_session, redis_client):
    await queue.submit(await make_session(), 720)
    task = await queue.claim(0.1)
    await queue.schedule_retry(task, delay=0)
    assert await redis_client.zcard(DELAYED) == 1 and await redis_client.llen(PROCESSING) == 0
    assert (await queue.get_task(task.task_id)).attempts == 1
    assert await queue.promote_due() == 1
    assert (await queue.claim(0.1)).task_id == task.task_id


async def test_retry_not_promoted_before_due(queue, make_session):
    await queue.submit(await make_session(), 720)
    await queue.schedule_retry(await queue.claim(0.1), delay=60)
    assert await queue.promote_due() == 0


async def test_stale_task_is_requeued_then_failed(queue, make_session, redis_client):
    await queue.submit(await make_session(), 720)
    task = await queue.claim(0.1)
    # simulate a crashed worker: heartbeat gone, last update long ago
    await redis_client.delete(f"dl:hb:{task.task_id}")
    await redis_client.hset(f"dl:task:{task.task_id}", "updated_at", time.time() - 600)

    assert await queue.recover_stale(max_retries=1, stale_after=60) == []
    assert await redis_client.lrange(PENDING, 0, -1) == [task.task_id]
    assert (await queue.get_task(task.task_id)).attempts == 1

    task = await queue.claim(0.1)
    await redis_client.delete(f"dl:hb:{task.task_id}")
    await redis_client.hset(f"dl:task:{task.task_id}", "updated_at", time.time() - 600)
    failed = await queue.recover_stale(max_retries=1, stale_after=60)
    assert [t.task_id for t in failed] == [task.task_id]
    assert (await queue.get_task(task.task_id)).status is TaskStatus.FAILED


async def test_live_task_is_not_recovered(queue, make_session, redis_client):
    await queue.submit(await make_session(), 720)
    task = await queue.claim(0.1)
    assert await queue.recover_stale(max_retries=1, stale_after=60) == []
    assert await redis_client.lrange(PROCESSING, 0, -1) == [task.task_id]


async def test_cancel_queued_task_removes_it(queue, make_session, redis_client):
    task = (await queue.submit(await make_session(), 720)).task
    result = await queue.request_cancel(task.task_id, user_id=1)
    assert result.status is TaskStatus.CANCELLED
    assert await redis_client.llen(PENDING) == 0
    assert await queue.claim(0.1) is None


async def test_cancel_running_task_sets_flag(queue, make_session):
    await queue.submit(await make_session(), 720)
    task = await queue.claim(0.1)
    result = await queue.request_cancel(task.task_id, user_id=1)
    assert result.status is not TaskStatus.CANCELLED  # worker finalises it
    assert await queue.is_cancel_requested(task.task_id)


async def test_cancel_rejects_other_users_and_finished_tasks(queue, make_session):
    task = (await queue.submit(await make_session(), 720)).task
    assert await queue.request_cancel(task.task_id, user_id=999) is None
    await queue.finish(task, TaskStatus.COMPLETED)
    assert await queue.request_cancel(task.task_id, user_id=1) is None


async def test_claim_skips_task_cancelled_while_delayed(queue, make_session, redis_client):
    task = (await queue.submit(await make_session(), 720)).task
    await redis_client.hset(f"dl:task:{task.task_id}", "cancel_requested", "1")
    assert await queue.claim(0.1) is None
    assert (await queue.get_task(task.task_id)).status is TaskStatus.CANCELLED


async def test_requeue_puts_task_at_front(queue, make_session):
    await queue.submit(await make_session(user_id=1), 720)
    await queue.submit(await make_session(user_id=2), 720)
    first = await queue.claim(0.1)
    await queue.requeue(first.task_id)
    assert (await queue.claim(0.1)).task_id == first.task_id


async def test_rate_limit(queue):
    for _ in range(3):
        await queue.check_rate_limit(7, per_minute=3)
    with pytest.raises(RateLimitedError):
        await queue.check_rate_limit(7, per_minute=3)
