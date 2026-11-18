"""Translations, user service, language picker, MySQL history hook, admin contact."""

from __future__ import annotations

import asyncio
import re
import string
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import Settings
from handlers.commands import handle_language_callback, start_command
from handlers.deps import DEPS_KEY, BotDeps
from models.task import TaskStatus
from services.database import DownloadRepository, UserProfile
from services.users import UserService
from utils.exceptions import FileTooLargeError, TelegramUploadError, VideoUnavailableError
from utils.i18n import DEFAULT_LANGUAGE, LANGUAGES, catalog, normalize_language, t
from workers.processor import ProcessorConfig, TaskProcessor

from tests.test_processor import FakeDownloader, FakeMessenger, FakeVideo


# ------------------------------------------------------------------ catalogs
def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("lang", [code for code in LANGUAGES if code != DEFAULT_LANGUAGE])
def test_every_language_has_every_key_with_same_placeholders(lang):
    english, other = catalog("en"), catalog(lang)
    assert set(other) == set(english), f"{lang}: missing {set(english) - set(other)} extra {set(other) - set(english)}"
    for key, text in english.items():
        assert _fields(other[key]) == _fields(text), f"{lang}:{key} placeholders differ"


@pytest.mark.parametrize("lang", list(LANGUAGES))
def test_html_tags_are_balanced(lang):
    for key, text in catalog(lang).items():
        for tag in ("b", "i", "code"):
            assert len(re.findall(f"<{tag}>", text)) == len(re.findall(f"</{tag}>", text)), f"{lang}:{key}"


@pytest.mark.parametrize("lang", list(LANGUAGES))
def test_command_descriptions_fit_telegram_limits(lang):
    for name in ("start", "help", "language", "cancel", "about"):
        assert 3 <= len(t(f"cmd.{name}", lang)) <= 256


def test_fallbacks():
    assert normalize_language("ru-RU") == "ru"
    assert normalize_language("de") == "en" and normalize_language(None) == "en"
    assert t("err.generic", "de") == t("err.generic", "en")
    assert t("no.such.key", "ru") == "no.such.key"


def test_exceptions_are_localized():
    error = VideoUnavailableError("private")
    assert error.localized("ru") == catalog("ru")["err.unavailable"]
    assert error.localized("ko") == catalog("ko")["err.unavailable"]
    assert error.user_message == catalog("en")["err.unavailable"]
    sized = FileTooLargeError("x", key="err.file_too_large_size", params={"size": "60 MB", "limit": "50 MB"})
    assert "60 MB" in sized.localized("uz") and "50 MB" in sized.localized("uz")


# ------------------------------------------------------------------ users
class FakeStore:
    def __init__(self):
        self.users: dict[int, UserProfile] = {}
        self.languages: dict[int, str] = {}
        self.upserts = 0
        self.fail = False

    async def upsert(self, profile):
        if self.fail:
            raise ConnectionError("db down")
        self.upserts += 1
        new = profile.user_id not in self.users
        self.users[profile.user_id] = profile
        return new

    async def get_language(self, user_id):
        return self.languages.get(user_id)

    async def set_language(self, user_id, language):
        self.languages[user_id] = language


async def test_touch_saves_profile_but_throttles_writes(redis_client):
    store = FakeStore()
    users = UserService(redis_client, store)
    profile = UserProfile(user_id=5, username="ann", first_name="Ann", telegram_language="ru")
    await users.touch(profile)
    await users.touch(profile)
    assert store.users[5].username == "ann" and store.upserts == 1


async def test_db_failure_does_not_break_bot(redis_client):
    store = FakeStore()
    store.fail = True
    users = UserService(redis_client, store)
    await users.touch(UserProfile(user_id=5))  # no exception
    store.fail = False
    await users.touch(UserProfile(user_id=5))  # retried, because the throttle key was released
    assert 5 in store.users


async def test_language_default_choice_and_cache(redis_client):
    store = FakeStore()
    users = UserService(redis_client, store)
    assert await users.chosen_language(1) is None
    assert await users.language(1) == "en"  # English by default
    await users.set_language(1, "uz")
    assert store.languages[1] == "uz" and await users.language(1) == "uz"
    # survives a cold Redis cache (loaded back from MySQL)
    await redis_client.flushall()
    assert await users.language(1) == "uz"
    with pytest.raises(ValueError):
        await users.set_language(1, "xx")


async def test_language_works_without_mysql(redis_client):
    users = UserService(redis_client, None)
    await users.set_language(2, "ko")
    assert await users.language(2) == "ko"


# ------------------------------------------------------------------ handlers
@pytest.fixture
def context(queue):
    deps = BotDeps(settings=Settings(), queue=queue, downloader=MagicMock(), metadata_slots=asyncio.Semaphore(1),
                   users=UserService(queue.r, FakeStore()))
    ctx = MagicMock()
    ctx.application.bot_data = {DEPS_KEY: deps}
    return ctx, deps


