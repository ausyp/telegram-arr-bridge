"""HTTP API: a Newznab indexer and a SABnzbd-compatible download client.

Radarr/Sonarr add this app twice: under Indexers as "Newznab" and under
Download Clients as "SABnzbd". Grabs are passed through a small fake NZB that
only carries the release id.
"""

import re
import time
from email.utils import formatdate
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

MOVIES, TV = 2000, 5000
_GUID_RE = re.compile(r'<meta type="x-bridge-guid">([0-9a-f]+)</meta>')
_ID_RE = re.compile(r"[?&]id=([0-9a-f]+)")


def _xml(body: str, status: int = 200) -> Response:
    return Response('<?xml version="1.0" encoding="UTF-8"?>\n' + body, status, media_type="application/xml")


def _error(code: int, text: str) -> Response:
    return _xml(f"<error code={quoteattr(str(code))} description={quoteattr(text)}/>")


CAPS = """<caps>
  <server title="Telegram Arr Bridge"/>
  <limits max="100" default="100"/>
  <searching>
    <search available="yes" supportedParams="q"/>
    <movie-search available="yes" supportedParams="q"/>
    <tv-search available="no" supportedParams="q"/>
  </searching>
  <categories>
    <category id="2000" name="Movies"><subcat id="2040" name="HD"/></category>
    <category id="5000" name="TV"><subcat id="5040" name="HD"/></category>
  </categories>
</caps>"""


