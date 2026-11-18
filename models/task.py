"""Download task model and lifecycle states."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    FETCHING_METADATA = "FETCHING_METADATA"
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    PROCESSING = "PROCESSING"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})


def new_task_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Task:
    user_id: int
    chat_id: int
    url: str
    platform: str
    quality: int  # target height in pixels; 0 = best available
    title: str = ""
    lang: str = "en"  # user's interface language for status messages
    task_id: str = field(default_factory=new_task_id)
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    attempts: int = 0
    status_message_id: int = 0
    cancel_requested: bool = False
    error: str = ""  # user-safe error text
    error_detail: str = ""  # technical detail (logs/ops only)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float = 0.0

    def to_redis(self) -> dict[str, str]:
        data = asdict(self)
        data["status"] = str(self.status)
        data["cancel_requested"] = "1" if self.cancel_requested else "0"
        return {key: str(value) for key, value in data.items()}

    @classmethod
    def from_redis(cls, data: dict[str, str]) -> Task:
        return cls(
            user_id=int(data["user_id"]),
            chat_id=int(data["chat_id"]),
            url=data["url"],
            platform=data.get("platform", ""),
            quality=int(data.get("quality", 0)),
            title=data.get("title", ""),
            lang=data.get("lang", "en"),
            task_id=data["task_id"],
            status=TaskStatus(data.get("status", TaskStatus.PENDING)),
            progress=int(data.get("progress", 0)),
            attempts=int(data.get("attempts", 0)),
            status_message_id=int(data.get("status_message_id", 0)),
            cancel_requested=data.get("cancel_requested") == "1",
            error=data.get("error", ""),
            error_detail=data.get("error_detail", ""),
            created_at=float(data.get("created_at", 0)),
            updated_at=float(data.get("updated_at", 0)),
            completed_at=float(data.get("completed_at", 0)),
        )
