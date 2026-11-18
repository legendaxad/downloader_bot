from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "")  # tests never need a real token

import fakeredis  # noqa: E402

from services.queue import Session, TaskQueue  # noqa: E402


@pytest.fixture
async def redis_client():
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def queue(redis_client) -> TaskQueue:
    return TaskQueue(redis_client, task_ttl=3600, session_ttl=600, heartbeat_ttl=30, max_active_per_user=2)


@pytest.fixture
def make_session(queue):
    async def _make(user_id: int = 1, url: str = "https://youtu.be/abc", qualities=(720, 360)) -> Session:
        sid = await queue.create_session(user_id=user_id, chat_id=user_id, url=url, platform="youtube",
                                         title="Test video", qualities=list(qualities))
        return await queue.get_session(sid)
    return _make
