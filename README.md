# Telegram Social Media Downloader Bot

A Telegram bot that downloads videos from social media and sends them back as
Telegram-ready MP4 files. Users send a link, choose a quality, watch live
progress, and receive the video. Downloads run in separate background
workers backed by a Redis queue.

## 1. Supported platforms

| Platform | Example link |
|---|---|
| YouTube (videos, Shorts) | `https://www.youtube.com/watch?v=…`, `https://youtu.be/…` |
| TikTok | `https://www.tiktok.com/@user/video/…`, `https://vm.tiktok.com/…` |
| Instagram (public reels/posts) | `https://www.instagram.com/reel/…` |
| Twitter / X | `https://x.com/user/status/…` |

Only **publicly accessible** content is supported. The bot never uses cookies or
credentials and never bypasses DRM, login walls, age gates or geo restrictions.
That content is reported to the user as restricted.

## 2. Features

- 🌐 **4 languages**: English (default), Русский, Oʻzbekcha and 한국어. New users pick a language on `/start` and can change it with `/language` or the 🌐 button. Every message, button, status, error and the command menu is translated.
- 💬 **Admin contact**: a "Contact admin" button (→ [@legend_axad](https://t.me/legend_axad)) on `/start`, `/help`, `/about` and every error. It is configurable with `ADMIN_USERNAME`.
- 🗄 **MySQL user data**: user profiles (ID, username, name, Telegram language, chosen language, premium flag, first and last seen, download count) and full download history. It is optional: without `MYSQL_HOST` the bot runs and stores nothing in a database.
- 🏠 Main menu with Help / Language / About / Contact admin buttons.
- URL validation with an SSRF-safe allowlist (hosts, schemes, ports, no credentials).
- Metadata preview with thumbnail, title, uploader, duration and the available qualities, including estimated sizes.
- Qualities over the Telegram limit are hidden up front.
- Quality buttons; expired, invalid, foreign and repeated presses are handled.
- Reliable Redis queue: unique task IDs, at-least-once processing, per-user duplicate detection, per-user limits and rate limiting.
- Lifecycle `PENDING → FETCHING_METADATA → QUEUED → DOWNLOADING → PROCESSING → UPLOADING → COMPLETED | FAILED | CANCELLED`.
- Live progress (percentage and progress bar) for download and FFmpeg processing. Message edits are throttled to avoid Telegram flood limits.
- Cancellation through the 🚫 button or `/cancel`, both while queued and while running. A running cancel stops yt-dlp, kills FFmpeg, or aborts the upload.
- Controlled retries with exponential backoff (respecting Telegram `RetryAfter`) for transient errors only. Permanent errors fail immediately.
- Stuck-task recovery: worker heartbeats, and tasks from dead workers are re-queued (or failed after max retries).
- Smart processing: H.264/AAC is only remuxed; H.264 with other audio gets the audio re-encoded; anything else becomes H.264 `yuv420p` + AAC. Output is always MP4 with `+faststart`, plus a generated thumbnail.
- Per-task temporary directories that are always deleted, with a periodic sweep of leftovers.
- Structured logging (text or JSON) with task, user and worker IDs and per-stage timings. Bot tokens and Redis passwords are redacted.
- Graceful shutdown: workers finish or re-queue running tasks on SIGTERM.
- Docker Compose deployment with Redis persistence, health checks, restart policies and resource limits.

## 3. Architecture

```
            Telegram
               │  updates                     ▲ sendVideo / editMessage
               ▼                              │
┌──────────────────────────┐        ┌───────────────────────────┐
│ bot.py (python-telegram- │        │ worker.py  (N processes)  │
│ bot, polling)            │        │  workers/runner.py        │
│  handlers/  keyboards/   │        │   ├ concurrency slots     │
│  ├ validate URL          │        │   ├ maintenance loop      │
│  ├ metadata (thread pool)│        │   └ graceful shutdown     │
│  └ create session/task   │        │  workers/processor.py     │
└────────────┬─────────────┘        │   download → process →    │
             │ LPUSH                │   upload, progress, cancel│
             ▼                      └─────────────┬─────────────┘
        ┌─────────────────── Redis ───────────────┴───┐
        │ queue:pending → (BLMOVE) → queue:processing │
        │ queue:delayed (retries)   task:<id> hashes  │
        │ hb:<id> heartbeats        session:<sid>     │
        └─────────────────────────────────────────────┘
```

| Path | Responsibility |
|---|---|
| `bot.py` | Bot entry point |
| `worker.py` | Worker entry point (signal handling, wiring) |
| `loader.py` | Builds the PTB application, registers handlers, creates Redis and services |
| `config.py` | Typed settings from environment variables |
| `handlers/` | `/start` `/help` `/about` `/cancel`, URL messages, callbacks, global error handler |
| `keyboards/` | Inline keyboards and callback-data patterns |
| `services/downloader.py` | yt-dlp metadata and download. **Independent of Telegram and Redis** |
| `services/video.py` | FFmpeg/ffprobe processing and thumbnails (async, cancellable) |
| `services/telegram.py` | Upload and status messages; maps API errors to `TelegramUploadError` |
| `services/queue.py` | Redis queue, task state, sessions, limits, recovery |
| `workers/` | Task pipeline (`processor.py`) and worker runtime (`runner.py`) |
| `models/task.py` | `Task` and the `TaskStatus` lifecycle |
| `utils/` | Validators, exceptions, logging, formatting, temp-file helpers |
| `healthcheck.py` | Container health check (Redis ping and process heartbeat) |

| `services/database.py` | MySQL pool, auto-created schema, `UserRepository`, `DownloadRepository` |
| `services/users.py` | User profiles and language preference (Redis cache + MySQL) |
| `utils/i18n.py`, `locales/*.json` | Translations (`en`, `ru`, `uz`, `ko`) |

**Data storage**

- **Redis**: the live queue, task state (kept for `TASK_TTL_SECONDS`), quality sessions, and the language cache.
- **MySQL** (optional): durable user data and download history. Tables are created automatically on first start:
  - `users`: `user_id`, `username`, `first_name`, `last_name`, `telegram_language`, `language`, `is_premium`, `downloads_count`, `created_at`, `last_seen_at`
  - `downloads`: `task_id`, `user_id`, `url`, `platform`, `quality`, `title`, `status`, `attempts`, `error`, `file_size`, `created_at`, `completed_at`

**Adding a language:** copy `locales/en.json` to `locales/<code>.json`, translate it,
and add the code to `LANGUAGES` in `utils/i18n.py`. The test suite checks that every
key and `{placeholder}` exists in all languages.

## 4. Prerequisites

- Python 3.12+ (tested on 3.14; the Docker image uses 3.13)
- FFmpeg (including `ffprobe`)
- Redis 6.2+ (for `BLMOVE`)
- Node.js 20+ or Deno (JavaScript runtime that yt-dlp needs for YouTube)
- A bot token from [@BotFather](https://t.me/BotFather)

## 5. Python environment setup

```bash
cd /path/to/downloader
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt          # runtime
pip install -r requirements-dev.txt      # + tests
```

## 6. FFmpeg installation

```bash
# macOS
brew install ffmpeg
# Debian / Ubuntu
sudo apt update && sudo apt install -y ffmpeg
# verify
ffmpeg -version && ffprobe -version
```

Install a JavaScript runtime for YouTube:

```bash
brew install node                 # macOS
sudo apt install -y nodejs        # Debian/Ubuntu (needs >= 20)
```

## 7. Redis installation and configuration

```bash
# macOS
brew install redis
brew services start redis         # or: redis-server
# Debian / Ubuntu
sudo apt install -y redis-server
sudo systemctl enable --now redis-server
# verify
redis-cli ping                    # → PONG
```

Recommended production settings (`/etc/redis/redis.conf`):

```
appendonly yes
maxmemory-policy noeviction     # never silently drop queued tasks
bind 127.0.0.1                  # do not expose Redis to the internet
requirepass <strong-password>   # then REDIS_URL=redis://:<password>@localhost:6379/0
```

## 7b. MySQL installation and configuration

```bash
# macOS
brew install mysql
brew services start mysql
# Debian / Ubuntu
sudo apt install -y mysql-server
sudo systemctl enable --now mysql
```

Create the database and a dedicated user (choose your own password):

```bash
mysql -u root -p          # on a fresh Homebrew install: mysql -u root
```
```sql
CREATE DATABASE downloader_bot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'downloader'@'localhost' IDENTIFIED BY 'your-strong-password';
GRANT ALL PRIVILEGES ON downloader_bot.* TO 'downloader'@'localhost';
FLUSH PRIVILEGES;
```

Then set `MYSQL_HOST=localhost`, `MYSQL_USER=downloader`, `MYSQL_PASSWORD=…` and
`MYSQL_DATABASE=downloader_bot` in `.env`. The tables are created automatically.

Useful queries:

```sql
SELECT COUNT(*) FROM users;
SELECT language, COUNT(*) FROM users GROUP BY language;
SELECT * FROM users ORDER BY last_seen_at DESC LIMIT 20;
SELECT status, COUNT(*) FROM downloads GROUP BY status;
```

## 8. Environment variables

```bash
cp .env.example .env
# edit .env and set at least BOT_TOKEN
```

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | — (**required**) | Telegram bot token |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `ADMIN_USERNAME` | `legend_axad` | Telegram username shown in "Contact admin" |
| `MYSQL_HOST` | empty (disabled) | MySQL host; empty means no database |
| `MYSQL_PORT` | `3306` | MySQL port |
| `MYSQL_USER` | `downloader` | MySQL user |
| `MYSQL_PASSWORD` | empty | MySQL password |
| `MYSQL_DATABASE` | `downloader_bot` | MySQL database name |
| `MYSQL_ROOT_PASSWORD` | — | Docker Compose only: root password for the MySQL container |
| `TELEGRAM_API_BASE_URL` | empty | Self-hosted Bot API server URL (allows uploads up to 2 GB) |
| `MAX_UPLOAD_MB` | `50` | Max file size sent to Telegram |
| `MAX_DURATION_SECONDS` | `10800` | Reject longer videos |
| `WORKER_CONCURRENCY` | `2` | Parallel tasks per worker process |
| `METADATA_CONCURRENCY` | `4` | Parallel metadata lookups in the bot |
| `MAX_ACTIVE_TASKS_PER_USER` | `2` | Queued plus running tasks per user |
| `USER_REQUESTS_PER_MINUTE` | `10` | Links per user per minute |
| `METADATA_TIMEOUT_SECONDS` | `60` | Metadata lookup timeout |
| `DOWNLOAD_TIMEOUT_SECONDS` | `1800` | Download timeout (counts as transient and is retried) |
| `PROCESSING_TIMEOUT_SECONDS` | `1800` | FFmpeg timeout |
| `UPLOAD_TIMEOUT_SECONDS` | `600` | Telegram upload timeout |
| `MAX_RETRIES` | `2` | Retries for transient failures |
| `RETRY_BASE_DELAY_SECONDS` | `15` | Backoff base (15 s, 30 s, …) |
| `STALE_TASK_SECONDS` | `120` | A task with no heartbeat for this long is recovered |
| `SESSION_TTL_SECONDS` | `1800` | How long quality buttons stay valid |
| `TASK_TTL_SECONDS` | `86400` | How long task records are kept |
| `SHUTDOWN_GRACE_SECONDS` | `45` | Time running tasks get on shutdown before re-queue |
| `DOWNLOAD_DIR` | `downloads` | Temporary working directory |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `text` | `text` or `json` |
| `LOG_FILE` | empty | Optional rotating log file |
| `YTDLP_JS_RUNTIMES` | empty | `node`, `deno` or both (comma-separated) |
| `WORKER_NAME` | `<host>-<pid>` | Worker identifier in logs and health checks |

## 9. Run locally

You need three terminals: Redis, the bot and at least one worker.

```bash
# terminal 1 — Redis
redis-server

# terminal 2 — bot
source venv/bin/activate
python bot.py

# terminal 3 — worker
source venv/bin/activate
python worker.py
```

## 10. Running workers

Every worker process handles up to `WORKER_CONCURRENCY` tasks. To scale, start
more processes, on the same host or on other hosts that share Redis:

```bash
WORKER_CONCURRENCY=3 python worker.py
WORKER_NAME=worker-2 python worker.py
```

Stop a worker with `Ctrl+C` or `SIGTERM`. It stops taking new tasks, waits up to
`SHUTDOWN_GRACE_SECONDS` for running tasks, and re-queues any that are left.
If a worker is killed outright (`SIGKILL`, crash or power loss), its tasks are
recovered automatically after `STALE_TASK_SECONDS`.

## 11. Running tests

```bash
source venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest                 # offline suite (Redis mocked with fakeredis; uses local ffmpeg)
python -m pytest -m network      # optional live check against YouTube
```

## 12. Docker setup

```bash
cp .env.example .env              # set BOT_TOKEN; REDIS_URL is overridden by compose
docker compose build
docker compose up -d
docker compose ps                 # all services should become "healthy"
docker compose logs -f bot worker
docker compose up -d --scale worker=3   # more workers
docker compose down               # graceful stop (Redis data persists in a volume)
```

The compose stack includes:

- `redis` with AOF persistence, `noeviction`, a health check and no published port.
- `mysql` (8.4, utf8mb4) with a persistent volume, a health check and no published port. Set `MYSQL_PASSWORD` and `MYSQL_ROOT_PASSWORD` in `.env`.
- `bot` and `worker`, which run as a non-root user with `tini` for signal forwarding, JSON logs with rotation, health checks and `restart: unless-stopped`.
- A worker CPU and memory limit, plus a 60 s stop grace period.

## 13. Production deployment (Linux VPS)

```bash
# 1. Install Docker (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

# 2. Get the code
git clone <your-repo-url> downloader && cd downloader

# 3. Configure
cp .env.example .env
nano .env                          # BOT_TOKEN, limits; keep the file private:
chmod 600 .env

# 4. Start
docker compose up -d --build
docker compose ps

# 5. Update later
git pull && docker compose up -d --build
```

Operational notes:

- **Only run one bot instance per token.** Telegram polling allows a single consumer. Scale **workers**, not the bot.
- Size `WORKER_CONCURRENCY × replicas` to the VPS. Each active task can use one CPU core while transcoding, plus up to about 2 × `MAX_UPLOAD_MB` of disk.
- Keep yt-dlp current, because platforms change often: bump `yt-dlp[default]` in `requirements.txt` and rebuild.
- Queue depth: `docker compose exec redis redis-cli LLEN dl:queue:pending`.
- Inspect a task: `docker compose exec redis redis-cli HGETALL dl:task:<task_id>`.
- User stats: `docker compose exec mysql mysql -u downloader -p downloader_bot -e "SELECT COUNT(*) FROM users"`.
- Back up user data: `docker compose exec mysql mysqldump -u root -p downloader_bot > backup.sql`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| YouTube offers only a few low qualities | Install Node.js/Deno and set `YTDLP_JS_RUNTIMES=node` |
| "requires login / restricted" for Instagram | The post isn't public. The bot intentionally doesn't use accounts |
| "too large for Telegram" | Choose a lower quality, or run a local Bot API server and raise `MAX_UPLOAD_MB` |
| Health check failing | `docker compose logs <service>`; check Redis connectivity and `BOT_TOKEN` |
