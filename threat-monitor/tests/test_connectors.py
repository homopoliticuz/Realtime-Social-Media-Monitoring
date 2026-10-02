import asyncio
from pathlib import Path

import httpx

from dozor.connectors.base import SearchPlan
from dozor.connectors.platforms import BlueskyConnector, VKConnector
from dozor.connectors.registry import build_connectors
from dozor.connectors.telegram import TelegramWebConnector, parse_channel_ref, parse_tme_html
from dozor.connectors.web import parse_feed
from dozor.text.expansion import expand_query

FIXTURES = Path(__file__).parent / "fixtures"


async def _noop(_msg):
    return None


def test_tme_parser_extracts_text_forward_reply_and_links():
    items, title = parse_tme_html((FIXTURES / "tme_channel.html").read_text(encoding="utf-8"))
    assert title == "Тестовый канал"
    assert len(items) == 2
    first, second = items
    assert first.external_id == "testchan/101"
    assert first.url == "https://t.me/testchan/101"
    assert "Завтра взорву здание\nадминистрации." in first.text
    assert first.published_at == "2026-09-30T10:00:00+00:00"
    assert first.forwarded_from == {"url": "https://t.me/origin/55", "name": "Origin Channel", "evidence": "telegram_forward_header"}
    assert "https://t.me/other/7" in first.links
    assert second.reply_to["url"] == "https://t.me/testchan/101"
    # Текст цитаты ответа не смешивается с текстом сообщения
    assert second.text.startswith("Это недопустимо")


def test_channel_ref_parsing():
    assert parse_channel_ref("https://t.me/s/durov") == ("durov", None)
    assert parse_channel_ref("t.me/durov/123") == ("durov", "123")
    assert parse_channel_ref("@durov") == ("durov", None)


def test_telegram_web_fetch_with_mock_transport(settings):
    html = (FIXTURES / "tme_channel.html").read_text(encoding="utf-8")
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(200, text=html)

    conn = TelegramWebConnector(settings)
    plan = SearchPlan(expansion=expand_query("взорвать", ["ru"]), source_urls=["https://t.me/testchan"], max_requests=2)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await conn.fetch(plan, client, _noop)

    res = asyncio.run(run())
    assert res.items and res.requests >= 1
    assert seen[0].startswith("https://t.me/s/testchan?q=")


def test_vk_parsing_and_repost_evidence(settings, monkeypatch):
    monkeypatch.setenv("VK_ACCESS_TOKEN", "x")
    payload = {"response": {"items": [
        {"id": 5, "owner_id": -100, "date": 1790000000, "text": "", "copy_history": [{"id": 1, "owner_id": -200, "text": "Смерть предателям!"}]},
        {"id": 6, "owner_id": 300, "date": 1790000100, "text": "Личный пост"},
    ], "groups": [{"id": 100, "name": "Группа А"}, {"id": 200, "name": "Группа Б"}]}}

    def handler(request):
        assert "access_token=x" in str(request.url)
        return httpx.Response(200, json=payload)

    conn = VKConnector(settings)
    assert conn.status().state == "connected"
    plan = SearchPlan(expansion=expand_query("смерть", ["ru"]), max_requests=1)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await conn.fetch(plan, client, _noop)

    res = asyncio.run(run())
    repost = res.items[0]
    assert repost.repost_of["url"] == "https://vk.com/wall-200_1"
    assert repost.repost_of["evidence"] == "vk_copy_history"
    assert repost.text == "Смерть предателям!"
    personal = res.items[1]
    assert personal.author_kind == "user_hidden"
    assert "не сохраняется" in personal.source_name


def test_vk_api_error_is_reported(settings, monkeypatch):
    monkeypatch.setenv("VK_ACCESS_TOKEN", "bad")

    def handler(request):
        return httpx.Response(200, json={"error": {"error_code": 5, "error_msg": "User authorization failed"}})

    conn = VKConnector(settings)
    plan = SearchPlan(expansion=expand_query("убить", ["ru"]), max_requests=1)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await conn.fetch(plan, client, _noop)

    res = asyncio.run(run())
    assert res.errors and "VK 5" in res.errors[0]
    assert not res.items


def test_bluesky_quote_and_reply_evidence(settings):
    data = {"posts": [{
        "uri": "at://did:plc:abc/app.bsky.feed.post/3k1", "author": {"handle": "user.bsky.social"},
        "record": {"text": "I will kill them all", "createdAt": "2026-09-30T10:00:00Z", "langs": ["en"],
                   "reply": {"parent": {"uri": "at://did:plc:xyz/app.bsky.feed.post/9zz"}}},
        "embed": {"$type": "app.bsky.embed.record#view", "record": {"uri": "at://did:plc:def/app.bsky.feed.post/7q", "author": {"handle": "other.bsky.social"}}},
    }]}
    items = BlueskyConnector(settings).parse(data, SearchPlan(expansion=expand_query("kill", ["en"])))
    assert items[0].url == "https://bsky.app/profile/user.bsky.social/post/3k1"
    assert items[0].quote_of["url"] == "https://bsky.app/profile/other.bsky.social/post/7q"
    assert items[0].reply_to["url"] == "https://bsky.app/profile/did:plc:xyz/post/9zz"


def test_rss_parsing():
    xml = """<?xml version="1.0"?><rss><channel><title>Новости</title>
    <item><title>Задержан подозреваемый</title><link>https://news.example/1</link>
    <description>&lt;p&gt;Полиция сообщила…&lt;/p&gt;</description><pubDate>Tue, 30 Sep 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>"""
    items, title = parse_feed(xml, "https://news.example/rss")
    assert title == "Новости"
    assert items[0].url == "https://news.example/1"
    assert "Полиция сообщила" in items[0].text
    assert items[0].published_at.startswith("2026-09-30")


def test_statuses_are_honest_without_keys(settings):
    conns = build_connectors(settings)
    assert conns["signal"].status().state == "unavailable"
    assert conns["max"].status().state == "requires_approval"
    assert conns["facebook"].status().state == "requires_approval"
    assert conns["tiktok"].status().state == "requires_approval"
    assert conns["vk"].status().state == "not_configured"
    assert conns["telegram_web"].status().state == "watchlist_needed"
    assert conns["demo"].status().state == "demo"
    assert not conns["signal"].usable
    # Платформы из ТЗ присутствуют в реестре
    platforms = {c.platform for c in conns.values()}
    for p in ("Telegram", "Instagram", "Discord", "Twitch", "TikTok", "Facebook", "Threads", "Одноклассники", "VK", "Signal", "MAX"):
        assert p in platforms


def test_tiktok_does_not_request_author_region(settings):
    from dozor.connectors import platforms

    src = Path(platforms.__file__).read_text(encoding="utf-8")
    fields_line = [l for l in src.splitlines() if "research/video/query" in l][0]
    assert "region_code" not in fields_line
