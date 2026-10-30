"""Talks to Telegram search bots from a user account.

Search: send the query to each bot (or use its inline mode) and turn the file
buttons it replies with into releases.
Fetch: press the chosen button, get past any "join these channels first" gate,
and download the file the bot sends back.
Commands: allowed users can add and remove bots by messaging this account (see commands.py).
"""

import asyncio
import hashlib
import logging
import os
import time
from collections.abc import Callable

from pyrogram import Client, errors, filters
from pyrogram.handlers import MessageHandler
from pyrogram.raw import functions
from pyrogram.raw import types as raw
from pyrogram.types import BotCommand

from . import commands
from .config import Settings
from .downloads import safe_name
from .parse import (
    is_join_button,
    is_retry_button,
    parse_file_button,
    release_title,
    start_param,
    strip_year,
)
from .store import Release, Store

log = logging.getLogger(__name__)

# How long to stop trying a bot's inline mode after a transient error.
INLINE_RETRY_AFTER = 600

Progress = Callable[[int, int], None]


class FetchError(Exception):
    pass


def _guid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _buttons(msg):
    markup = getattr(msg, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or []:
        yield from row


def _use_running_loop(client) -> None:
    """Clients are made before uvicorn starts its event loop, so pyrogram picked up a different one.
    Its dispatcher would then queue update handlers on a loop that never runs, and no message
    (our commands) would ever be handled."""
    loop = asyncio.get_running_loop()
    if hasattr(client, "dispatcher"):
        client.loop = client.dispatcher.loop = loop


def _media(msg):
    return msg.document or msg.video or None


class TelegramSource:
    def __init__(self, settings: Settings, client: Client | None = None, store: Store | None = None):
        self.s = settings
        self.store = store
        self.client = client or Client(
            "bridge",
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            workdir=settings.data_dir,
        )
        self.ready = False
        self._locks: dict[str, asyncio.Lock] = {}
        # Saved Messages is shared by every download, so only one may use it at a time.
        self._me_lock = asyncio.Lock()
        self._last_used: dict[str, float] = {}
        self._no_inline: dict[str, float] = {}  # bot -> skip inline mode until this time
        self._cache: dict[str, tuple[float, list[Release]]] = {}
        self._reconnect: asyncio.Task | None = None
        # Optional bot (from @BotFather) that takes the same commands, so users don't message the account itself.
        self.command_bot: Client | None = None
        if settings.command_bot_token and store:
            self.command_bot = Client(
                "command-bot",
                api_id=settings.api_id,
                api_hash=settings.api_hash,
                bot_token=settings.command_bot_token,
                workdir=settings.data_dir,
            )

    # ---- connection -------------------------------------------------------

    async def connect_forever(self) -> None:
        """Wait until the session is logged in, then stay connected."""
        while not self.ready:
            try:
                _use_running_loop(self.client)
                authorized = await self.client.connect()
                if authorized:
                    await self.client.initialize()
                    # initialize() starts with no handlers, so add ours every time.
                    if self.store:
                        self.client.add_handler(MessageHandler(self._on_command, filters.private & filters.text))
                    me = await self.client.get_me()
                    self.client.me = me
                    # Telegram only pushes new messages (our commands) after this call; start() would make it.
                    await self.client.invoke(functions.updates.GetState())
                    log.info("Telegram connected as %s", me.username or me.id)
                    self.ready = True
                    await self._start_command_bot()
                    return
                await self.client.disconnect()
                log.warning("Telegram session is not logged in. Run: python -m bridge.login")
            except errors.Unauthorized as e:
                log.error("Telegram session was logged out (%s). Run: python -m bridge.login", e)
                await self._reset_client()
            except Exception as e:
                log.warning("Telegram connect failed: %s", e)
                await self._reset_client()
            await asyncio.sleep(15)

    async def _reset_client(self) -> None:
        """Get back to a disconnected client, even if shutting down part of it fails."""
        try:
            if self.client.is_initialized:
                await self.client.terminate()
        except Exception as e:
            log.warning("Terminate: %s", e)
            self.client.is_initialized = False
        try:
            if self.client.is_connected:
                await self.client.disconnect()
        except Exception as e:
            # Left marked as connected, every later connect() fails with "Client is already connected".
            log.warning("Disconnect: %s", e)
            self.client.is_connected = False

    async def _lost_auth(self, e: Exception) -> None:
        """The session was revoked or logged out: report it and wait for a new login."""
        if not self.ready:
            return
        self.ready = False
        log.error("Telegram session is no longer authorized (%s). Run: python -m bridge.login", e)
        await self._reset_client()
        self._reconnect = asyncio.create_task(self.connect_forever())

    async def stop(self) -> None:
        if self._reconnect:
            self._reconnect.cancel()
        if self.ready:
            await self.client.stop()
        if self.command_bot and self.command_bot.is_connected:
            await self.command_bot.stop()

    # ---- commands ---------------------------------------------------------

    async def _start_command_bot(self) -> None:
        bot = self.command_bot
        if bot is None or bot.is_connected:
            return
        try:
            _use_running_loop(bot)
            await bot.start()
            bot.add_handler(MessageHandler(self._on_command, filters.private & filters.text))
            await bot.set_bot_commands([BotCommand(c, d) for c, d in commands.MENU])
            log.info("Command bot running as @%s", bot.me.username)
        except Exception as e:
            log.error("Command bot failed to start, check COMMAND_BOT_TOKEN: %s", e)

    async def _on_command(self, client, msg) -> None:
        parsed = commands.parse(msg.text or "")
        if not parsed:
            return
        user = msg.from_user
        saved_messages = msg.outgoing and user and msg.chat.id == user.id
        if not saved_messages and (msg.outgoing or not commands.is_allowed(user, self.s.allowed_users)):
            if not msg.outgoing:
                log.info("Ignored %s from %s (not in ALLOWED_USERS)", parsed[0], user and (user.username or user.id))
                if client is not None and client is self.command_bot and user:
                    # Only the bot answers strangers: it tells them the id to add, nothing else.
                    await msg.reply_text(f"You can't manage bots yet. Add your user id {user.id} to ALLOWED_USERS.")
            return
        reply = await commands.run(*parsed, self.store, self._check_bot)
        self.s.bots = self.store.bots()
        self._cache.clear()
        await msg.reply_text(reply, quote=True)

    async def _check_bot(self, name: str) -> tuple[str | None, str | None]:
        try:
            user = await self.client.get_users(name)
        except (errors.RPCError, KeyError, ValueError):
            return None, f"Couldn't find @{name} on Telegram"
        if not getattr(user, "is_bot", False):
            return None, f"@{name} isn't a bot"
        return user.username or name, None

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
            # Bots are independent, so ask them all at once.
            for found in await asyncio.gather(*(self._search_one(bot, q) for bot in self.s.bots)):
                results += found
            if results:
                break
        seen, unique = set(), []
        for r in results:
            if r.guid not in seen:
                seen.add(r.guid)
                unique.append(r)
        now = time.time()
        self._cache = {k: v for k, v in self._cache.items() if now - v[0] < self.s.search_cache_ttl}
        # An empty result may just be a slow or rate-limited bot, so don't remember it.
        if unique:
            self._cache[key] = (now, unique)
        return unique

    async def _search_one(self, bot: str, query: str) -> list[Release]:
        try:
            return await self._search_bot(bot, query)
        except errors.FloodWait as e:
            log.warning("Flood wait from %s: %ss", bot, e.value)
        except errors.Unauthorized as e:
            await self._lost_auth(e)
        except Exception:
            log.exception("Search failed on %s", bot)
        return []

    async def _search_bot(self, bot: str, query: str) -> list[Release]:
        async with self._lock(bot):
            await self._pace(bot)
            if self.s.use_inline and self._no_inline.get(bot, 0) < time.time():
                found = await self._search_inline(bot, query)
                if found is not None:
                    return found
            return await self._search_chat(bot, query)

    async def _search_inline(self, bot: str, query: str) -> list[Release] | None:
        try:
            res = await self.client.get_inline_bot_results(bot, query)
        except errors.Unauthorized:
            raise
        except errors.BotInlineDisabled:
            log.info("%s has no inline mode, using chat", bot)
            self._no_inline[bot] = float("inf")
            return None
        except Exception as e:
            log.info("Inline search failed on %s (%s), using chat for %ss", bot, e, INLINE_RETRY_AFTER)
            self._no_inline[bot] = time.time() + INLINE_RETRY_AFTER
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
        try:
            async with self._lock(rel.bot):
                await self._pace(rel.bot)
                msg = await self._get_file_message(rel)
            return await self._download(msg, rel, dest_dir, progress)
        except errors.Unauthorized as e:
            await self._lost_auth(e)
            raise FetchError("Telegram session is no longer authorized") from e

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
                async with self._me_lock:
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
        delay = 1.0
        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            # Most replies come within seconds; poll less often after that to spare the API.
            delay = min(delay * 1.5, 5.0)
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
        # The name comes from the bot, so keep it inside dest_dir.
        fname = safe_name(os.path.basename(getattr(media, "file_name", None) or "")) if media else ""
        if fname == "download":
            fname = safe_name(f"{rel.title}.mkv")
        os.makedirs(dest_dir, exist_ok=True)
        # Bots often delete files after a few minutes, so keep a copy in Saved Messages.
        source, copy = msg, None
        if rel.kind == "inline":
            copy = msg  # already in Saved Messages; remove it afterwards
        else:
            try:
                async with self._me_lock:
                    fwd = await self.client.forward_messages("me", msg.chat.id, msg.id)
                source = copy = fwd[0] if isinstance(fwd, list) else fwd
            except errors.Unauthorized:
                raise
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
