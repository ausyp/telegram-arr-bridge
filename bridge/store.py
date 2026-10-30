"""SQLite store for search results (releases) and download jobs."""

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
            self._db.commit()
