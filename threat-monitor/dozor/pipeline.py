"""Конвейер поиска и анализа.

Этапы, которые видит пользователь (события публикуются по мере выполнения):

1. validate     — проверка запроса политикой (телефоны, e-mail, ФИО, цель поиска)
2. expand       — многоязычное расширение запроса и транслитерация
3. plan         — какие источники подключены и будут опрошены, какие — нет и почему
4. fetch        — опрос каждого подключённого источника (параллельно)
5. normalize    — нормализация, снятие обфускации, определение языка
6. dedup        — точные дубли и похожие тексты
7. filter       — локальная проверка совпадения, периода, языка, страны
8. classify     — оценка содержания, приоритет, тяжесть, уверенность
9. propagation  — подтверждённые связи между материалами
10. store       — сохранение карточек
11. done        — итог и ограничения охвата
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import httpx

from . import audit, repository as repo
from .analysis import dedup
from .analysis.classifier import assess_features, merge_llm
from .analysis.features import extract
from .analysis.taxonomy import CATEGORIES
from .connectors.base import STATES, Connector, RawItem, SearchPlan
from .db import Database, dumps, now_iso
from .text.expansion import Expansion, expand_query
from .text.langid import LANGUAGES, PARALLEL_DEFAULT, REGIONAL, LangGuess, detect
from .text.pii import QueryPolicyError, check_query, mask_pii, person_name_notice

log = logging.getLogger(__name__)

STAGES = {
    "validate": "Проверка запроса",
    "expand": "Многоязычное расширение запроса",
    "plan": "План опроса источников",
    "fetch": "Опрос источников",
    "normalize": "Нормализация и определение языка",
    "dedup": "Устранение дублей",
    "filter": "Проверка совпадений и фильтров",
    "classify": "Оценка содержания",
    "propagation": "Карта распространения",
    "store": "Сохранение карточек",
    "done": "Готово",
}

LLM_CALL_LIMIT = 50


@dataclass
class SearchParams:
    query: str = ""
    topics: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=lambda: PARALLEL_DEFAULT + REGIONAL)
    mode: str = "all"
    date_from: str | None = None
    date_to: str | None = None
    countries: list[str] = field(default_factory=list)
    material_langs: list[str] = field(default_factory=list)
    source_url: str = ""
    connectors: list[str] = field(default_factory=list)
    include_demo: bool = False
    purpose: str = ""

    @property
    def source_urls(self) -> list[str]:
        return [u for u in re.split(r"[\s,]+", self.source_url or "") if u]


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    p = urlparse(url)
    query = p.query
    if "t.me" in p.netloc:
        query = ""
    path = p.path.rstrip("/") or "/"
    path = re.sub(r"^/s/", "/", path) if "t.me" in p.netloc else path
    return urlunparse((p.scheme.lower(), p.netloc.lower().removeprefix("www."), path, "", query, ""))


def _parse_date(value: str | None, end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value + ("T23:59:59" if end and len(value) == 10 else ""))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def make_excerpt(text: str, surfaces: list[str], limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    low = text.casefold()
    pos = -1
    for s in surfaces:
        pos = low.find(s.casefold())
        if pos >= 0:
            break
    if pos < 0:
        return text[:limit].rstrip() + " …"
    start = max(0, pos - limit // 3)
    end = min(len(text), start + limit)
    return ("… " if start else "") + text[start:end].strip() + (" …" if end < len(text) else "")


class SearchJob:
    def __init__(self, params: SearchParams, user) -> None:
        self.id = "S-" + uuid.uuid4().hex[:10].upper()
        self.params = params
        self.user = user
        self.events: list[dict] = []
        self.status = "running"
        self.stats: dict = {}
        self.cond = asyncio.Condition()
        self.started = time.monotonic()

    async def emit(self, stage: str, status: str, message: str, **extra) -> None:
        event = {
            "seq": len(self.events) + 1,
            "ts": now_iso(),
            "elapsed": round(time.monotonic() - self.started, 2),
            "stage": stage,
            "stage_label": STAGES.get(stage, stage),
            "status": status,
            "message": message,
            **extra,
        }
        async with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    async def finish(self, status: str) -> None:
        async with self.cond:
            self.status = status
            self.cond.notify_all()


class Pipeline:
    def __init__(self, db: Database, settings, connectors: dict[str, Connector], analyzer=None, transport=None) -> None:
        self.db = db
        self.settings = settings
        self.connectors = connectors
        self.analyzer = analyzer
        self.transport = transport  # для тестов: httpx.MockTransport
        self.jobs: dict[str, SearchJob] = {}

    # ------------------------------------------------------------------ API
    def validate(self, params: SearchParams) -> list[str]:
        """Возвращает предупреждения; при нарушении политики — QueryPolicyError."""
        check_query(params.query)
        check_query(params.source_url)
        if not params.query.strip() and not params.topics and not params.source_urls:
            raise QueryPolicyError("Укажите запрос, тему или ссылку на публичный материал/канал.")
        if len(params.purpose.strip()) < 5:
            raise QueryPolicyError("Укажите цель поиска (не менее 5 символов): она фиксируется в журнале действий.")
        warnings = []
        notice = person_name_notice(params.query)
        if notice:
            warnings.append(notice)
        return warnings

    def start(self, params: SearchParams, user) -> SearchJob:
        job = SearchJob(params, user)
        self.jobs[job.id] = job
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO searches(id, user, created_at, purpose, params, status) VALUES (?, ?, ?, ?, ?, 'running')",
                (job.id, user.username, now_iso(), params.purpose, dumps(asdict(params))),
            )
        job.task = asyncio.create_task(self._run_safe(job))
        return job

    async def _run_safe(self, job: SearchJob) -> None:
        try:
            await self.run(job)
        except Exception as exc:  # noqa: BLE001 — сбой не должен оставлять задачу «висящей»
            log.exception("search failed")
            await job.emit("done", "error", f"Поиск прерван ошибкой: {exc.__class__.__name__}: {exc}")
            await job.finish("error")
            self._persist(job, "error")

    # ------------------------------------------------------------------ ход
    async def run(self, job: SearchJob) -> None:
        p = job.params
        await job.emit("validate", "start", "Проверка запроса политикой использования")
        warnings = self.validate(p)
        for w in warnings:
            await job.emit("validate", "warn", w)
        await job.emit("validate", "done", f"Цель поиска зафиксирована: «{p.purpose[:120]}»")

        # 2. Расширение
        await job.emit("expand", "start", "Сопоставление запроса с многоязычным лексиконом")
        translator = self.analyzer.translate_terms if self.analyzer else None
        expansion = await asyncio.to_thread(
            expand_query, p.query, p.languages, p.topics, p.mode,
            (lambda t, l: translator(t, l)) if translator else None,
        )
        for w in expansion.warnings:
            await job.emit("expand", "warn", w)
        langs = ", ".join(LANGUAGES.get(l, l) for l in expansion.languages)
        await job.emit(
            "expand", "done",
            f"Терминов запроса: {len(expansion.terms)}; вариантов написания: {expansion.variant_count}; языки: {langs}",
            expansion=expansion.to_dict(),
        )

        # 3. План
        await job.emit("plan", "start", "Определение доступных источников")
        selected = self._select_connectors(p)
        plan_rows = []
        active: list[Connector] = []
        for c in self.connectors.values():
            st = c.status()
            row = {"connector": c.name, "platform": c.platform, "state": st.state, "state_label": STATES[st.state], "message": st.message}
            if c.name not in selected:
                row["action"] = "не выбран"
            elif c.name == "manual_import":
                row["action"] = "не участвует в поиске (используйте импорт)"
            elif not c.usable:
                row["action"] = "пропущен: " + st.message
            elif st.state == "watchlist_needed" and not any(c.handles_url(u) for u in p.source_urls):
                row["action"] = "пропущен: " + st.message
            elif p.source_urls and not any(c.handles_url(u) for u in p.source_urls) and c.name not in ("demo",):
                row["action"] = "пропущен: не относится к указанным ссылкам"
            elif c.name == "web_url" and not p.source_urls:
                row["action"] = "пропущен: нет ссылки на страницу"
            else:
                row["action"] = "будет опрошен"
                active.append(c)
            plan_rows.append(row)
        # Ссылки обрабатывает специализированный коннектор, общий веб — только «прочие»
        if p.source_urls:
            specific = [c for c in active if c.name not in ("web_url", "demo")]
            if any(any(c.handles_url(u) for c in specific) for u in p.source_urls):
                leftovers = [u for u in p.source_urls if not any(c.handles_url(u) for c in specific)]
                if not leftovers:
                    active = [c for c in active if c.name != "web_url"]
                    for row in plan_rows:
                        if row["connector"] == "web_url" and row["action"] == "будет опрошен":
                            row["action"] = "пропущен: ссылки обработает профильный коннектор"
        await job.emit(
            "plan", "done",
            f"Будет опрошено источников: {len(active)} из {len(self.connectors)}",
            plan=plan_rows,
            active=[c.name for c in active],
        )
        if not active:
            await job.emit("fetch", "warn", "Нет подключённых источников для этого запроса. Подключите источники или включите демо-данные.")

        # 4. Опрос
        plan = SearchPlan(
            expansion=expansion,
            date_from=_parse_date(p.date_from),
            date_to=_parse_date(p.date_to, end=True),
            max_requests=self.settings.max_requests_per_connector,
            max_items=self.settings.max_items_per_search,
        )
        connector_stats: dict[str, dict] = {}

        async def run_connector(c: Connector, client: httpx.AsyncClient) -> list[RawItem]:
            t0 = time.monotonic()
            urls = [u for u in p.source_urls if c.handles_url(u)] if c.name != "web_url" else [
                u for u in p.source_urls if not any(o.handles_url(u) for o in active if o.name not in ("web_url", "demo"))
            ]
            cplan = SearchPlan(**{**plan.__dict__, "source_urls": urls})

            async def progress(msg: str) -> None:
                await job.emit("fetch", "progress", msg, connector=c.name, platform=c.platform)

            await job.emit("fetch", "start", f"{c.platform}: начало опроса", connector=c.name, platform=c.platform)
            timeout = 90 + (self.settings.twitch_chat_capture_seconds if c.name == "twitch" else 0)
            try:
                result = await asyncio.wait_for(c.fetch(cplan, client, progress), timeout=timeout)
            except asyncio.TimeoutError:
                connector_stats[c.name] = {"platform": c.platform, "status": "error", "requests": 0, "items": 0,
                                           "errors": [f"Превышено время ожидания ({timeout} с)"], "queries": [], "notes": []}
                await job.emit("fetch", "error", f"{c.platform}: превышено время ожидания", connector=c.name, platform=c.platform)
                self._log_run(job.id, c.name, "error", 0, 0, "timeout", [])
                return []
            except Exception as exc:  # noqa: BLE001
                connector_stats[c.name] = {"platform": c.platform, "status": "error", "requests": 0, "items": 0,
                                           "errors": [f"{exc.__class__.__name__}: {exc}"], "queries": [], "notes": []}
                await job.emit("fetch", "error", f"{c.platform}: ошибка {exc.__class__.__name__}", connector=c.name, platform=c.platform)
                self._log_run(job.id, c.name, "error", 0, 0, str(exc)[:300], [])
                return []
            for item in result.items:
                item.meta["_connector"] = c.name
            status = "ok" if not result.errors else ("partial" if result.items else "error")
            connector_stats[c.name] = {
                "platform": c.platform, "status": status, "requests": result.requests, "items": len(result.items),
                "errors": result.errors, "queries": result.queries[:30], "notes": result.notes,
                "duration": round(time.monotonic() - t0, 2),
            }
            for note in result.notes:
                await job.emit("fetch", "warn", f"{c.platform}: {note}", connector=c.name, platform=c.platform)
            for err in result.errors[:5]:
                await job.emit("fetch", "error", f"{c.platform}: {err}", connector=c.name, platform=c.platform)
            await job.emit(
                "fetch", "done",
                f"{c.platform}: запросов {result.requests}, материалов {len(result.items)}"
                + (f", ошибок {len(result.errors)}" if result.errors else ""),
                connector=c.name, platform=c.platform, items=len(result.items), connector_status=status,
            )
            self._log_run(job.id, c.name, status, result.requests, len(result.items), "; ".join(result.errors)[:500], result.queries)
            return result.items

        headers = {"User-Agent": self.settings.user_agent}
        async with httpx.AsyncClient(timeout=self.settings.http_timeout, headers=headers, transport=self.transport,
                                     follow_redirects=True) as client:
            results = await asyncio.gather(*(run_connector(c, client) for c in active))
        raw_items = [i for batch in results for i in batch]
        raw_items = raw_items[: plan.max_items]
        await job.emit("fetch", "done", f"Всего получено материалов: {len(raw_items)}", total=len(raw_items))

        # 5–10. Обработка
        summary = await asyncio.to_thread(self._process, raw_items, expansion, p, job.id, job.user)
        for stage, status, message, extra in summary["events"]:
            await job.emit(stage, status, message, **extra)

        coverage = self._coverage(plan_rows, connector_stats)
        job.stats = {
            "connectors": connector_stats,
            "plan": plan_rows,
            "counts": summary["counts"],
            "coverage": coverage,
            "expansion": expansion.to_dict(),
        }
        await job.emit("done", "done", summary["final"], counts=summary["counts"], coverage=coverage, search_id=job.id)
        await job.finish("done")
        self._persist(job, "done")
        audit.log(self.db, job.user.username, job.user.role, "search", "search", job.id, {
            "query": p.query[:300], "topics": p.topics, "source_url": p.source_url[:300], "purpose": p.purpose[:300],
            "connectors": [c.name for c in active], "found": summary["counts"].get("stored", 0),
        })

    def _select_connectors(self, p: SearchParams) -> set[str]:
        names = set(p.connectors) if p.connectors else {n for n in self.connectors if n != "demo"}
        if p.include_demo:
            names.add("demo")
        else:
            names.discard("demo")
        return names

    def _coverage(self, plan_rows: list[dict], stats: dict) -> dict:
        by_state: dict[str, list[str]] = {}
        for r in plan_rows:
            if r["connector"] in ("manual_import",):
                continue
            by_state.setdefault(r["state"], []).append(r["platform"])
        polled = [f"{s['platform']}" for s in stats.values()]
        failed = [f"{s['platform']}" for s in stats.values() if s["status"] == "error"]
        return {
            "polled": polled,
            "failed": failed,
            "not_configured": sorted(set(by_state.get("not_configured", []))),
            "requires_approval": sorted(set(by_state.get("requires_approval", []))),
            "unavailable": sorted(set(by_state.get("unavailable", []))),
            "watchlist_needed": sorted(set(by_state.get("watchlist_needed", []))),
            "statement": (
                "Поиск охватывает только перечисленные подключённые источники и только общедоступные материалы. "
                "Это не полный охват интернета; местонахождение пользователей не определяется."
            ),
        }

    def _log_run(self, search_id, connector, status, requests, items, error, queries) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO connector_runs(search_id, connector, started_at, finished_at, status, requests, items, error, queries) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (search_id, connector, now_iso(), now_iso(), status, requests, items, error, dumps(queries[:50])),
            )

    def _persist(self, job: SearchJob, status: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE searches SET finished_at = ?, status = ?, stats = ?, stages = ?, expansion = ? WHERE id = ?",
                (now_iso(), status, dumps(job.stats), dumps(job.events), dumps(job.stats.get("expansion")), job.id),
            )

    # ------------------------------------------------------------ обработка
    def _process(self, items: list[RawItem], expansion: Expansion, p: SearchParams, search_id: str | None, user) -> dict:
        events: list[tuple] = []
        counts = {"received": len(items)}
        items = sorted(items, key=lambda i: i.published_at or "9999")
        events.append(("normalize", "start", f"Нормализация {len(items)} материалов", {}))
        matcher = expansion.matcher()
        prepared = []
        langs_seen: dict[str, int] = {}
        obfuscated = 0
        for it in items:
            masked, pii_counts = mask_pii(it.text)
            lang = detect(masked)
            if lang.lang == "und" and it.lang_hint and it.lang_hint[:2] in LANGUAGES:
                lang = LangGuess(it.lang_hint[:2], 0.6, lang.script, "platform")
            f = extract(masked, lang=lang)
            if f.obfuscated:
                obfuscated += 1
            langs_seen[lang.lang] = langs_seen.get(lang.lang, 0) + 1
            prepared.append((it, masked, pii_counts, f))
        events.append((
            "normalize", "done",
            "Языки: " + ", ".join(f"{LANGUAGES.get(k, k)} — {v}" for k, v in sorted(langs_seen.items(), key=lambda kv: -kv[1]))
            + (f"; обфускация снята в {obfuscated}" if obfuscated else ""),
            {"languages": langs_seen},
        ))

        # Фильтр совпадений
        events.append(("filter", "start", "Проверка совпадения с запросом, периодом, языком", {}))
        selected = []
        dropped = {"no_match": 0, "language": 0, "period": 0, "country": 0}
        has_terms = bool(expansion.terms)
        date_plan = SearchPlan(expansion=expansion, date_from=_parse_date(p.date_from), date_to=_parse_date(p.date_to, end=True))
        for it, masked, pii_counts, f in prepared:
            if not date_plan.in_range(it.published_at):
                dropped["period"] += 1
                continue
            pre = assess_features(f)
            matched = matcher(f.norm, f.hits, pre.category) if has_terms else []
            if has_terms and not expansion.is_match(matched):
                dropped["no_match"] += 1
                continue
            if p.material_langs and f.lang.lang not in p.material_langs:
                dropped["language"] += 1
                continue
            selected.append((it, masked, pii_counts, f, matched, pre))
        counts["matched"] = len(selected)
        counts["dropped"] = dropped
        events.append((
            "filter", "done",
            f"Соответствуют запросу: {len(selected)}; отклонено — нет совпадения: {dropped['no_match']}, "
            f"период: {dropped['period']}, язык: {dropped['language']} (не сохраняются)",
            {"matched": len(selected)},
        ))

        # Дубли, классификация, сохранение
        events.append(("dedup", "start", "Поиск точных дублей и похожих текстов", {}))
        primaries = repo.recent_primaries(self.db)
        prim_hashes = [(m["id"], int(m["simhash"], 16) if m["simhash"] else 0, m["excerpt"]) for m in primaries]
        exact_dups = near = 0
        stored = []
        cat_counts: dict[str, int] = {}
        llm_calls = llm_errors = 0
        country_dropped = 0
        already = 0
        for it, masked, pii_counts, f, matched, pre in selected:
            mid = repo.material_id(it.platform, it.external_id)
            existing = repo.find_material(self.db, mid)
            chash = dedup.content_hash(masked)
            if existing:
                primary_id = existing["duplicate_of"] or mid
                if search_id:
                    self._link_result(search_id, primary_id, matched)
                if not existing["duplicate_of"]:
                    cat = repo.incident_category(self.db, primary_id)
                    if cat:
                        cat_counts[cat] = cat_counts.get(cat, 0) + 1
                    already += 1
                stored.append((it, primary_id, None))
                continue
            primary = repo.find_by_hash(self.db, chash)
            duplicate_of = primary["id"] if primary and primary["id"] != mid else None
            sh = dedup.simhash(masked)
            assessment = None
            if duplicate_of:
                exact_dups += 1
            else:
                a = pre
                if self.analyzer and llm_calls < LLM_CALL_LIMIT and (a.is_threat or a.scores):
                    try:
                        llm_calls += 1
                        a = merge_llm(a, self.analyzer.classify(masked, it.context, it.platform))
                    except Exception as exc:  # noqa: BLE001
                        llm_errors += 1
                        a.explanation.append({"kind": "limitation", "text": f"Второй классификатор недоступен: {exc}"})
                if p.countries and not ({g["country_code"] for g in a.geo} & set(p.countries)):
                    country_dropped += 1
                    continue
                assessment = a.to_dict()
                cat_counts[a.category] = cat_counts.get(a.category, 0) + 1
            surfaces = [e["surface"] for e in (assessment or {}).get("evidence", [])]
            lang = f.lang
            material = {
                "id": mid,
                "platform": it.platform,
                "connector": it.meta.get("_connector") or it.provenance,
                "external_id": it.external_id,
                "source_url": normalize_url(it.url) or it.url,
                "source_name": it.source_name,
                "source_kind": it.source_kind,
                "author_kind": it.author_kind,
                "published_at": it.published_at,
                "collected_at": now_iso(),
                "lang": lang.lang,
                "lang_label": LANGUAGES.get(lang.lang, lang.lang),
                "lang_confidence": lang.confidence,
                "script": lang.script,
                "excerpt": make_excerpt(masked, surfaces, self.settings.excerpt_chars),
                "context": mask_pii(it.context or "")[0][: self.settings.context_chars] or None,
                "content_hash": chash,
                "evidence_hash": dedup.evidence_hash(it.text),
                "simhash": format(sh, "016x"),
                "duplicate_of": duplicate_of,
                "is_demo": int(it.is_demo),
                "provenance": it.provenance,
                "legal_basis": it.legal_basis,
                "pii_masked": int(any(pii_counts.values())),
                "meta": {k: v for k, v in it.meta.items() if k in ("channel", "demo_key", "is_quote_post", "uploaded_by")},
            }
            repo.insert_material(self.db, material)
            self._relink(material["source_url"], mid)
            if assessment is not None:
                repo.save_assessment(self.db, mid, assessment)
                repo.upsert_incident(self.db, mid, assessment, material)
                # Похожие тексты: техническая пометка, не связь
                for pid, psh, pexcerpt in prim_hashes:
                    if pid == mid:
                        continue
                    if dedup.hamming(sh, psh) <= 12:
                        ok, j = dedup.near_duplicate(masked, pexcerpt, sh, psh)
                        if ok:
                            near += 1
                            repo.add_edge(self.db, pid, mid, "text_similarity", "simhash_jaccard",
                                          f"Сходство текста (Жаккар {j}). Не является доказательством связи.",
                                          is_evidence=False, similarity=j, is_demo=it.is_demo)
                prim_hashes.append((mid, sh, masked))
            if search_id:
                self._link_result(search_id, duplicate_of or mid, matched)
            stored.append((it, mid, duplicate_of))
        if country_dropped:
            dropped["country"] = country_dropped
        events.append(("dedup", "done", f"Точных дублей: {exact_dups} (присоединены к карточкам как копии); похожих текстов: {near}", {}))
        threats = sum(v for k, v in cat_counts.items() if k in ("direct_threat", "call_to_violence", "recruitment", "incitement",
                                                                  "harassment", "dangerous_involvement", "ambiguous"))
        msg = "Категории: " + (", ".join(f"{CATEGORIES[k]} — {v}" for k, v in sorted(cat_counts.items(), key=lambda kv: -kv[1])) or "—")
        if self.analyzer:
            msg += f"; вызовов LLM: {llm_calls}" + (f", ошибок: {llm_errors}" if llm_errors else "")
        events.append(("classify", "done", msg, {"categories": cat_counts}))
        if country_dropped:
            events.append(("filter", "done", f"Отклонено по стране, упомянутой в тексте: {country_dropped}", {}))

        # Карта распространения
        edges = self._build_edges(stored)
        events.append(("propagation", "done", f"Подтверждённых связей между материалами: {edges}", {"edges": edges}))
        counts.update({"stored": len({m for _, m, d in stored if not d}), "exact_duplicates": exact_dups, "near_duplicates": near,
                       "categories": cat_counts, "threats": threats, "edges": edges, "already_known": already})
        events.append(("store", "done",
                       f"Карточек в результатах: {counts['stored']} (новых: {counts['stored'] - already}, уже были в базе: {already}); "
                       f"потенциальных угроз: {threats}", {}))
        final = (
            f"Поиск завершён: получено {counts['received']}, соответствует запросу {counts['matched']}, "
            f"потенциальных угроз {threats}. Критические оценки направлены на обязательную проверку человеком."
        )
        return {"events": events, "counts": counts, "final": final}

    def _link_result(self, search_id: str, mid: str, matched: list[str]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO search_results(search_id, material_id, matched_terms) VALUES (?, ?, ?)",
                (search_id, mid, dumps(matched)),
            )

    def _relink(self, url: str, mid: str) -> None:
        key = "url:" + url
        with self.db.connect() as conn:
            conn.execute("UPDATE OR IGNORE edges SET src = ? WHERE src = ?", (mid, key))
            conn.execute("UPDATE OR IGNORE edges SET dst = ? WHERE dst = ?", (mid, key))

    def _node_for(self, url: str | None) -> str | None:
        norm = normalize_url(url)
        if not norm:
            return None
        m = repo.material_by_url(self.db, norm)
        return m["id"] if m else "url:" + norm

    def _build_edges(self, stored: list[tuple]) -> int:
        n = 0
        social = [c for c in self.connectors.values() if c.url_patterns and c.name != "web_url"]
        for it, mid, _dup in stored:
            demo = it.is_demo
            for field, relation, label in (
                ("forwarded_from", "repost", "Поле «Переслано из»"),
                ("repost_of", "repost", "Поле платформы о репосте"),
                ("quote_of", "quote", "Встроенная цитата (поле платформы)"),
                ("reply_to", "reply", "Ответ на материал (поле платформы)"),
            ):
                ref = getattr(it, field)
                if not ref:
                    continue
                src = self._node_for(ref.get("url"))
                if not src:
                    continue
                detail = f"{label}: {ref.get('name') or ref.get('url')}"
                repo.add_edge(self.db, src, mid, relation, ref.get("evidence", field), detail, it.url, is_demo=demo)
                n += 1
            urls = set(it.links) | set(re.findall(r"https?://[^\s<>\"']+", it.text or ""))
            for url in urls:
                norm = normalize_url(url)
                if not norm or norm == normalize_url(it.url):
                    continue
                known = repo.material_by_url(self.db, norm)
                if not known and not any(c.handles_url(norm) for c in social):
                    continue
                src = known["id"] if known else "url:" + norm
                repo.add_edge(self.db, src, mid, "link", "url_in_text", f"Ссылка в тексте: {norm}", it.url, is_demo=demo)
                n += 1
        return n

    # ---------------------------------------------------------- прямой анализ
    def ingest(self, items: list[RawItem], user, search_id: str | None = None) -> dict:
        """Анализ и сохранение материалов без опроса источников (импорт, демо)."""
        expansion = expand_query("", [])
        params = SearchParams(purpose="import")
        return self._process(items, expansion, params, search_id, user)
