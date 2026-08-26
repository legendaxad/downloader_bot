from pathlib import Path

from utils.logger import logger

def delete_file(
    file_path: str | Path | None,
) -> None:
    if file_path is None:
        return

    path = Path(file_path)

    if path.exists():
        path.unlink()
        logger.info(
            "Temporary file deleted | file=%s",
            path,
        )

def delete_files(
    *file_paths: str | Path | None,
) -> None:
    for file_path in file_paths:
        delete_file(file_path)