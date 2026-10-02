"""HTTP API и раздача веб-интерфейса."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, audit, repository as repo, security
from .analysis.classifier import assess, merge_llm
from .analysis.llm import build_analyzer
from .analysis.taxonomy import (
    CATEGORIES,
    FRAMINGS,
    MANDATORY_REVIEW,
    NON_THREAT_CATEGORIES,
    PRIORITIES,
    REVIEW_STATUSES,
    SEVERITIES,
    THREAT_CATEGORIES,
)
from . import envfile
from .config import BASE_DIR, Settings, load_settings, refresh_settings
from .connectors.demo import demo_items
from .connectors.registry import build_connectors
from .data.demo import DEMO_NOTICE
from .db import Database, dumps, loads, now_iso
from .importers import ImportError_, parse as parse_import
from .pipeline import Pipeline, SearchParams
from .text import geo
from .text.expansion import TOPICS, expand_query
from .text.langid import LANGUAGES, PARALLEL_DEFAULT, REGIONAL
from .text.lexicon import get_lexicon
from .text.pii import QueryPolicyError, check_query, person_name_notice

WEB_DIR = BASE_DIR / "web"
COOKIE = "dozor_session"
CSRF_HEADER = "x-requested-with"


# ------------------------------------------------------------------ модели
class LoginBody(BaseModel):
    username: str
    password: str


class SearchBody(BaseModel):
    query: str = ""
    topics: list[str] = []
    languages: list[str] = Field(default_factory=lambda: PARALLEL_DEFAULT + REGIONAL)
    mode: str = "all"
    date_from: str | None = None
    date_to: str | None = None
    countries: list[str] = []
    material_langs: list[str] = []
    source_url: str = ""
    connectors: list[str] = []
    include_demo: bool = False
    purpose: str = ""


class ExpandBody(BaseModel):
    query: str = ""
    topics: list[str] = []
    languages: list[str] = Field(default_factory=lambda: PARALLEL_DEFAULT + REGIONAL)
    mode: str = "all"


class ReviewBody(BaseModel):
    status: str
    note: str = ""


class CorrectBody(BaseModel):
    category: str | None = None
    priority: str | None = None
    severity: str | None = None
    reason: str


class HoldBody(BaseModel):
    hold: bool
    reason: str


class AnalyzeBody(BaseModel):
    text: str = Field(max_length=20000)
    context: str | None = None


class ImportBody(BaseModel):
    content: str = Field(max_length=5_000_000)
    format: str = "json"
    legal_basis: str


class UserBody(BaseModel):
    username: str
    password: str
    role: str


class UserPatch(BaseModel):
    active: bool


class RetentionBody(BaseModel):
    values: dict[str, int]


class SourcesBody(BaseModel):
    values: dict[str, str | int | bool | None] = {}
    clear: list[str] = []


# ------------------------------------------------------------------ фабрика
def create_app(settings: Settings | None = None, db: Database | None = None, transport=None) -> FastAPI:
    settings = settings or load_settings()
    db = db or Database(settings.db_path)
    connectors = build_connectors(settings)
    pipeline = Pipeline(db, settings, connectors, build_analyzer(settings), transport)
    if not settings.demo_enabled:
        removed = repo.purge_demo(db)
        if removed:
            audit.log(db, "system", None, "purge", details={"demo_incidents_removed": removed})

    async def retention_loop():
        while True:
            try:
                result = await asyncio.to_thread(repo.purge_expired, db)
                security.purge_sessions(db)
                if any(result.values()):
                    audit.log(db, "system", None, "purge", details=result)
            except Exception:  # noqa: BLE001 — фоновая очистка не должна падать
                pass
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(retention_loop())
        yield
        task.cancel()

    app = FastAPI(title="Дозор — мониторинг угроз в общедоступном контенте", version=__version__,
                  docs_url="/api/docs", lifespan=lifespan)
    app.state.db = db
    app.state.settings = settings
    app.state.pipeline = pipeline
    failures: dict[str, list[float]] = defaultdict(list)

    # ------------------------------------------------------------ middleware
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.headers.get(CSRF_HEADER) != "dozor":
                return JSONResponse({"detail": "Отсутствует заголовок X-Requested-With: dozor"}, status_code=403)
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # ------------------------------------------------------------ авторизация
    def current_user(request: Request) -> security.User:
        token = request.cookies.get(COOKIE)
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        user = security.user_for_token(db, token)
        if not user:
            raise HTTPException(401, "Требуется вход в систему")
        return user

    def require(permission: str):
        def dep(user: security.User = Depends(current_user)) -> security.User:
            if not user.can(permission):
                raise HTTPException(403, f"Недостаточно прав ({permission})")
            return user
        return dep

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response):
        key = f"{body.username}|{request.client.host if request.client else ''}"
        now = time.time()
        failures[key] = [t for t in failures[key] if now - t < 600]
        if len(failures[key]) >= 5:
            raise HTTPException(429, "Слишком много неудачных попыток. Повторите через 10 минут.")
        user = security.authenticate(db, body.username, body.password)
        if not user:
            failures[key].append(now)
            audit.log(db, body.username[:64], None, "login_failed")
            raise HTTPException(401, "Неверное имя пользователя или пароль")
        failures.pop(key, None)
        token = security.create_session(db, user, settings.session_hours)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=settings.cookie_secure,
                            max_age=settings.session_hours * 3600)
        audit.log(db, user.username, user.role, "login")
        return {"user": user.to_dict(), "token": token}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, user: security.User = Depends(current_user)):
        token = request.cookies.get(COOKIE)
        if token:
            security.drop_session(db, token)
        response.delete_cookie(COOKIE)
        audit.log(db, user.username, user.role, "logout")
        return {"ok": True}

    @app.get("/api/me")
    def me(user: security.User = Depends(current_user)):
        return user.to_dict()

    # ------------------------------------------------------------ справочники
    @app.get("/api/meta")
    def meta(user: security.User = Depends(current_user)):
        lex = get_lexicon()
        cov = lex.coverage()
        return {
            "version": __version__,
            "languages": [
                {"code": c, "label": l, "parallel_default": c in PARALLEL_DEFAULT, "regional": c in REGIONAL,
                 "lexicon_concepts": len(cov.get(c, {})), "lexicon_terms": sum(cov.get(c, {}).values())}
                for c, l in LANGUAGES.items() if c != "und"
            ],
            "parallel_default": PARALLEL_DEFAULT,
            "regional": REGIONAL,
            "topics": [{"id": k, "label": v["label"]} for k, v in TOPICS.items()],
            "categories": CATEGORIES,
            "threat_categories": list(THREAT_CATEGORIES),
            "non_threat_categories": list(NON_THREAT_CATEGORIES),
            "framings": FRAMINGS,
            "priorities": PRIORITIES,
            "severities": SEVERITIES,
            "statuses": REVIEW_STATUSES,
            "countries": geo.country_options(),
            "roles": security.ROLES,
            "demo_notice": DEMO_NOTICE,
            "demo_enabled": settings.demo_enabled,
            "llm_enabled": pipeline.analyzer is not None,
            "llm_model": settings.llm_model if pipeline.analyzer else None,
            "geo_disclaimer": geo.DISCLAIMER,
            "retention_labels": repo.RETENTION_LABELS,
        }

    @app.get("/api/connectors")
    def connectors_list(user: security.User = Depends(current_user)):
        return [c.describe() for c in connectors.values()]

    @app.post("/api/connectors/{name}/test")
    def connector_test(name: str, user: security.User = Depends(require("manage_settings"))):
        c = connectors.get(name)
        if not c:
            raise HTTPException(404, "Неизвестный источник")
        st = c.status().to_dict()
        audit.log(db, user.username, user.role, "connector_test", "connector", name, st)
        return st

    # ------------------------------------------------------------ поиск
    @app.post("/api/expand")
    def expand(body: ExpandBody, user: security.User = Depends(require("search"))):
        try:
            check_query(body.query)
        except QueryPolicyError as exc:
            raise HTTPException(400, str(exc)) from exc
        exp = expand_query(body.query, body.languages, body.topics, body.mode)
        data = exp.to_dict()
        notice = person_name_notice(body.query)
        if notice:
            data["warnings"].insert(0, notice)
        return data

    @app.post("/api/search")
    async def search(body: SearchBody, user: security.User = Depends(require("search"))):
        params = SearchParams(**body.model_dump())
        try:
            warnings = pipeline.validate(params)
        except QueryPolicyError as exc:
            audit.log(db, user.username, user.role, "search_rejected", details={"reason": str(exc)[:200]})
            raise HTTPException(400, str(exc)) from exc
        job = pipeline.start(params, user)
        return {"search_id": job.id, "warnings": warnings}

    def _own_search(owner: str, user: security.User) -> None:
        # Запросы и цели поиска видят их автор и руководители
        if owner != user.username and not user.can("audit"):
            raise HTTPException(403, "Поиск выполнен другим пользователем")

    @app.get("/api/search/{sid}")
    def search_status(sid: str, user: security.User = Depends(require("search"))):
        job = pipeline.jobs.get(sid)
        if job:
            _own_search(job.user.username, user)
            return {"id": sid, "status": job.status, "events": job.events, "stats": job.stats}
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM searches WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "Поиск не найден")
        _own_search(row["user"], user)
        return {"id": sid, "status": row["status"], "events": loads(row["stages"], []), "stats": loads(row["stats"], {})}

    @app.get("/api/search/{sid}/events")
    async def search_events(sid: str, user: security.User = Depends(require("search"))):
        job = pipeline.jobs.get(sid)
        if job:
            _own_search(job.user.username, user)

        async def stream():
            if not job:
                data = search_status(sid, user)
                for ev in data["events"]:
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                yield "event: end\ndata: {}\n\n"
                return
            idx = 0
            idle = 0.0
            while True:
                if idx < len(job.events):
                    while idx < len(job.events):
                        yield f"data: {json.dumps(job.events[idx], ensure_ascii=False)}\n\n"
                        idx += 1
                    idle = 0.0
                elif job.status != "running":
                    yield "event: end\ndata: {}\n\n"
                    return
                else:
                    await asyncio.sleep(0.15)
                    idle += 0.15
                    if idle >= 10:
                        yield ": keepalive\n\n"
                        idle = 0.0

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/searches")
    def searches(user: security.User = Depends(require("search")), limit: int = 30):
        sql = "SELECT id, user, created_at, finished_at, purpose, params, status, stats FROM searches"
        args: list = []
        if not user.can("audit"):
            sql += " WHERE user = ?"
            args.append(user.username)
        sql += " ORDER BY created_at DESC LIMIT ?"
        with db.connect() as conn:
            rows = conn.execute(sql, (*args, min(limit, 200))).fetchall()
        out = []
        for r in rows:
            st = loads(r["stats"], {})
            out.append({"id": r["id"], "user": r["user"], "created_at": r["created_at"], "finished_at": r["finished_at"],
                        "purpose": r["purpose"], "params": loads(r["params"], {}), "status": r["status"],
                        "counts": st.get("counts", {}), "coverage": st.get("coverage", {})})
        return out

    # ------------------------------------------------------------ карточки
    def filters_from(request: Request) -> dict:
        q = request.query_params
        def multi(name):
            vals = q.getlist(name)
            out = []
            for v in vals:
                out += [x for x in v.split(",") if x]
            return out or None
        return {
            "priority": multi("priority"), "category": multi("category"), "status": multi("status"),
            "platform": multi("platform"), "lang": multi("lang"), "country": multi("country"),
            "date_from": q.get("date_from"), "date_to": q.get("date_to"), "q": q.get("q"),
            "include_non_threat": q.get("include_non_threat") in ("1", "true"),
            "requires_review": q.get("requires_review") in ("1", "true"),
            "demo": q.get("demo", "include"), "search_id": q.get("search_id"), "day": q.get("day"),
            "sort": q.get("sort", "priority"), "limit": q.get("limit", 100), "offset": q.get("offset", 0),
        }

    @app.get("/api/incidents")
    def incidents(request: Request, user: security.User = Depends(require("view"))):
        return repo.list_incidents(db, filters_from(request))

    @app.get("/api/incidents/{iid}")
    def incident(iid: str, user: security.User = Depends(require("view"))):
        card = repo.get_incident(db, iid)
        if not card:
            raise HTTPException(404, "Карточка не найдена")
        card["audit"] = audit.entries(db, limit=50, object_id=iid) if user.can("audit") else []
        card["propagation_count"] = repo.propagation_count(db, card["material"]["id"])
        audit.log(db, user.username, user.role, "view_incident", "incident", iid)
        return card

    @app.get("/api/incidents/{iid}/context")
    def incident_context(iid: str, user: security.User = Depends(require("view_context"))):
        ctx = repo.get_context(db, iid)
        if not ctx:
            raise HTTPException(404, "Карточка не найдена")
        audit.log(db, user.username, user.role, "view_context", "incident", iid)
        return ctx

    @app.post("/api/incidents/{iid}/review")
    def incident_review(iid: str, body: ReviewBody, user: security.User = Depends(require("review"))):
        try:
            return repo.review(db, iid, user, body.status, body.note)
        except repo.ReviewError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/incidents/{iid}/correct")
    def incident_correct(iid: str, body: CorrectBody, user: security.User = Depends(require("correct"))):
        try:
            return repo.correct(db, iid, user, {"category": body.category, "priority": body.priority, "severity": body.severity}, body.reason)
        except repo.ReviewError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/incidents/{iid}/legal-hold")
    def incident_hold(iid: str, body: HoldBody, user: security.User = Depends(require("legal_hold"))):
        try:
            return repo.set_legal_hold(db, iid, user, body.hold, body.reason)
        except repo.ReviewError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/incidents/{iid}")
    def incident_delete(iid: str, user: security.User = Depends(require("delete"))):
        if not repo.delete_incident(db, iid):
            raise HTTPException(404, "Карточка не найдена")
        audit.log(db, user.username, user.role, "delete", "incident", iid)
        return {"ok": True}

    # ------------------------------------------------------------ панели
    @app.get("/api/timeline")
    def timeline(request: Request, field: str = "published", user: security.User = Depends(require("view"))):
        return repo.timeline(db, filters_from(request), field)

    @app.get("/api/graph")
    def graph(request: Request, incident: str | None = None, similarity: bool = False,
              user: security.User = Depends(require("view"))):
        focus = None
        if incident:
            card = repo.get_incident(db, incident)
            if not card:
                raise HTTPException(404, "Карточка не найдена")
            focus = card["material"]["id"]
        return repo.graph(db, filters_from(request), include_similarity=similarity, focus=focus)

    @app.get("/api/stats")
    def stats(demo: str = "include", user: security.User = Depends(require("view"))):
        return repo.stats(db, demo)

    # ------------------------------------------------------------ анализ и импорт
    @app.post("/api/analyze")
    def analyze(body: AnalyzeBody, user: security.User = Depends(require("analyze_text"))):
        a = assess(body.text)
        if pipeline.analyzer and (a.is_threat or a.scores):
            try:
                a = merge_llm(a, pipeline.analyzer.classify(body.text, body.context))
            except Exception as exc:  # noqa: BLE001
                a.explanation.append({"kind": "limitation", "text": f"Второй классификатор недоступен: {exc}"})
        audit.log(db, user.username, user.role, "analyze_text", details={"chars": len(body.text), "category": a.category,
                                                                         "priority": a.priority})
        return a.to_dict()

    @app.post("/api/import")
    def import_materials(body: ImportBody, user: security.User = Depends(require("import"))):
        try:
            items = parse_import(body.content, body.format, body.legal_basis, user.username)
        except ImportError_ as exc:
            raise HTTPException(400, str(exc)) from exc
        summary = pipeline.ingest(items, user)
        audit.log(db, user.username, user.role, "import", details={"items": len(items), "legal_basis": body.legal_basis[:300],
                                                                   "stored": summary["counts"].get("stored")})
        return {"counts": summary["counts"], "events": [e[2] for e in summary["events"]]}

    @app.post("/api/demo/load")
    def demo_load(user: security.User = Depends(require("manage_settings"))):
        if not settings.demo_enabled:
            raise HTTPException(404, "Учебный режим выключен")
        summary = pipeline.ingest(demo_items(), user)
        audit.log(db, user.username, user.role, "demo_load", details=summary["counts"])
        return {"counts": summary["counts"]}

    @app.get("/api/export")
    def export(request: Request, format: str = "json", user: security.User = Depends(require("export"))):
        f = filters_from(request)
        f["limit"] = 500
        data = repo.list_incidents(db, f)["items"]
        reviewed = {"confirmed", "escalated", "false_positive", "not_threat", "closed"}
        allowed, withheld = [], 0
        for it in data:
            if it["priority"] in MANDATORY_REVIEW and it["status"] not in reviewed:
                withheld += 1
                continue
            card = repo.get_incident(db, it["id"])
            a = card["assessment"]
            allowed.append({
                "incident_id": card["id"],
                "is_demo": card["is_demo"],
                "facts": {
                    "platform": card["material"]["platform"],
                    "source_url": card["material"]["source_url"],
                    "source_name": card["material"]["source_name"],
                    "published_at": card["material"]["published_at"],
                    "detected_at": card["detected_at"],
                    "language": card["material"]["lang_label"],
                    "excerpt": card["material"]["excerpt"],
                    "evidence_hash_sha256": card["material"]["evidence_hash"],
                    "observed_features": [e["text"] for e in a.get("explanation", []) if e["kind"] == "fact"],
                },
                "assessment": {
                    "category": card["category_label"],
                    "priority": card["priority_label"],
                    "severity": card["severity_label"],
                    "confidence": card["confidence"],
                    "rationale": [e["text"] for e in a.get("explanation", []) if e["kind"] in ("assessment", "rule")],
                    "geo_mentions": a.get("geo", []),
                    "engine": a.get("engine"),
                    "corrected_by_analyst": card["corrected"],
                },
                "review": {"status": card["status_label"], "reviewed_by": card["reviewed_by"], "note": card["analyst_note"]},
                "limitation": "Автоматическая оценка не устанавливает причастность автора к противоправной деятельности.",
            })
        audit.log(db, user.username, user.role, "export", details={"format": format, "items": len(allowed), "withheld": withheld})
        if format == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["incident_id", "demo", "platform", "source_url", "published_at", "detected_at", "language",
                        "category", "priority", "severity", "confidence", "review_status", "excerpt", "evidence_sha256"])
            for x in allowed:
                w.writerow([x["incident_id"], x["is_demo"], x["facts"]["platform"], x["facts"]["source_url"],
                            x["facts"]["published_at"], x["facts"]["detected_at"], x["facts"]["language"],
                            x["assessment"]["category"], x["assessment"]["priority"], x["assessment"]["severity"],
                            x["assessment"]["confidence"], x["review"]["status"], x["facts"]["excerpt"],
                            x["facts"]["evidence_hash_sha256"]])
            return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="dozor-export.csv"'})
        return {"exported_at": now_iso(), "items": allowed, "withheld_unreviewed_critical": withheld}

    # ------------------------------------------------------------ журнал и настройки
    @app.get("/api/audit")
    def audit_list(limit: int = 200, offset: int = 0, object_id: str | None = None, action: str | None = None,
                   user: security.User = Depends(require("audit"))):
        return audit.entries(db, min(limit, 1000), offset, object_id, action)

    @app.get("/api/audit/verify")
    def audit_verify(user: security.User = Depends(require("audit"))):
        return audit.verify(db)

    @app.get("/api/settings/sources")
    def sources_get(user: security.User = Depends(require("manage_settings"))):
        data = envfile.read_values()
        data["env_file"] = str(settings.env_file)
        return data

    @app.put("/api/settings/sources")
    def sources_put(body: SourcesBody, user: security.User = Depends(require("manage_settings"))):
        try:
            changed = envfile.save(settings.env_file, body.values, body.clear)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        refresh_settings(settings)
        if {"DOZOR_LLM_ENABLED", "ANTHROPIC_API_KEY"} & set(changed):
            pipeline.analyzer = build_analyzer(settings)
        # В журнал — только имена полей, без значений и секретов
        audit.log(db, user.username, user.role, "settings_change", "settings", "sources", {"changed": changed})
        return {"changed": changed, "connectors": [c.describe() for c in connectors.values()]}

    @app.get("/api/settings/retention")
    def retention_get(user: security.User = Depends(require("view"))):
        return {"values": repo.retention_policy(db), "labels": repo.RETENTION_LABELS}

    @app.put("/api/settings/retention")
    def retention_put(body: RetentionBody, user: security.User = Depends(require("manage_settings"))):
        clean = {k: max(1, min(int(v), 3650)) for k, v in body.values.items() if k in repo.DEFAULT_RETENTION}
        db.set_setting("retention", {**repo.retention_policy(db), **clean})
        audit.log(db, user.username, user.role, "settings_change", "settings", "retention", clean)
        return {"values": repo.retention_policy(db)}

    @app.post("/api/retention/purge")
    def retention_purge(user: security.User = Depends(require("purge"))):
        result = repo.purge_expired(db)
        security.purge_sessions(db)
        audit.log(db, user.username, user.role, "purge", details=result)
        return result

    @app.get("/api/users")
    def users(user: security.User = Depends(require("manage_users"))):
        return security.list_users(db)

    @app.post("/api/users")
    def user_create(body: UserBody, user: security.User = Depends(require("manage_users"))):
        try:
            created = security.create_user(db, body.username, body.password, body.role)
        except (ValueError, Exception) as exc:  # noqa: BLE001 — дубликат имени и пр.
            raise HTTPException(400, str(exc)) from exc
        audit.log(db, user.username, user.role, "user_create", "user", str(created.id), {"username": body.username, "role": body.role})
        return created.to_dict()

    @app.patch("/api/users/{uid}")
    def user_patch(uid: int, body: UserPatch, user: security.User = Depends(require("manage_users"))):
        if uid == user.id and not body.active:
            raise HTTPException(400, "Нельзя отключить собственную учётную запись")
        security.set_user_active(db, uid, body.active)
        audit.log(db, user.username, user.role, "user_update", "user", str(uid), {"active": body.active})
        return {"ok": True}

    # ------------------------------------------------------------ интерфейс
    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(WEB_DIR / "index.html")

    return app
