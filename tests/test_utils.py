"""Temp-file cleanup, logging redaction, task model, formatting."""

from __future__ import annotations

import json
import logging
import os
import time

import pytest

from models.task import Task, TaskStatus
from utils.files import cleanup_stale_dirs, task_workdir
from utils.formatting import fmt_duration, fmt_size, render_status
from utils.logger import JsonFormatter, RedactingFilter, TextFormatter, redact

FAKE_TOKEN = "1234567890:" + "A" * 35


def test_cleanup_removes_only_old_entries(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    (old / "video.mp4").write_bytes(b"x")
    new.mkdir()
    past = time.time() - 7200
    os.utime(old, (past, past))
    assert cleanup_stale_dirs(tmp_path, 3600) == 1
    assert not old.exists() and new.exists()


def test_task_workdir_rejects_traversal(tmp_path):
    assert task_workdir(tmp_path, "abc123").parent == tmp_path.resolve()
    for bad in ("../etc", "a/b", ".."):
        with pytest.raises(ValueError):
            task_workdir(tmp_path, bad)


def test_redaction_of_tokens_and_redis_passwords():
    text = f"POST https://api.telegram.org/bot{FAKE_TOKEN}/sendVideo redis://:s3cret@redis:6379/0"
    cleaned = redact(text)
    assert FAKE_TOKEN not in cleaned and "s3cret" not in cleaned


@pytest.mark.parametrize("formatter", [TextFormatter(), JsonFormatter()])
def test_formatters_include_context_and_redact(formatter):
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "token %s", (FAKE_TOKEN,), None)
    record.task_id = "t1"
    record.user_id = 5
    RedactingFilter().filter(record)
    out = formatter.format(record)
    assert FAKE_TOKEN not in out and "t1" in out
    if isinstance(formatter, JsonFormatter):
        assert json.loads(out)["user_id"] == 5


def test_task_roundtrip():
    task = Task(user_id=1, chat_id=2, url="https://youtu.be/x", platform="youtube", quality=720, title="t")
    task.cancel_requested = True
    restored = Task.from_redis(task.to_redis())
    assert restored == task


def test_lifecycle_terminal_states():
    assert {s for s in TaskStatus if s.is_terminal} == {TaskStatus.COMPLETED, TaskStatus.FAILED,
                                                       TaskStatus.CANCELLED}


def test_formatting():
    assert fmt_duration(3725) == "1:02:05" and fmt_duration(65) == "1:05" and fmt_duration(None) == "—"
    assert fmt_size(1536 * 1024) == "1.5 MB"
    text = render_status("<b>x</b>", 720, TaskStatus.DOWNLOADING, 40)
    assert "&lt;b&gt;" in text and "40%" in text  # titles are HTML-escaped
