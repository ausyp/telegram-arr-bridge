"""End-to-end through the HTTP API with a fake Telegram source."""

import os
import time

import pytest
from fastapi.testclient import TestClient

from bridge.config import Settings
from bridge.main import create_app
from bridge.store import Release


class FakeSource:
    ready = True

    def __init__(self):
        self.searches = []

    async def connect_forever(self):
        pass

    async def stop(self):
        pass

    async def search(self, query):
        self.searches.append(query)
        return [
            Release(
                guid="abc123",
                title="Aaru Aanugal (2026) Malayalam 720p",
                size=1500,
                bot="SomeBot",
                kind="cb",
                data="file#1",
                query=query,
            )
        ]

    async def fetch(self, rel, dest_dir, progress):
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, "Aaru.Aanugal.2026.720p.mkv")
        with open(path, "wb") as f:
            f.write(b"x" * rel.size)
        progress(rel.size, rel.size)
        return path


@pytest.fixture
def client(tmp_path):
    s = Settings(
        api_key="k",
        bots=["SomeBot"],
        data_dir=str(tmp_path / "data"),
        complete_dir=str(tmp_path / "complete"),
        categories=["radarr"],
    )
    with TestClient(create_app(s, FakeSource())) as c:
        yield c


def test_rejects_wrong_key(client):
    assert "Incorrect user credentials" in client.get("/api?t=caps&apikey=nope").text


def test_later_pages_are_empty(client):
    assert "<item>" not in client.get("/api?t=search&q=x&offset=100&apikey=k").text


def test_caps_and_empty_rss(client):
    assert "movie-search" in client.get("/api?t=caps&apikey=k").text
    assert "<item>" in client.get("/api?t=movie&apikey=k").text  # placeholder for the indexer test


def test_search_grab_and_complete(client, tmp_path):
    feed = client.get("/api?t=movie&q=Aaru Aanugal 2026&apikey=k").text
    assert "Aaru Aanugal (2026) Malayalam 720p" in feed
    nzb = client.get("/api?t=get&id=abc123&apikey=k").content
    r = client.post(
        "/sabnzbd/api?mode=addfile&cat=radarr&priority=-100&apikey=k&output=json",
        files={"name": ("Aaru Aanugal (2026) Malayalam 720p.nzb", nzb, "application/x-nzb")},
    ).json()
    assert r["status"] is True
    nzo = r["nzo_ids"][0]
    for _ in range(50):
        hist = client.get("/api?mode=history&apikey=k").json()["history"]["slots"]
        if hist:
            break
        time.sleep(0.05)
    assert hist[0]["nzo_id"] == nzo and hist[0]["status"] == "Completed"
    folder = hist[0]["storage"]
    assert folder == str(tmp_path / "complete" / "radarr" / "Aaru Aanugal (2026) Malayalam 720p")
    assert os.listdir(folder) == ["Aaru.Aanugal.2026.720p.mkv"]
    client.get(f"/api?mode=history&name=delete&value={nzo}&del_files=1&apikey=k")
    assert not os.path.exists(folder)
    assert client.get("/api?mode=history&apikey=k").json()["history"]["slots"] == []


def test_sab_config_has_categories(client):
    cfg = client.get("/sabnzbd/api?mode=get_config&apikey=k").json()["config"]
    assert [c["name"] for c in cfg["categories"]] == ["*", "radarr"]
    assert client.get("/sabnzbd/api?mode=version&apikey=k").json()["version"]


def test_tv_only_search_is_empty(client):
    assert "<item>" not in client.get("/api?t=search&q=Aaru&cat=5000,5040&apikey=k").text
    assert "<item>" in client.get("/api?t=search&q=Aaru&cat=2000,5000&apikey=k").text


def test_rss_skips_old_results(client):
    store = client.app.state.bridge.store
    old = Release(guid="old1", title="Old Movie (2020) 720p", size=1, bot="b", kind="cb", data="x", query="q")
    old.created = time.time() - 2 * 86400
    store.put_release(old)
    new = Release(guid="new1", title="New Movie (2026) 720p", size=1, bot="b", kind="cb", data="x", query="q")
    store.put_release(new)
    feed = client.get("/api?t=movie&apikey=k").text
    assert "New Movie" in feed and "Old Movie" not in feed


def test_dot_names_cannot_escape_the_folder(client, tmp_path):
    client.get("/api?t=movie&q=Aaru&apikey=k")
    r = client.get("/api?mode=addurl&name=http://x/api?id=abc123&nzbname=..&cat=..&apikey=k").json()
    nzo = r["nzo_ids"][0]
    for _ in range(50):
        hist = client.get("/api?mode=history&apikey=k").json()["history"]["slots"]
        if hist:
            break
        time.sleep(0.05)
    assert hist[0]["storage"] == str(tmp_path / "complete" / "download" / "download")
    client.get(f"/api?mode=history&name=delete&value={nzo}&del_files=1&apikey=k")
    assert os.path.isdir(tmp_path / "complete")
