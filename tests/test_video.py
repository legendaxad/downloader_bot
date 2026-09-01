import asyncio

from services.queue import DownloadQueue
from services.redis import redis_client


queue = DownloadQueue()


async def main():

    job_id = await queue.enqueue(
        user_id=123,
        chat_id=123,
        url="https://youtu.be/307A_HhcymU?si=dJb_vm5YcYC6uXFG",
        quality=720,
    )

    print("Created:", job_id)

    job = await queue.get_job(job_id)

    print("Initial:", job)

    await queue.set_status(
        job_id,
        "downloading",
    )

    await queue.set_progress(
        job_id,
        35,
    )

    job = await queue.get_job(job_id)

    print("Updated:", job)

    await queue.set_status(
        job_id,
        "completed",
    )

    await queue.set_progress(
        job_id,
        100,
    )

    job = await queue.get_job(job_id)

    print("Completed:", job)

    await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())