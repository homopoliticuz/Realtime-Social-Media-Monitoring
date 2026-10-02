"""Конфигурация из переменных окружения и необязательного файла .env."""

from __future__ import annotations

import os
import re
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


def _vk_domain(value: str) -> str:
    """vk.com/club123, https://vk.ru/durov, @durov → club123, durov."""
    m = re.search(r"vk\.(?:com|ru)/([A-Za-z0-9_.]+)", value)
    return m.group(1) if m else value.strip().lstrip("@").strip("/")


def _host(value: str) -> str:
    """https://mastodon.social/@user → mastodon.social."""
    return re.sub(r"^[a-z]+://", "", value.strip(), flags=re.I).split("/")[0].strip().lower()


def _twitch_channel(value: str) -> str:
    """https://www.twitch.tv/name, #name, @name → name."""
    v = re.sub(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/", "", value.strip(), flags=re.I)
    return v.split("/")[0].split("?")[0].lstrip("#@").lower()


def _discord_channel(value: str) -> str:
    """https://discord.com/channels/<сервер>/<канал> → <канал>."""
    ids = re.findall(r"\d{15,22}", value)
    return ids[-1] if ids else value.strip()


def _normalized(items: list[str], fn) -> list[str]:
    out: list[str] = []
    for item in items:
        v = fn(item)
        if v and v not in out:
            out.append(v)
    return out


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
    demo_enabled: bool
    env_file: Path
    telegram_pages: int
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


def env_file_path() -> Path:
    return Path(os.environ.get("DOZOR_ENV_FILE", BASE_DIR / ".env"))


def load_settings() -> Settings:
    env_path = env_file_path()
    _load_dotenv(env_path)
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
        # Учебный режим с вымышленными материалами. В рабочем режиме выключен.
        demo_enabled=_bool("DOZOR_DEMO", False),
        env_file=env_path,
        telegram_pages=max(1, min(_int("DOZOR_TELEGRAM_PAGES", 2), 10)),
        telegram_channels=_list("DOZOR_TELEGRAM_CHANNELS"),
        vk_domains=_normalized(_list("DOZOR_VK_DOMAINS"), _vk_domain),
        rss_feeds=_list("DOZOR_RSS_FEEDS"),
        mastodon_instances=_normalized(_list("DOZOR_MASTODON_INSTANCES"), _host)
        if "DOZOR_MASTODON_INSTANCES" in os.environ else ["mastodon.social"],
        twitch_channels=_normalized(_list("DOZOR_TWITCH_CHANNELS"), _twitch_channel),
        discord_channel_ids=_normalized(_list("DOZOR_DISCORD_CHANNEL_IDS"), _discord_channel),
        youtube_channel_ids=_list("DOZOR_YOUTUBE_CHANNEL_IDS"),
        twitch_chat_capture_seconds=_int("DOZOR_TWITCH_CHAT_CAPTURE_SECONDS", 0),
    )


def refresh_settings(settings: Settings) -> Settings:
    """Перечитывает переменные окружения в существующий объект настроек.

    Коннекторы держат ссылку на этот объект, поэтому новые списки
    источников и ключи применяются без перезапуска сервера.
    """
    fresh = load_settings()
    for name in ("telegram_channels", "vk_domains", "rss_feeds", "mastodon_instances", "twitch_channels",
                 "discord_channel_ids", "youtube_channel_ids", "twitch_chat_capture_seconds", "llm_enabled",
                 "llm_model", "llm_effort", "telegram_pages", "max_requests_per_connector"):
        setattr(settings, name, getattr(fresh, name))
    return settings
