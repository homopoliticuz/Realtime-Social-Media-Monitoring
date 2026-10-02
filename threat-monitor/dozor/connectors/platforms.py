"""Коннекторы к официальным API платформ.

Каждый коннектор запрашивает только общедоступные материалы и только те
поля, которые нужны для карточки материала. Поля о местоположении или
регионе аккаунта (например, ``region_code`` в TikTok Research API) не
запрашиваются: система не определяет местонахождение авторов.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from .base import Connector, ConnectorStatus, FetchResult, Progress, RawItem, SearchPlan, get_json, strip_html, to_iso


def _ts(dt: datetime | None) -> int | None:
    return int(dt.timestamp()) if dt else None


def _rfc3339(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


# ===================================================================== VK
class VKConnector(Connector):
    name = "vk"
    platform = "VK"
    title = "ВКонтакте — официальный API"
    access = "VK API (dev.vk.com): wall.get / wall.search / wall.getById по публичным сообществам; newsfeed.search"
    capabilities = ["keyword_search", "community_feed", "search_in_community", "post_by_url", "repost_evidence"]
    requires = ["VK_ACCESS_TOKEN"]
    limitations = [
        "newsfeed.search (поиск по всем публичным записям) требует ключ с соответствующими правами",
        "Закрытые сообщества и профили недоступны",
        "Имена владельцев личных страниц не сохраняются",
    ]
    docs_url = "https://dev.vk.com/ru/method"
    url_patterns = [r"vk\.com/", r"vkontakte\.ru/"]
    API = "https://api.vk.com/method/"
    VERSION = "5.199"

    async def _call(self, client, method: str, params: dict) -> tuple[dict | None, str | None]:
        params = {**params, "access_token": self.env("VK_ACCESS_TOKEN"), "v": self.VERSION}
        data, err = await get_json(client, self.API + method, params=params)
        if err:
            return None, err
        if isinstance(data, dict) and "error" in data:
            e = data["error"]
            return None, f"VK {e.get('error_code')}: {e.get('error_msg', '')[:200]}"
        return (data or {}).get("response"), None

    def _items(self, resp: dict | None, plan: SearchPlan) -> list[RawItem]:
        if not resp:
            return []
        posts = resp.get("items", resp if isinstance(resp, list) else [])
        groups = {-g["id"]: g.get("name", "") for g in resp.get("groups", [])} if isinstance(resp, dict) else {}
        out = []
        for p in posts:
            owner, pid = p.get("owner_id"), p.get("id")
            text = p.get("text") or ""
            copies = p.get("copy_history") or []
            repost = None
            if copies:
                c = copies[0]
                repost = {"url": f"https://vk.com/wall{c.get('owner_id')}_{c.get('id')}", "name": groups.get(c.get("owner_id"), ""),
                          "evidence": "vk_copy_history"}
                if not text:
                    text = c.get("text") or ""
            if not text:
                continue
            published = to_iso(p.get("date"))
            if not plan.in_range(published):
                continue
            is_group = isinstance(owner, int) and owner < 0
            out.append(RawItem(
                platform="VK",
                external_id=f"{owner}_{pid}",
                url=f"https://vk.com/wall{owner}_{pid}",
                text=text,
                published_at=published,
                source_name=(groups.get(owner) or f"Сообщество {owner}") if is_group else "Личная страница (имя не сохраняется)",
                author_kind="public_source" if is_group else "user_hidden",
                repost_of=repost,
                links=re.findall(r"https?://\S+", text),
            ))
        return out

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        budget = plan.max_requests
        domains, post_ids = [], []
        for url in plan.source_urls:
            m = re.search(r"wall(-?\d+_\d+)", url)
            if m:
                post_ids.append(m.group(1))
                continue
            m = re.search(r"vk\.com/([A-Za-z0-9_.]+)", url)
            if m:
                domains.append(m.group(1))
        if not plan.source_urls:
            domains = list(self.settings.vk_domains)
        if post_ids:
            await progress("VK: записи по ссылкам")
            resp, err = await self._call(client, "wall.getById", {"posts": ",".join(post_ids), "extended": 1})
            res.requests += 1
            budget -= 1
            res.queries.append("wall.getById")
            if err:
                res.errors.append(err)
            else:
                res.items += self._items(resp, plan)
        queries = plan.expansion.platform_queries(max_queries=max(1, budget))
        if domains:
            for d in domains:
                for _, term in (queries or [("und", None)]):
                    if budget <= 0:
                        break
                    method, params = ("wall.search", {"domain": d, "query": term, "count": 100, "extended": 1}) if term \
                        else ("wall.get", {"domain": d, "count": 100, "extended": 1})
                    await progress(f"VK: {d}" + (f" — «{term}»" if term else ""))
                    resp, err = await self._call(client, method, params)
                    res.requests += 1
                    budget -= 1
                    res.queries.append(f"{d}: {term or 'лента'}")
                    if err:
                        res.errors.append(f"{d}: {err}")
                        break
                    res.items += self._items(resp, plan)
        elif not post_ids:
            for _, term in queries:
                if budget <= 0:
                    break
                params = {"q": term, "count": 100, "extended": 1}
                if plan.date_from:
                    params["start_time"] = _ts(plan.date_from)
                if plan.date_to:
                    params["end_time"] = _ts(plan.date_to)
                await progress(f"VK newsfeed.search — «{term}»")
                resp, err = await self._call(client, "newsfeed.search", params)
                res.requests += 1
                budget -= 1
                res.queries.append(term)
                if err:
                    res.errors.append(f"newsfeed.search: {err}")
                    break
                res.items += self._items(resp, plan)
        return res


# ================================================================= YouTube
class YouTubeConnector(Connector):
    name = "youtube"
    platform = "YouTube"
    title = "YouTube — Data API v3"
    access = "Google Cloud API key: search.list, videos.list, commentThreads.list"
    capabilities = ["keyword_search", "comments", "video_by_url", "language_hint"]
    requires = ["YOUTUBE_API_KEY"]
    limitations = [
        "Квота API: search.list стоит 100 единиц из 10 000 в сутки по умолчанию",
        "Анализируются заголовки, описания и комментарии; аудио/видео без транскрипта не анализируется",
        "Идентификаторы авторов комментариев не сохраняются",
    ]
    docs_url = "https://developers.google.com/youtube/v3"
    url_patterns = [r"youtube\.com/watch", r"youtu\.be/", r"youtube\.com/shorts/"]
    API = "https://www.googleapis.com/youtube/v3/"

    async def _comments(self, client, vid: str, title: str, term: str | None, res: FetchResult, plan: SearchPlan) -> None:
        params = {"part": "snippet", "videoId": vid, "maxResults": 50, "textFormat": "plainText", "key": self.env("YOUTUBE_API_KEY")}
        if term:
            params["searchTerms"] = term
        data, err = await get_json(client, self.API + "commentThreads", params=params)
        res.requests += 1
        if err:
            res.errors.append(f"comments {vid}: {err}")
            return
        for it in (data or {}).get("items", []):
            sn = it.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            published = to_iso(sn.get("publishedAt"))
            if not plan.in_range(published):
                continue
            cid = it.get("id")
            res.items.append(RawItem(
                platform="YouTube", external_id=f"c:{cid}", url=f"https://www.youtube.com/watch?v={vid}&lc={cid}",
                text=sn.get("textOriginal") or sn.get("textDisplay") or "", published_at=published,
                source_name=f"Комментарии к видео «{title[:80]}»", source_kind="comment", author_kind="user_hidden",
                context=f"Видео: {title}", reply_to={"url": f"https://www.youtube.com/watch?v={vid}", "evidence": "youtube_comment_thread"},
            ))

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        key = self.env("YOUTUBE_API_KEY")
        budget = plan.max_requests
        video_ids = []
        for url in plan.source_urls:
            m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{6,})", url)
            if m:
                video_ids.append(m.group(1))
        found: list[tuple[str, str]] = []
        if video_ids:
            data, err = await get_json(client, self.API + "videos", params={"part": "snippet", "id": ",".join(video_ids), "key": key})
            res.requests += 1
            budget -= 1
            if err:
                res.errors.append(err)
            for it in (data or {}).get("items", []):
                found.append((it["id"], it["snippet"]))
        else:
            for lang, term in plan.expansion.platform_queries(max_queries=max(1, budget // 2)):
                if budget <= 0:
                    break
                params = {"part": "snippet", "type": "video", "q": term, "maxResults": 25, "key": key}
                if lang not in ("und",):
                    params["relevanceLanguage"] = lang
                if plan.date_from:
                    params["publishedAfter"] = _rfc3339(plan.date_from)
                if plan.date_to:
                    params["publishedBefore"] = _rfc3339(plan.date_to)
                await progress(f"YouTube: «{term}»")
                data, err = await get_json(client, self.API + "search", params=params)
                res.requests += 1
                budget -= 1
                res.queries.append(term)
                if err:
                    res.errors.append(err)
                    break
                for it in (data or {}).get("items", []):
                    vid = it.get("id", {}).get("videoId")
                    if vid:
                        found.append((vid, it.get("snippet", {})))
        seen = set()
        for vid, sn in found:
            if vid in seen:
                continue
            seen.add(vid)
            title = sn.get("title", "")
            res.items.append(RawItem(
                platform="YouTube", external_id=vid, url=f"https://www.youtube.com/watch?v={vid}",
                text=f"{title}\n{sn.get('description', '')}".strip(), published_at=to_iso(sn.get("publishedAt")),
                source_name=f"Канал «{sn.get('channelTitle', '')}»", source_kind="video_description",
            ))
        terms = [q for _, q in plan.expansion.platform_queries(max_queries=1)] or [None]
        for vid, sn in list(found)[: max(0, budget)]:
            await progress(f"YouTube: комментарии к {vid}")
            await self._comments(client, vid, sn.get("title", ""), terms[0], res, plan)
        return res


# ================================================================= Bluesky
class BlueskyConnector(Connector):
    name = "bluesky"
    platform = "Bluesky"
    title = "Bluesky — AT Protocol (app.bsky.feed.searchPosts)"
    access = "Открытый протокол; при необходимости — пароль приложения (BSKY_HANDLE / BSKY_APP_PASSWORD)"
    capabilities = ["keyword_search", "language_filter", "quote_evidence", "reply_evidence"]
    limitations = ["Публичный AppView может ограничивать анонимный поиск — тогда нужен пароль приложения"]
    docs_url = "https://docs.bsky.app/docs/api/app-bsky-feed-search-posts"
    url_patterns = [r"bsky\.app/profile/"]

    def status(self) -> ConnectorStatus:
        if self.env("BSKY_HANDLE") and self.env("BSKY_APP_PASSWORD"):
            return ConnectorStatus("connected", "Авторизованный доступ (пароль приложения)")
        return ConnectorStatus("connected", "Анонимный доступ к публичному AppView (может быть ограничен)")

    @staticmethod
    def _web_url(uri: str, handle: str | None) -> str:
        m = re.match(r"at://([^/]+)/app\.bsky\.feed\.post/([^/]+)", uri or "")
        if not m:
            return uri
        return f"https://bsky.app/profile/{handle or m.group(1)}/post/{m.group(2)}"

    def parse(self, data: dict, plan: SearchPlan) -> list[RawItem]:
        out = []
        for p in (data or {}).get("posts", []):
            rec = p.get("record", {})
            handle = p.get("author", {}).get("handle")
            published = to_iso(rec.get("createdAt"))
            if not plan.in_range(published):
                continue
            quote_of = None
            emb = p.get("embed") or {}
            et = emb.get("$type", "")
            qrec = None
            if et.startswith("app.bsky.embed.record#view"):
                qrec = emb.get("record")
            elif et.startswith("app.bsky.embed.recordWithMedia#view"):
                qrec = (emb.get("record") or {}).get("record")
            if qrec and qrec.get("uri"):
                quote_of = {"url": self._web_url(qrec["uri"], (qrec.get("author") or {}).get("handle")),
                            "evidence": "bluesky_embed_record"}
            reply = None
            parent = (rec.get("reply") or {}).get("parent", {}).get("uri")
            if parent:
                reply = {"url": self._web_url(parent, None), "evidence": "bluesky_reply_ref"}
            out.append(RawItem(
                platform="Bluesky", external_id=p.get("uri", ""), url=self._web_url(p.get("uri", ""), handle),
                text=rec.get("text", ""), published_at=published, source_name=f"@{handle}",
                lang_hint=(rec.get("langs") or [None])[0], quote_of=quote_of, reply_to=reply,
            ))
        return out

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        base, headers = "https://public.api.bsky.app", {}
        if self.env("BSKY_HANDLE") and self.env("BSKY_APP_PASSWORD"):
            sess, err = await get_json(client, "https://bsky.social/xrpc/com.atproto.server.createSession", method="POST",
                                       json={"identifier": self.env("BSKY_HANDLE"), "password": self.env("BSKY_APP_PASSWORD")})
            res.requests += 1
            if err:
                res.errors.append(f"Авторизация Bluesky: {err}")
                return res
            base, headers = "https://bsky.social", {"Authorization": f"Bearer {sess['accessJwt']}"}
        for lang, term in plan.expansion.platform_queries(max_queries=plan.max_requests):
            params = {"q": term, "limit": 50, "sort": "latest"}
            if plan.date_from:
                params["since"] = _rfc3339(plan.date_from)
            if plan.date_to:
                params["until"] = _rfc3339(plan.date_to)
            await progress(f"Bluesky: «{term}»")
            data, err = await get_json(client, base + "/xrpc/app.bsky.feed.searchPosts", params=params, headers=headers)
            res.requests += 1
            res.queries.append(term)
            if err:
                res.errors.append(err)
                break
            res.items += self.parse(data, plan)
        return res


# ================================================================ Mastodon
class MastodonConnector(Connector):
    name = "mastodon"
    platform = "Mastodon"
    title = "Mastodon / Fediverse — публичные хэштеги"
    access = "Публичный REST API инстансов: /api/v1/timelines/tag/<хэштег>"
    capabilities = ["hashtag_search", "repost_evidence"]
    limitations = [
        "Полнотекстовый поиск на большинстве серверов требует авторизации и охватывает только посты, авторы которых разрешили поиск",
        "Используется поиск по хэштегам на перечисленных серверах (DOZOR_MASTODON_INSTANCES)",
    ]
    docs_url = "https://docs.joinmastodon.org/methods/timelines/#tag"

    def status(self) -> ConnectorStatus:
        if not self.settings.mastodon_instances:
            return ConnectorStatus("watchlist_needed", "Укажите серверы в DOZOR_MASTODON_INSTANCES (например, mastodon.social)")
        return ConnectorStatus("connected", "Серверов: " + ", ".join(self.settings.mastodon_instances))

    def parse(self, statuses: list, instance: str, plan: SearchPlan) -> list[RawItem]:
        out = []
        for s in statuses or []:
            original = s.get("reblog")
            target = original or s
            text, links = strip_html(target.get("content", ""))
            published = to_iso(s.get("created_at"))
            if not text or not plan.in_range(published):
                continue
            out.append(RawItem(
                platform="Mastodon", external_id=f"{instance}:{s.get('id')}", url=s.get("url") or s.get("uri", ""),
                text=text, published_at=published, source_name=f"@{(s.get('account') or {}).get('acct', '')}@{instance}",
                lang_hint=target.get("language"), links=links,
                repost_of={"url": original.get("url"), "evidence": "mastodon_reblog"} if original else None,
            ))
        return out

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        tags = []
        for _, term in plan.expansion.platform_queries(max_queries=plan.max_requests * 3):
            tag = re.sub(r"[^\w]", "", term)
            if tag and tag not in tags and " " not in term:
                tags.append(tag)
        if not tags:
            res.notes.append("Нет однословных терминов для поиска по хэштегам")
            return res
        budget = plan.max_requests
        for inst in self.settings.mastodon_instances:
            for tag in tags:
                if budget <= 0:
                    break
                await progress(f"Mastodon {inst}: #{tag}")
                data, err = await get_json(client, f"https://{inst}/api/v1/timelines/tag/{quote(tag)}", params={"limit": 40})
                budget -= 1
                res.requests += 1
                res.queries.append(f"{inst} #{tag}")
                if err:
                    res.errors.append(f"{inst}: {err}")
                    break
                res.items += self.parse(data if isinstance(data, list) else [], inst, plan)
        return res


# ================================================================= Threads
class ThreadsConnector(Connector):
    name = "threads"
    platform = "Threads"
    title = "Threads — Threads API (keyword_search)"
    access = "Meta: токен с разрешением threads_keyword_search (после проверки приложения)"
    capabilities = ["keyword_search", "quote_flag"]
    requires = ["THREADS_ACCESS_TOKEN"]
    limitations = [
        "Без прохождения проверки приложения Meta поиск ограничен собственными публикациями",
        "Лимиты Meta на количество запросов поиска",
    ]
    docs_url = "https://developers.facebook.com/docs/threads/keyword-search"

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        for _, term in plan.expansion.platform_queries(max_queries=plan.max_requests):
            params = {"q": term, "search_type": "RECENT", "access_token": self.env("THREADS_ACCESS_TOKEN"),
                      "fields": "id,text,media_type,permalink,timestamp,username,is_quote_post"}
            if plan.date_from:
                params["since"] = _ts(plan.date_from)
            if plan.date_to:
                params["until"] = _ts(plan.date_to)
            await progress(f"Threads: «{term}»")
            data, err = await get_json(client, "https://graph.threads.net/v1.0/keyword_search", params=params)
            res.requests += 1
            res.queries.append(term)
            if err:
                res.errors.append(err)
                break
            for p in (data or {}).get("data", []):
                if not p.get("text"):
                    continue
                res.items.append(RawItem(
                    platform="Threads", external_id=p["id"], url=p.get("permalink", ""), text=p["text"],
                    published_at=to_iso(p.get("timestamp")), source_name=f"@{p.get('username', '')}",
                    meta={"is_quote_post": p.get("is_quote_post")},
                ))
        return res


# =============================================================== Instagram
class InstagramConnector(Connector):
    name = "instagram"
    platform = "Instagram"
    title = "Instagram — Graph API (поиск по хэштегам)"
    access = "Meta: профессиональный аккаунт + разрешение Instagram Public Content Access"
    capabilities = ["hashtag_search"]
    requires = ["INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID"]
    limitations = [
        "Только хэштеги (одно слово), не более 30 уникальных хэштегов за 7 дней",
        "recent_media возвращает публикации за последние 24 часа",
        "Авторы публикаций по хэштегу API не раскрываются и не сохраняются",
    ]
    docs_url = "https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-hashtag-search"
    API = "https://graph.facebook.com/v21.0/"

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        token, uid = self.env("INSTAGRAM_ACCESS_TOKEN"), self.env("INSTAGRAM_USER_ID")
        tags = []
        for _, term in plan.expansion.platform_queries(max_queries=plan.max_requests * 2):
            tag = re.sub(r"[^\w]", "", term)
            if tag and " " not in term and tag not in tags:
                tags.append(tag)
        for tag in tags[: max(1, plan.max_requests // 2)]:
            await progress(f"Instagram: #{tag}")
            data, err = await get_json(client, self.API + "ig_hashtag_search", params={"user_id": uid, "q": tag, "access_token": token})
            res.requests += 1
            res.queries.append(f"#{tag}")
            if err:
                res.errors.append(err)
                break
            for h in (data or {}).get("data", []):
                media, err2 = await get_json(client, self.API + f"{h['id']}/recent_media", params={
                    "user_id": uid, "fields": "id,caption,permalink,timestamp,media_type", "limit": 50, "access_token": token})
                res.requests += 1
                if err2:
                    res.errors.append(err2)
                    continue
                for p in (media or {}).get("data", []):
                    if p.get("caption") and plan.in_range(to_iso(p.get("timestamp"))):
                        res.items.append(RawItem(
                            platform="Instagram", external_id=p["id"], url=p.get("permalink", ""), text=p["caption"],
                            published_at=to_iso(p.get("timestamp")), source_name=f"Instagram (хэштег #{tag})",
                            author_kind="user_hidden",
                        ))
        return res


# ================================================================== TikTok
class TikTokResearchConnector(Connector):
    name = "tiktok"
    platform = "TikTok"
    title = "TikTok — Research API"
    access = "Одобренный исследовательский доступ TikTok (client key/secret)"
    capabilities = ["keyword_search", "comments", "voice_to_text"]
    requires = ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"]
    limitations = [
        "Доступ только после одобрения заявки исследователя/организации",
        "Окно поиска — не более 30 дней за запрос",
        "Поле region_code (регион аккаунта) намеренно не запрашивается",
    ]
    docs_url = "https://developers.tiktok.com/doc/research-api-specs-query-videos"

    def status(self) -> ConnectorStatus:
        base = super().status()
        if base.state == "not_configured":
            return ConnectorStatus("requires_approval", "Нужен одобренный доступ к TikTok Research API: " + base.message)
        return base

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        tok, err = await get_json(client, "https://open.tiktokapis.com/v2/oauth/token/", method="POST", data={
            "client_key": self.env("TIKTOK_CLIENT_KEY"), "client_secret": self.env("TIKTOK_CLIENT_SECRET"),
            "grant_type": "client_credentials"})
        res.requests += 1
        if err or not (tok or {}).get("access_token"):
            res.errors.append(f"Токен TikTok: {err or 'нет access_token'}")
            return res
        headers = {"Authorization": f"Bearer {tok['access_token']}"}
        end = plan.date_to or datetime.now(timezone.utc)
        start = max(plan.date_from or end - timedelta(days=7), end - timedelta(days=29))
        terms = [t for _, t in plan.expansion.platform_queries(max_queries=20)]
        for i in range(0, len(terms), 10):
            chunk = terms[i:i + 10]
            body = {"query": {"or": [{"operation": "IN", "field_name": "keyword", "field_values": chunk}]},
                    "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"), "max_count": 100}
            await progress("TikTok Research API: " + ", ".join(chunk[:3]) + ("…" if len(chunk) > 3 else ""))
            data, err = await get_json(
                client, "https://open.tiktokapis.com/v2/research/video/query/?fields=id,video_description,create_time,username,voice_to_text",
                method="POST", json=body, headers=headers)
            res.requests += 1
            res.queries.extend(chunk)
            if err:
                res.errors.append(err)
                break
            for v in ((data or {}).get("data") or {}).get("videos", []):
                text = "\n".join(x for x in (v.get("video_description"), v.get("voice_to_text")) if x)
                if not text:
                    continue
                res.items.append(RawItem(
                    platform="TikTok", external_id=str(v["id"]), url=f"https://www.tiktok.com/@{v.get('username', '')}/video/{v['id']}",
                    text=text, published_at=to_iso(v.get("create_time")), source_name=f"@{v.get('username', '')}",
                    source_kind="video_description",
                ))
            if res.requests >= plan.max_requests:
                break
        return res


# ================================================================== Twitch
class TwitchConnector(Connector):
    name = "twitch"
    platform = "Twitch"
    title = "Twitch — Helix API и публичный чат (IRC, только чтение)"
    access = "Helix: client credentials; чат: анонимное подключение только для чтения к публичным каналам"
    capabilities = ["channel_title_search", "live_chat_capture"]
    requires = ["TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"]
    limitations = [
        "История чата через API недоступна: чат фиксируется только в реальном времени в окне захвата",
        "Захват чата — только для каналов из DOZOR_TWITCH_CHANNELS и при DOZOR_TWITCH_CHAT_CAPTURE_SECONDS > 0",
        "Видео и голос стримов не анализируются (нужен транскрипт)",
    ]
    docs_url = "https://dev.twitch.tv/docs/api/reference/#search-channels"

    async def _chat(self, channel: str, seconds: int, plan: SearchPlan, res: FetchResult) -> None:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("irc.chat.twitch.tv", 6667), timeout=10)
        except (OSError, asyncio.TimeoutError) as exc:
            res.errors.append(f"Чат {channel}: {exc.__class__.__name__}")
            return
        nick = f"justinfan{int(time.time()) % 100000}"
        writer.write(f"NICK {nick}\r\nJOIN #{channel.lower()}\r\n".encode())
        await writer.drain()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            msg = line.decode("utf-8", "ignore").strip()
            if msg.startswith("PING"):
                writer.write(b"PONG :tmi.twitch.tv\r\n")
                await writer.drain()
                continue
            m = re.match(r":[^ ]+ PRIVMSG #\S+ :(.*)", msg)
            if m:
                text = m.group(1)
                now = datetime.now(timezone.utc).isoformat()
                ext = hashlib.sha1(f"{channel}|{now}|{text}".encode()).hexdigest()[:16]
                res.items.append(RawItem(
                    platform="Twitch", external_id=f"chat:{ext}", url=f"https://www.twitch.tv/{channel}", text=text,
                    published_at=now, source_name=f"Чат канала {channel}", source_kind="chat_message", author_kind="user_hidden",
                ))
        writer.close()

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        tok, err = await get_json(client, "https://id.twitch.tv/oauth2/token", method="POST", params={
            "client_id": self.env("TWITCH_CLIENT_ID"), "client_secret": self.env("TWITCH_CLIENT_SECRET"),
            "grant_type": "client_credentials"})
        res.requests += 1
        if err:
            res.errors.append(f"Токен Twitch: {err}")
            return res
        headers = {"Client-Id": self.env("TWITCH_CLIENT_ID"), "Authorization": f"Bearer {tok['access_token']}"}
        for _, term in plan.expansion.platform_queries(max_queries=plan.max_requests):
            await progress(f"Twitch: каналы по «{term}»")
            data, err = await get_json(client, "https://api.twitch.tv/helix/search/channels", params={"query": term, "first": 20},
                                       headers=headers)
            res.requests += 1
            res.queries.append(term)
            if err:
                res.errors.append(err)
                break
            for ch in (data or {}).get("data", []):
                title = ch.get("title") or ""
                if not title:
                    continue
                ext = hashlib.sha1(f"{ch.get('id')}|{title}".encode()).hexdigest()[:16]
                res.items.append(RawItem(
                    platform="Twitch", external_id=f"title:{ext}", url=f"https://www.twitch.tv/{ch.get('broadcaster_login')}",
                    text=title, published_at=to_iso(ch.get("started_at")) or datetime.now(timezone.utc).isoformat(),
                    source_name=f"Канал {ch.get('display_name')}", source_kind="stream_title",
                ))
        seconds = self.settings.twitch_chat_capture_seconds
        if seconds > 0 and self.settings.twitch_channels:
            for ch in self.settings.twitch_channels:
                await progress(f"Twitch: захват публичного чата {ch} ({seconds} с)")
                await self._chat(ch, seconds, plan, res)
        return res


# ================================================================= Discord
class DiscordConnector(Connector):
    name = "discord"
    platform = "Discord"
    title = "Discord — бот на серверах, где он установлен с согласия администрации"
    access = "Bot token; читаются только каналы, к которым боту выдан доступ (DOZOR_DISCORD_CHANNEL_IDS)"
    capabilities = ["channel_messages", "reply_evidence", "forward_evidence"]
    requires = ["DISCORD_BOT_TOKEN"]
    limitations = [
        "Публичного поиска по Discord нет: только серверы, куда бот добавлен администраторами",
        "Для чтения текста нужен привилегированный intent Message Content",
        "Личные сообщения и закрытые каналы без выданного доступа не читаются",
    ]
    docs_url = "https://discord.com/developers/docs/resources/message#get-channel-messages"

    def status(self) -> ConnectorStatus:
        base = super().status()
        if base.state == "connected" and not self.settings.discord_channel_ids:
            return ConnectorStatus("watchlist_needed", "Укажите каналы в DOZOR_DISCORD_CHANNEL_IDS")
        return base

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        headers = {"Authorization": f"Bot {self.env('DISCORD_BOT_TOKEN')}"}
        api = "https://discord.com/api/v10"
        for cid in self.settings.discord_channel_ids[: plan.max_requests]:
            await progress(f"Discord: канал {cid}")
            ch, err = await get_json(client, f"{api}/channels/{cid}", headers=headers)
            res.requests += 1
            if err:
                res.errors.append(f"{cid}: {err}")
                continue
            msgs, err = await get_json(client, f"{api}/channels/{cid}/messages", params={"limit": 100}, headers=headers)
            res.requests += 1
            res.queries.append(f"#{ch.get('name', cid)}")
            if err:
                res.errors.append(f"{cid}: {err}")
                continue
            guild = ch.get("guild_id", "@me")
            for m in msgs or []:
                if not m.get("content"):
                    continue
                ref = m.get("message_reference") or {}
                ref_url = None
                if ref.get("message_id"):
                    ref_url = f"https://discord.com/channels/{ref.get('guild_id', guild)}/{ref.get('channel_id', cid)}/{ref['message_id']}"
                forwarded = ref_url and ref.get("type") == 1
                res.items.append(RawItem(
                    platform="Discord", external_id=f"{cid}:{m['id']}", url=f"https://discord.com/channels/{guild}/{cid}/{m['id']}",
                    text=m["content"], published_at=to_iso(m.get("timestamp")), source_name=f"Discord #{ch.get('name', cid)}",
                    source_kind="message", author_kind="user_hidden",
                    forwarded_from={"url": ref_url, "evidence": "discord_forward_reference"} if forwarded else None,
                    reply_to={"url": ref_url, "evidence": "discord_message_reference"} if ref_url and not forwarded else None,
                ))
        return res


# ====================================================================== X
class XConnector(Connector):
    name = "x"
    platform = "X"
    title = "X (Twitter) — API v2 recent search"
    access = "Платный тариф X API: Bearer token"
    capabilities = ["keyword_search", "language_filter", "repost_evidence", "quote_evidence", "reply_evidence"]
    requires = ["X_BEARER_TOKEN"]
    limitations = ["recent search охватывает последние 7 дней", "Объём ограничен тарифом"]
    docs_url = "https://docs.x.com/x-api/posts/search/introduction"
    url_patterns = [r"(?:twitter|x)\.com/\w+/status/"]

    async def fetch(self, plan: SearchPlan, client: httpx.AsyncClient, progress: Progress) -> FetchResult:
        res = FetchResult()
        headers = {"Authorization": f"Bearer {self.env('X_BEARER_TOKEN')}"}
        for lang, term in plan.expansion.platform_queries(max_queries=plan.max_requests):
            q = f'"{term}"' if " " in term else term
            if lang not in ("und",):
                q += f" lang:{lang}"
            params = {"query": q, "max_results": 100, "tweet.fields": "created_at,lang,referenced_tweets,entities",
                      "expansions": "author_id", "user.fields": "username"}
            if plan.date_from:
                params["start_time"] = _rfc3339(max(plan.date_from, datetime.now(timezone.utc) - timedelta(days=6, hours=23)))
            await progress(f"X: «{term}»")
            data, err = await get_json(client, "https://api.x.com/2/tweets/search/recent", params=params, headers=headers)
            res.requests += 1
            res.queries.append(q)
            if err:
                res.errors.append(err)
                break
            users = {u["id"]: u.get("username", "") for u in (data or {}).get("includes", {}).get("users", [])}
            for t in (data or {}).get("data", []):
                refs = {r["type"]: f"https://x.com/i/web/status/{r['id']}" for r in t.get("referenced_tweets", [])}
                res.items.append(RawItem(
                    platform="X", external_id=t["id"], url=f"https://x.com/i/web/status/{t['id']}", text=t.get("text", ""),
                    published_at=to_iso(t.get("created_at")), source_name=f"@{users.get(t.get('author_id'), '')}",
                    lang_hint=t.get("lang"),
                    repost_of={"url": refs["retweeted"], "evidence": "x_referenced_tweets"} if "retweeted" in refs else None,
                    quote_of={"url": refs["quoted"], "evidence": "x_referenced_tweets"} if "quoted" in refs else None,
                    reply_to={"url": refs["replied_to"], "evidence": "x_referenced_tweets"} if "replied_to" in refs else None,
                ))
        return res
