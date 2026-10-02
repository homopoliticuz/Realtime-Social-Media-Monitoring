"""Открытый веб: RSS/Atom-ленты и отдельные публичные страницы по ссылке."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .base import Connector, ConnectorStatus, FetchResult, Progress, RawItem, SearchPlan, get_text, strip_html, to_iso

_ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(xml_text: str, feed_url: str) -> tuple[list[RawItem], str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], ""
    items: list[RawItem] = []
    title = ""
    channel = root.find("channel")
    if channel is not None:  # RSS 2.0
        title = (channel.findtext("title") or "").strip()
        for it in channel.findall("item"):
            link = (it.findtext("link") or "").strip()
            text, links = strip_html(f"{it.findtext('title') or ''}\n{it.findtext('description') or ''}")
            ext = it.findtext("guid") or link or hashlib.sha1(text.encode()).hexdigest()
            items.append(RawItem(platform="Веб/RSS", external_id=ext, url=link, text=text,
                                 published_at=to_iso(it.findtext("pubDate")), source_name=title or feed_url,
                                 source_kind="article", links=links))
    elif root.tag == f"{_ATOM}feed":
        title = (root.findtext(f"{_ATOM}title") or "").strip()
        for e in root.findall(f"{_ATOM}entry"):
            link_el = e.find(f"{_ATOM}link")
            link = link_el.get("href", "") if link_el is not None else ""
            body = e.findtext(f"{_ATOM}summary") or e.findtext(f"{_ATOM}content") or ""
            text, links = strip_html(f"{e.findtext(f'{_ATOM}title') or ''}\n{body}")
            items.append(RawItem(platform="Веб/RSS", external_id=e.findtext(f"{_ATOM}id") or link, url=link, text=text,
                                 published_at=to_iso(e.findtext(f"{_ATOM}published") or e.findtext(f"{_ATOM}updated")),
                                 source_name=title or feed_url, source_kind="article", links=links))
    return items, title


class RSSConnector(Connector):
    name = "rss"
    platform = "Веб/RSS"
    title = "RSS/Atom-ленты (СМИ, блоги, мосты к публичным каналам)"
    access = "Публичные ленты из DOZOR_RSS_FEEDS"
    capabilities = ["feed", "local_filter"]
    limitations = ["Ленты содержат только последние записи", "Фильтрация по запросу выполняется локально"]
    docs_url = "https://www.rssboard.org/rss-specification"

    def status(self) -> ConnectorStatus:
        if not self.settings.rss_feeds:
            return ConnectorStatus("watchlist_needed", "Укажите ленты в DOZOR_RSS_FEEDS")
        return ConnectorStatus("connected", f"Лент: {len(self.settings.rss_feeds)}")

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        for url in self.settings.rss_feeds[: plan.max_requests]:
            await progress(f"RSS: {urlparse(url).netloc}")
            xml_text, err = await get_text(client, url)
            res.requests += 1
            res.queries.append(url)
            if err:
                res.errors.append(f"{url}: {err}")
                continue
            items, _ = parse_feed(xml_text or "", url)
            res.items += [i for i in items if plan.in_range(i.published_at)]
        return res


class WebPageConnector(Connector):
    name = "web_url"
    platform = "Веб-страница"
    title = "Публичная веб-страница по ссылке"
    access = "HTTP GET с соблюдением robots.txt; только страницы, открытые без авторизации"
    capabilities = ["url_lookup"]
    limitations = ["Страницы, требующие входа или JavaScript, не извлекаются", "Сохраняется только фрагмент текста"]
    docs_url = "https://www.rfc-editor.org/rfc/rfc9309"
    url_patterns = [r"^https?://"]

    async def _allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        parts = urlparse(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        text, err = await get_text(client, robots_url)
        if err or text is None:
            return True
        rp = RobotFileParser()
        rp.parse(text.splitlines())
        return rp.can_fetch(self.settings.user_agent, url)

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        for url in plan.source_urls[: plan.max_requests]:
            await progress(f"Веб: {urlparse(url).netloc}")
            if not await self._allowed(client, url):
                res.notes.append(f"{url}: запрещено robots.txt — не загружается")
                continue
            html, err = await get_text(client, url)
            res.requests += 1
            res.queries.append(url)
            if err:
                res.errors.append(f"{url}: {err}")
                continue
            text, links = strip_html(html or "")
            if not text:
                continue
            res.items.append(RawItem(platform="Веб-страница", external_id=url, url=url, text=text[:20000], published_at=None,
                                     source_name=urlparse(url).netloc, source_kind="article", links=links[:50]))
        return res
