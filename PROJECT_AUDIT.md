# Project Audit — Telegram Social Media Downloader Bot

Audit date: 2026-11-18 · Baseline commit: `a75eccd`

## 1. Existing architecture (as found)

```
bot.py                 entry point → loader.create_application().run_polling()
loader.py              builds PTB Application, registers /start, /help, text, callback handlers
config.py              loads BOT_TOKEN (raises at import time if missing), REDIS_URL
handlers/commands.py   /start, /help (minimal text)
handlers/messages.py   URL → validate → yt-dlp metadata (BLOCKING, in event loop) → quality keyboard
handlers/callbacks.py  quality button → `from worker import add_job` (DOES NOT EXIST)
keyboards/buttons.py   quality keyboard, callback_data "quality:<height>"
services/video.py      VideoService.get_metadata (yt-dlp, no error mapping)
services/downloader.py VideoDownloader.download (yt-dlp, H.264-preferring format string,
                       progress hook writing to Redis via run_coroutine_threadsafe)
services/media.py      MediaService: ffprobe inspect, remux, transcode (blocking subprocess)
services/queue.py      DownloadQueue: RPUSH/BLPOP JSON jobs, job:<id> hash (status, progress)
services/redis.py      module-level async Redis client
services/telegram.py   TelegramService.send_video → TelegramUploadError
utils/                 exceptions, logger (file + console), files (delete helpers), validators
worker.py              BLPOP loop: download → "processing" placeholder → "completed"
tests/                 5 ad-hoc scripts (network/Redis dependent, not pytest tests)
database.py, models/   empty
```

## 2. Implemented functionality (verified)

| Area | State |
|---|---|
| URL syntax + domain check | Partial — YouTube/Instagram/TikTok only, no Twitter/X, bogus `youtube.be` entry |
| Metadata extraction | Works, but blocks the asyncio event loop and has no error mapping |
| Quality keyboard | Works for one URL per user (single `user_data` slot) |
| yt-dlp download w/ merge to MP4 | Works (logs show successful downloads) |
| ffprobe/ffmpeg remux/transcode | Implemented but **not wired into the worker** |
| Telegram upload service | Implemented but **never called by the worker** |
| Redis queue | Enqueue/dequeue works; not reliable (BLPOP loses jobs on crash) |
| Logging | Plain text to console + `logs/bot.log` |

## 3. Missing functionality

- Bot → queue integration (callback imports a non-existent function).
- Worker does not process media, upload to Telegram, notify users, or clean up files.
- No progress messages, no cancellation, no retries, no stuck-task recovery.
- No `/about`, `/cancel`; `/start` / `/help` lack platform info and instructions.
- No thumbnail in the selection message; no thumbnail for uploaded video.
- No Telegram file-size limit handling, no duration limits, no per-user limits.
- No concurrency limits for metadata extraction or downloads.
- No real tests, no Docker, no README, no `.env.example`.

## 4. Critical bugs

1. **Bot cannot start**: `handlers/callbacks.py` → `from worker import add_job` → `ImportError`.
2. **Worker never delivers videos**: processing/upload are placeholders; jobs are marked `completed` with nothing sent.
3. **Download progress never reported**: worker calls `download()` without `job_id/loop`.
4. **Downloaded files are never deleted** (3 files currently in `downloads/`).
5. **Blocking yt-dlp call inside an async handler** freezes the whole bot for every user during metadata extraction.
6. **Lost jobs**: `BLPOP` removes the job before processing; a worker crash loses it forever.
7. `tests/test_media.py` calls a non-existent method `convert_to_telegram_mp4`; `pytest` is not installed; tests require network/Redis.
8. `requirements.txt` is missing `redis`.
9. `config.py` raises at import time without `BOT_TOKEN`, which makes every module (incl. downloader) untestable in isolation.
10. Second URL from the same user overwrites the first → pressing an old keyboard downloads the wrong video.
11. `services/downloader.py` imports the Redis queue → the downloader is not Telegram/Redis-independent.

## 5. Security findings

- `.env` is correctly git-ignored; no bot token found in git history or logs (verified with a token-pattern search).
- `logs/bot.log` (contains chat IDs and URLs) and `dump.rdb` are **committed** to git.
- No SSRF protection: yt-dlp's generic extractor would fetch any URL on an allowed host suffix; no port/userinfo checks.
- `httpx` logs request URLs (which contain the bot token) at INFO if root logging is enabled — must be silenced.
- No limits → a single user could exhaust disk/CPU/bandwidth (no max file size, duration, or per-user active tasks).
- Subprocess calls already use argument lists (good) but have no timeouts.

