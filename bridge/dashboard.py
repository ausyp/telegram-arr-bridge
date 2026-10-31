"""Web dashboard at /: live status, downloads and the activity log.

The page itself is static; it reads /dashboard/status and /dashboard/events,
which need BRIDGE_API_KEY (as ?apikey= or an X-Api-Key header) when one is set.
"""

import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

PAGE = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
DAY = 86400


def make_dashboard(app_state) -> APIRouter:
    router = APIRouter()
    s = app_state.settings

    def authorized(request: Request) -> bool:
        key = request.headers.get("x-api-key") or request.query_params.get("apikey")
        return not s.api_key or key == s.api_key

    def denied() -> JSONResponse:
        return JSONResponse({"error": "API key needed"}, status_code=401)

    def job(j) -> dict:
        return {
            "id": j.nzo_id,
            "name": j.name,
            "category": j.category,
            "status": j.status,
            "size": j.size,
            "done": j.done,
            "error": j.error,
            "created": j.created,
            "completed": j.completed,
        }

    def bot_stats(since: float) -> list[dict]:
        """Results each bot gave over recent searches."""
        searches = app_state.store.events(limit=500, kind="search")
        searches = [e for e in searches if e["ts"] >= since]
        stats = []
        for bot in s.bots:
            counts = [e["data"].get("bots", {}).get(bot, 0) for e in searches]
            stats.append({"name": bot, "results": sum(counts), "hits": sum(1 for c in counts if c)})
        return stats

    @router.get("/", include_in_schema=False)
    async def page():
        return FileResponse(PAGE, headers={"Cache-Control": "no-cache"})

    @router.get("/dashboard/status")
    async def status(request: Request):
        if not authorized(request):
            return denied()
        src = app_state.source
        dl = app_state.downloads
        jobs = dl.all() if dl else []
        since = time.time() - DAY
        active = [j for j in jobs if j.status in ("Queued", "Downloading")]
        finished = sorted((j for j in jobs if j.status in ("Completed", "Failed")), key=lambda j: -j.completed)
        counts = app_state.store.event_counts(since)
        return {
            "now": time.time(),
            "started": app_state.started,
            "telegram": {"connected": bool(src.ready), "account": getattr(src, "account", "")},
            "command_bot": {
                "configured": bool(s.command_bot_token),
                "name": getattr(src, "command_bot_name", ""),
            },
            "bots": bot_stats(since),
            "active": [job(j) for j in active],
            "finished": [job(j) for j in finished[:20]],
            "day": {
                "searches": counts.get("search", 0),
                "grabs": counts.get("grab", 0),
                "completed": sum(1 for j in finished if j.status == "Completed" and j.completed >= since),
                "failed": sum(1 for j in finished if j.status == "Failed" and j.completed >= since),
                "problems": counts.get("error", 0),
            },
            "settings": {
                "max_downloads": s.max_downloads,
                "history_days": s.history_days,
                "api_key_set": bool(s.api_key),
            },
        }

    @router.get("/dashboard/events")
    async def events(request: Request, limit: int = 100, before: int = 0, after: int = 0, kind: str = ""):
        if not authorized(request):
            return denied()
        return {"events": app_state.store.events(limit=min(max(limit, 1), 500), before=before, after=after, kind=kind)}

    return router
