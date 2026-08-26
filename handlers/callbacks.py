from telegram import Update
from telegram.ext import ContextTypes

from services import (
    MediaService,
    TelegramService,
    VideoDownloader,
)

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

    _, quality = query.data.split(":")

    quality = int(quality)

    url = context.user_data.get("video_url")

    if not url:
        await query.message.reply_text(
            "❌ Video URL expired. Please send the URL again."
        )
        return

    await query.message.reply_text(
        f"⏳ Downloading {quality}p video..."
    )

    try:
        file_path = downloader.download(
            url,
            quality,
        )

        await query.message.reply_text(
            "🔄 Processing video..."
        )

        processed_path = media_service.prepare_for_telegram(
            file_path
        )
        await query.message.reply_text(
            "📤 Uploading video..."
        )

        await telegram_service.send_video(
            query.message,
            processed_path,
        )

    except Exception as error:
        print(f"Download/upload error: {error}")

        await query.message.reply_text(
            "❌ Failed to process the video."
        )