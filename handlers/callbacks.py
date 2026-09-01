from telegram import Update
from telegram.ext import ContextTypes

from services import TelegramService
from utils.logger import logger
from utils.exceptions import (
    FormatNotAvailableError,
    VideoUnavailableError,
)

# Import your job/queue function here.
# Change this import if your actual queue module has a different name.
from worker import add_job


telegram_service = TelegramService()


async def handle_quality_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if not query:
        return

    await query.answer()

    if not query.data:
        return

    # --------------------------------------------------
    # Parse callback data
    # --------------------------------------------------

    try:
        _, quality = query.data.split(":", maxsplit=1)
        quality = int(quality)

    except (ValueError, TypeError):
        logger.warning(
            "Invalid quality callback | data=%s",
            query.data,
        )

        await query.message.reply_text(
            "❌ Invalid quality selection."
        )
        return

    # --------------------------------------------------
    # Get stored video URL
    # --------------------------------------------------

    url = context.user_data.get("video_url")

    if not url:
        logger.warning(
            "Video URL expired | quality=%sp",
            quality,
        )

        await query.message.reply_text(
            "❌ Video URL expired. Please send the URL again."
        )
        return

    # --------------------------------------------------
    # Create background job
    # --------------------------------------------------

    try:
        logger.info(
            "Creating download job | quality=%sp",
            quality,
        )

        job_id = await add_job(
            url=url,
            quality=quality,
            chat_id=query.message.chat_id,
        )

        # Remove URL after successfully creating the job
        context.user_data.pop("video_url", None)

        logger.info(
            "Job created | job=%s | quality=%sp",
            job_id,
            quality,
        )

        await query.message.reply_text(
            f"⏳ Your {quality}p video has been added to the queue.\n"
            "The download will start shortly."
        )

    # --------------------------------------------------
    # Known errors
    # --------------------------------------------------

    except FormatNotAvailableError:
        logger.warning(
            "Requested quality unavailable | quality=%sp",
            quality,
        )

        await query.message.reply_text(
            "❌ This quality is not available. "
            "Please choose another quality."
        )

    except VideoUnavailableError:
        logger.warning(
            "Video unavailable | quality=%sp",
            quality,
        )

        await query.message.reply_text(
            "❌ This video is unavailable, private, "
            "or has been removed."
        )

    # --------------------------------------------------
    # Unexpected error
    # --------------------------------------------------

    except Exception:
        logger.exception(
            "Failed to create download job"
        )

        await query.message.reply_text(
            "❌ Something went wrong while creating the job. "
            "Please try again."
        )