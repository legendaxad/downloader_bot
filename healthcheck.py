"""Container health check: ``python healthcheck.py bot|worker``.

Exits 0 if Redis is reachable and a process of the given kind on this host
has written its liveness heartbeat recently.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys

from config import get_settings
from services.redis import create_redis


async def check(component: str) -> bool:
    client = create_redis(get_settings().redis_url)
    try:
        await client.ping()
        name = os.getenv("WORKER_NAME") or socket.gethostname()
        async for _ in client.scan_iter(match=f"dl:alive:{component}:{name}*", count=100):
            return True
        return False
    finally:
        await client.aclose()


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "worker"
    try:
        ok = asyncio.run(check(kind))
    except Exception as error:  # noqa: BLE001
        print(f"unhealthy: {error}")
        sys.exit(1)
    print("healthy" if ok else "unhealthy: no heartbeat")
    sys.exit(0 if ok else 1)
