"""Callback handlers: expired sessions, invalid/repeated presses, ownership, cancel."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import Settings
from handlers.callbacks import handle_cancel_callback, handle_quality_callback
from handlers.deps import DEPS_KEY, BotDeps
from models.task import TaskStatus
from services.users import UserService


def make_query(data: str, user_id: int = 1):
    status_msg = MagicMock(message_id=77)
    status_msg.edit_text = AsyncMock()
    status_msg.delete = AsyncMock()
    message = MagicMock(message_id=10, chat_id=user_id)
    message.reply_text = AsyncMock(return_value=status_msg)
    query = MagicMock(data=data, message=message,
                      from_user=SimpleNamespace(id=user_id, username="u", first_name="Ann", last_name=None,
                                                language_code="en", is_premium=False))
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    return query, status_msg


@pytest.fixture
def context(queue):
    deps = BotDeps(settings=Settings(), queue=queue, downloader=MagicMock(), metadata_slots=asyncio.Semaphore(1),
                   users=UserService(queue.r))
    ctx = MagicMock()
    ctx.application.bot_data = {DEPS_KEY: deps}
    ctx.bot.edit_message_text = AsyncMock()
    return ctx


async def test_quality_selection_queues_task(context, make_session, queue):
    session = await make_session()
    query, status_msg = make_query(f"q:{session.session_id}:720")
    await handle_quality_callback(SimpleNamespace(callback_query=query), context)
    task_id = (await queue.get_session(session.session_id)).task_id
    task = await queue.get_task(task_id)
    assert task.status is TaskStatus.QUEUED and task.status_message_id == 77 and task.quality == 720
    query.edit_message_reply_markup.assert_awaited()  # keyboard removed
    assert "Queued" in query.answer.await_args.args[0]


async def test_expired_session(context):
    query, _ = make_query("q:0123456789ab:720")
    await handle_quality_callback(SimpleNamespace(callback_query=query), context)
    assert "expired" in query.answer.await_args.args[0]
    query.message.reply_text.assert_not_awaited()


async def test_invalid_quality(context, make_session):
    session = await make_session()
    query, _ = make_query(f"q:{session.session_id}:1440")
    await handle_quality_callback(SimpleNamespace(callback_query=query), context)
    assert "isn't available" in query.answer.await_args.args[0]


async def test_other_users_cannot_use_menu(context, make_session):
    session = await make_session(user_id=1)
    query, _ = make_query(f"q:{session.session_id}:720", user_id=2)
    await handle_quality_callback(SimpleNamespace(callback_query=query), context)
    assert "someone else" in query.answer.await_args.args[0]


async def test_repeated_press_does_not_duplicate(context, make_session, queue):
    session = await make_session()
    for _ in range(2):
        query, _ = make_query(f"q:{session.session_id}:720")
        await handle_quality_callback(SimpleNamespace(callback_query=query), context)
    assert "Already" in query.answer.await_args.args[0]
    assert len(await queue.active_tasks(1)) == 1


async def test_limit_error_shown_as_alert(context, make_session):
    for i in range(2):
        q, _ = make_query(f"q:{(await make_session(url=f'https://youtu.be/{i}')).session_id}:720")
        await handle_quality_callback(SimpleNamespace(callback_query=q), context)
    query, status_msg = make_query(f"q:{(await make_session(url='https://youtu.be/9')).session_id}:720")
    await handle_quality_callback(SimpleNamespace(callback_query=query), context)
    assert query.answer.await_args.kwargs.get("show_alert") is True
    status_msg.delete.assert_awaited()


async def test_cancel_button_cancels_queued_task(context, make_session, queue):
    task = (await queue.submit(await make_session(), 720, status_message_id=77)).task
    query, _ = make_query(f"c:{task.task_id}")
    query.message.message_id = 77
    await handle_cancel_callback(SimpleNamespace(callback_query=query), context)
    assert (await queue.get_task(task.task_id)).status is TaskStatus.CANCELLED
    context.bot.edit_message_text.assert_awaited()


async def test_cancel_finished_task(context, make_session, queue):
    task = (await queue.submit(await make_session(), 720)).task
    await queue.finish(task, TaskStatus.COMPLETED)
    query, _ = make_query(f"c:{task.task_id}")
    await handle_cancel_callback(SimpleNamespace(callback_query=query), context)
    assert "already finished" in query.answer.await_args.args[0]
