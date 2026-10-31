"""App entry point: python -m bridge.main"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import uvicorn
from fastapi import FastAPI

from .activity import EventLogHandler
from .api import make_router
from .config import Settings
from .dashboard import make_dashboard
from .downloads import Downloads
from .store import Store

log = logging.getLogger("bridge")


def create_app(settings: Settings, source=None) -> FastAPI:
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(settings.complete_dir, exist_ok=True)
    store = Store(os.path.join(settings.data_dir, "bridge.db"))
    # BOTS seeds the list; after that, /addbot and /removebot change it (see commands.py).
    store.seed_bots(settings.bots)
    settings.bots = store.bots()
    if source is None:
        from .telegram import TelegramSource

        source = TelegramSource(settings, store=store)
    state = SimpleNamespace(settings=settings, store=store, source=source, downloads=None, started=time.time())

    async def prune_daily() -> None:
        while True:
            store.prune(before=time.time() - settings.history_days * 86400)
            await asyncio.sleep(86400)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        events = EventLogHandler(store)
        logging.getLogger("bridge").addHandler(events)
        store.log_event("system", "Bridge started")
        state.downloads = Downloads(settings, store, source)
        state.downloads.resume()
        tasks = [asyncio.create_task(source.connect_forever()), asyncio.create_task(prune_daily())]
        yield
        for t in tasks:
            t.cancel()
        await source.stop()
        logging.getLogger("bridge").removeHandler(events)

    app = FastAPI(title="Telegram Arr Bridge", lifespan=lifespan)
    app.include_router(make_router(state))
    app.include_router(make_dashboard(state))
    app.state.bridge = state
    return app


def main() -> None:
    os.umask(int(os.environ.get("UMASK", "002"), 8))
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    if not settings.api_id or not settings.api_hash:
        raise SystemExit("API_ID and API_HASH are required (get them at https://my.telegram.org/apps)")
    if not settings.api_key:
        log.warning("BRIDGE_API_KEY is empty: the API is open to anyone who can reach it")
    app = create_app(settings)
    if not settings.bots:
        log.warning("No bots to search: set BOTS or send /addbot to the Telegram account")
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8765")))


if __name__ == "__main__":
    main()
