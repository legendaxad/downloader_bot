from loader import create_application
from utils.logger import logger


def main() -> None:
    application = create_application()

    logger.info("Bot is starting...")

    application.run_polling()


if __name__ == "__main__":
    main()