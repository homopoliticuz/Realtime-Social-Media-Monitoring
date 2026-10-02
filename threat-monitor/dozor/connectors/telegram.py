"""Telegram: публичные каналы.

1. ``telegram_web`` — публичный веб-предпросмотр ``https://t.me/s/<канал>``
   (без авторизации, как в браузере). Поддерживает поиск внутри канала
   (``?q=``) и отдельные посты. Поле «Переслано из» — подтверждённая связь.
2. ``telegram_mtproto`` — официальный клиентский API (Telethon) с ключами
   разработчика; только публичные каналы и публичные чаты с открытым
   username. Закрытые группы и личная переписка не читаются.

Глобального поиска по всему Telegram у этих интерфейсов нет: анализируются
публичные каналы, которые пользователь указал (список наблюдения или ссылка).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import quote

import httpx

from .base import Connector, ConnectorStatus, FetchResult, Progress, RawItem, SearchPlan, get_text

_CHANNEL_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{4,64})(?:/(\d+))?", re.IGNORECASE)
_VOID = {"br", "img", "meta", "link", "input", "hr", "source", "wbr"}


def parse_channel_ref(ref: str) -> tuple[str | None, str | None]:
    ref = ref.strip()
    m = _CHANNEL_RE.search(ref)
    if m:
        return m.group(1), m.group(2)
    if re.fullmatch(r"@?[A-Za-z0-9_]{4,64}", ref):
        return ref.lstrip("@"), None
    return None, None


class _TmeParser(HTMLParser):
    """Разбор HTML виджета сообщений t.me (публичный предпросмотр)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[dict] = []
        self.cur: dict | None = None
        self.stack: list[tuple[str, set[str]]] = []
        self.text_depth: int | None = None
        self.fwd_depth: int | None = None
        self.owner_depth: int | None = None
        self.reply_depth: int | None = None
        self.channel_title_depth: int | None = None
        self.channel_title = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        if tag in _VOID:
            if tag == "br" and self.cur is not None and self.text_depth is not None:
                self.cur["text"].append("\n")
            return
        self.stack.append((tag, classes))
        depth = len(self.stack)
        if tag == "div" and "tgme_widget_message" in classes and a.get("data-post"):
            self.cur = {"post": a["data-post"], "text": [], "links": [], "fwd": None, "fwd_name": [], "reply": None,
                        "date": None, "owner": [], "depth": depth}
            self.messages.append(self.cur)
            return
        if "tgme_channel_info_header_title" in classes:
            self.channel_title_depth = depth
        if self.cur is None:
            return
        if tag == "a" and "tgme_widget_message_reply" in classes:
            self.cur["reply"] = a.get("href")
            self.reply_depth = depth
            return
        if self.reply_depth is not None:
            return  # превью сообщения, на которое отвечают, не является текстом поста
        if "tgme_widget_message_text" in classes and self.text_depth is None:
            self.text_depth = depth
        elif "tgme_widget_message_forwarded_from_name" in classes:
            self.fwd_depth = depth
            self.cur["fwd"] = a.get("href") or ""
        elif "tgme_widget_message_owner_name" in classes:
            self.owner_depth = depth
        elif tag == "time" and a.get("datetime") and not self.cur["date"]:
            self.cur["date"] = a["datetime"]
        if tag == "a" and self.text_depth is not None and a.get("href", "").startswith("http"):
            self.cur["links"].append(a["href"])

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        # Восстановление при несбалансированной разметке
        while self.stack:
            t, _ = self.stack.pop()
            depth = len(self.stack) + 1
            if self.text_depth is not None and depth <= self.text_depth:
                self.text_depth = None
            if self.fwd_depth is not None and depth <= self.fwd_depth:
                self.fwd_depth = None
            if self.owner_depth is not None and depth <= self.owner_depth:
                self.owner_depth = None
            if self.reply_depth is not None and depth <= self.reply_depth:
                self.reply_depth = None
            if self.channel_title_depth is not None and depth <= self.channel_title_depth:
                self.channel_title_depth = None
            if self.cur is not None and depth <= self.cur["depth"]:
                self.cur = None
            if t == tag:
                break

    def handle_data(self, data):
        if self.channel_title_depth is not None:
            self.channel_title += data
        if self.cur is None or self.reply_depth is not None:
            return
        if self.text_depth is not None:
            self.cur["text"].append(data)
        elif self.fwd_depth is not None:
            self.cur["fwd_name"].append(data)
        elif self.owner_depth is not None:
            self.cur["owner"].append(data)


