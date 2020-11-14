import logging
import os

from steambot import aioutils
from steambot.checker import BanChecker
from steambot.log import setup_logging

LOGGER = logging.getLogger(__name__)


def main():
    setup_logging(os.environ.get("LOGGING_CONFIG", "/config/logging.json"))

    if not (discord_token := os.getenv("DISCORD_API_TOKEN")):
        LOGGER.critical("DISCORD_API_TOKEN envvar not found.")
        return

    if not (steam_token := os.getenv("STEAM_API_TOKEN")):
        LOGGER.critical("STEAM_API_TOKEN envvar not found.")
        return
    database = os.environ.get("DATABASE", "/data/steam_ban_checker.db")
    if os.path.isdir(database):
        database = os.path.join(database, "steam_ban_checker.db")
    aioutils.install()
    checker = BanChecker(database, steam_token, discord_token)
    checker.run()


if __name__ == "__main__":
    main()
