import json
import logging
import re
import os
import datetime
import discord
import steamapi
import emoji
from dateutil.parser import parse
from steamapi.errors import UserNotFoundError
from steamapi.user import SteamUser

discord_client = discord.Client()


class User:

    def __init__(self, steam_id, name="", date_added=None, date_banned=None):
        super().__init__()

        self.steamid = int(steam_id)
        self.name = name
        self.date_added = date_added
        self.date_banned = date_banned

    def is_banned(self):
        return self.date_banned is not None

    def to_dict(self):
        date_added = self.date_added
        if date_added is not None:
            date_added = date_added.isoformat()
        date_banned = self.date_banned
        if date_banned is not None:
            date_banned = date_banned.isoformat()

        return {
            "steam_id": self.steamid,
            "name": self.name,
            "date_added": date_added,
            "date_banned": date_banned
        }

    @staticmethod
    def from_dict(d):
        """

        :param d:
        :type d dict
        :return:
        """
        steam_id = d["steam_id"]
        name = None
        if "name" in d:
            name = d["name"]
        date_added = parse(d["date_added"])
        date_banned = None
        if d["date_banned"] is not None and d["date_banned"] != "":
            date_banned = parse(d["date_banned"])
        return User(steam_id=steam_id, name=name, date_added=date_added, date_banned=date_banned)

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

    async def on_server_join(self, server):
        """

        :param server:
        :type server discord.Server
        :return:
        """
        logging.info("Bot joined new server: {} ({})!".format(server.name, server.id))

        self.config[server.id] = self.default_config
        self.save_config()
        self.users[server.id] = {}
        self.save_users(server.id)

    async def on_ready(self):
        logging.info("Connected to discord as {} ({})".format(self.discord.user.name, self.discord.user.id))
        await self.discord.change_presence(game=discord.Game(name="Cheater-Strike: Globally Offensive"))

        logging.info("Creating default server configs...")
        for s in self.discord.servers:
            if s.id not in self.config:
                self.config[s.id] = self.default_config
                self.users[s.id] = {}
                self.save_users(s.id)
            await self.process_unadded_users(s.id)
        self.save_config()
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
                user = self.get_user(message.content, False)
                if isinstance(user, SteamUser):
                    self.add_user(user, message.server.id)
                elif isinstance(user, list):
                    for u in user:
                        self.add_user(u, message.server.id)
                await discord_client.add_reaction(message, emoji.emojize(':thumbsup:', use_aliases=True))
            except UserNotFoundError:
                pass

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
            file = os.path.join(self.users_path, s + ".json")
            try:
                self.users[s] = {}
                with open(file, "r") as f:
                    users = json.loads(f.read())
                    for u in users:
                        user = User.from_dict(u)
                        self.users[s][user.steamid] = user
            except IOError as e:
                logging.warning("Failed to load {}: {}".format(file, e.strerror))
        logging.info("Loaded users!")

    def save_users(self, server=None):
        """
        Saved tracked users to file

        :param server: ID of server to save
        :type server: None, int
        """
        if server is None:
            logging.info("Saving all users...")
            for server in self.config:
                file = os.path.join(self.users_path, server + ".json")
                users = [self.users[server][u].to_dict() for u in self.users[server]]
                with open(file, "w") as f:
                    f.write(json.dumps(users))
        else:
            logging.info("Saving users for {}...".format(server))
            file = os.path.join(self.users_path, server + ".json")
            users = [self.users[server][u].to_dict() for u in self.users[server]]
            with open(file, "w") as f:
                f.write(json.dumps(users))

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
                if await self.check_users(message.server.id, message.channel) == 0:
                    await self.discord.send_message(message.channel,
                                                    "No tracked users have been banned recently :(")
            else:
                logging.info("Checking user: {}".format(command[1]))
                try:
                    user = self.get_user(command[1])
                    if self.is_user_banned(user):
                        await self.discord.send_message(
                            message.channel,
                            "{} was last banned {} days ago!\nView it here: {}".format(
                                user.name,
                                user.days_since_last_ban,
                                user.profile_url
                            )
                        )
                    else:
                        await self.discord.send_message(message.channel,
                                                        "{} has *not* been banned!".format(user.name))
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
        elif command[0] == "list":
            await self.send_list(command, message)
        elif command[0] == "update":
            await self.update_users(message)
        elif command[0] == "add":
            if len(command) != 2:
                await self.discord.send_message(message.channel, "Add requires a user: `!add [user]`")
                return
            try:
                user = self.get_user(message.content)
                self.add_user(user, message.server.id)
                await discord_client.add_reaction(message, emoji.emojize(':thumbsup:', use_aliases=True))
            except UserNotFoundError:
                await discord_client.add_reaction(message, emoji.emojize(':thumbsdown:', use_aliases=True))
                pass

    async def set_command(self, command, message):
        if len(command) != 2:
            await self.discord.send_message(message.channel,
                                            "Missing required argument for !command [command char]")
            return
        if len(command[1]) != 1:
            await self.discord.send_message(message.channel,
                                            "Command character can only be of length 1 (e.g.: !, $, %")
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

    async def send_list(self, command, message):
        """
        Send the list of tracked users

        :param message:
        :return:
        """
        users = ""
        if len(command) == 1:
            for u in self.users[message.server.id]:
                user = self.users[message.server.id][u]
                users = users + "**{}**:\nhttp://steamcommunity.com/profiles/{}: ".format(user.name, user.steamid)
                if user.is_banned():
                    users = users + " Banned!"
                    users = users + "\n"
        elif command[1] == "bans":
            for u in self.users[message.server.id]:
                user = self.users[message.server.id][u]
                if user.is_banned():
                    users = users + "**{}**:\nhttp://steamcommunity.com/profiles/{} \n".format(user.name, user.steamid)
        await self.discord.send_message(message.channel, users)

    async def update_users(self, message):
        """
        Send the ban stats

        :param message:
        :type message: discord.Message
        """
        logging.info("Updated user information for {}...".format(message.server.id))
        percentage = 0.0
        step = 1 / len(self.users[message.server.id])
        for u in self.users[message.server.id]:
            percentage = percentage + step
            user = self.get_user(u)
            print("Checking {:.2%} complete...\r".format(percentage), end="")
            self.users[message.server.id][u].name = user.name
        logging.info("Updated user information for {}!".format(message.server.id))
        self.save_users(message.server.id)


    async def send_stats(self, message):
        """
        Send the ban stats

        :param message:
        :type message: discord.Message
        """
        banned_count = 0
        count = len(self.users[message.server.id])
        if count == 0:
            await self.discord.send_message(
                message.channel,
                "No players are being tracked. Add some players by pasting their profiles into the chat before asking "
                "for statistics! "
            )
            return
        for u in self.users[message.server.id]:
            if self.users[message.server.id][u].is_banned():
                banned_count = banned_count + 1

        await self.discord.send_message(message.channel,
                                        "Tracking {} users.\n{} have been banned... or {:.2%}".format(
                                            count,
                                            banned_count,
                                            banned_count / count
                                        ))

    @staticmethod
    def get_user(user_string, full_check=True):
        """
        Fetch a SteamUser from string

        :param user_string: steam profile ID or url
        :type user_string: str, int
        :param full_check: If full check is enabled, will match for steamid/profile url instead of full url
        :type bool
        :rtype: SteamUser
        :raises UserNotFoundError: if the user is not found
        """
        logging.debug("Fetching user: {}".format(user_string))
        if isinstance(user_string, int):
            user_string = str(user_string)

        if full_check:
            user = None
            if re.match("[0-9]+", user_string):
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
        else:
            users = []
            if re.match('http(|s):\/\/steamcommunity\.com\/id\/([^/\s]*).*', user_string):
                user_match = re.findall('http(|s):\/\/steamcommunity\.com\/id\/([^/\s]*).*', user_string,
                                        re.IGNORECASE)
                for u in user_match:
                    try:
                        user = steamapi.user.SteamUser(userurl=u[1].strip("/"))
                        if user is not None:
                            users.append(user)
                    except UserNotFoundError:
                        pass
            if re.match('http(|s):\/\/steamcommunity\.com\/profiles\/([0-9]*)', user_string):
                user_match = re.findall('http(|s):\/\/steamcommunity\.com\/profiles\/([0-9]*)', user_string,
                                        re.IGNORECASE)
                for u in user_match:
                    try:
                        user = steamapi.user.SteamUser(userid=int(u[1].strip("/")))
                        if user is not None:
                            users.append(user)
                    except UserNotFoundError:
                        pass

            if len(users) == 0:
                raise UserNotFoundError

            return users

    def add_user(self, user, server):
        """
        Add user to the list of tracked users

        :param user: SteamUser
        :param server: ID of server
        :type user: SteamUser
        :type server: int
        """
        user = User(steam_id=user.steamid, name=user.name, date_added=datetime.datetime.now())
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

    async def check_users(self, server=None, channel=None):
        """
        Checks the list of tracked users to see if they've been banned

        :param server: Server ID of server to check, or None to check all servers
        :type server: None, int
        """
        count = 0
        if server is None:
            logging.info("Checking banned users for all servers...")
            percentage = 0.0
            server_step = 1 / len(self.config)
            for s in self.config:
                logging.info(" - Checking server {}".format(s))
                if len(self.users[s]) == 0:
                    percentage = percentage + server_step
                    continue
                step = server_step / len(self.users[s])
                for user in self.users[s]:
                    percentage = percentage + step
                    print("Checking {:.2%} complete...\r".format(percentage), end="")
                    steamuser = self.get_user(user)
                    if self.is_user_banned(steamuser) and not self.users[s][user].is_banned():
                        await self.user_banned(user, steamuser, s, channel)
                        count = count + 1
        else:
            logging.info("Checking banned users for server {}...".format(server))
            if len(self.users[server]) != 0:
                percentage = 0.0
                step = 1 / len(self.users[server])
                for user in self.users[server]:
                    percentage = percentage + step
                    print("Checking {:.2%} complete...\r".format(percentage), end="")
                    steamuser = self.get_user(user)
                    if self.is_user_banned(steamuser) and not self.users[server][user].is_banned():
                        await self.user_banned(user, steamuser, server, channel)
                        count = count + 1
        logging.info("Checked users for bans!")
        if count == 0:
            logging.info("No new users were banned!")
        else:
            self.save_users(server)
            logging.info("{} users have been banned!".format(count))
        return count

    async def user_banned(self, user, steamuser, server, channel=None):
        if channel is None:
            channel = self.discord.get_channel(self.config[server]["channel"])
        logging.info("{} ({}) has been banned!".format(steamuser.name, user))
        user = self.users[server][user]
        user.date_banned = datetime.datetime.now() - datetime.timedelta(days=(0 - steamuser.days_since_last_ban))
        user.name = steamuser.name
        await self.discord.send_message(channel,
                                        "{} was last banned {} days ago!\n{}".format(
                                            steamuser.name,
                                            steamuser.days_since_last_ban,
                                            steamuser.profile_url
                                        ))

    async def process_unadded_users(self, server):
        if self.config[server]["channel"] is None:
            return
        logging.info(" - Checking server {} for missed messages".format(server))
        channel = self.discord.get_channel(self.config[server]["channel"])
        if channel is None:
            return
        async for message in self.discord.logs_from(channel):
            if message is None:
                continue
            if message.author.id == self.discord.user.id:
                continue
            if message.content[0:1] == self.config[server]["command"]:
                continue
            if len(message.reactions) is 0:
                try:
                    user = self.get_user(message.content, False)
                    if isinstance(user, SteamUser):
                        self.add_user(user, server)
                    elif isinstance(user, list):
                        for u in user:
                            self.add_user(u, server)
                    await discord_client.add_reaction(message, emoji.emojize(':thumbsup:', use_aliases=True))
                except UserNotFoundError:
                    pass
                continue
            for r in message.reactions:
                if not r.me:
                    try:
                        user = self.get_user(message.content, False)
                        if isinstance(user, SteamUser):
                            self.add_user(user, server)
                        elif isinstance(user, list):
                            for u in user:
                                self.add_user(u, server)
                        await discord_client.add_reaction(message, emoji.emojize(':thumbsup:', use_aliases=True))
                    except UserNotFoundError:
                        pass