def parse_tme_html(html: str) -> tuple[list[RawItem], str]:
    p = _TmeParser()
    p.feed(html or "")
    items = []
    for m in p.messages:
        text = re.sub(r"[ \t]+", " ", "".join(m["text"])).strip()
        if not text:
            continue
        channel, msg_id = m["post"].split("/", 1) if "/" in m["post"] else (m["post"], "")
        owner = "".join(m["owner"]).strip() or p.channel_title.strip() or channel
        fwd = None
        if m["fwd"] is not None:
            fwd = {"url": m["fwd"] or None, "name": "".join(m["fwd_name"]).strip(), "evidence": "telegram_forward_header"}
        reply = {"url": m["reply"], "evidence": "telegram_reply_header"} if m["reply"] else None
        items.append(RawItem(
            platform="Telegram",
            external_id=m["post"],
            url=f"https://t.me/{channel}/{msg_id}",
            text=text,
            published_at=m["date"],
            source_name=f"{owner} (@{channel})",
            source_kind="post",
            author_kind="public_source",
            links=[l for l in m["links"] if "t.me/" in l or "http" in l],
            forwarded_from=fwd,
            reply_to=reply,
            meta={"channel": channel},
        ))
    return items, p.channel_title.strip()


class TelegramWebConnector(Connector):
    name = "telegram_web"
    platform = "Telegram"
    title = "Telegram — публичный веб-предпросмотр каналов"
    access = "Публичная страница t.me/s/<канал> (без авторизации, только публичные каналы)"
    capabilities = ["channel_feed", "search_in_channel", "post_by_url", "forward_evidence"]
    limitations = [
        "Нет глобального поиска по всему Telegram — только указанные публичные каналы",
        "Поиск внутри канала выполняется по одному слову/фразе за запрос",
        "Комментарии к постам и закрытые чаты недоступны",
        "Публичные чаты (супергруппы) с открытым username доступны не всегда",
    ]
    docs_url = "https://telegram.org/tos"
    url_patterns = [r"(?:t\.me|telegram\.me)/"]

    def status(self) -> ConnectorStatus:
        if not self.settings.telegram_channels:
            return ConnectorStatus(
                "watchlist_needed",
                "Укажите публичные каналы в DOZOR_TELEGRAM_CHANNELS или ссылку на канал/пост в поиске",
            )
        return ConnectorStatus("connected", f"Каналов в списке наблюдения: {len(self.settings.telegram_channels)}")

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        channels: list[str] = []
        posts: list[tuple[str, str]] = []
        for url in plan.source_urls:
            ch, mid = parse_channel_ref(url)
            if ch and mid:
                posts.append((ch, mid))
            elif ch:
                channels.append(ch)
        if not plan.source_urls:
            channels = [c for c in (parse_channel_ref(x)[0] for x in self.settings.telegram_channels) if c]
        if not channels and not posts:
            res.notes.append("Не указаны публичные каналы: Telegram пропущен")
            return res
        budget = plan.max_requests
        for ch, mid in posts:
            if budget <= 0:
                break
            url = f"https://t.me/{ch}/{mid}?embed=1&mode=tme"
            await progress(f"Telegram: пост {ch}/{mid}")
            html, err = await get_text(client, url)
            budget -= 1
            res.requests += 1
            res.queries.append(f"{ch}/{mid}")
            if err:
                res.errors.append(f"{ch}/{mid}: {err}")
                continue
            items, _ = parse_tme_html(html or "")
            res.items.extend(items)
        queries = plan.expansion.platform_queries(max_queries=max(1, budget // max(1, len(channels))))
        for ch in channels:
            terms = [q for _, q in queries] or [None]
            for term in terms:
                if budget <= 0:
                    res.notes.append("Достигнут лимит запросов: часть вариантов запроса не отправлена")
                    break
                url = f"https://t.me/s/{ch}" + (f"?q={quote(term)}" if term else "")
                await progress(f"Telegram: @{ch}" + (f" — «{term}»" if term else ""))
                html, err = await get_text(client, url)
                budget -= 1
                res.requests += 1
                res.queries.append(f"@{ch}: {term or 'лента'}")
                if err:
                    res.errors.append(f"@{ch}: {err}")
                    break
                items, _ = parse_tme_html(html or "")
                res.items.extend(i for i in items if plan.in_range(i.published_at))
        return res


class TelegramMTProtoConnector(Connector):
    name = "telegram_mtproto"
    platform = "Telegram"
    title = "Telegram — официальный клиентский API (MTProto, Telethon)"
    access = "my.telegram.org: api_id/api_hash; только публичные каналы и чаты с открытым username"
    capabilities = ["channel_feed", "search_in_channel", "forward_evidence", "reply_evidence"]
    requires = ["TELEGRAM_API_ID", "TELEGRAM_API_HASH"]
    limitations = [
        "Требуется вход аккаунтом аналитика (python -m dozor telegram-login)",
        "Только публичные каналы/чаты с username; закрытые группы и переписка не читаются",
        "Соблюдайте Условия использования Telegram API и лимиты (FloodWait)",
    ]
    docs_url = "https://core.telegram.org/api/terms"

    def status(self) -> ConnectorStatus:
        base = super().status()
        if base.state != "connected":
            return base
        try:
            import telethon  # noqa: F401
        except ImportError:
            return ConnectorStatus("not_configured", "Не установлен пакет telethon (pip install telethon)")
        if not self.settings.telegram_channels:
            return ConnectorStatus("watchlist_needed", "Укажите публичные каналы в DOZOR_TELEGRAM_CHANNELS")
        return base

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        try:
            from telethon import TelegramClient  # type: ignore
        except ImportError:
            res.errors.append("telethon не установлен")
            return res
        session = self.env("TELEGRAM_SESSION") or str(self.settings.db_path.parent / "telegram.session")
        channels = [c for c in (parse_channel_ref(x)[0] for x in (plan.source_urls or self.settings.telegram_channels)) if c]
        queries = [q for _, q in plan.expansion.platform_queries(max_queries=plan.max_requests)] or [None]
        try:
            async with TelegramClient(session, int(self.env("TELEGRAM_API_ID")), self.env("TELEGRAM_API_HASH")) as tg:
                if not await tg.is_user_authorized():
                    res.errors.append("Сессия Telegram не авторизована: выполните python -m dozor telegram-login")
                    return res
                budget = plan.max_requests
                for ch in channels:
                    entity = await tg.get_entity(ch)
                    if not getattr(entity, "username", None):
                        res.notes.append(f"{ch}: не публичный источник — пропущен")
                        continue
                    for term in queries:
                        if budget <= 0:
                            break
                        budget -= 1
                        res.requests += 1
                        res.queries.append(f"@{ch}: {term or 'лента'}")
                        await progress(f"Telegram API: @{ch}" + (f" — «{term}»" if term else ""))
                        async for msg in tg.iter_messages(entity, search=term, limit=50, offset_date=plan.date_to):
                            if not msg.message:
                                continue
                            published = msg.date.isoformat() if msg.date else None
                            if plan.date_from and msg.date and msg.date < plan.date_from:
                                break
                            fwd = None
                            if msg.fwd_from is not None:
                                post = getattr(msg.fwd_from, "channel_post", None)
                                name = getattr(msg.fwd_from, "from_name", None) or ""
                                fwd = {"url": None, "name": name, "evidence": "telegram_forward_header",
                                       "channel_post": post}
                            reply = None
                            if msg.reply_to and getattr(msg.reply_to, "reply_to_msg_id", None):
                                reply = {"url": f"https://t.me/{entity.username}/{msg.reply_to.reply_to_msg_id}",
                                         "evidence": "telegram_reply_header"}
                            is_broadcast = bool(getattr(entity, "broadcast", False))
                            res.items.append(RawItem(
                                platform="Telegram",
                                external_id=f"{entity.username}/{msg.id}",
                                url=f"https://t.me/{entity.username}/{msg.id}",
                                text=msg.message,
                                published_at=published,
                                source_name=f"{getattr(entity, 'title', ch)} (@{entity.username})",
                                source_kind="post" if is_broadcast else "comment",
                                author_kind="public_source" if is_broadcast else "user_hidden",
                                forwarded_from=fwd,
                                reply_to=reply,
                            ))
        except Exception as exc:  # noqa: BLE001 — ошибки Telethon разнообразны
            res.errors.append(f"Telegram API: {exc.__class__.__name__}: {str(exc)[:200]}")
        return res
