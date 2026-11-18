"""Telegram upload error mapping (kept separate from downloader errors)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from services.telegram import TelegramService, map_telegram_error
from utils.exceptions import DownloaderError, TelegramUploadError


@pytest.mark.parametrize("error,retryable", [
    (TimedOut(), True),
    (NetworkError("connection reset"), True),
    (Forbidden("bot was blocked by the user"), False),
    (BadRequest("Request Entity Too Large"), False),
    (BadRequest("Wrong file identifier"), False),
])
def test_mapping_retryable_flag(error, retryable):
    mapped = map_telegram_error(error)
    assert isinstance(mapped, TelegramUploadError)
    assert not isinstance(mapped, DownloaderError)
    assert mapped.retryable is retryable


def test_retry_after_is_preserved():
    mapped = map_telegram_error(RetryAfter(timedelta(seconds=17)))
    assert mapped.retryable and mapped.retry_after == 17


def test_too_large_has_helpful_message():
    assert "lower quality" in map_telegram_error(BadRequest("Request Entity Too Large")).user_message


async def test_send_video_maps_errors(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    bot = MagicMock()
    bot.send_video = AsyncMock(side_effect=TimedOut())
    with pytest.raises(TelegramUploadError) as info:
        await TelegramService(bot).send_video(1, video)
    assert info.value.retryable


async def test_send_video_passes_metadata_and_thumbnail(tmp_path):
    video, thumb = tmp_path / "v.mp4", tmp_path / "t.jpg"
    video.write_bytes(b"x")
    thumb.write_bytes(b"y")
    bot = MagicMock()
    bot.send_video = AsyncMock()
    await TelegramService(bot).send_video(1, video, width=640, height=360, duration=5, thumbnail=thumb)
    kwargs = bot.send_video.await_args.kwargs
    assert kwargs["supports_streaming"] is True and kwargs["width"] == 640 and kwargs["thumbnail"] is not None


async def test_edit_status_never_raises():
    bot = MagicMock()
    bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))
    await TelegramService(bot).edit_status(1, 2, "text")
    bot.edit_message_text = AsyncMock(side_effect=NetworkError("down"))
    await TelegramService(bot).edit_status(1, 2, "text")
