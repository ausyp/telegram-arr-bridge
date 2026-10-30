"""Replays the flow from a real auto-filter bot against a fake Telegram client:
search -> list of file buttons -> press one -> "join 2 channels" gate ->
join + Try Again -> file arrives -> download."""

import asyncio
import os
from types import SimpleNamespace as NS

from pyrogram import errors
from pyrogram.raw import types as raw

from bridge.config import Settings
from bridge.telegram import TelegramSource

BOT = "SophiaBot"


def btn(text, cb=None, url=None):
    return NS(text=text, callback_data=cb, url=url)


class FakeClient:
    def __init__(self):
        self.msgs = []  # bot chat
        self.joined = []
        self._id = 100
        self.file_name = "Aaru.Aanugal.2026.Malayalam.HQ.HDRip.720p.x264.mkv"

    def _add(self, outgoing, text="", buttons=None, document=None):
        self._id += 1
        markup = NS(inline_keyboard=[[b] for b in buttons]) if buttons else None
        m = NS(
            id=self._id,
            outgoing=outgoing,
            text=text,
            caption=None,
            reply_markup=markup,
            document=document,
            video=None,
            chat=NS(id=1),
        )
        self.msgs.append(m)
        return m

    async def get_inline_bot_results(self, bot, query):
        raise errors.BotInlineDisabled()

    async def send_message(self, chat, text):
        sent = self._add(True, text)
        if text.startswith("/start file_7"):
            if len(self.joined) < 2:
                self._add(
                    False,
                    "JOIN 2 CHANNELS AND THEN CLICK TRY AGAIN",
                    [
                        btn("Join Channel 1", url="https://t.me/+chan1"),
                        btn("Join Channel 2", url="https://t.me/+chan2"),
                        btn("🔄 Try Again", url=f"https://t.me/{BOT}?start=file_7"),
                    ],
                )
            else:
                self._add(False, "", document=NS(file_name=self.file_name, file_size=10))
        elif not text.startswith("/"):
            self._add(
                False,
                "Your Files is Ready Now",
                [
                    btn("Files: 300", cb="info"),
                    btn("2.57 GB • Dhoomakethu (2026) Malayalam...", url=f"https://t.me/{BOT}?start=file_1"),
                    btn("1.42 GB • Aaru Aanugal (2026) Malayalam H...", url=f"https://t.me/{BOT}?start=file_7"),
                    btn("Next ⏩", cb="next_1"),
                ],
            )
        return sent

    async def get_chat_history(self, chat, limit=0):
        for m in reversed(self.msgs[-limit:] if limit else self.msgs):
            yield m

    async def get_messages(self, chat, mid):
        return next(m for m in self.msgs if m.id == mid)

    async def request_callback_answer(self, chat, mid, data, timeout=10):
        return NS(url=None)

    async def join_chat(self, url):
        self.joined.append(url)
        raise errors.InviteRequestSent()

    async def forward_messages(self, to, chat, mid):
        raise errors.ChatForwardsRestricted()

    async def download_media(self, msg, file_name, progress):
        os.makedirs(os.path.dirname(file_name), exist_ok=True)
        with open(file_name, "wb") as f:
            f.write(b"0123456789")
        progress(10, 10)
        return file_name


def make_source(tmp_path, fake, **kw):
    kw = {"bots": [BOT], "bot_min_interval": 0, "search_timeout": 5, "file_timeout": 5, "max_pages": 1, **kw}
    src = TelegramSource(Settings(data_dir=str(tmp_path), **kw), client=fake)
    src.ready = True
    return src


def test_search_gate_and_download(tmp_path):
    fake = FakeClient()
    src = make_source(tmp_path, fake)

    async def flow():
        rels = await src.search("Aaru Aanugal 2026")
        assert [r.title for r in rels] == [
            "Dhoomakethu (2026) Malayalam 1080p",
            "Aaru Aanugal (2026) Malayalam H 720p",
        ]
        path = await src.fetch(rels[1], str(tmp_path / "dl"), lambda c, t: None)
        return path

    path = asyncio.run(flow())
    assert os.path.basename(path) == "Aaru.Aanugal.2026.Malayalam.HQ.HDRip.720p.x264.mkv"
    assert fake.joined == ["https://t.me/+chan1", "https://t.me/+chan2"]
    assert src._no_inline[BOT] == float("inf")  # the bot has no inline mode


def test_file_name_from_bot_stays_in_folder(tmp_path):
    fake = FakeClient()
    fake.joined = ["already", "joined"]
    fake.file_name = "../../evil.mkv"
    src = make_source(tmp_path, fake)

    async def flow():
        rels = await src.search("Aaru Aanugal 2026")
        return await src.fetch(rels[1], str(tmp_path / "dl"), lambda c, t: None)

    path = asyncio.run(flow())
    assert path == str(tmp_path / "dl" / "evil.mkv")


