import time

from bridge.downloads import safe_name
from bridge.store import Job, Release, Store


def test_prune_keeps_recent_and_active(tmp_path):
    store = Store(str(tmp_path / "db"))
    old = time.time() - 90 * 86400
    for guid in ("used", "unused"):
        r = Release(guid=guid, title=guid, size=1, bot="b", kind="cb", data="x", query="q")
        r.created = old
        store.put_release(r)
    store.put_release(Release(guid="fresh", title="fresh", size=1, bot="b", kind="cb", data="x", query="q"))
    store.put_job(Job(nzo_id="done", guid="x", name="n", category="c", status="Completed", created=old))
    store.put_job(Job(nzo_id="queued", guid="used", name="n", category="c", status="Queued", created=old))

    store.prune(before=time.time() - 30 * 86400)

    assert [j.nzo_id for j in store.jobs()] == ["queued"]
    assert store.get_release("used") and store.get_release("fresh")
    assert store.get_release("unused") is None


def test_safe_name():
    assert safe_name("..") == "download"
    assert safe_name(".") == "download"
    assert safe_name("a/../b") == "a .. b"
    assert safe_name("Movie (2026) 720p") == "Movie (2026) 720p"
