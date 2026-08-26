from loader import create_application


def main() -> None:
    application = create_application()

    print("Bot is starting...")

    application.run_polling()


if __name__ == "__main__":
    main()