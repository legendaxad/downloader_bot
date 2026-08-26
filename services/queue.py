import json
import uuid

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

        await redis_client.rpush(
            DOWNLOAD_QUEUE,
            json.dumps(job),
        )

        return job_id

    async def dequeue(self) -> dict | None:

        job_data = await redis_client.lpop(
            DOWNLOAD_QUEUE,
        )

        if job_data is None:
            return None

        return json.loads(job_data)