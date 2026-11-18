# syntax=docker/dockerfile:1
FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg/ffprobe for merging and processing; nodejs is the JavaScript runtime
# yt-dlp needs for full YouTube format support (YTDLP_JS_RUNTIMES=node).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as an unprivileged user; downloads live in a dedicated writable directory.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/downloads \
    && chown -R app:app /app/downloads
USER app

ENV DOWNLOAD_DIR=/app/downloads \
    LOG_FORMAT=json \
    YTDLP_JS_RUNTIMES=node

# tini forwards SIGTERM so bot/worker can shut down gracefully.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "bot.py"]
