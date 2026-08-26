import asyncio

from services.queue import DownloadQueue
from services.redis import redis_client


queue = DownloadQueue()


async def main() -> None:

    job_id = await queue.enqueue(
        user_id=123,
        chat_id=123,
        url="https://youtu.be/HWjCStB6k4o",
        quality=360,
    )

    print(f"Created job: {job_id}")

    await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())