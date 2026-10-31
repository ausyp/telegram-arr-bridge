"""SQLite store for search results (releases), download jobs, the bots to search, and the activity log."""

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field


@dataclass
class Release:
    guid: str
    title: str
    size: int
    bot: str
    kind: str  # "cb", "url" or "inline"
    data: str  # callback data, URL, or inline result id
    query: str
    msg_id: int = 0
    created: float = field(default_factory=time.time)


@dataclass
class Job:
    nzo_id: str
    guid: str
    name: str
    category: str
    status: str = "Queued"  # Queued, Downloading, Completed, Failed
    size: int = 0
    done: int = 0
    path: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)
    completed: float = 0.0


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS releases (guid TEXT PRIMARY KEY, body TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS jobs (nzo_id TEXT PRIMARY KEY, body TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS bots (name TEXT PRIMARY KEY COLLATE NOCASE, removed INTEGER, created REAL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, level TEXT, text TEXT, data TEXT
                );
                CREATE INDEX IF NOT EXISTS events_kind ON events (kind, id);
                """
            )

    def put_release(self, r: Release) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO releases VALUES (?, ?, ?)",
                (r.guid, json.dumps(asdict(r)), r.created),
            )
            self._db.commit()

    def get_release(self, guid: str) -> Release | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM releases WHERE guid = ?", (guid,)).fetchone()
        return Release(**json.loads(row[0])) if row else None

    def recent_releases(self, limit: int = 100, since: float = 0.0) -> list[Release]:
        with self._lock:
            rows = self._db.execute(
                "SELECT body FROM releases WHERE created >= ? ORDER BY created DESC LIMIT ?", (since, limit)
            ).fetchall()
        return [Release(**json.loads(r[0])) for r in rows]

    def put_job(self, j: Job) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)",
                (j.nzo_id, json.dumps(asdict(j)), j.created),
            )
            self._db.commit()

    def get_job(self, nzo_id: str) -> Job | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM jobs WHERE nzo_id = ?", (nzo_id,)).fetchone()
        return Job(**json.loads(row[0])) if row else None

    def jobs(self) -> list[Job]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM jobs ORDER BY created").fetchall()
        return [Job(**json.loads(r[0])) for r in rows]

    def delete_job(self, nzo_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM jobs WHERE nzo_id = ?", (nzo_id,))
            self._db.commit()

    def prune(self, before: float) -> None:
        """Drop finished jobs, and releases no job uses, that are older than `before`."""
        with self._lock:
            self._db.execute(
                "DELETE FROM jobs WHERE created < ? AND json_extract(body, '$.status') IN ('Completed', 'Failed')",
                (before,),
            )
            self._db.execute(
                "DELETE FROM releases WHERE created < ?"
                " AND guid NOT IN (SELECT json_extract(body, '$.guid') FROM jobs)",
                (before,),
            )
            self._db.execute("DELETE FROM events WHERE ts < ?", (before,))
            self._db.commit()

    def seed_bots(self, names: list[str]) -> None:
        """Add bots from BOTS that haven't been seen before. A bot removed by command stays removed."""
        with self._lock:
            self._db.executemany("INSERT OR IGNORE INTO bots VALUES (?, 0, ?)", [(n, time.time()) for n in names])
            self._db.commit()

    def bots(self) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT name FROM bots WHERE removed = 0 ORDER BY created, name").fetchall()
        return [r[0] for r in rows]

    def add_bot(self, name: str) -> bool:
        """Returns False if the bot was already in the list."""
        with self._lock:
            row = self._db.execute("SELECT removed FROM bots WHERE name = ?", (name,)).fetchone()
            if row and not row[0]:
                return False
            self._db.execute("DELETE FROM bots WHERE name = ?", (name,))
            self._db.execute("INSERT INTO bots VALUES (?, 0, ?)", (name, time.time()))
            self._db.commit()
        return True

    def remove_bot(self, name: str) -> bool:
        """Returns False if the bot wasn't in the list."""
        with self._lock:
            cur = self._db.execute("UPDATE bots SET removed = 1 WHERE name = ? AND removed = 0", (name,))
            self._db.commit()
        return cur.rowcount > 0

    # ---- activity log (shown on the dashboard) ----------------------------

    def log_event(self, kind: str, text: str, level: str = "info", **data) -> None:
        """kind: search, grab, download, command, telegram or system. level: info, warning or error."""
        with self._lock:
            self._db.execute(
                "INSERT INTO events (ts, kind, level, text, data) VALUES (?, ?, ?, ?, ?)",
                (time.time(), kind, level, text, json.dumps(data)),
            )
            self._db.commit()

    def events(self, limit: int = 100, before: int = 0, after: int = 0, kind: str = "") -> list[dict]:
        """Newest first. `before`/`after` are event ids, for paging back and for polling new ones."""
        where, args = ["id > ?"], [after]
        if before:
            where.append("id < ?")
            args.append(before)
        if kind == "error":
            where.append("level != 'info'")
        elif kind:
            where.append("kind = ?")
            args.append(kind)
        with self._lock:
            rows = self._db.execute(
                f"SELECT id, ts, kind, level, text, data FROM events WHERE {' AND '.join(where)}"
                " ORDER BY id DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [
            {"id": i, "ts": ts, "kind": k, "level": lv, "text": t, "data": json.loads(d or "{}")}
            for i, ts, k, lv, t, d in rows
        ]

    def event_counts(self, since: float) -> dict[str, int]:
        """Events per kind since `since`, plus "error" for warnings and errors."""
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, COUNT(*) FROM events WHERE ts >= ? AND level = 'info' GROUP BY kind", (since,)
            ).fetchall()
            errors = self._db.execute(
                "SELECT COUNT(*) FROM events WHERE ts >= ? AND level != 'info'", (since,)
            ).fetchone()[0]
        return {**dict(rows), "error": errors}
