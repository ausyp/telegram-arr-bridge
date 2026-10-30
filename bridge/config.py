"""Settings, read from environment variables."""

import os
from dataclasses import dataclass, field


def _list(value: str) -> list[str]:
    return [v.strip().lstrip("@") for v in value.split(",") if v.strip()]


@dataclass
class Settings:
    api_id: int = 0
    api_hash: str = ""
    api_key: str = ""
    bots: list[str] = field(default_factory=list)
    allowed_users: list[str] = field(default_factory=list)
    command_bot_token: str = ""
    data_dir: str = "/data"
    complete_dir: str = "/downloads/complete"
    categories: list[str] = field(default_factory=list)
    search_timeout: float = 25.0
    file_timeout: float = 120.0
    bot_min_interval: float = 5.0
    max_pages: int = 2
    max_downloads: int = 2
    search_cache_ttl: float = 3600.0
    use_inline: bool = True
    rss_max_age: float = 86400.0
    history_days: float = 30.0

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            api_id=int(env("API_ID", "0") or 0),
            api_hash=env("API_HASH", ""),
            api_key=env("BRIDGE_API_KEY", ""),
            bots=_list(env("BOTS", "")),
            allowed_users=_list(env("ALLOWED_USERS", "")),
            command_bot_token=env("COMMAND_BOT_TOKEN", ""),
            data_dir=env("DATA_DIR", "/data"),
            complete_dir=env("COMPLETE_DIR", "/downloads/complete"),
            categories=_list(env("CATEGORIES", "movies,radarr,tv,sonarr")),
            search_timeout=float(env("SEARCH_TIMEOUT", "25")),
            file_timeout=float(env("FILE_TIMEOUT", "120")),
            bot_min_interval=float(env("BOT_MIN_INTERVAL", "5")),
            max_pages=int(env("MAX_PAGES", "2")),
            max_downloads=int(env("MAX_DOWNLOADS", "2")),
            search_cache_ttl=float(env("SEARCH_CACHE_TTL", "3600")),
            use_inline=env("USE_INLINE", "true").lower() in ("1", "true", "yes"),
            rss_max_age=float(env("RSS_MAX_AGE_HOURS", "24")) * 3600,
            history_days=float(env("HISTORY_DAYS", "30")),
        )
