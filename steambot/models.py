from datetime import datetime, timedelta
from typing import Optional, Union

import humanfriendly
from sqlalchemy import Column, Integer, String, DateTime, Table, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

_join_table = Table(
    "user_guild",
    Base.metadata,
    Column("guild_id", Integer, ForeignKey("guild.id")),
    Column("user_id", Integer, ForeignKey("user.id")),
)


class Guild(Base):

    __tablename__ = "guild"

    id = Column(Integer, primary_key=True)
    command = Column(String(length=1), nullable=False, default="!")
    channel = Column(Integer)
    users = relationship("User", secondary=_join_table)


class User(Base):

    __tablename__ = "user"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    date_added = Column(DateTime, nullable=False, default=datetime.utcnow)
    date_banned = Column(DateTime)
    servers = relationship("Guild", secondary=_join_table)

    def time_since_last_ban(
        self, as_str: bool = True
    ) -> Optional[Union[timedelta, str]]:
        if self.date_banned is None:
            return None
        time_since_last_ban = datetime.utcnow() - self.date_banned
        if not as_str:
            return time_since_last_ban
        return humanfriendly.format_timespan(
            time_since_last_ban.total_seconds(), max_units=1
        )
