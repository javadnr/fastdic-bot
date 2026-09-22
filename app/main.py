import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from app.handlers import bot

logger = logging.getLogger(__name__)
logger.info("Bot starting")

if __name__ == "__main__":
    logger.info("Starting infinity polling...")
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
