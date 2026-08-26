from telegram import Update
from telegram.ext import ContextTypes

from keyboards.buttons import quality_keyboard
from services import VideoService
from utils.validators import (
    is_valid_url,
    is_supported_platform,
)


video_service = VideoService()


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.message.text:
        return

    url = update.message.text.strip()

    if not is_valid_url(url):
        await update.message.reply_text(
            "❌ Please send a valid URL."
        )
        return

    if not is_supported_platform(url):
        await update.message.reply_text(
            "❌ This platform is not supported yet."
        )
        return

    context.user_data["video_url"] = url

    await update.message.reply_text(
        "🔎 Getting video information..."
    )

    try:
        metadata = video_service.get_metadata(url)

        qualities = metadata["qualities"]

        await update.message.reply_text(
            f"🎬 {metadata['title']}\n\n"
            f"⏱ Duration: {metadata['duration']} seconds\n\n"
            "Choose quality:",
            reply_markup=quality_keyboard(qualities),
        )

    except Exception as error:
        print(f"Metadata error: {error}")

        await update.message.reply_text(
            "❌ Could not get video information."
        )