def make_router(app_state) -> APIRouter:
    router = APIRouter()
    s = app_state.settings

    def authorized(params) -> bool:
        return not s.api_key or params.get("apikey") == s.api_key

    # ---- Newznab ----------------------------------------------------------

    def item_xml(rel, base: str) -> str:
        link = f"{base}api?t=get&id={rel.guid}&apikey={s.api_key}"
        return f"""<item>
  <title>{escape(rel.title)}</title>
  <guid isPermaLink="false">{rel.guid}</guid>
  <link>{escape(link)}</link>
  <pubDate>{formatdate(rel.created)}</pubDate>
  <category>Movies</category>
  <description>{escape(f"via @{rel.bot}")}</description>
  <size>{rel.size}</size>
  <enclosure url={quoteattr(link)} length="{rel.size}" type="application/x-nzb"/>
  <newznab:attr name="category" value="{MOVIES}"/>
  <newznab:attr name="size" value="{rel.size}"/>
</item>"""

    def feed(items: list[str]) -> Response:
        return _xml(
            '<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">'
            "<channel><title>Telegram Arr Bridge</title>"
            f'<newznab:response offset="0" total="{len(items)}"/>' + "".join(items) + "</channel></rss>"
        )

    def nzb(rel) -> Response:
        body = f"""<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head>
    <meta type="name">{escape(rel.title)}</meta>
    <meta type="x-bridge-guid">{rel.guid}</meta>
  </head>
  <file poster="telegram" date="{int(rel.created)}" subject={quoteattr(rel.title + ".mkv")}>
    <groups><group>alt.binaries.telegram</group></groups>
    <segments><segment bytes="{rel.size}" number="1">{rel.guid}@telegram-arr-bridge</segment></segments>
  </file>
</nzb>"""
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>\n' + body,
            media_type="application/x-nzb",
            headers={"Content-Disposition": f'attachment; filename="{rel.guid}.nzb"'},
        )

    async def newznab(request: Request, params) -> Response:
        if not authorized(params):
            return _error(100, "Incorrect user credentials")
        t = params.get("t", "")
        base = str(request.base_url)
        if t == "caps":
            return _xml(CAPS)
        if t == "get":
            rel = app_state.store.get_release(params.get("id", ""))
            return nzb(rel) if rel else _error(300, "No such item")
        if t in ("search", "movie"):
            q = (params.get("q") or "").strip()
            if int(params.get("offset") or 0) > 0:
                return feed([])  # everything is returned on the first page
            if q:
                releases = await app_state.source.search(q)
                for r in releases:
                    app_state.store.put_release(r)
            else:
                # RSS: Radarr polls this and tests the indexer with it.
                releases = app_state.store.recent_releases(50)
                if not releases:
                    return feed([PLACEHOLDER.replace("{ts}", formatdate(time.time()))])
            return feed([item_xml(r, base) for r in releases])
        if t == "tvsearch":
            return feed([])
        return _error(202, f"No such function ({t})")

    # ---- SABnzbd ----------------------------------------------------------

    def sab_config() -> dict:
        cats = [{"name": "*", "pp": "3", "script": "None", "dir": "", "priority": 0}]
        cats += [{"name": c, "pp": "", "script": "Default", "dir": c, "priority": -100} for c in s.categories]
        return {
            "config": {
                "misc": {
                    "complete_dir": s.complete_dir,
                    "download_dir": s.complete_dir + "/.incomplete",
                    "enable_tv_sorting": False,
                    "enable_movie_sorting": False,
                    "enable_date_sorting": False,
                    "tv_categories": [],
                    "movie_categories": [],
                    "date_categories": [],
                    "pre_check": False,
                    "history_retention": "",
                },
                "categories": cats,
                "sorters": [],
            }
        }

    def queue_slot(i: int, j) -> dict:
        mb = j.size / 1024**2
        left = max(j.size - j.done, 0)
        elapsed = max(time.time() - j.created, 1)
        rate = j.done / elapsed
        eta = int(left / rate) if rate > 0 else 3600
        return {
            "index": i,
            "nzo_id": j.nzo_id,
            "filename": j.name,
            "cat": j.category,
            "status": j.status,
            "priority": "Normal",
            "mb": f"{mb:.2f}",
            "mbleft": f"{left / 1024**2:.2f}",
            "percentage": str(int(100 * j.done / j.size)) if j.size else "0",
            "timeleft": f"{eta // 3600}:{eta % 3600 // 60:02d}:{eta % 60:02d}",
        }

    def history_slot(j) -> dict:
        return {
            "nzo_id": j.nzo_id,
            "name": j.name,
            "nzb_name": j.name + ".nzb",
            "category": j.category,
            "status": j.status,
            "bytes": j.size,
            "storage": j.path,
            "fail_message": j.error,
            "download_time": int(max(j.completed - j.created, 0)),
            "completed": int(j.completed),
        }

    async def sab(request: Request, params) -> Response:
        if not authorized(params):
            return JSONResponse({"status": False, "error": "API Key Incorrect"})
        mode = params.get("mode", "")
        dl = app_state.downloads
        if mode == "version":
            return JSONResponse({"version": "4.3.3"})
        if mode == "get_config":
            return JSONResponse(sab_config())
        if mode == "fullstatus":
            return JSONResponse({"status": {"completedir": s.complete_dir}})
        if mode in ("addfile", "addurl"):
            guid, name = None, params.get("nzbname") or ""
            if mode == "addfile":
                form = await request.form()
                for v in form.values():
                    if hasattr(v, "read"):
                        m = _GUID_RE.search((await v.read()).decode(errors="replace"))
                        guid = m and m.group(1)
                        # Radarr/Sonarr name the upload "<release title>.nzb".
                        name = name or re.sub(r"\.nzb$", "", v.filename or "")
            else:
                m = _ID_RE.search(params.get("name", ""))
                guid = m and m.group(1)
            nzo_id = dl.add(guid, name, params.get("cat", "")) if guid else None
            if not nzo_id:
                return JSONResponse({"status": False, "error": "Unknown release"})
            return JSONResponse({"status": True, "nzo_ids": [nzo_id]})
        if mode == "queue":
            if params.get("name") == "delete":
                for nid in params.get("value", "").split(","):
                    dl.remove(nid, delete_files=True)
                return JSONResponse({"status": True})
            active = [j for j in dl.all() if j.status in ("Queued", "Downloading")]
            return JSONResponse(
                {
                    "queue": {
                        "paused": False,
                        "noofslots": len(active),
                        "slots": [queue_slot(i, j) for i, j in enumerate(active)],
                    }
                }
            )
        if mode == "history":
            if params.get("name") == "delete":
                for nid in params.get("value", "").split(","):
                    dl.remove(nid, delete_files=params.get("del_files") == "1")
                return JSONResponse({"status": True})
            done = [j for j in dl.all() if j.status in ("Completed", "Failed")]
            cat = params.get("category")
            if cat and cat != "*":
                done = [j for j in done if j.category == cat]
            done.sort(key=lambda j: j.completed, reverse=True)
            return JSONResponse({"history": {"noofslots": len(done), "slots": [history_slot(j) for j in done]}})
        if mode == "retry":
            return JSONResponse({"status": False})
        return JSONResponse({"status": False, "error": f"not implemented: {mode}"})

    # ---- routes -----------------------------------------------------------

    async def params_of(request: Request) -> dict:
        params = dict(request.query_params)
        if request.method == "POST" and "multipart" not in request.headers.get("content-type", ""):
            form = await request.form()
            params.update({k: v for k, v in form.items() if isinstance(v, str)})
        return params

    @router.api_route("/api", methods=["GET", "POST"])
    async def api(request: Request):
        params = await params_of(request)
        # One URL serves both, so the SABnzbd "URL Base" can be left empty.
        if "mode" in params:
            return await sab(request, params)
        return await newznab(request, params)

    @router.api_route("/sabnzbd/api", methods=["GET", "POST"])
    async def sab_api(request: Request):
        return await sab(request, await params_of(request))

    @router.get("/health")
    async def health():
        return {"telegram": app_state.source.ready, "bots": s.bots}

    return router


# Shown when nothing has been searched yet, so the Radarr indexer test passes.
# Its name does not parse as a movie, so it is never grabbed.
PLACEHOLDER = """<item>
  <title>Telegram Arr Bridge is running</title>
  <guid isPermaLink="false">placeholder</guid>
  <pubDate>{ts}</pubDate>
  <size>1</size>
  <enclosure url="http://localhost/placeholder" length="1" type="application/x-nzb"/>
  <newznab:attr name="category" value="2000"/>
</item>"""
