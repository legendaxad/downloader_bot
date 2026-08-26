from telegram import Update
from telegram.ext import ContextTypes
import asyncio
from services import (
    MediaService,
    TelegramService,
    VideoDownloader,
)

from utils.exceptions import (
    DownloadFailedError,
    FormatNotAvailableError,
    MediaProcessingError,
    TelegramUploadError,
    VideoUnavailableError,
)

from utils.files import delete_files
from utils.logger import logger


downloader = VideoDownloader()
media_service = MediaService()
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
            "❌ Video URL expired. "
            "Please send the URL again."
        )
        return

    # --------------------------------------------------
    # Temporary file variables
    # --------------------------------------------------

    file_path = None
    processed_path = None

    # --------------------------------------------------
    # Download → Process → Upload
    # --------------------------------------------------

    try:
        logger.info(
            "Video processing started | quality=%sp",
            quality,
        )

        await query.message.reply_text(
            f"⏳ Downloading {quality}p video..."
        )

        # --------------------------------------------------
        # Download
        # --------------------------------------------------

        file_path = await asyncio.to_thread(
            downloader.download,
            url,
            quality,
        )

        logger.info(
            "Video download completed | quality=%sp | file=%s",
            quality,
            file_path,
        )

        await query.message.reply_text(
            "🔄 Processing video..."
        )

        # --------------------------------------------------
        # Prepare media for Telegram
        # --------------------------------------------------

        processed_path = await asyncio.to_thread(
            media_service.prepare_for_telegram,
            file_path,
        )

        logger.info(
            "Media processing completed | file=%s",
            processed_path,
        )

        await query.message.reply_text(
            "📤 Uploading video..."
        )

        # --------------------------------------------------
        # Upload
        # --------------------------------------------------

        await telegram_service.send_video(
            query.message,
            processed_path,
        )

        # --------------------------------------------------
        # Successfully processed
        # --------------------------------------------------

        context.user_data.pop(
            "video_url",
            None,
        )

        logger.info(
            "Video processing completed successfully | quality=%sp",
            quality,
        )

        await query.message.reply_text(
            "✅ Video downloaded successfully!"
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

    except DownloadFailedError:
        logger.error(
            "Video download failed | quality=%sp",
            quality,
        )

        await query.message.reply_text(
            "❌ Failed to download the video. "
            "Please try again."
        )

    except MediaProcessingError:
        logger.error(
            "Media processing failed | file=%s",
            file_path,
        )

        await query.message.reply_text(
            "❌ Failed to process the video."
        )

    except TelegramUploadError:
        logger.error(
            "Telegram upload failed | file=%s",
            processed_path,
        )

        await query.message.reply_text(
            "❌ The video was downloaded, "
            "but Telegram could not upload it."
        )

    # --------------------------------------------------
    # Unexpected error
    # --------------------------------------------------

    except Exception:
        logger.exception(
            "Unexpected error during video processing"
        )

        await query.message.reply_text(
            "❌ Something went wrong. "
            "Please try again later."
        )

    # --------------------------------------------------
    # Always cleanup temporary files
    # --------------------------------------------------

    finally:
        delete_files(
            file_path,
            processed_path,
        )

        logger.info(
            "Temporary files cleanup completed"
        )