class SilentClient(FakeClient):
    """A bot that never answers, then starts answering."""

    def __init__(self):
        super().__init__()
        self.awake = False
        self.inline_calls = 0

    async def get_inline_bot_results(self, bot, query):
        self.inline_calls += 1
        raise TimeoutError()

    async def send_message(self, chat, text):
        if self.awake:
            return await super().send_message(chat, text)
        return self._add(True, text)


def test_empty_results_and_inline_errors_are_not_remembered(tmp_path):
    fake = SilentClient()
    src = make_source(tmp_path, fake, search_timeout=1)

    async def flow():
        assert await src.search("Aaru Aanugal") == []
        fake.awake = True
        return await src.search("Aaru Aanugal")

    assert len(asyncio.run(flow())) == 2  # asked the bot again instead of a cached empty answer
    # A timeout pauses inline mode for a while; it doesn't turn it off for good.
    assert fake.inline_calls == 1 and src._no_inline[BOT] < float("inf")


class RevokedClient(FakeClient):
    async def send_message(self, chat, text):
        raise errors.AuthKeyUnregistered()

    async def connect(self):
        return False


def test_revoked_session_marks_not_ready(tmp_path):
    src = make_source(tmp_path, RevokedClient(), use_inline=False)

    async def flow():
        assert await src.search("Aaru Aanugal") == []
        assert src.ready is False
        await src.stop()

    asyncio.run(flow())


def inline_doc(doc_id, name):
    return raw.Document(
        id=doc_id,
        access_hash=0,
        file_reference=b"",
        date=0,
        mime_type="video/x-matroska",
        size=10,
        dc_id=1,
        attributes=[raw.DocumentAttributeFilename(file_name=name)],
    )


class InlineClient:
    """Two bots in inline mode; the slow bot's file reaches Saved Messages last."""

    delays = {"SlowBot": 0.6, "FastBot": 0.05}

    def __init__(self):
        self.me = []
        self._id = 0

    async def get_inline_bot_results(self, bot, query):
        doc = inline_doc(1 if bot == "SlowBot" else 2, f"{bot}.mkv")
        return NS(query_id=bot, results=[NS(id="r", title="", document=doc)])

    async def send_inline_bot_result(self, chat, query_id, result_id):
        async def arrive():
            await asyncio.sleep(self.delays[query_id])
            self._id += 1
            self.me.append(NS(id=self._id, outgoing=True, reply_markup=None, document=NS(file_name=f"{query_id}.mkv"),
                              video=None, delete=self._noop))

        asyncio.ensure_future(arrive())

    async def _noop(self):
        pass

    async def get_chat_history(self, chat, limit=0):
        for m in reversed(self.me[-limit:]):
            yield m

    async def download_media(self, msg, file_name, progress):
        os.makedirs(os.path.dirname(file_name), exist_ok=True)
        open(file_name, "wb").close()
        return file_name


def test_parallel_inline_downloads_get_their_own_files(tmp_path):
    src = make_source(tmp_path, InlineClient(), bots=["SlowBot", "FastBot"])

    async def flow():
        rels = await src.search("Movie")
        by_bot = {r.bot: r for r in rels}
        return await asyncio.gather(
            src.fetch(by_bot["SlowBot"], str(tmp_path / "slow"), lambda c, t: None),
            src.fetch(by_bot["FastBot"], str(tmp_path / "fast"), lambda c, t: None),
        )

    slow, fast = asyncio.run(flow())
    assert os.path.basename(slow) == "SlowBot.mkv"
    assert os.path.basename(fast) == "FastBot.mkv"


class StuckClient:
    """A client whose session is revoked and whose shutdown fails part way."""

    def __init__(self):
        self.is_connected = self.is_initialized = False
        self.connects = 0

    async def connect(self):
        if self.is_connected:
            raise ConnectionError("Client is already connected")
        self.connects += 1
        self.is_connected = True
        return True

    async def initialize(self):
        self.is_initialized = True

    async def get_me(self):
        if self.connects == 1:
            raise errors.AuthKeyUnregistered()
        return NS(username="me", id=1)

    async def terminate(self):
        raise RuntimeError("watchdog failed")

    async def disconnect(self):
        raise RuntimeError("session stop failed")


def test_reconnects_after_failed_shutdown(tmp_path, monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _: real_sleep(0))
    client = StuckClient()
    src = TelegramSource(Settings(data_dir=str(tmp_path)), client=client)
    asyncio.run(src.connect_forever())
    assert src.ready and client.connects == 2
