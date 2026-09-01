import asyncio

from services.downloader import VideoDownloader
from services.queue import DownloadQueue
from services.redis import redis_client
from utils.logger import logger


queue = DownloadQueue()
downloader = VideoDownloader()


async def worker() -> None:
    logger.info("Worker started...")

    while True:
        try:
            job = await queue.dequeue()

            if job is None:
                continue

            job_id = job["job_id"]
            quality = job["quality"]

            logger.info(
                "Job received | job=%s | quality=%sp",
                job_id,
                quality,
            )

            await queue.set_status(
                job_id,
                "downloading",
            )

            await queue.set_progress(
                job_id,
                0,
            )

            try:
                logger.info(
                    "Download started | job=%s | quality=%sp",
                    job_id,
                    quality,
                )

                file_path = downloader.download(
                    job["url"],
                    quality,
                )

                logger.info(
                    "Download completed | job=%s | file=%s",
                    job_id,
                    file_path,
                )

                await queue.set_progress(
                    job_id,
                    90,
                )

                await queue.set_status(
                    job_id,
                    "processing",
                )

                logger.info(
                    "Processing started | job=%s",
                    job_id,
                )

                # Processing will be connected here later.

                await queue.set_progress(
                    job_id,
                    100,
                )

                await queue.set_status(
                    job_id,
                    "completed",
                )

                logger.info(
                    "Job completed | job=%s",
                    job_id,
                )

            except Exception:
                logger.exception(
                    "Job failed | job=%s",
                    job_id,
                )

                await queue.set_status(
                    job_id,
                    "failed",
                )

        except asyncio.CancelledError:
            logger.info("Worker shutting down...")
            raise

        except Exception:
            logger.exception("Worker error")
            await asyncio.sleep(2)


async def main() -> None:
    try:
        await worker()

    finally:
        await redis_client.aclose()
        logger.info("Redis connection closed")


if __name__ == "__main__":
    asyncio.run(main())