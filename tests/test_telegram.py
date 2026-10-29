"""Replays the flow from a real auto-filter bot against a fake Telegram client:
search -> list of file buttons -> press one -> "join 2 channels" gate ->
join + Try Again -> file arrives -> download."""

import asyncio
import os
from types import SimpleNamespace as NS

from pyrogram import errors

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
        raise errors.BadRequest("BOT_INLINE_DISABLED")

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
                self._add(
                    False, "", document=NS(file_name="Aaru.Aanugal.2026.Malayalam.HQ.HDRip.720p.x264.mkv", file_size=10)
                )
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


def test_search_gate_and_download(tmp_path):
    s = Settings(bots=[BOT], bot_min_interval=0, search_timeout=5, file_timeout=5, max_pages=1, data_dir=str(tmp_path))
    fake = FakeClient()
    src = TelegramSource(s, client=fake)
    src.ready = True

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