## 6. Technical debt

- Responsibility mismatch: `services/video.py` is metadata, `services/media.py` is FFmpeg; spec wants `video.py` = FFmpeg.
- Module-level singletons (Redis client, services) make testing hard.
- `print()` used for errors; exceptions lack user-facing messages / retryable flags.
- Status strings are ad hoc; no task model.
- Empty `database.py`.

## 7. Recommended implementation order (followed)

1. Config/logging/exceptions/task model foundation; fix startup crash.
2. Telegram-independent downloader (metadata + download) with error mapping, SSRF-safe extractor allowlist.
3. Async FFmpeg processor (probe → copy/remux/audio-only/full transcode, progress, thumbnail, cancel).
4. Reliable Redis queue (BLMOVE processing list, heartbeats, delayed retries, stale recovery, dedupe, sessions).
5. Worker runtime with concurrency + graceful shutdown; task pipeline with lifecycle, progress, cancel, retries.
6. Telegram UX: /start /help /about /cancel, selection sessions, cancel buttons, error handler.
7. Tests (pytest, fakeredis, mocked yt-dlp, real ffmpeg on synthetic clips), Docker, docs.

---

## 8. Progress log

| Phase | Result |
|---|---|
| 1 Audit | This document. Startup crash, missing pipeline, lost-job risk, blocking handler and committed artefacts confirmed. |
| 2 Critical fixes | Lazy typed `config.Settings`; exception hierarchy with `user_message`/`retryable`; `Task` model and lifecycle enum; logging with JSON/text, context fields and token redaction (httpx silenced); `logs/bot.log` and `dump.rdb` untracked; empty `database.py` removed; `redis` added to requirements. |
| 3 Downloader + FFmpeg | `services/downloader.py` handles metadata and download with no Telegram/Redis imports. It uses `bv*+ba/b` with a `height:N, vcodec:h264, acodec:aac` sort, maps errors, disables the generic extractor (SSRF), uses fixed filenames, and supports cancel and size abort from the progress hook. `services/video.py` (absorbed `media.py`) is async ffprobe/ffmpeg with remux / audio-only / full transcode, `yuv420p`, even dimensions, `+faststart`, progress, cancel, timeout and thumbnails. Verified live: YouTube → H.264/AAC → remux only. |
| 4 Telegram UX | `/start` `/help` `/about` `/cancel`; thumbnail preview with size-annotated quality buttons in Redis-backed sessions (no more single `user_data` slot); expired, foreign, invalid and repeated presses handled; cancel buttons; global error handler; `concurrent_updates`; metadata in a bounded thread pool. |
| 5 Queue + workers | BLMOVE pending→processing, per-task heartbeats, stale recovery, delayed-retry zset, idempotent submit, dedupe, per-user limits and rate limits; worker runtime with concurrency slots, maintenance loop, liveness heartbeat, graceful drain and re-queue. |
| 6 Lifecycle | All 9 states used; throttled progress edits (stage changes immediate, % in 5 % buckets ≥3 s apart); bounded exponential retries for transient errors only; `RetryAfter` honoured; user-safe error texts; technical detail logged and stored in `error_detail`. |
| 7 Security/resources | URL allowlist (host boundary, scheme, port, no userinfo, no playlists/channels); extractor allowlist; argv-only subprocesses with timeouts; per-task temp dirs with traversal guard, always removed, plus a stale sweep; upload-size, duration, concurrency, per-user and rate limits; Redis failures don't crash the worker. |
| 8 Tests/Docs/Docker | 129 offline tests (fakeredis, fake yt-dlp, real ffmpeg); live integration run with real Redis + yt-dlp + ffmpeg passed; Dockerfile, docker-compose (Redis AOF, health checks, restart, limits), `.env.example`, README. |

## 9. Remaining limitations

- A Telegram upload that times out *after* Telegram accepted it can be retried, so the user may get a duplicate video (rare).
- Size estimates are approximate; some platforms (Twitter/X, Instagram) provide none. Those files are only checked during and after download.
- The public Bot API caps uploads at 50 MB. Larger files need a self-hosted Bot API server (`TELEGRAM_API_BASE_URL` + `MAX_UPLOAD_MB`).
- Instagram increasingly requires login even for public posts. Those requests fail as "restricted" by design.
- The Docker image was not built in this environment (Docker is not installed locally).
- No persistent task history beyond Redis TTL and logs (by design; see README).
