"""Download queue: turns grabbed releases into files in the completed folder."""

import asyncio
import logging
import os
import re
import shutil
import time
import uuid

from .config import Settings
from .store import Job, Store

log = logging.getLogger(__name__)


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name).strip()
    # "." and ".." would point at the parent folder.
    return name if name.strip(".") else "download"


class Downloads:
    def __init__(self, settings: Settings, store: Store, source):
        self.s = settings
        self.store = store
        self.source = source
        self._sem = asyncio.Semaphore(settings.max_downloads)
        self._tasks: dict[str, asyncio.Task] = {}
        self._live: dict[str, Job] = {}

    def resume(self) -> None:
        """Restart jobs that were unfinished when the app stopped."""
        for job in self.store.jobs():
            if job.status in ("Queued", "Downloading"):
                job.status, job.done = "Queued", 0
                self.store.put_job(job)
                self._start(job)

    def add(self, guid: str, name: str, category: str) -> str | None:
        rel = self.store.get_release(guid)
        if rel is None:
            return None
        job = Job(
            nzo_id=f"SABnzbd_nzo_{uuid.uuid4().hex[:12]}",
            guid=guid,
            name=safe_name(name or rel.title),
            category=category or "",
            size=rel.size,
        )
        self.store.put_job(job)
        self._start(job)
        return job.nzo_id

    def _start(self, job: Job) -> None:
        self._live[job.nzo_id] = job
        self._tasks[job.nzo_id] = asyncio.create_task(self._run(job))

    def job_dir(self, job: Job) -> str:
        return os.path.join(self.s.complete_dir, safe_name(job.category), job.name)

    async def _run(self, job: Job) -> None:
        async with self._sem:
            rel = self.store.get_release(job.guid)
            job.status = "Downloading"
            self.store.put_job(job)
            dest = self.job_dir(job)
            # Download into a hidden folder so Radarr never sees a partial file.
            tmp = os.path.join(self.s.complete_dir, ".incomplete", job.nzo_id)

            def progress(cur: int, total: int) -> None:
                job.done, job.size = cur, total or job.size

            try:
                if rel is None:
                    raise RuntimeError("release not found")
                path = await self.source.fetch(rel, tmp, progress)
                os.makedirs(dest, exist_ok=True)
                final = os.path.join(dest, os.path.basename(path))
                shutil.move(path, final)
                shutil.rmtree(tmp, ignore_errors=True)
                job.status, job.path, job.done = "Completed", dest, job.size
                log.info("Finished %s -> %s", job.name, final)
            except asyncio.CancelledError:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            except Exception as e:
                log.exception("Download failed: %s", job.name)
                shutil.rmtree(tmp, ignore_errors=True)
                job.status, job.error = "Failed", str(e) or type(e).__name__
            job.completed = time.time()
            self.store.put_job(job)
            self._live.pop(job.nzo_id, None)
            self._tasks.pop(job.nzo_id, None)

    def _is_job_folder(self, path: str) -> bool:
        """Only ever delete a release folder: complete/<category>/<release>."""
        if not path or not os.path.isdir(path):
            return False
        root = os.path.realpath(self.s.complete_dir)
        rel = os.path.relpath(os.path.realpath(path), root)
        return not rel.startswith("..") and len(rel.split(os.sep)) == 2

    def get(self, nzo_id: str) -> Job | None:
        return self._live.get(nzo_id) or self.store.get_job(nzo_id)

    def all(self) -> list[Job]:
        return [self._live.get(j.nzo_id, j) for j in self.store.jobs()]

    def remove(self, nzo_id: str, delete_files: bool = False) -> None:
        task = self._tasks.pop(nzo_id, None)
        if task:
            task.cancel()
        job = self.get(nzo_id)
        self._live.pop(nzo_id, None)
        if job and delete_files and self._is_job_folder(job.path):
            shutil.rmtree(job.path, ignore_errors=True)
        self.store.delete_job(nzo_id)
