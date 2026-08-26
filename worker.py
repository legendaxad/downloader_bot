import asyncio

from services.downloader import VideoDownloader
from services.queue import DownloadQueue
from services.redis import redis_client


queue = DownloadQueue()
downloader = VideoDownloader()


async def worker() -> None:
    print("Worker started...")

    while True:
        try:
            job = await queue.dequeue()

            if job is None:
                await asyncio.sleep(1)
                continue

            print(
                f"Job received | "
                f"id={job['job_id']} | "
                f"quality={job['quality']}p"
            )

            file_path = downloader.download(
                job["url"],
                job["quality"],
            )

            print(
                f"Download completed | "
                f"job={job['job_id']} | "
                f"file={file_path}"
            )

        except Exception as error:
            print(
                f"Worker error: {error}"
            )

            await asyncio.sleep(2)


async def main() -> None:
    try:
        await worker()

    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())