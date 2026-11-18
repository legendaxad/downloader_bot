"""Temporary file helpers."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from utils.logger import logger


def delete_file(file_path: str | Path | None) -> None:
    if file_path is None:
        return
    path = Path(file_path)
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning("Could not delete file %s: %s", path, error)


def delete_files(*file_paths: str | Path | None) -> None:
    for file_path in file_paths:
        delete_file(file_path)


def task_workdir(root: Path, task_id: str) -> Path:
    """Per-task directory. ``task_id`` is a server-generated hex UUID (never user input)."""
    if not task_id.isalnum():
        raise ValueError("invalid task id")
    path = (root / task_id).resolve()
    if path.parent != root.resolve():
        raise ValueError("task directory escapes download root")
    return path


def remove_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def cleanup_stale_dirs(root: Path, max_age_seconds: float) -> int:
    """Remove leftovers (from crashes/kills) older than ``max_age_seconds``."""
    if not root.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for entry in root.iterdir():
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed += 1
        except OSError as error:
            logger.warning("Cleanup failed for %s: %s", entry, error)
    if removed:
        logger.info("Removed %d stale download entries", removed)
    return removed
