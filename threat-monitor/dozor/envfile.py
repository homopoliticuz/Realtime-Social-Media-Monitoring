"""Настройка источников и ключей доступа из интерфейса.

Значения сохраняются в файл ``.env`` рядом с программой (только для
администратора) и сразу применяются к работающему серверу. Секреты
никогда не возвращаются в интерфейс целиком — только признак «задан» и
последние символы.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    group: str
    kind: str = "text"  # text | list | secret | int | bool
    help: str = ""


GROUPS = {
    "watch": "Списки наблюдения — публичные источники, которые нужно отслеживать",
    "telegram": "Telegram (официальный API, необязательно)",
    "vk": "ВКонтакте",
    "google": "YouTube",
    "meta": "Instagram и Threads (Meta)",
    "tiktok": "TikTok Research API",
    "twitch": "Twitch",
    "discord": "Discord",
    "x": "X (Twitter)",
    "bluesky": "Bluesky",
    "llm": "Второй классификатор (LLM)",
}

FIELDS: list[Field] = [
    Field("DOZOR_TELEGRAM_CHANNELS", "Публичные Telegram-каналы", "watch", "list",
          "Через запятую или с новой строки: @channel, t.me/channel или ссылка. Работает без ключей."),
    Field("DOZOR_VK_DOMAINS", "Публичные сообщества VK", "watch", "list",
          "Короткие имена или ссылки (vk.com/club123). Нужен ключ VK ниже."),
    Field("DOZOR_RSS_FEEDS", "RSS/Atom-ленты", "watch", "list", "Адреса лент СМИ, блогов, мостов к каналам."),
    Field("DOZOR_MASTODON_INSTANCES", "Серверы Mastodon", "watch", "list",
          "Например: mastodon.social. Поиск по хэштегам на этих серверах."),
    Field("DOZOR_TWITCH_CHANNELS", "Каналы Twitch для захвата чата", "watch", "list", "Имена каналов."),
    Field("DOZOR_TWITCH_CHAT_CAPTURE_SECONDS", "Секунд захвата чата Twitch при поиске", "watch", "int",
          "0 — не захватывать чат."),
    Field("DOZOR_DISCORD_CHANNEL_IDS", "ID каналов Discord", "watch", "list",
          "Каналы на серверах, куда администраторы добавили вашего бота."),
    Field("DOZOR_TELEGRAM_PAGES", "Страниц ленты Telegram на канал", "watch", "int",
          "1 страница ≈ 20 последних сообщений. По умолчанию 2."),
    Field("DOZOR_MAX_REQUESTS_PER_CONNECTOR", "Лимит запросов к одному источнику за поиск", "watch", "int",
          "По умолчанию 12. Больше — шире охват, но медленнее и выше риск ограничений платформы."),
    Field("TELEGRAM_API_ID", "api_id", "telegram", "text", "my.telegram.org → API development tools."),
    Field("TELEGRAM_API_HASH", "api_hash", "telegram", "secret",
          "После ввода выполните однократный вход: .venv\\Scripts\\python -m dozor telegram-login"),
    Field("VK_ACCESS_TOKEN", "Ключ доступа VK", "vk", "secret", "dev.vk.com: сервисный ключ приложения или ключ пользователя."),
    Field("YOUTUBE_API_KEY", "Ключ YouTube Data API v3", "google", "secret", "console.cloud.google.com → API и сервисы."),
    Field("INSTAGRAM_ACCESS_TOKEN", "Токен Instagram Graph API", "meta", "secret",
          "Профессиональный аккаунт и разрешение Instagram Public Content Access."),
    Field("INSTAGRAM_USER_ID", "ID аккаунта Instagram", "meta", "text"),
    Field("THREADS_ACCESS_TOKEN", "Токен Threads API", "meta", "secret", "Разрешение threads_keyword_search."),
    Field("TIKTOK_CLIENT_KEY", "Client key", "tiktok", "text", "Только для одобренного исследовательского доступа."),
    Field("TIKTOK_CLIENT_SECRET", "Client secret", "tiktok", "secret"),
    Field("TWITCH_CLIENT_ID", "Client ID", "twitch", "text", "dev.twitch.tv → Applications."),
    Field("TWITCH_CLIENT_SECRET", "Client secret", "twitch", "secret"),
    Field("DISCORD_BOT_TOKEN", "Токен бота Discord", "discord", "secret", "Включите intent Message Content."),
    Field("X_BEARER_TOKEN", "Bearer token X API", "x", "secret", "Платный тариф X API."),
    Field("BSKY_HANDLE", "Имя аккаунта Bluesky", "bluesky", "text", "Необязательно: без него поиск анонимный."),
    Field("BSKY_APP_PASSWORD", "Пароль приложения Bluesky", "bluesky", "secret", "Настройки Bluesky → App passwords."),
    Field("DOZOR_LLM_ENABLED", "Включить второй классификатор", "llm", "bool",
          "Тексты материалов будут отправляться в Anthropic API. Включайте при наличии правового основания."),
    Field("ANTHROPIC_API_KEY", "Ключ Anthropic API", "llm", "secret"),
]
BY_NAME = {f.name: f for f in FIELDS}

_LINE = re.compile(r"^\s*([A-Z0-9_]+)\s*=(.*)$")


def _mask(value: str) -> str:
    return "•••• " + value[-4:] if len(value) > 8 else "••••"


def read_values() -> dict:
    groups = []
    for gid, label in GROUPS.items():
        items = []
        for f in FIELDS:
            if f.group != gid:
                continue
            value = os.environ.get(f.name, "")
            item = asdict(f)
            if f.kind == "secret":
                item["is_set"] = bool(value)
                item["hint"] = _mask(value) if value else ""
                item["value"] = ""
            elif f.kind == "list":
                item["value"] = "\n".join(x.strip() for x in value.split(",") if x.strip())
            else:
                item["value"] = value
            items.append(item)
        groups.append({"id": gid, "label": label, "fields": items})
    return {"groups": groups}


def _normalize(field: Field, raw) -> str:
    if field.kind == "list":
        parts = re.split(r"[\s,;]+", str(raw or ""))
        return ",".join(dict.fromkeys(p.strip() for p in parts if p.strip()))
    if field.kind == "int":
        try:
            return str(max(0, int(str(raw).strip() or 0)))
        except ValueError as exc:
            raise ValueError(f"«{field.label}»: нужно целое число") from exc
    if field.kind == "bool":
        return "1" if str(raw).strip().lower() in ("1", "true", "yes", "on", "да") else "0"
    value = str(raw or "").strip()
    if "\n" in value or "\r" in value:
        raise ValueError(f"«{field.label}»: значение должно быть в одну строку")
    return value


def save(env_path: Path, values: dict, clear: list[str] | None = None) -> list[str]:
    """Сохраняет значения в .env и окружение процесса. Возвращает имена изменённых полей."""
    updates: dict[str, str] = {}
    for name, raw in (values or {}).items():
        field = BY_NAME.get(name)
        if not field:
            continue
        if field.kind == "secret" and not str(raw or "").strip():
            continue  # пустое поле секрета — оставить как есть
        new = _normalize(field, raw)
        if os.environ.get(name, "") != new:
            updates[name] = new
    for name in clear or []:
        if name in BY_NAME and os.environ.get(name, ""):
            updates[name] = ""
    if not updates:
        return []
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else [
        "# Настройки Дозора. Изменяются в интерфейсе: Администрирование → Источники и ключи.",
    ]
    seen = set()
    out = []
    for line in lines:
        m = _LINE.match(line)
        if m and m.group(1) in updates:
            out.append(f"{m.group(1)}={updates[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(line)
    for name, value in updates.items():
        if name not in seen:
            out.append(f"{name}={value}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = env_path.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, env_path)
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    for name, value in updates.items():
        os.environ[name] = value
    return sorted(updates)
