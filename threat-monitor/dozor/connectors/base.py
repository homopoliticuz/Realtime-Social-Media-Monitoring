"""Базовые типы коннекторов к источникам.

Коннектор работает только через разрешённые интерфейсы: официальные API,
публичный веб-предпросмотр, RSS, открытые протоколы, а также материалы,
правомерно предоставленные пользователем. Коннекторы не обходят
авторизацию, ограничения доступа и не читают закрытые группы и переписку.

Состояние коннектора всегда отражает реальность: если ключи не заданы или
платформа требует одобрения исследовательского доступа, коннектор так и
сообщает, а интерфейс не изображает поиск по нему.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Awaitable, Callable

import httpx

from ..text.expansion import Expansion

STATES = {
    "connected": "Подключён",
    "watchlist_needed": "Подключён, нужен список публичных источников",
    "not_configured": "Не настроен (нет ключей доступа)",
    "requires_approval": "Требует одобрения платформы (исследовательский/партнёрский доступ)",
    "unavailable": "Подключение невозможно: нет разрешённого интерфейса",
    "error": "Ошибка подключения",
    "demo": "Демонстрационные данные",
}


@dataclass
class RawItem:
    platform: str
    external_id: str
    url: str
    text: str
    published_at: str | None
    source_name: str
    source_kind: str = "post"
    author_kind: str = "public_source"  # public_source | user_hidden | provided
    context: str | None = None
    links: list[str] = field(default_factory=list)
    forwarded_from: dict | None = None  # {"url", "name", "evidence"}
    repost_of: dict | None = None
    quote_of: dict | None = None
    reply_to: dict | None = None
    lang_hint: str | None = None
    is_demo: bool = False
    provenance: str = "connector"
    legal_basis: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class ConnectorStatus:
    state: str
    message: str

    def to_dict(self) -> dict:
        return {"state": self.state, "label": STATES.get(self.state, self.state), "message": self.message}


@dataclass
class SearchPlan:
    expansion: Expansion
    date_from: datetime | None = None
    date_to: datetime | None = None
    source_urls: list[str] = field(default_factory=list)
    max_requests: int = 12
    max_items: int = 300

    def before_start(self, published_iso: str | None) -> bool:
        """Материал опубликован раньше начала периода поиска."""
        if not published_iso or not self.date_from:
            return False
        try:
            dt = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
        except ValueError:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < self.date_from

    def in_range(self, published_iso: str | None) -> bool:
        if not published_iso:
            return True
        try:
            dt = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
        except ValueError:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if self.date_from and dt < self.date_from:
            return False
        if self.date_to and dt > self.date_to:
            return False
        return True


@dataclass
class FetchResult:
    items: list[RawItem] = field(default_factory=list)
    requests: int = 0
    queries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


Progress = Callable[[str], Awaitable[None]]


class Connector:
    name = "base"
    platform = "—"
    title = "—"
    access = "—"
    official = True
    capabilities: list[str] = []
    requires: list[str] = []
    limitations: list[str] = []
    docs_url = ""
    url_patterns: list[str] = []

    def __init__(self, settings) -> None:
        self.settings = settings

    def env(self, name: str) -> str | None:
        return self.settings.env(name)

    def status(self) -> ConnectorStatus:
        missing = [r for r in self.requires if not self.env(r)]
        if missing:
            return ConnectorStatus("not_configured", "Не заданы: " + ", ".join(missing))
        return ConnectorStatus("connected", "Готов к работе")

    @property
    def usable(self) -> bool:
        return self.status().state in ("connected", "watchlist_needed", "demo")

    def handles_url(self, url: str) -> bool:
        return any(re.search(p, url, re.IGNORECASE) for p in self.url_patterns)

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        return FetchResult(notes=["Коннектор не поддерживает поиск"])

    def describe(self) -> dict:
        return {
            "name": self.name,
            "platform": self.platform,
            "title": self.title,
            "access": self.access,
            "official": self.official,
            "capabilities": self.capabilities,
            "requires": self.requires,
            "limitations": self.limitations,
            "docs_url": self.docs_url,
            "status": self.status().to_dict(),
        }


# ------------------------------------------------------------------ утилиты
def to_iso(value) -> str | None:
    """Приводит дату (ISO, unix, RFC 2822) к ISO 8601 с часовым поясом."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
    except (ValueError, TypeError, OverflowError):
        return None
    return None


_VOID = {"br", "img", "meta", "link", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "param", "track"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if tag in ("br", "p", "div", "li", "h1", "h2", "h3", "tr"):
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith("http"):
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def strip_html(html: str) -> tuple[str, list[str]]:
    p = _TextExtractor()
    try:
        p.feed(html or "")
    except Exception:  # noqa: BLE001 — некорректный HTML
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text, p.links


async def get_json(client: httpx.AsyncClient, url: str, **kwargs) -> tuple[dict | list | None, str | None]:
    """GET/POST с разбором JSON; возвращает (данные, текст ошибки)."""
    method = kwargs.pop("method", "GET")
    try:
        resp = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        return None, f"Сетевая ошибка: {exc.__class__.__name__}"
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code >= 400:
        detail = ""
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                detail = err.get("message") or err.get("error_msg") or ""
            elif isinstance(err, str):
                detail = err
            detail = detail or data.get("message", "")
        return data, f"HTTP {resp.status_code}{': ' + str(detail)[:200] if detail else ''}"
    return data, None


async def get_text(client: httpx.AsyncClient, url: str, **kwargs) -> tuple[str | None, str | None]:
    try:
        resp = await client.get(url, **kwargs)
    except httpx.HTTPError as exc:
        return None, f"Сетевая ошибка: {exc.__class__.__name__}"
    if resp.status_code >= 400:
        return None, f"HTTP {resp.status_code}"
    return resp.text, None
