"""Shared dependencies for handlers, stored in ``application.bot_data``."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from telegram import User
from telegram.ext import ContextTypes

from config import Settings
from services.database import UserProfile
from services.downloader import VideoDownloader
from services.queue import TaskQueue
from services.users import UserService

DEPS_KEY = "deps"


@dataclass
class BotDeps:
    settings: Settings
    queue: TaskQueue
    downloader: VideoDownloader
    metadata_slots: asyncio.Semaphore
    users: UserService


def get_deps(context: ContextTypes.DEFAULT_TYPE) -> BotDeps:
    return context.application.bot_data[DEPS_KEY]


def profile_of(user: User) -> UserProfile:
    return UserProfile(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        telegram_language=user.language_code,
        is_premium=bool(user.is_premium),
    )


async def user_language(context: ContextTypes.DEFAULT_TYPE, user: User | None) -> str:
    """Save/refresh the user's profile and return their interface language."""
    deps = get_deps(context)
    if user is None:
        return "en"
    await deps.users.touch(profile_of(user))
    return await deps.users.language(user.id)
