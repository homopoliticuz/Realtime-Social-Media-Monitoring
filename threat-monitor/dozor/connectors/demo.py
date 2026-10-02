"""Коннектор демонстрационных данных (локальный набор, без сети).

Все материалы помечаются ``is_demo`` и показываются с плашкой «ДЕМО».
"""

from __future__ import annotations

import httpx

from ..data.demo import DEMO_MATERIALS
from .base import Connector, ConnectorStatus, FetchResult, Progress, RawItem, SearchPlan


def demo_items() -> list[RawItem]:
    by_key = {m["key"]: m for m in DEMO_MATERIALS}
    items = []
    for m in DEMO_MATERIALS:
        fwd = repost = None
        if m.get("forwarded_from"):
            src = by_key[m["forwarded_from"]]
            fwd = {"url": src["url"], "name": src["source_name"], "evidence": "demo_forward_header"}
        if m.get("reposted_from"):
            src = by_key[m["reposted_from"]]
            repost = {"url": src["url"], "name": src["source_name"], "evidence": "demo_copy_history"}
        links = [by_key[k]["url"] for k in m.get("links_to", [])]
        items.append(RawItem(
            platform=m["platform"],
            external_id=f"demo:{m['key']}",
            url=m["url"],
            text=m["text"],
            published_at=m["published_at"],
            source_name=m["source_name"],
            source_kind=m.get("source_kind", "post"),
            author_kind="user_hidden" if m.get("source_kind") in ("comment", "chat_message", "message") else "public_source",
            context=m.get("context"),
            links=links,
            forwarded_from=fwd,
            repost_of=repost,
            is_demo=True,
            provenance="demo",
            meta={"demo_key": m["key"]},
        ))
    return items


class DemoConnector(Connector):
    name = "demo"
    platform = "Демо"
    title = "Демонстрационные данные (вымышленные материалы)"
    access = "Локальный набор, сеть не используется"
    capabilities = ["demo"]
    limitations = ["Материалы, каналы, люди и события вымышлены"]

    def status(self) -> ConnectorStatus:
        return ConnectorStatus("demo", f"Локальный набор: {len(DEMO_MATERIALS)} вымышленных материалов")

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        await progress("Демо: чтение локального набора")
        items = [i for i in demo_items() if plan.in_range(i.published_at)]
        if plan.source_urls:
            items = [i for i in items if any(u in (i.url, i.url.rsplit("/", 1)[0]) or i.url.startswith(u) for u in plan.source_urls)]
        return FetchResult(items=items, requests=0, queries=["локальный набор"], notes=["Демонстрационные данные"])

    def handles_url(self, url: str) -> bool:
        return "demo.invalid" in url
