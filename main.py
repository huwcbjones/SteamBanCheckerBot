import argparse
import logging
import os
from steambot import BanChecker

logging.basicConfig(format='%(asctime)s[%(levelname)8s][%(module)s] %(message)s', datefmt='[%m/%d/%Y %H:%M:%S]')
logger = logging.getLogger(__name__)

parser = argparse.ArgumentParser(description='Steam Ban Checker Bot')
parser.add_argument('-v', '--verbose', dest='verbosity', action='count', help='Increase verbosity.')
args = parser.parse_args()

# Set root logging level
if args.verbosity is not None:
    logger = logging.getLogger()
    if args.verbosity == 1:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.DEBUG)

discord_token = os.getenv('DISCORD_API_TOKEN')
if discord_token is None:
    parser.error("DISCORD_API_TOKEN environmental variable not found. Please check your environmental vars are set.")

steam_token = os.getenv('STEAM_API_TOKEN')
if discord_token is None:
    parser.error("STEAM_API_TOKEN environmental variable not found. Please check your environmental vars are set.")


checker = BanChecker(steamapi_token=steam_token, discord_token=discord_token)


@checker.discord.event
async def on_message(message):
    await checker.on_message(message)


@checker.discord.event
async def on_ready():
    await checker.on_ready()


@checker.discord.event
async def on_server_join(server):
    await checker.on_server_join(server)

checker.run()
