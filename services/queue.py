import json
import uuid

import redis

from services.redis import redis_client


DOWNLOAD_QUEUE = "download_queue"


class DownloadQueue:

    async def enqueue(
        self,
        *,
        user_id: int,
        chat_id: int,
        url: str,
        quality: int,
    ) -> str:

        job_id = str(uuid.uuid4())

        job = {
            "job_id": job_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "url": url,
            "quality": quality,
        }

        await redis_client.hset(
            f"job:{job_id}",
            mapping={
                "status": "queued",
                "progress": 0,
            },
        )

        await redis_client.rpush(
            DOWNLOAD_QUEUE,
            json.dumps(job),
        )

        return job_id

    async def dequeue(self) -> dict | None:

        try:
            result = await redis_client.blpop(
                DOWNLOAD_QUEUE,
                timeout=5,
            )

        except redis.exceptions.TimeoutError:
            return None

        if result is None:
            return None

        _, job_data = result

        return json.loads(job_data)

    async def set_status(
        self,
        job_id: str,
        status: str,
    ) -> None:

        await redis_client.hset(
            f"job:{job_id}",
            "status",
            status,
        )

    async def set_progress(
        self,
        job_id: str,
        progress: int,
    ) -> None:

        await redis_client.hset(
            f"job:{job_id}",
            "progress",
            progress,
        )

    async def get_job(
        self,
        job_id: str,
    ) -> dict:

        return await redis_client.hgetall(
            f"job:{job_id}"
        )