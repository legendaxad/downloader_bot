import os

from dotenv import load_dotenv


load_dotenv()


BOT_TOKEN = os.getenv("BOT_TOKEN")


if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in .env")

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)