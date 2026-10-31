"""Copies the bridge's warnings and errors into the activity log, so the dashboard shows them."""

import logging
import time

from .store import Store

KINDS = {"bridge.telegram": "telegram", "bridge.downloads": "download", "bridge.commands": "command"}

# The same warning within this many seconds is logged once (e.g. "not logged in" every 15s).
REPEAT_AFTER = 600


class EventLogHandler(logging.Handler):
    def __init__(self, store: Store):
        super().__init__(logging.WARNING)
        self.store = store
        self._seen: dict[str, float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                e = record.exc_info[1]
                text += f": {e}" if str(e) else f": {type(e).__name__}"
            now = time.time()
            if now - self._seen.get(text, 0) < REPEAT_AFTER:
                return
            self._seen = {t: ts for t, ts in self._seen.items() if now - ts < REPEAT_AFTER}
            self._seen[text] = now
            level = "error" if record.levelno >= logging.ERROR else "warning"
            self.store.log_event(KINDS.get(record.name, "system"), text, level)
        except Exception:
            self.handleError(record)
