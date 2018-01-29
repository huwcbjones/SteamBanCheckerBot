import json
import logging
import re
import os
import datetime
import discord
import steamapi
import csv
import emoji
from discord import Channel
from steamapi.errors import UserNotFoundError
from steamapi.user import SteamUser

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
    config_path = os.path.join("config")
    config_file = os.path.join(config_path, "config.json")
    users_path = os.path.join(config_path, "users")

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
            await self.process_unadded_users(s.id)
        await self.check_users()

    async def on_message(self, message):
        if message.server is None:
            return

        if message.author.id == self.discord.user.id:
            return

        if message.content[0:1] == self.config[message.server.id]["command"]:
            await self.process_command(message)
        else:
            try:
                user = self.get_user(message.content)
            except UserNotFoundError:
                return
            await discord_client.add_reaction(message, emoji.emojize(':thumbsup:', use_aliases=True))
            self.add_user(user, message.server.id)

    def load_config(self):
        """
        Load server config
        """
        if not os.path.exists(self.users_path):
            os.makedirs(self.users_path)
        logging.info("Loading config...")

        try:
            with open(self.config_file, "r") as f:
                self.config = json.loads(f.read())
                logging.info("Loaded config!")
        except IOError as e:
            logging.warning("Failed to load {}: {}".format(self.config_file, e.strerror))

    def save_config(self):
        """
        Save server config
        """
        logging.info("Saving config...")
        with open(self.config_file, "w") as f:
            f.write(json.dumps(self.config))
            logging.info("Saved config!")

    def load_users(self):
        """
        Load tracked users from file
        """
        logging.info("Loading users...")
        for s in self.config:
            file = os.path.join(self.users_path, s + ".csv")
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
        """
        Saved tracked users to file
        """
        logging.info("Saving users...")
        if server is None:
            for s in self.config:
                file = os.path.join(self.users_path, s + ".csv")
                with open(file, "w") as f:
                    writer = csv.writer(f)
                    for u in self.users:
                        writer.writerow(u.get_row())
        else:
            file = os.path.join(self.users_path, server + ".csv")
            with open(file, "w") as f:
                writer = csv.writer(f)
                users = self.users[server]
                for u in users:
                    writer.writerow(users[u].get_row())

        logging.info("Saved users!")

    async def process_command(self, message):
        """
        Process a command

        :param message:
        :type message discord.Message
        :return:
        """
        command = message.content[1:].split(" ")

        if command[0] == "check":
            await self.discord.send_typing(message.channel)
            if len(command) == 1:
                if await self.check_users(message.server.id) == 0:
                    await self.discord.send_message(message.channel, "No tracked users have been banned recently :(")
            else:
                logging.info("Checking user: {}".format(command[1]))
                try:
                    user = self.get_user(command[1])
                    if self.is_user_banned(user):
                        await self.discord.send_message(
                            message.channel,
                            "{} was last banned {} days ago! View it here: {}".format(
                                user.name,
                                user.days_since_last_ban,
                                user.profile_url
                            )
                        )
                    else:
                        await self.discord.send_message(message.channel, "{} has *not* been banned!".format(user.name))
                except UserNotFoundError:
                    await self.discord.send_message(message.channel, emoji.emojize("I couldn't find that user :("))
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
        elif command[0] == "stats":
            await self.send_stats(message)

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
        """
        Set the hackusation channel

        :param message:
        :type message: discord.Message
        """
        if len(message.channel_mentions) != 1:
            await self.discord.send_message(message.channel, "Please mention a channel.")
            return
        self.config[message.server.id]["channel"] = message.channel_mentions[0].id
        self.save_config()
        await self.discord.send_message(message.channel,
                                        "Ban channel is now {}".format(message.channel_mentions[0].name))

    async def send_stats(self, message):
        """
        Send the ban stats

        :param message:
        :type message: discord.Message
        """
        banned_count = 0
        count = len(self.users[message.server.id])
        for u in self.users[message.server.id]:
            if self.users[message.server.id][u].is_banned:
                banned_count = banned_count + 1

        await self.discord.send_message(message.channel, "Tracking {} users.\n{} have been banned... or {:.2%}".format(
            count,
            banned_count,
            banned_count / count
        ))

    @staticmethod
    def get_user(user_string):
        """
        Fetch a SteamUser from string

        :param user_string: steam profile ID or url
        :type user_string: str, int
        :rtype: SteamUser
        :raises UserNotFoundError: if the user is not found
        """
        logging.debug("Fetching user: {}".format(user_string))
        if isinstance(user_string, int):
            user_string = str(user_string)
        user = None
        if re.match('http(|s):\/\/steamcommunity\.com\/id\/(.*)', user_string):
            user_url = re.match('http(|s):\/\/steamcommunity\.com\/id\/(.*)', user_string, re.IGNORECASE)
            if user_url is not None:
                user_url = user_url.group(2).strip("/")
                logging.debug("User URL: {}".format(user_url))
                user = steamapi.user.SteamUser(userurl=user_url)
        elif re.match('http(|s):\/\/steamcommunity\.com\/profiles\/([0-9]*)', user_string):
            user_id = re.match('http(|s):\/\/steamcommunity\.com\/profiles\/([0-9]*)', user_string, re.IGNORECASE)
            if user_id is not None:
                user_id = user_id.group(2).strip("/")
                logging.debug("User ID: {}".format(user_id))
                user = steamapi.user.SteamUser(userid=int(user_id))
        elif re.match("[0-9]+", user_string):
            user_id = re.match("([0-9]+)", user_string, re.IGNORECASE)
            if user_id is not None:
                user_id = user_id.group(1)
                logging.debug("User ID: {}".format(user_id))
                user = steamapi.user.SteamUser(userid=int(user_id))
        elif re.match("[a-zA-Z0-9]+", user_string):
            user_url = re.match("([a-zA-Z0-9]+)", user_string, re.IGNORECASE)
            if user_url is not None:
                user_url = user_url.group(1)
                logging.debug("User URL: {}".format(user_url))
                user = steamapi.user.SteamUser(userurl=user_url)
        if user is None:
            raise UserNotFoundError
        return user

    def add_user(self, user, server):
        """
        Add user to the list of tracked users

        :param user: SteamUser
        :param server: ID of server
        :type user: SteamUser
        :type server: int
        """
        user = User(steam_id=user.steamid, date_added=datetime.datetime.now())
        self.users[server][user.steamid] = user
        self.save_users(server)

    def remove_user(self, user, server):
        """
        Remove a user from the checklist

        :param user: SteamID of the user
        :param server: ID of the server
        :type user: int
        :type server: int
        """
        del self.users[server][user]
        self.save_users(server)

    @staticmethod
    def is_user_banned(user):
        """
        :param user: User
        :return: bool
        """
        if not isinstance(user, SteamUser):
            if isinstance(user, User):
                user = user.steamid
            user = steamapi.user.SteamUser(user)
        return user.is_community_banned or user.is_vac_banned or user.is_game_banned

    async def check_users(self, server=None):
        """
        Checks the list of tracked users to see if they've been banned

        :param server: Server ID of server to check, or None to check all servers
        :type server: None, int
        """
        count = 0
        if server is None:
            logging.info("Checking banned users for all servers...")
            for s in self.config:
                for user in self.users[s]:
                    steamuser = self.get_user(user)
                    if self.is_user_banned(steamuser) and not self.users[s][user].is_banned:
                        await self.user_banned(user, steamuser, s)
                        count = count + 1
        else:
            logging.info("Checking banned users for server {}...".format(server))
            for user in self.users[server]:
                steamuser = self.get_user(user)
                if self.is_user_banned(steamuser) and not self.users[server][user].is_banned:
                    await self.user_banned(user, steamuser, server)
                    count = count + 1
        logging.info("Checked users for bans!")
        logging.info("No new users were banned!")
        return count

    async def user_banned(self, user, steamuser, server):
        channel = self.discord.get_channel(self.config[server]["channel"])
        logging.info("{} ({}) has been banned!".format(steamuser.name, user))
        user = self.users[server][user]
        user.is_banned = True
        user.date_banned = datetime.timedelta(days=(0 - steamuser.days_since_last_ban))
        await self.discord.send_message(channel,
                                        "{} was last banned {} days ago! View it here: {}".format(
                                            steamuser.name,
                                            steamuser.days_since_last_ban,
                                            steamuser.profile_url
                                        ))

    async def process_unadded_users(self, server):
        if self.config[server]["channel"] is None:
            return
        channel = self.discord.get_channel(self.config[server]["channel"])
        async for message in self.discord.logs_from(channel):
            if message.author.id is self.discord.user.id:
                continue
            if len(message.reactions) is 0:
                try:
                    user = self.get_user(message.content)
                    await discord_client.add_reaction(message, emoji.emojize(':thumbsup:', use_aliases=True))
                    self.add_user(user, server)
                except UserNotFoundError:
                    pass
                continue
            for r in message.reactions:
                if not r.me:
                    try:
                        user = self.get_user(message.content)
                        await discord_client.add_reaction(message, emoji.emojize(':thumbsup:', use_aliases=True))
                        self.add_user(user, server)
                    except UserNotFoundError:
                        pass
