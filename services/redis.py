import redis.asyncio as redis

from config import REDIS_URL


redis_client = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=30,
)