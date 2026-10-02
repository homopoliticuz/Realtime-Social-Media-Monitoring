"""Конфигурация из переменных окружения и необязательного файла .env."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Минимальный загрузчик .env: KEY=VALUE, комментарии через #.

    Уже заданные переменные окружения не перезаписываются.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _list(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


@dataclass
class Settings:
    db_path: Path
    secret: str
    host: str
    port: int
    session_hours: int
    user_agent: str
    http_timeout: float
    max_requests_per_connector: int
    max_items_per_search: int
    excerpt_chars: int
    context_chars: int
    llm_enabled: bool
    llm_model: str
    llm_effort: str
    cookie_secure: bool
    # Списки наблюдения (публичные источники, которые пользователь указал сам)
    telegram_channels: list[str] = field(default_factory=list)
    vk_domains: list[str] = field(default_factory=list)
    rss_feeds: list[str] = field(default_factory=list)
    mastodon_instances: list[str] = field(default_factory=list)
    twitch_channels: list[str] = field(default_factory=list)
    discord_channel_ids: list[str] = field(default_factory=list)
    youtube_channel_ids: list[str] = field(default_factory=list)
    twitch_chat_capture_seconds: int = 0

    def env(self, name: str) -> str | None:
        value = os.environ.get(name)
        return value if value else None


def load_settings() -> Settings:
    _load_dotenv(BASE_DIR / ".env")
    db_path = Path(os.environ.get("DOZOR_DB_PATH", BASE_DIR / "var" / "dozor.sqlite3"))
    secret = os.environ.get("DOZOR_SECRET") or ""
    if not secret:
        # Секрет нужен для подписи сессий и HMAC-хэшей. Если он не задан,
        # генерируем и сохраняем рядом с БД, чтобы он переживал перезапуск.
        secret_file = db_path.parent / ".dozor_secret"
        if secret_file.is_file():
            secret = secret_file.read_text(encoding="utf-8").strip()
        else:
            secret = secrets.token_hex(32)
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            secret_file.write_text(secret, encoding="utf-8")
            try:
                secret_file.chmod(0o600)
            except OSError:
                pass
    return Settings(
        db_path=db_path,
        secret=secret,
        host=os.environ.get("DOZOR_HOST", "127.0.0.1"),
        port=_int("DOZOR_PORT", 8080),
        session_hours=_int("DOZOR_SESSION_HOURS", 8),
        user_agent=os.environ.get(
            "DOZOR_USER_AGENT", "DozorMonitor/1.0 (public-content safety research)"
        ),
        http_timeout=float(os.environ.get("DOZOR_HTTP_TIMEOUT", "20")),
        max_requests_per_connector=_int("DOZOR_MAX_REQUESTS_PER_CONNECTOR", 12),
        max_items_per_search=_int("DOZOR_MAX_ITEMS_PER_SEARCH", 500),
        excerpt_chars=_int("DOZOR_EXCERPT_CHARS", 700),
        context_chars=_int("DOZOR_CONTEXT_CHARS", 500),
        llm_enabled=_bool("DOZOR_LLM_ENABLED", False),
        llm_model=os.environ.get("DOZOR_LLM_MODEL", "claude-opus-5-5"),
        llm_effort=os.environ.get("DOZOR_LLM_EFFORT", "medium"),
        cookie_secure=_bool("DOZOR_COOKIE_SECURE", False),
        telegram_channels=_list("DOZOR_TELEGRAM_CHANNELS"),
        vk_domains=_list("DOZOR_VK_DOMAINS"),
        rss_feeds=_list("DOZOR_RSS_FEEDS"),
        mastodon_instances=_list("DOZOR_MASTODON_INSTANCES"),
        twitch_channels=_list("DOZOR_TWITCH_CHANNELS"),
        discord_channel_ids=_list("DOZOR_DISCORD_CHANNEL_IDS"),
        youtube_channel_ids=_list("DOZOR_YOUTUBE_CHANNEL_IDS"),
        twitch_chat_capture_seconds=_int("DOZOR_TWITCH_CHAT_CAPTURE_SECONDS", 0),
    )