def tg_user(user_id=1):
    return SimpleNamespace(id=user_id, username="ann", first_name="Ann", last_name=None, language_code="ko",
                           is_premium=False)


def _button_urls(markup):
    return [b.url for row in markup.inline_keyboard for b in row if b.url]


async def test_first_start_asks_language_then_shows_menu_with_admin(context):
    ctx, deps = context
    message = MagicMock()
    message.reply_text = AsyncMock()
    update = SimpleNamespace(effective_message=message, effective_user=tg_user())

    await start_command(update, ctx)
    first = message.reply_text.await_args
    assert "Choose your language" in first.args[0]
    assert {b.callback_data for row in first.kwargs["reply_markup"].inline_keyboard for b in row} == {
        f"lang:{code}" for code in LANGUAGES}

    await deps.users.set_language(1, "ru")
    await start_command(update, ctx)
    second = message.reply_text.await_args
    assert "Привет, Ann" in second.args[0]
    assert "https://t.me/legend_axad" in _button_urls(second.kwargs["reply_markup"])


async def test_language_callback_saves_choice(context):
    ctx, deps = context
    query = MagicMock(data="lang:ko", from_user=tg_user())
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    await handle_language_callback(SimpleNamespace(callback_query=query), ctx)
    assert await deps.users.language(1) == "ko"
    assert "안녕하세요" in query.edit_message_text.await_args.args[0]


# ------------------------------------------------------------------ worker
async def test_failure_message_is_localized_with_admin_button(queue, make_session, tmp_path):
    await queue.submit(await make_session(), 720, status_message_id=9, lang="ru")
    task = await queue.claim(0.1)
    messenger = FakeMessenger()
    markups = []
    original = messenger.edit_status

    async def capture(chat_id, message_id, text, markup=None):
        markups.append(markup)
        await original(chat_id, message_id, text, markup)

    messenger.edit_status = capture
    config = ProcessorConfig(download_dir=tmp_path, max_upload_bytes=10_000, poll_interval=0.01)
    await TaskProcessor(queue, FakeDownloader(error=VideoUnavailableError("x")), FakeVideo(), messenger,
                        config).run(task)
    assert catalog("ru")["err.unavailable"] in messenger.edits[-1]
    assert "@legend_axad" in messenger.edits[-1]
    assert _button_urls(markups[-1]) == ["https://t.me/legend_axad"]


async def test_status_messages_use_task_language(queue, make_session, tmp_path):
    await queue.submit(await make_session(), 720, status_message_id=9, lang="uz")
    task = await queue.claim(0.1)
    messenger = FakeMessenger()
    config = ProcessorConfig(download_dir=tmp_path, max_upload_bytes=10_000, poll_interval=0.01)
    await TaskProcessor(queue, FakeDownloader(), FakeVideo(), messenger, config).run(task)
    assert catalog("uz")["status.COMPLETED"] in messenger.edits[-1]


async def test_telegram_errors_are_localized():
    assert TelegramUploadError("x", key="err.bot_blocked").localized("ko") == catalog("ko")["err.bot_blocked"]


# ------------------------------------------------------------------ history
class FakeHistory:
    def __init__(self, fail=False):
        self.rows: list[tuple[str, TaskStatus, int | None]] = []
        self.fail = fail

    async def record(self, task, file_size=None):
        if self.fail:
            raise ConnectionError("db down")
        self.rows.append((task.task_id, task.status, file_size))


async def test_queue_records_every_state_change(queue, make_session):
    history = FakeHistory()
    queue.history = history
    created = (await queue.submit(await make_session(), 720)).task
    task = await queue.claim(0.1)
    await queue.schedule_retry(task, 0)
    await queue.promote_due()
    task = await queue.claim(0.1)
    await queue.finish(task, TaskStatus.COMPLETED, file_size=1234)
    assert [(r[1], r[2]) for r in history.rows] == [
        (TaskStatus.QUEUED, None), (TaskStatus.QUEUED, None), (TaskStatus.COMPLETED, 1234)]
    assert {r[0] for r in history.rows} == {created.task_id}


async def test_history_failure_does_not_break_queue(queue, make_session):
    queue.history = FakeHistory(fail=True)
    task = (await queue.submit(await make_session(), 720)).task
    assert (await queue.get_task(task.task_id)).status is TaskStatus.QUEUED


async def test_download_repository_sql():
    db = MagicMock()
    db.execute = AsyncMock(return_value=1)
    from models.task import Task
    task = Task(user_id=7, chat_id=7, url="https://youtu.be/x", platform="youtube", quality=720, title="T",
                status=TaskStatus.COMPLETED, completed_at=1.0)
    await DownloadRepository(db).record(task, file_size=99)
    sqls = [call.args[0] for call in db.execute.await_args_list]
    assert "INSERT INTO downloads" in sqls[0] and "downloads_count = downloads_count + 1" in sqls[1]
    assert db.execute.await_args_list[0].args[1][9] == 99  # file_size parameter
