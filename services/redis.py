"""Redis client factory."""

from __future__ import annotations

import redis.asyncio as redis


def create_redis(url: str) -> redis.Redis:
    return redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=30,  # must exceed the BLMOVE timeout used by workers
        health_check_interval=30,
        retry_on_timeout=True,
    )
