"""MySQL persistence for user profiles and download history (aiomysql).

Tables are created automatically on startup (``CREATE TABLE IF NOT EXISTS``).
All timestamps are stored in UTC.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiomysql

from models.task import Task, TaskStatus
from utils.logger import logger

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id            BIGINT       NOT NULL PRIMARY KEY,
        username           VARCHAR(64)  NULL,
        first_name         VARCHAR(128) NULL,
        last_name          VARCHAR(128) NULL,
        telegram_language  VARCHAR(16)  NULL,
        language           VARCHAR(8)   NULL COMMENT 'chosen interface language; NULL = not chosen yet',
        is_premium         TINYINT(1)   NOT NULL DEFAULT 0,
        downloads_count    INT UNSIGNED NOT NULL DEFAULT 0,
        created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_users_last_seen (last_seen_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS downloads (
        task_id       CHAR(32)      NOT NULL PRIMARY KEY,
        user_id       BIGINT        NOT NULL,
        url           VARCHAR(2048) NOT NULL,
        platform      VARCHAR(16)   NOT NULL,
        quality       INT           NOT NULL,
        title         VARCHAR(255)  NULL,
        status        VARCHAR(20)   NOT NULL,
        attempts      INT           NOT NULL DEFAULT 0,
        error         VARCHAR(512)  NULL,
        file_size     BIGINT        NULL,
        created_at    DATETIME      NOT NULL,
        completed_at  DATETIME      NULL,
        KEY idx_downloads_user (user_id, created_at),
        KEY idx_downloads_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
)


@dataclass(frozen=True)
class UserProfile:
    user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    telegram_language: str | None = None
    is_premium: bool = False


class Database:
    def __init__(self, *, host: str, port: int, user: str, password: str, database: str,
                 minsize: int = 1, maxsize: int = 5) -> None:
        self._params = dict(host=host, port=port, user=user, password=password, db=database,
                            minsize=minsize, maxsize=maxsize, autocommit=True, charset="utf8mb4",
                            init_command="SET time_zone = '+00:00'", connect_timeout=10, pool_recycle=3600)
        self.pool: aiomysql.Pool | None = None

    async def connect(self, retries: int = 10, delay: float = 3) -> None:
        for attempt in range(1, retries + 1):
            try:
                self.pool = await aiomysql.create_pool(**self._params)
                break
            except Exception as error:  # MySQL may still be starting (docker)
                if attempt == retries:
                    raise
                logger.warning("MySQL not ready (%s); retrying %d/%d", error, attempt, retries)
                await asyncio.sleep(delay)
        for statement in SCHEMA:
            await self.execute(statement)
        logger.info("MySQL connected and schema ready")

    async def close(self) -> None:
        if self.pool is not None:
            self.pool.close()
            await self.pool.wait_closed()
            self.pool = None

    async def execute(self, sql: str, args: tuple[Any, ...] | None = None) -> int:
        assert self.pool is not None, "Database.connect() was not called"
        async with self.pool.acquire() as conn, conn.cursor() as cur:
            return await cur.execute(sql, args)

    async def fetchone(self, sql: str, args: tuple[Any, ...] | None = None) -> tuple[Any, ...] | None:
        assert self.pool is not None, "Database.connect() was not called"
        async with self.pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(sql, args)
            return await cur.fetchone()


class UserRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert(self, profile: UserProfile) -> bool:
        """Insert or refresh a user's profile. Returns True if the user is new."""
        affected = await self.db.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, telegram_language, is_premium)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                username = VALUES(username), first_name = VALUES(first_name), last_name = VALUES(last_name),
                telegram_language = VALUES(telegram_language), is_premium = VALUES(is_premium),
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (profile.user_id, profile.username, _cut(profile.first_name, 128), _cut(profile.last_name, 128),
             profile.telegram_language, int(profile.is_premium)),
        )
        return affected == 1  # 1 = inserted, 2 = updated

    async def get_language(self, user_id: int) -> str | None:
        row = await self.db.fetchone("SELECT language FROM users WHERE user_id = %s", (user_id,))
        return row[0] if row and row[0] else None

    async def set_language(self, user_id: int, language: str) -> None:
        await self.db.execute(
            """
            INSERT INTO users (user_id, language) VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE language = VALUES(language), last_seen_at = CURRENT_TIMESTAMP
            """,
            (user_id, language),
        )


class DownloadRepository:
    """Implements the queue's ``history`` hook: one row per task, upserted on every state change."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, task: Task, file_size: int | None = None) -> None:
        completed = task.completed_at or None
        await self.db.execute(
            """
            INSERT INTO downloads (task_id, user_id, url, platform, quality, title, status, attempts, error,
                                   file_size, created_at, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FROM_UNIXTIME(%s), FROM_UNIXTIME(%s))
            ON DUPLICATE KEY UPDATE
                status = VALUES(status), attempts = VALUES(attempts), error = VALUES(error),
                file_size = COALESCE(VALUES(file_size), file_size), completed_at = VALUES(completed_at)
            """,
            (task.task_id, task.user_id, task.url[:2048], task.platform, task.quality, _cut(task.title, 255),
             task.status.value, task.attempts, _cut(task.error, 512) or None, file_size,
             task.created_at, completed),
        )
        if task.status is TaskStatus.COMPLETED:
            await self.db.execute(
                "UPDATE users SET downloads_count = downloads_count + 1 WHERE user_id = %s", (task.user_id,))


def _cut(value: str | None, limit: int) -> str | None:
    return value[:limit] if value else value
