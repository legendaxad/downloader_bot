"""User profiles and language preferences.

Language lookups are hot (every message), so the chosen language is cached in
Redis; MySQL (when configured) is the durable source of truth. Without MySQL
the bot still works and keeps languages in Redis only.
"""

from __future__ import annotations

from typing import Protocol

import redis.asyncio as redis

from services.database import UserProfile
from utils.i18n import DEFAULT_LANGUAGE, LANGUAGES
from utils.logger import logger

LANG_KEY = "dl:lang:{}"
SEEN_KEY = "dl:seen:{}"


class UserStore(Protocol):
    async def upsert(self, profile: UserProfile) -> bool: ...
    async def get_language(self, user_id: int) -> str | None: ...
    async def set_language(self, user_id: int, language: str) -> None: ...


class UserService:
    def __init__(self, client: redis.Redis, store: UserStore | None = None, *, seen_ttl: int = 300) -> None:
        self.r = client
        self.store = store
        self.seen_ttl = seen_ttl

    async def touch(self, profile: UserProfile) -> None:
        """Save/refresh the user's profile (at most once per ``seen_ttl`` per user)."""
        if self.store is None:
            return
        if not await self.r.set(SEEN_KEY.format(profile.user_id), "1", nx=True, ex=self.seen_ttl):
            return
        try:
            if await self.store.upsert(profile):
                logger.info("New user registered", extra={"user_id": profile.user_id})
        except Exception as error:  # DB hiccups must never break the bot
            logger.warning("Saving user failed: %s", error, extra={"user_id": profile.user_id})
            await self.r.delete(SEEN_KEY.format(profile.user_id))

    async def chosen_language(self, user_id: int) -> str | None:
        """The language the user picked, or None if they never chose one."""
        cached = await self.r.get(LANG_KEY.format(user_id))
        if cached:
            return cached
        if self.store is None:
            return None
        try:
            lang = await self.store.get_language(user_id)
        except Exception as error:
            logger.warning("Reading language failed: %s", error, extra={"user_id": user_id})
            return None
        if lang:
            await self.r.set(LANG_KEY.format(user_id), lang)
        return lang

    async def language(self, user_id: int) -> str:
        lang = await self.chosen_language(user_id)
        return lang if lang in LANGUAGES else DEFAULT_LANGUAGE

    async def set_language(self, user_id: int, lang: str) -> None:
        if lang not in LANGUAGES:
            raise ValueError(f"unsupported language: {lang}")
        await self.r.set(LANG_KEY.format(user_id), lang)
        if self.store is not None:
            try:
                await self.store.set_language(user_id, lang)
            except Exception as error:
                logger.warning("Saving language failed: %s", error, extra={"user_id": user_id})
