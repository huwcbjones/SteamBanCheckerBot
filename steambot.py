import json
import logging
import re

import datetime
import discord
import steamapi
import csv
import emoji
from steamapi.errors import UserNotFoundError

discord_client = discord.Client()


class User:
    def __init__(self, steam_id, date_added=None, date_banned=None):
        self.steamid = int(steam_id)
        self.date_added = date_added
        self.date_banned = date_banned
        self.is_banned = date_banned is not None

    def get_row(self):
        return [self.steamid, self.date_added, self.date_banned, self.is_banned]

    def __hash__(self):
        return self.steamid


class BanChecker:

    def __init__(self, steamapi_token, discord_token):
        self.discord_token = discord_token

        self.default_config = {
            "command": "!",
            "channel": None
        }

        self.users = {}
        self.config = {}

        # Load Config
        self.load_config()

        # Load Users
        self.load_users()

        # Login to APIs
        self.discord = discord_client
        logging.info("Logging into Steam API...")
        self.steamapi = steamapi.core.APIConnection(api_key=steamapi_token, validate_key=True)
        logging.info("Logged into Steam API!")

    def run(self):
        logging.info("Starting bot loop...")
        self.discord.run(self.discord_token)

    async def on_ready(self):
        logging.info("Connected to discord as {} ({})".format(self.discord.user.name, self.discord.user.id))
        await self.discord.change_presence(game=discord.Game(name="Cheater-Strike: Globally Offensive"))
        for s in self.discord.servers:
            if s.id not in self.config:
                self.config[s.id] = self.default_config
        self.check_users()

    async def on_message(self, message):
        if message.server is None:
            return

        if message.author.id == self.discord.user.id:
            return

        if message.content[0:1] == self.config[message.server.id]["command"]:
            await self.process_command(message)
        else:
            user = self.get_user(message.content)
            if user is None:
                return
            await discord_client.add_reaction(message, emoji.emojize(':thumbsup:', use_aliases=True))
            self.add_user(user, message.server.id)

    def load_config(self):
        logging.info("Loading config...")
        try:
            with open("config.json", "r") as f:
                self.config = json.loads(f.read())
                logging.info("Loaded config!")
        except IOError as e:
            logging.warning("Failed to load config.json: {}".format(e.strerror))

    def save_config(self):
        logging.info("Saving config...")
        with open("config.json", "w") as f:
            f.write(json.dumps(self.config))
            logging.info("Saved config!")

    def load_users(self):
        logging.info("Loading users...")
        for s in self.config:
            file = s + ".csv"
            try:
                self.users[s] = {}
                with open(file, "r") as f:
                    reader = csv.reader(f)
                    for row in reader:
                        user = User(steam_id=row[0], date_added=row[1], date_banned=row[2])
                        self.users[s][user.steamid] = user
            except IOError as e:
                logging.warning("Failed to load {}: {}".format(file, e.strerror))
        logging.info("Loaded users!")

    def save_users(self, server=None):
        logging.info("Saving users...")
        if server is None:
            for s in self.config:
                file = s + ".csv"
                with open(file, "w") as f:
                    writer = csv.writer(f)
                    for u in self.users:
                        writer.writerow(u.get_row())
        else:
            file = server + ".csv"
            with open(file, "w") as f:
                writer = csv.writer(f)
                users = self.users[server]
                for u in users:
                    writer.writerow(users[u].get_row())

        logging.info("Saved users!")

    async def process_command(self, message):
        command = message.content[1:].split(" ")

        if command[0] == "check":
            if len(command) == 1:
                self.check_users()
            else:
                logging.info("Checking user: {}".format(command[1]))
                user = self.get_user(command[1])
                if user is not None:
                    if self.check_user(user):
                        await self.discord.send_message(message.channel, "{} has been banned!".format(user.name))
                    else:
                        await self.discord.send_message(message.channel, "{} has *not* been banned!".format(user.name))
        elif command[0] == "remove":
            user = self.get_user(command[1])
            if user is None:
                await self.discord.send_message(message.channel, "Could not find user!")
            else:
                self.remove_user(user.steamid, message.server.id)
                await self.discord.send_message(message.channel, "Removed {} from the ban list.".format(user.name))
        elif command[0] == "channel":
            await self.set_channel(message)
        elif command[0] == "command":
            await self.set_command(command, message)

    async def set_command(self, command, message):
        if len(command) != 2:
            await self.discord.send_message(message.channel, "Missing required argument for !command [command char]")
            return
        if len(command[1]) != 1:
            await self.discord.send_message(message.channel, "Command character can only be of length 1 (e.g.: !, $, %")
            return
        self.config[message.server.id]["command"] = command[1]
        await self.discord.send_message(
            message.channel,
            "Command character is now {}".format(self.config[message.server.id]["command"])
        )
        self.save_config()

    async def set_channel(self, message):
        if len(message.channel_mentions) != 1:
            await self.discord.send_message(message.channel, "Please mention a channel.")
            return
        self.config[message.server.id]["channel"] = message.channel_mentions[0].id
        self.save_config()
        await self.discord.send_message(message.channel,
                                        "Ban channel is now {}".format(message.channel_mentions[0].name))

    @staticmethod
    def get_user(user_string):
        logging.debug("Fetching user: {}".format(user_string))
        try:
            user = None
            if re.match('http[|s]://steamcommunity.com/id/.*', user_string):
                user_url = re.search("http[|s]://steamcommunity.com/id/(.*)", user_string, re.IGNORECASE)
                if user_url is not None:
                    user = steamapi.user.SteamUser(userurl=user_url.group(1))
            elif re.match('http[|s]://steamcommunity.com/profiles/[0-9]+', user_string):
                user_id = re.search("http[|s]://steamcommunity.com/profiles/([0-9]+)", user_string, re.IGNORECASE)
                if user_id is not None:
                    user = steamapi.user.SteamUser(userid=int(user_id.group(1)))
            elif re.match("[0-9]+", user_string):
                user_id = re.search("([0-9]+)", user_string, re.IGNORECASE)
                if user_id is not None:
                    user = steamapi.user.SteamUser(userid=int(user_id.group(1)))
            elif re.match("[a-zA-Z0-9]+", user_string):
                user_id = re.search("([a-zA-Z0-9]+)", user_string, re.IGNORECASE)
                if user_id is not None:
                    user = steamapi.user.SteamUser(userurl=user_id.group(1))
            return user
        except UserNotFoundError:
            return None

    def add_user(self, user, server):
        user = User(steam_id=user.steamid, date_added=datetime.datetime.now())
        self.users[server][user.steamid] = user
        self.save_users(server)

    def remove_user(self, user, server):
        del self.users[server][user]
        self.save_users(server)
        pass

    @staticmethod
    def check_user(user):
        if user is User:
            user = user.steamid
        user = steamapi.user.SteamUser(user)
        return user.is_community_banned or user.is_vac_banned

    def check_users(self, server=None):
        if server is None:
            logging.info("Checking banned users for all servers...")
            for s in self.config:
                for user in self.users[s]:
                    if self.check_user(user):
                        self.user_banned(user, s)
        else:
            logging.info("Checking banned users for server {}...", server)
            for user in self.users[server]:
                if self.check_user(user):
                    self.user_banned(user, server)
        logging.info("Checked users for bans!")

    def user_banned(self, user, kwargs):
        self.discord.send_message()
