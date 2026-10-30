import asyncio
from types import SimpleNamespace as NS

from pyrogram import errors

from bridge import commands
from bridge.config import Settings
from bridge.store import Store
from bridge.telegram import TelegramSource


def test_parse():
    assert commands.parse("/addbot @SomeBot") == ("addbot", ["SomeBot"])
    assert commands.parse("/AddBot https://t.me/OtherBot, Third") == ("addbot", ["OtherBot", "Third"])
    assert commands.parse("/bots@MyAccount") == ("bots", [])
    assert commands.parse("/removebot") == ("removebot", [])
    assert commands.parse("Avatar 2009") is None
    assert commands.parse("/start file_7") is None


def test_is_allowed():
    allowed = ["12345", "Austin"]
    assert commands.is_allowed(NS(id=12345, username=None), allowed)
    assert commands.is_allowed(NS(id=1, username="austin"), allowed)
    assert not commands.is_allowed(NS(id=1, username="someone"), allowed)
    assert not commands.is_allowed(NS(id=1, username=None), [])
    assert not commands.is_allowed(None, allowed)


def test_bot_list_survives_restart_and_reseed(tmp_path):
    store = Store(str(tmp_path / "db"))
    store.seed_bots(["SomeMovieBot", "OtherBot"])
    assert store.add_bot("NewBot")
    assert not store.add_bot("newbot")
    assert store.remove_bot("otherbot")
    assert not store.remove_bot("NotThere")

    # Restart with the same BOTS: the removed bot stays removed, a new BOTS entry is added.
    store = Store(str(tmp_path / "db"))
    store.seed_bots(["SomeMovieBot", "OtherBot", "LaterBot"])
    assert store.bots() == ["SomeMovieBot", "NewBot", "LaterBot"]

    assert store.add_bot("OtherBot")
    assert "OtherBot" in store.bots()


class FakeClient:
    def __init__(self):
        self.users = {"newbot": NS(username="NewBot", is_bot=True), "person": NS(username="Person", is_bot=False)}

    async def get_users(self, name):
        if name.lower() not in self.users:
            raise errors.UsernameNotOccupied()
        return self.users[name.lower()]


def make(tmp_path, allowed=("111",)):
    store = Store(str(tmp_path / "db"))
    store.seed_bots(["SomeMovieBot"])
    s = Settings(data_dir=str(tmp_path), bots=store.bots(), allowed_users=list(allowed))
    return TelegramSource(s, client=FakeClient(), store=store), store


def msg(text, user_id, outgoing=False, chat_id=None):
    replies = []

    async def reply_text(text, quote=False):
        replies.append(text)

    return NS(
        text=text,
        outgoing=outgoing,
        from_user=NS(id=user_id, username=None),
        chat=NS(id=chat_id or user_id),
        reply_text=reply_text,
        replies=replies,
    )


def send(src, m):
    asyncio.run(src._on_command(None, m))
    return m.replies


def test_allowed_user_adds_and_removes_bots(tmp_path):
    src, store = make(tmp_path)
    src._cache["avatar"] = (0, [])

    assert send(src, msg("/addbot @newbot", 111)) == ["Added @NewBot"]
    assert src.s.bots == ["SomeMovieBot", "NewBot"]
    assert src._cache == {}

    assert send(src, msg("/addbot person missing", 111)) == ["@person isn't a bot\nCouldn't find @missing on Telegram"]
    assert send(src, msg("/removebot SomeMovieBot", 111)) == ["Removed @SomeMovieBot"]
    assert send(src, msg("/bots", 111)) == ["Searching:\n@NewBot"]
    assert src.s.bots == store.bots() == ["NewBot"]


def test_saved_messages_always_allowed(tmp_path):
    src, _ = make(tmp_path, allowed=())
    assert send(src, msg("/bots", 999, outgoing=True)) == ["Searching:\n@SomeMovieBot"]


def test_others_are_ignored(tmp_path):
    src, store = make(tmp_path)
    assert send(src, msg("/addbot newbot", 222)) == []
    # The account's own message in someone else's chat isn't a command for us.
    assert send(src, msg("/removebot SomeMovieBot", 999, outgoing=True, chat_id=5)) == []
    assert store.bots() == ["SomeMovieBot"]
