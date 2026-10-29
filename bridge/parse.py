"""Pure helpers for turning bot buttons into release names."""

import re
from urllib.parse import parse_qs, urlparse

_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([KMGT]i?B|B)\b", re.IGNORECASE)
# Emoji, bullets and bracket debris left around the size in button labels.
_JUNK_RE = re.compile(r"[\U0001F000-\U0001FFFF☀-➿⬀-⯿️•·|►▶➤→📁📂]+")
_RES_RE = re.compile(r"\b(2160p|1080p|720p|576p|480p|4k|uhd)\b", re.IGNORECASE)
_RETRY_RE = re.compile(r"try\s*again|retry|refresh|joined|verify|check\s*again", re.IGNORECASE)
_JOIN_RE = re.compile(r"join|subscribe|channel|request", re.IGNORECASE)


def parse_size(text: str) -> int | None:
    m = _SIZE_RE.search(text)
    if not m:
        return None
    unit = m.group(2).upper().replace("I", "")
    return int(float(m.group(1).replace(",", ".")) * _UNITS[unit])


def parse_file_button(text: str) -> tuple[int, str] | None:
    """'2.57 GB • Dhoomakethu (2026) Malayalam...' -> (bytes, 'Dhoomakethu (2026) Malayalam')."""
    size = parse_size(text)
    if size is None:
        return None
    name = _SIZE_RE.sub(" ", text, count=1)
    name = _JUNK_RE.sub(" ", name)
    name = re.sub(r"^[\s\[\]\-–—:()]+", "", name)
    name = re.sub(r"(\.{2,}|…)\s*$", "", name.strip())
    name = re.sub(r"\s+", " ", name).strip(" -–—:[]")
    if not name:
        return None
    return size, name


def release_title(name: str, size: int) -> str:
    """Make a name Radarr/Sonarr can parse.

    Bot buttons are often cut off before the quality, so when no resolution is
    present one is guessed from the file size.
    """
    name = re.sub(r"\.(mkv|mp4|avi|m4v|webm)$", "", name.strip(), flags=re.IGNORECASE)
    name = re.sub(r"[\[\]{}@#]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not _RES_RE.search(name):
        gb = size / 1024**3
        res = "480p" if gb < 0.9 else "720p" if gb < 1.9 else "1080p" if gb < 8 else "2160p"
        name = f"{name} {res}"
    return name


def is_retry_button(text: str) -> bool:
    return bool(_RETRY_RE.search(text))


def is_join_button(text: str, url: str | None) -> bool:
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return host in ("t.me", "telegram.me") and bool(_JOIN_RE.search(text) or "/+" in url or "joinchat" in url)


def start_param(url: str) -> tuple[str, str] | None:
    """'https://t.me/SomeBot?start=abc' -> ('SomeBot', 'abc')."""
    u = urlparse(url)
    if u.netloc.lower() not in ("t.me", "telegram.me"):
        return None
    start = parse_qs(u.query).get("start")
    bot = u.path.strip("/").split("/")[0]
    if not start or not bot:
        return None
    return bot, start[0]


def strip_year(query: str) -> str | None:
    m = re.match(r"^(.*\S)\s+\(?(19|20)\d{2}\)?$", query.strip())
    return m.group(1) if m else None
