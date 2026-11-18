"""User-facing text helpers shared by handlers and workers (HTML parse mode)."""

from __future__ import annotations

from html import escape

from models.task import TaskStatus
from utils.i18n import DEFAULT_LANGUAGE, t


def status_text(status: TaskStatus, lang: str = DEFAULT_LANGUAGE) -> str:
    return t(f"status.{status.value}", lang)


def fmt_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def fmt_size(size: int | float | None) -> str:
    if not size:
        return "?"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def progress_bar(percent: int, width: int = 10) -> str:
    percent = max(0, min(100, percent))
    filled = round(percent / 100 * width)
    return "▓" * filled + "░" * (width - filled)


def quality_label(quality: int, lang: str = DEFAULT_LANGUAGE) -> str:
    return f"{quality}p" if quality > 0 else t("quality.best", lang)


def render_status(
    title: str,
    quality: int,
    status: TaskStatus,
    percent: int | None = None,
    detail: str = "",
    lang: str = DEFAULT_LANGUAGE,
) -> str:
    lines = [f"🎬 <b>{escape(truncate(title, 80))}</b>", f"🎚 {quality_label(quality, lang)}", "",
             status_text(status, lang)]
    if percent is not None and status in (TaskStatus.DOWNLOADING, TaskStatus.PROCESSING):
        lines[-1] += f" — {percent}%"
        lines.append(progress_bar(percent))
    if detail:
        lines.append(escape(detail))
    return "\n".join(lines)


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
