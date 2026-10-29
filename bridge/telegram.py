"""Talks to Telegram search bots from a user account.

Search: send the query to each bot (or use its inline mode) and turn the file
buttons it replies with into releases.
Fetch: press the chosen button, get past any "join these channels first" gate,
and download the file the bot sends back.
"""

import asyncio
import hashlib
import logging
import os
import time
from collections.abc import Callable

from pyrogram import Client, errors
from pyrogram.raw import types as raw

from .config import Settings
from .parse import (
    is_join_button,
    is_retry_button,
    parse_file_button,
    release_title,
    start_param,
    strip_year,
)
from .store import Release

log = logging.getLogger(__name__)

Progress = Callable[[int, int], None]


class FetchError(Exception):
    pass


def _guid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _buttons(msg):
    markup = getattr(msg, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or []:
        yield from row


def _media(msg):
    return msg.document or msg.video or None


class TelegramSource:
    def __init__(self, settings: Settings, client: Client | None = None):
        self.s = settings
        self.client = client or Client(
            "bridge",
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            workdir=settings.data_dir,
        )
        self.ready = False
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_used: dict[str, float] = {}
        self._no_inline: set[str] = set()
        self._cache: dict[str, tuple[float, list[Release]]] = {}

    # ---- connection -------------------------------------------------------

    async def connect_forever(self) -> None:
        """Wait until the session is logged in, then stay connected."""
        while not self.ready:
            try:
                authorized = await self.client.connect()
                if authorized:
                    await self.client.initialize()
                    me = await self.client.get_me()
                    log.info("Telegram connected as %s", me.username or me.id)
                    self.ready = True
                    return
                await self.client.disconnect()
                log.warning("Telegram session is not logged in. Run: python -m bridge.login")
            except Exception as e:
                log.warning("Telegram connect failed: %s", e)
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(15)

    async def stop(self) -> None:
        if self.ready:
            await self.client.stop()

    # ---- pacing -----------------------------------------------------------

    def _lock(self, bot: str) -> asyncio.Lock:
        return self._locks.setdefault(bot, asyncio.Lock())

    async def _pace(self, bot: str) -> None:
        wait = self._last_used.get(bot, 0) + self.s.bot_min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_used[bot] = time.monotonic()

    async def _last_id(self, bot: str) -> int:
        async for m in self.client.get_chat_history(bot, limit=1):
            return m.id
        return 0

    async def _new_from_bot(self, bot: str, after: int) -> list:
        msgs = [m async for m in self.client.get_chat_history(bot, limit=15)]
        return sorted((m for m in msgs if m.id > after and not m.outgoing), key=lambda m: m.id)

    # ---- search -----------------------------------------------------------

    async def search(self, query: str) -> list[Release]:
        if not self.ready or not query.strip():
            return []
        key = query.strip().lower()
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.s.search_cache_ttl:
            return hit[1]
        results: list[Release] = []
        for q in filter(None, [query, strip_year(query)]):
            for bot in self.s.bots:
                try:
                    results += await self._search_bot(bot, q)
                except errors.FloodWait as e:
                    log.warning("Flood wait from %s: %ss", bot, e.value)
                except Exception:
                    log.exception("Search failed on %s", bot)
            if results:
                break
        seen, unique = set(), []
        for r in results:
            if r.guid not in seen:
                seen.add(r.guid)
                unique.append(r)
        self._cache[key] = (time.time(), unique)
        return unique

    async def _search_bot(self, bot: str, query: str) -> list[Release]:
        async with self._lock(bot):
            await self._pace(bot)
            if self.s.use_inline and bot not in self._no_inline:
                found = await self._search_inline(bot, query)
                if found is not None:
                    return found
            return await self._search_chat(bot, query)

    async def _search_inline(self, bot: str, query: str) -> list[Release] | None:
        try:
            res = await self.client.get_inline_bot_results(bot, query)
        except Exception as e:
            log.info("Inline mode unavailable for %s (%s), using chat", bot, e)
            self._no_inline.add(bot)
            return None
        out = []
        for item in res.results:
            doc = getattr(item, "document", None)
            if not isinstance(doc, raw.Document):
                continue
            fname = next(
                (a.file_name for a in doc.attributes if isinstance(a, raw.DocumentAttributeFilename)),
                None,
            )
            name = fname or item.title or ""
            if not name:
                continue
            out.append(
                Release(
                    guid=_guid(bot, "inline", str(doc.id)),
                    title=release_title(name, doc.size),
                    size=doc.size,
                    bot=bot,
                    kind="inline",
                    data=str(doc.id),
                    query=query,
                )
            )
        if not out:
            return None  # inline gave nothing useful, try the chat
        return out

    async def _search_chat(self, bot: str, query: str) -> list[Release]:
        sent = await self.client.send_message(bot, query)
        msg = await self._wait_for(bot, sent.id, lambda m: any(self._file_buttons(m)), self.s.search_timeout)
        if msg is None:
            return []
        msg = await self._pass_gate(bot, msg, sent.id, lambda m: any(self._file_buttons(m)))
        if msg is None:
            return []
        out = self._releases_from(bot, query, msg)
        for _ in range(self.s.max_pages - 1):
            nxt = next((b for b in _buttons(msg) if b.callback_data and "next" in b.text.lower()), None)
            if not nxt:
                break
            await self._press(bot, msg.id, nxt.callback_data)
            await asyncio.sleep(2)
            msg = await self.client.get_messages(bot, msg.id)
            out += self._releases_from(bot, query, msg)
        return out

    def _file_buttons(self, msg):
        for b in _buttons(msg):
            parsed = parse_file_button(b.text)
            if parsed and (b.callback_data or b.url):
                yield b, parsed

    def _releases_from(self, bot: str, query: str, msg) -> list[Release]:
        out = []
        for b, (size, name) in self._file_buttons(msg):
            kind, data = ("cb", b.callback_data) if b.callback_data else ("url", b.url)
            if isinstance(data, bytes):
                data = data.decode(errors="replace")
            out.append(
                Release(
                    guid=_guid(bot, kind, data),
                    title=release_title(name, size),
                    size=size,
                    bot=bot,
                    kind=kind,
                    data=data,
                    query=query,
                    msg_id=msg.id,
                )
            )
        return out

    # ---- fetch ------------------------------------------------------------

    async def fetch(self, rel: Release, dest_dir: str, progress: Progress) -> str:
        if not self.ready:
            raise FetchError("Telegram is not logged in")
        async with self._lock(rel.bot):
            await self._pace(rel.bot)
            msg = await self._get_file_message(rel)
        return await self._download(msg, rel, dest_dir, progress)

    async def _get_file_message(self, rel: Release):
        bot = rel.bot
        if rel.kind == "inline":
            return await self._fetch_inline(rel)
        marker = await self._last_id(bot)
        if rel.kind == "cb":
            await self._press(bot, rel.msg_id, rel.data)
        else:
            await self._open_link(bot, rel.data)
        has_file = lambda m: _media(m) is not None
        msg = await self._wait_for(bot, marker, has_file, self.s.file_timeout)
        if msg is None:
            raise FetchError(f"{bot} did not send a file")
        msg = await self._pass_gate(bot, msg, marker, has_file)
        if msg is None:
            raise FetchError(f"{bot} kept asking to join channels; approve the join requests and retry")
        return msg

    async def _fetch_inline(self, rel: Release):
        res = await self.client.get_inline_bot_results(rel.bot, rel.query)
        for item in res.results:
            doc = getattr(item, "document", None)
            if isinstance(doc, raw.Document) and str(doc.id) == rel.data:
                marker = await self._last_id("me")
                await self.client.send_inline_bot_result("me", res.query_id, item.id)
                msg = await self._wait_for("me", marker, lambda m: _media(m) is not None, 30, mine=True)
                if msg:
                    return msg
        raise FetchError("inline result is no longer offered by the bot")

    async def _open_link(self, bot: str, url: str) -> None:
        sp = start_param(url)
        if not sp:
            raise FetchError(f"button opens a website ({url}), which can't be automated")
        await self.client.send_message(sp[0], f"/start {sp[1]}")

    async def _press(self, bot: str, msg_id: int, data) -> None:
        try:
            ans = await self.client.request_callback_answer(bot, msg_id, data, timeout=10)
        except (TimeoutError, errors.RPCError) as e:
            log.debug("Callback answer from %s: %s", bot, e)
            return
        url = getattr(ans, "url", None)
        if url:
            await self._open_link(bot, url)

    async def _pass_gate(self, bot: str, msg, marker: int, done) -> object | None:
        """If the bot says 'join these channels first', join them and press Try Again."""
        for _ in range(3):
            if done(msg):
                return msg
            joins = [b for b in _buttons(msg) if is_join_button(b.text, b.url)]
            retry = next((b for b in _buttons(msg) if is_retry_button(b.text)), None)
            if not joins and not retry:
                log.info("Unexpected reply from %s: %s", bot, (msg.text or msg.caption or "")[:200])
                return None
            for b in joins:
                await self._join(b.url)
            await asyncio.sleep(3)
            new_marker = await self._last_id(bot)
            if retry and retry.callback_data:
                await self._press(bot, msg.id, retry.callback_data)
            elif retry and retry.url:
                await self._open_link(bot, retry.url)
            nxt = await self._wait_for(bot, new_marker, lambda m: done(m) or any(_buttons(m)), self.s.file_timeout)
            if nxt is None:
                # Some bots edit the gate message in place instead of sending a new one.
                nxt = await self.client.get_messages(bot, msg.id)
            msg = nxt
        return msg if done(msg) else None

    async def _join(self, url: str) -> None:
        try:
            await self.client.join_chat(url)
            log.info("Joined %s", url)
        except errors.InviteRequestSent:
            log.info("Join request sent to %s", url)
        except errors.UserAlreadyParticipant:
            pass
        except errors.RPCError as e:
            log.warning("Could not join %s: %s", url, e)

    async def _wait_for(self, chat: str, after: int, ok, timeout: float, mine: bool = False):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            if mine:
                msgs = [m async for m in self.client.get_chat_history(chat, limit=5) if m.id > after]
            else:
                msgs = await self._new_from_bot(chat, after)
            for m in reversed(msgs):
                if ok(m):
                    return m
            # Remember a gate-style reply so the caller can deal with it.
            gated = [m for m in msgs if any(_buttons(m))]
            if gated:
                last = gated[-1]
                if any(is_retry_button(b.text) for b in _buttons(last)):
                    return last
        return last

    async def _download(self, msg, rel: Release, dest_dir: str, progress: Progress) -> str:
        media = _media(msg)
        fname = getattr(media, "file_name", None) or f"{rel.title}.mkv"
        os.makedirs(dest_dir, exist_ok=True)
        # Bots often delete files after a few minutes, so keep a copy in Saved Messages.
        source, copy = msg, None
        if rel.kind == "inline":
            copy = msg  # already in Saved Messages; remove it afterwards
        else:
            try:
                fwd = await self.client.forward_messages("me", msg.chat.id, msg.id)
                source = copy = fwd[0] if isinstance(fwd, list) else fwd
            except errors.RPCError:
                pass  # protected content can't be forwarded; download directly
        try:
            path = await self.client.download_media(
                source,
                file_name=os.path.join(dest_dir, fname),
                progress=lambda cur, total: progress(cur, total),
            )
        finally:
            if copy is not None:
                try:
                    await copy.delete()
                except errors.RPCError:
                    pass
        if not path:
            raise FetchError("download failed")
        return path
