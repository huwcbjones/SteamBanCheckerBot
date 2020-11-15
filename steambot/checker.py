import asyncio
import contextlib
import logging
import os
from collections import defaultdict
from datetime import timedelta, datetime
from math import ceil
from typing import Sequence, Mapping, Tuple, Optional, Union

import discord
import sqlalchemy as sa
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker
from steamapi.core import APIConnection
from steamapi.user import SteamUser, UserNotFoundError

from steambot import search
from steambot.aioutils import run_in_thread
from steambot.models import Base, Guild, User
from steambot.search import STEAM_ID_REGEX

LOGGER = logging.getLogger(__name__)

THUMBS_UP_EMOJI = "👍"
THUMBS_DOWN_EMOJI = "👎"


class BanChecker(discord.Client):

    CHECK_INTERVAL = 24
    """Number of times per day to check for bans"""

    def __init__(self, db_path: str, steam_token: str, discord_token: str) -> None:
        super().__init__()
        self._steam_token = steam_token
        self._discord_token = discord_token
        if create_schema := not os.path.exists(db_path):
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        LOGGER.info('Database="%s"', db_path)
        engine = sa.create_engine(f"sqlite:///{db_path}")
        if create_schema:
            LOGGER.info('Message="Creating database schema"')
            Base.metadata.create_all(engine)
        self._db = sessionmaker(bind=engine)
        self.steam_api = APIConnection(api_key=steam_token, validate_key=True)
        self._scheduler: AsyncIOScheduler = AsyncIOScheduler()

        self._command_map = {
            "list": self.send_stats
        }

    @contextlib.contextmanager
    def _session(self):
        session = self._db()
        try:
            yield session
        finally:
            session.close()

    def run(self):
        self._scheduler.add_job(
            self.check_bans_task, IntervalTrigger(seconds=86400 // self.CHECK_INTERVAL)
        )
        self._scheduler.start()
        super().run(self._discord_token)

    async def on_ready(self):
        LOGGER.info(
            'Message="Connected to discord" UserID="%s" Name="%s"',
            self.user.id,
            self.user.name,
        )
        await self.change_presence(
            activity=discord.Activity(
                name="CSGO for cheaters",
                type=discord.ActivityType.watching,
            )
        )
        user_ids = await self.find_missed_user_ids()
        await self.process_user_ids(user_ids)

    async def on_guild_join(self, guild: discord.Guild):
        LOGGER.info('Message="Joined guild" ID="%s" Name="%s"', guild.id, guild.name)
        db_guild = Guild(id=guild.id)
        with self._session() as session:
            session.add(db_guild)
            session.commit()

    async def on_message(self, message: discord.Message):
        with self._session() as session:
            db_guild = (
                session.query(Guild).filter(Guild.id == message.guild.id).one_or_none()
            )
        if db_guild.channel is not None and db_guild.channel != message.channel.id:
            LOGGER.debug(
                'Message="Ignoring message, not monitoring channel" Channel="%s" MonitoredChannel="%s"',
                message.channel.id,
                db_guild.channel,
            )
            return
        if message.content.startswith(db_guild.command):
            await self.dispatch_command(message)
            return

        user_ids = await self.get_user_ids_from_message(message, db_guild.command)
        LOGGER.debug('Message="Found user IDs" Count="%s"', len(user_ids))
        await self.process_user_ids({u: [message.guild.id] for u in user_ids})

    async def dispatch_command(self, message: discord.Message):
        if message.content[1:].startswith("stats"):
            await self.send_stats(message.guild.id)

    async def find_missed_user_ids(
        self, guild: discord.Guild = None
    ) -> Mapping[str, Sequence[int]]:
        """Find all the missed user IDs"""
        if guild is None:
            results = defaultdict(list)
            data = await asyncio.gather(
                *[self.find_missed_user_ids(s) for s in self.guilds]
            )
            for guild_data in data:
                for user_id, guild_ids in guild_data.items():
                    results[user_id].extend(guild_ids)
            return results
        with self._session() as session:
            db_guild = session.query(Guild).filter(Guild.id == guild.id).one_or_none()
            if db_guild is None:
                return {}
        channel = guild.get_channel(db_guild.channel)
        LOGGER.info(
            'Message="Checking guild for missed messages" GuildID="%s" Guild="%s" ChannelID="%s" Channel="#%s"',
            guild.id,
            guild.name,
            channel.id,
            channel.name,
        )
        results = await asyncio.gather(
            *[
                self.get_user_ids_from_message(m, db_guild.command)
                async for m in channel.history()
            ]
        )
        user_ids = {user_id: [guild.id] for user_ids in results for user_id in user_ids}
        LOGGER.debug('Message="Found missed user IDs" Count="%s"', len(user_ids))
        return user_ids

    async def get_user_ids_from_message(
        self, message: discord.Message, command: str
    ) -> Sequence[str]:
        if message is None:
            return []
        if message.author.id == self.user.id:
            return []
        content = message.content
        if not content:
            return []
        if content.startswith(command):
            return []
        for r in message.reactions:
            if r.me:
                return []
        user_ids = search.find_user_ids_in_string(content)
        await message.add_reaction(THUMBS_UP_EMOJI if user_ids else THUMBS_DOWN_EMOJI)
        return user_ids

    async def process_user_ids(self, user_ids: Mapping[str, Sequence[int]]):
        results = await asyncio.gather(
            *[self.process_user_id(u, g_ids) for u, g_ids in user_ids.items()],
            return_exceptions=True,
        )
        errors = [r for r in results if r is not None]
        LOGGER.info(
            'Message="Processed user IDs" Count="%s" Errors="%s"',
            len(results),
            len(errors),
        )

    @staticmethod
    def _get_steam_user(
        user_id: Union[int, str]
    ) -> Tuple[int, str, Optional[datetime]]:
        try:
            if isinstance(user_id, int) or STEAM_ID_REGEX.fullmatch(user_id):
                steam_user = SteamUser(userid=int(user_id))
            else:
                steam_user = SteamUser(userurl=user_id)
            ban_date = None
            if steam_user.is_game_banned or steam_user.is_vac_banned:
                ban_date = datetime.utcnow() - timedelta(
                    days=steam_user.days_since_last_ban
                )
            return steam_user.steamid, steam_user.name, ban_date
        except UserNotFoundError:
            LOGGER.error('Message="Failed to find user" UserID="%s"', user_id)
            raise

    async def process_user_id(self, user_id: str, guild_ids: Sequence[int]):
        user_id, user_name, ban_date = await run_in_thread(
            self._get_steam_user, user_id
        )

        with self._session() as session:
            db_user = session.query(User).filter(User.id == user_id).one_or_none()
            if db_user is None:
                db_user = User(id=user_id, name=user_name)
            db_user.date_banned = ban_date

            guilds = session.query(Guild).filter(Guild.id.in_(guild_ids)).all()
            db_user.servers.extend(guilds)
            session.add(db_user)
            session.commit()

            LOGGER.info('Message="Processed user" User="%s"', db_user.id)
            asyncio.create_task(self.check_ban(db_user.id, guild_ids))

    @staticmethod
    def ban_status_changed(source: Optional[datetime], comparison: Optional[datetime]) -> bool:
        if comparison is None:
            return False
        if source is None:
            return True
        return comparison.date() > source.date()

    async def check_ban(self, user_id: int, guild_ids: Sequence[int] = None):
        LOGGER.debug('Message="Checking user for bans" UserID="%s"', user_id)
        with self._session() as session:
            user = session.query(User).filter(User.id == user_id).one_or_none()
            _, _, ban_date = await run_in_thread(self._get_steam_user, user.id)

            if ban_date is None:
                LOGGER.info('Message="User has not been banned" UserID="%s"', user_id)
                return

            if not self.ban_status_changed(user.date_banned, ban_date):
                LOGGER.info(
                    'Message="User ban status has not changed" UserID="%s" BanDate="%s"',
                    user_id,
                    user.date_banned,
                )
                return

            LOGGER.warning(
                'Message="User has been banned" UserID="%s" BanDate="%s"',
                user_id,
                user.date_banned,
            )

            user.date_banned = ban_date.replace(hour=0, minute=0, second=0, microsecond=0)
            session.add(user)
            session.commit()

            LOGGER.warning(
                'Message="User has been banned" UserID="%s" BanDate="%s"',
                user_id,
                user.date_banned,
            )

            channels: Sequence[discord.TextChannel] = await asyncio.gather(
                *[
                    self.fetch_channel(g.channel)
                    for g in user.servers
                    if not guild_ids or guild_ids and g.id in guild_ids
                ]
            )
            message = discord.Embed(
                title=f"{user.name} was last banned {user.time_since_last_ban()} ago",
                color=discord.Color.red(),
            )
            message.description = f"""\
[View their profile on Steam](https://steamcommunity.com/profiles/{user.id})
[View their profile on CSGO Stats](https://csgostats.gg/player/{user.id})
"""
        await asyncio.gather(*[c.send(embed=message) for c in channels])

    async def check_bans_task(self):
        """check bans task"""
        hour = datetime.utcnow().hour
        with self._session() as session:
            total_users = session.query(User.id).count()
            batch_size = ceil(total_users / self.CHECK_INTERVAL)
            users = (
                session.query(User.id).limit(batch_size).offset(batch_size * hour).all()
            )
            await asyncio.gather(*[self.check_ban(u[0]) for u in users])

    async def send_stats(self, guild_id: int):
        with self._session() as session:
            total_count = session.query(func.count(User.id)).scalar()
            banned_count = (
                session.query(func.count(User.id))
                .filter(User.date_banned.isnot(None))
                .scalar()
            )
            guild = session.query(Guild).filter(Guild.id == guild_id).one_or_none()
            channel: discord.TextChannel = await self.fetch_channel(guild.channel)
            if total_count == 0:
                message = "No players are being tracked."
            else:
                message = f"{banned_count} of {total_count} players have been banned (or {banned_count/total_count:.2%})"
            await channel.send(message)
