"""Chat commands for changing which bots are searched.

Send them from a user in ALLOWED_USERS to the command bot (COMMAND_BOT_TOKEN)
or to the bridge's Telegram account, or from that account in Saved Messages:

    /addbot SomeMovieBot     start searching a bot
    /removebot SomeMovieBot  stop searching it
    /bots                    list the bots being searched
"""

import logging
import re
from collections.abc import Awaitable, Callable

from .store import Store

log = logging.getLogger(__name__)

HELP = (
    "/addbot <username> - search this bot too\n"
    "/removebot <username> - stop searching this bot\n"
    "/bots - list the bots being searched"
)

MENU = [
    ("addbot", "Search this bot too: /addbot @SomeMovieBot"),
    ("removebot", "Stop searching a bot: /removebot @SomeMovieBot"),
    ("bots", "List the bots being searched"),
]

# Looks a username up on Telegram. Returns its exact spelling, or an error to show the user.
CheckBot = Callable[[str], Awaitable[tuple[str | None, str | None]]]


def parse(text: str) -> tuple[str, list[str]] | None:
    """'/addbot@x @SomeBot' -> ('addbot', ['SomeBot']). None if it isn't one of our commands."""
    m = re.match(r"^/(addbot|removebot|delbot|bots|help|start)(?:@\w+)?(?:\s+(.*))?$", text.strip(), re.S | re.I)
    # "/start file_7" is how we ask movie bots for files, not a command for us.
    if not m or (m.group(1).lower() == "start" and m.group(2)):
        return None
    args = [username(a) for a in re.split(r"[\s,]+", m.group(2) or "") if username(a)]
    return m.group(1).lower(), args


def username(arg: str) -> str:
    """Accepts '@Bot', 'Bot' or a t.me link."""
    arg = re.sub(r"^(https?://)?(t\.me|telegram\.me)/", "", arg.strip(), flags=re.I)
    return arg.split("?")[0].strip("/").lstrip("@")


def is_allowed(user, allowed: list[str]) -> bool:
    """`allowed` holds numeric user ids and/or usernames."""
    if user is None:
        return False
    names = {a.lower() for a in allowed}
    return str(user.id) in names or (user.username or "").lower() in names


async def run(cmd: str, args: list[str], store: Store, check_bot: CheckBot) -> str:
    if cmd == "bots":
        bots = store.bots()
        return "Searching:\n" + "\n".join(f"@{b}" for b in bots) if bots else "No bots yet. Add one with /addbot"
    if cmd in ("help", "start") or not args:
        return HELP
    lines = []
    for arg in args:
        if cmd == "addbot":
            name, err = await check_bot(arg)
            if err:
                lines.append(err)
            elif store.add_bot(name):
                log.info("Bot added by command: %s", name)
                lines.append(f"Added @{name}")
            else:
                lines.append(f"@{name} is already in the list")
        elif store.remove_bot(arg):
            log.info("Bot removed by command: %s", arg)
            lines.append(f"Removed @{arg}")
        else:
            lines.append(f"@{arg} isn't in the list")
    return "\n".join(lines)
