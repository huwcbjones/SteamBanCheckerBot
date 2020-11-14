import re
from typing import Sequence

STEAM_ID_REGEX = re.compile(r"([1-9][0-9]*)")
USERNAME_REGEX = re.compile(r"([a-zA-Z[0-9]+)")

COMMUNITY_URL_REGEX = re.compile(r"http(?:|s)://steamcommunity\.com/id/([^/]*)")
PROFILE_URL_REGEX = re.compile(r"http(?:|s)://steamcommunity\.com/profiles/([1-9][0-9]*)[/]*")
CSGO_STATS_REGEX = re.compile(r"http(?:|s)://csgostats\.gg/player/([1-9][0-9]*)[/#]*")


def find_user_ids_in_string(string: str, full_check: bool = True) -> Sequence[str]:
    """Find user IDs in """
    if full_check:
        regexes = [COMMUNITY_URL_REGEX, PROFILE_URL_REGEX, CSGO_STATS_REGEX]
    else:
        regexes = [STEAM_ID_REGEX, USERNAME_REGEX]

    user_ids = []
    for regex in regexes:
        user_ids += regex.findall(string)
    return user_ids
