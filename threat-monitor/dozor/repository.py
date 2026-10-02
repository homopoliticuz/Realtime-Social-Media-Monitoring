"""Операции с данными: материалы, карточки инцидентов, связи, выборки."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from . import audit
from .analysis.taxonomy import (
    CATEGORIES,
    MANDATORY_REVIEW,
    PRIORITIES,
    PRIORITY_ORDER,
    REVIEW_STATUSES,
    SEVERITIES,
    THREAT_CATEGORIES,
    confidence_label,
)
from .db import Database, dumps, loads, now_iso

DEFAULT_RETENTION = {
    "not_threat_days": 30,
    "unreviewed_days": 90,
    "false_positive_days": 30,
    "confirmed_days": 365,
    "closed_days": 180,
    "search_history_days": 90,
    "audit_days": 1095,
}
RETENTION_LABELS = {
    "not_threat_days": "Материалы без признаков угрозы",
    "unreviewed_days": "Непроверенные потенциальные угрозы",
    "false_positive_days": "Ложные срабатывания (после проверки)",
    "confirmed_days": "Подтверждённые / переданные инциденты",
    "closed_days": "Закрытые инциденты",
    "search_history_days": "История поисков",
    "audit_days": "Журнал действий",
}

AUTHOR_KIND_LABELS = {
    "public_source": "Публичный канал / страница / аккаунт (источник публикации)",
    "user_hidden": "Пользователь (идентификатор не сохраняется)",
    "provided": "Материал предоставлен пользователем системы",
}

RELATION_LABELS = {
    "repost": "Репост / пересылка",
    "quote": "Цитирование",
    "link": "Ссылка на материал",
    "reply": "Ответ / комментарий к материалу",
    "text_similarity": "Текстовое сходство (НЕ доказательство связи)",
}

_PRIORITY_SQL = "CASE i.priority " + " ".join(
    f"WHEN '{p}' THEN {n}" for n, p in enumerate(PRIORITY_ORDER, start=1)
) + " ELSE 0 END"


def material_id(platform: str, external_id: str) -> str:
    return "m_" + hashlib.sha1(f"{platform}|{external_id}".encode("utf-8")).hexdigest()[:16]


def incident_id(mid: str) -> str:
    return "INC-" + mid[2:12].upper()


def retention_policy(db: Database) -> dict:
    stored = db.get_setting("retention", {}) or {}
    return {**DEFAULT_RETENTION, **{k: int(v) for k, v in stored.items() if k in DEFAULT_RETENTION}}


def compute_retention(status: str, is_threat: bool, base_iso: str | None, policy: dict) -> str:
    base = datetime.fromisoformat(base_iso) if base_iso else datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    if status in ("confirmed", "escalated"):
        days = policy["confirmed_days"]
    elif status == "false_positive":
        days = policy["false_positive_days"]
    elif status == "closed":
        days = policy["closed_days"]
    elif not is_threat or status == "not_threat":
        days = policy["not_threat_days"]
    else:
        days = policy["unreviewed_days"]
    return (base + timedelta(days=days)).replace(microsecond=0).isoformat()


# ----------------------------------------------------------------- материалы
def find_material(db: Database, mid: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM materials WHERE id = ?", (mid,)).fetchone()
    return dict(row) if row else None


def find_by_hash(db: Database, content_hash: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM materials WHERE content_hash = ? AND duplicate_of IS NULL ORDER BY published_at LIMIT 1",
            (content_hash,),
        ).fetchone()
    return dict(row) if row else None


def recent_primaries(db: Database, limit: int = 3000) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, excerpt, simhash, is_demo FROM materials WHERE duplicate_of IS NULL ORDER BY collected_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def insert_material(db: Database, m: dict) -> None:
    cols = [
        "id", "platform", "connector", "external_id", "source_url", "source_name", "source_kind", "author_kind",
        "published_at", "collected_at", "lang", "lang_label", "lang_confidence", "script", "excerpt", "context",
        "content_hash", "evidence_hash", "simhash", "duplicate_of", "is_demo", "provenance", "legal_basis",
        "pii_masked", "meta",
    ]
    values = [m.get(c) for c in cols]
    values[cols.index("meta")] = dumps(m.get("meta") or {})
    with db.connect() as conn:
        conn.execute(
            f"INSERT OR IGNORE INTO materials({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", values
        )


def save_assessment(db: Database, mid: str, a: dict) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE assessments SET is_current = 0 WHERE material_id = ?", (mid,))
        conn.execute(
            "INSERT INTO assessments(material_id, created_at, engine, is_current, is_threat, category, priority, severity, confidence, data) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (mid, now_iso(), a["engine"], int(a["is_threat"]), a["category"], a["priority"], a["severity"], a["confidence"], dumps(a)),
        )


def upsert_incident(db: Database, mid: str, a: dict, material: dict) -> str:
    iid = incident_id(mid)
    policy = retention_policy(db)
    countries = sorted({g["country_code"] for g in a.get("geo", [])})
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (iid,)).fetchone()
        if row and row["corrected"]:
            # Исправленную аналитиком оценку автоматический пересчёт не перезаписывает
            conn.execute(
                "UPDATE incidents SET auto_category = ?, auto_priority = ?, updated_at = ? WHERE id = ?",
                (a["category"], a["priority"], now_iso(), iid),
            )
            return iid
        status = row["status"] if row else ("needs_review" if a["requires_human_review"] else "new")
        if row and a["requires_human_review"] and row["status"] == "new":
            status = "needs_review"
        detected = row["detected_at"] if row else material.get("collected_at") or now_iso()
        retention = compute_retention(status, a["is_threat"], detected, policy)
        params = {
            "id": iid, "mid": mid, "now": now_iso(), "status": status, "is_threat": int(a["is_threat"]),
            "category": a["category"], "priority": a["priority"], "severity": a["severity"],
            "confidence": a["confidence"], "requires_review": int(a["requires_human_review"]),
            "review_reason": a.get("review_reason", ""), "retention": retention, "is_demo": material.get("is_demo", 0),
            "platform": material.get("platform"), "lang": a.get("language", {}).get("code"),
            "countries": ",".join(countries), "published_at": material.get("published_at"), "detected": detected,
        }
        if row:
            conn.execute(
                "UPDATE incidents SET updated_at=:now, status=:status, is_threat=:is_threat, category=:category, priority=:priority, "
                "severity=:severity, confidence=:confidence, auto_category=:category, auto_priority=:priority, "
                "requires_review=:requires_review, review_reason=:review_reason, retention_until=:retention, "
                "lang=:lang, countries=:countries WHERE id=:id",
                params,
            )
        else:
            conn.execute(
                "INSERT INTO incidents(id, material_id, created_at, updated_at, status, is_threat, category, priority, severity, "
                "confidence, auto_category, auto_priority, requires_review, review_reason, retention_until, is_demo, platform, "
                "lang, countries, published_at, detected_at) VALUES (:id, :mid, :now, :now, :status, :is_threat, :category, "
                ":priority, :severity, :confidence, :category, :priority, :requires_review, :review_reason, :retention, "
                ":is_demo, :platform, :lang, :countries, :published_at, :detected)",
                params,
            )
    return iid


def add_edge(db: Database, src: str, dst: str, relation: str, evidence_type: str, evidence_detail: str = "",
             evidence_url: str = "", is_evidence: bool = True, similarity: float | None = None, is_demo: bool = False) -> None:
    if src == dst:
        return
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO edges(src, dst, relation, evidence_type, evidence_detail, evidence_url, observed_at, "
            "is_evidence, similarity, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (src, dst, relation, evidence_type, evidence_detail, evidence_url, now_iso(), int(is_evidence), similarity, int(is_demo)),
        )


def incident_category(db: Database, mid: str) -> str | None:
    with db.connect() as conn:
        row = conn.execute("SELECT category FROM incidents WHERE material_id = ?", (mid,)).fetchone()
    return row["category"] if row else None


def material_by_url(db: Database, url: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM materials WHERE source_url = ? LIMIT 1", (url,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- выборки
def _filters_sql(f: dict) -> tuple[str, list]:
    where = ["m.duplicate_of IS NULL"]
    args: list[Any] = []

    def multi(field: str, values: list | None) -> None:
        if values:
            where.append(f"{field} IN ({', '.join('?' for _ in values)})")
            args.extend(values)

    if not f.get("include_non_threat"):
        where.append("i.is_threat = 1")
    multi("i.priority", f.get("priority"))
    multi("i.category", f.get("category"))
    multi("i.status", f.get("status"))
    multi("i.platform", f.get("platform"))
    multi("i.lang", f.get("lang"))
    if f.get("country"):
        where.append("(" + " OR ".join("(',' || i.countries || ',') LIKE ?" for _ in f["country"]) + ")")
        args.extend(f"%,{c},%" for c in f["country"])
    if f.get("date_from"):
        where.append("COALESCE(i.published_at, i.detected_at) >= ?")
        args.append(f["date_from"])
    if f.get("date_to"):
        where.append("COALESCE(i.published_at, i.detected_at) <= ?")
        args.append(f["date_to"] + ("T23:59:59" if len(f["date_to"]) == 10 else ""))
    if f.get("requires_review"):
        where.append("i.requires_review = 1 AND i.status IN ('new', 'needs_review', 'in_review')")
    demo = f.get("demo", "include")
    if demo == "exclude":
        where.append("i.is_demo = 0")
    elif demo == "only":
        where.append("i.is_demo = 1")
    if f.get("q"):
        where.append("LOWER(m.excerpt) LIKE ?")
        args.append(f"%{f['q'].lower()}%")
    if f.get("search_id"):
        where.append("m.id IN (SELECT material_id FROM search_results WHERE search_id = ?)")
        args.append(f["search_id"])
    if f.get("day"):
        where.append("SUBSTR(COALESCE(i.published_at, i.detected_at), 1, 10) = ?")
        args.append(f["day"])
    return " AND ".join(where), args


def list_incidents(db: Database, f: dict) -> dict:
    where, args = _filters_sql(f)
    sort = {
        "priority": f"{_PRIORITY_SQL} DESC, i.published_at DESC",
        "published": "i.published_at DESC",
        "detected": "i.detected_at DESC",
        "confidence": "i.confidence DESC",
    }.get(f.get("sort", "priority"), f"{_PRIORITY_SQL} DESC")
    limit = min(int(f.get("limit", 100)), 500)
    offset = int(f.get("offset", 0))
    sql = (
        "SELECT i.*, m.platform AS m_platform, m.source_url, m.source_name, m.source_kind, m.excerpt, m.lang_label, "
        "m.published_at AS m_published, m.collected_at, m.is_demo AS m_demo, "
        "(SELECT COUNT(*) FROM materials d WHERE d.duplicate_of = m.id) AS copies, "
        "(SELECT data FROM assessments a WHERE a.material_id = m.id AND a.is_current = 1) AS adata "
        f"FROM incidents i JOIN materials m ON m.id = i.material_id WHERE {where} ORDER BY {sort} LIMIT ? OFFSET ?"
    )
    with db.connect() as conn:
        rows = conn.execute(sql, (*args, limit, offset)).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM incidents i JOIN materials m ON m.id = i.material_id WHERE {where}", args
        ).fetchone()[0]
    items = []
    for r in rows:
        a = loads(r["adata"], {})
        items.append({
            "id": r["id"],
            "status": r["status"],
            "status_label": REVIEW_STATUSES.get(r["status"], r["status"]),
            "is_threat": bool(r["is_threat"]),
            "category": r["category"],
            "category_label": CATEGORIES.get(r["category"], r["category"]),
            "priority": r["priority"],
            "priority_label": PRIORITIES.get(r["priority"], "— (не угроза)") if r["priority"] else "— (не угроза)",
            "severity": r["severity"],
            "severity_label": SEVERITIES.get(r["severity"], ""),
            "confidence": r["confidence"],
            "confidence_label": confidence_label(r["confidence"] or 0),
            "requires_review": bool(r["requires_review"]),
            "corrected": bool(r["corrected"]),
            "is_demo": bool(r["is_demo"]),
            "platform": r["m_platform"],
            "source_url": r["source_url"],
            "source_name": r["source_name"],
            "source_kind": r["source_kind"],
            "excerpt": r["excerpt"],
            "lang": r["lang"],
            "lang_label": r["lang_label"],
            "published_at": r["m_published"],
            "detected_at": r["detected_at"],
            "countries": [c for c in (r["countries"] or "").split(",") if c],
            "copies": r["copies"],
            "highlights": sorted({e["surface"] for e in a.get("evidence", []) if e.get("group") != "movement"}),
            "obfuscation": a.get("obfuscation", []),
        })
    return {"total": total, "items": items}


def get_incident(db: Database, iid: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (iid,)).fetchone()
        if not row:
            return None
        inc = dict(row)
        m = dict(conn.execute("SELECT * FROM materials WHERE id = ?", (inc["material_id"],)).fetchone())
        arows = conn.execute(
            "SELECT id, created_at, engine, is_current, category, priority, severity, confidence, data FROM assessments "
            "WHERE material_id = ? ORDER BY id DESC",
            (m["id"],),
        ).fetchall()
        copies = conn.execute(
            "SELECT id, platform, source_url, source_name, published_at FROM materials WHERE duplicate_of = ? ORDER BY published_at",
            (m["id"],),
        ).fetchall()
        corrections = conn.execute(
            "SELECT * FROM corrections WHERE incident_id = ? ORDER BY id DESC", (iid,)
        ).fetchall()
    current = next((loads(a["data"], {}) for a in arows if a["is_current"]), {})
    history = [
        {k: a[k] for k in ("id", "created_at", "engine", "category", "priority", "severity", "confidence")}
        | {"category_label": CATEGORIES.get(a["category"], a["category"])}
        for a in arows
    ]
    return {
        "id": inc["id"],
        "status": inc["status"],
        "status_label": REVIEW_STATUSES.get(inc["status"], inc["status"]),
        "is_threat": bool(inc["is_threat"]),
        "category": inc["category"],
        "category_label": CATEGORIES.get(inc["category"], inc["category"]),
        "priority": inc["priority"],
        "priority_label": PRIORITIES.get(inc["priority"], "— (не угроза)") if inc["priority"] else "— (не угроза)",
        "severity": inc["severity"],
        "severity_label": SEVERITIES.get(inc["severity"], ""),
        "confidence": inc["confidence"],
        "confidence_label": confidence_label(inc["confidence"] or 0),
        "auto": {
            "category": inc["auto_category"],
            "category_label": CATEGORIES.get(inc["auto_category"], inc["auto_category"]),
            "priority": inc["auto_priority"],
            "priority_label": PRIORITIES.get(inc["auto_priority"], "—") if inc["auto_priority"] else "—",
        },
        "corrected": bool(inc["corrected"]),
        "requires_review": bool(inc["requires_review"]),
        "review_reason": inc["review_reason"],
        "analyst_note": inc["analyst_note"],
        "reviewed_by": inc["reviewed_by"],
        "reviewed_at": inc["reviewed_at"],
        "second_reviewed_by": inc["second_reviewed_by"],
        "legal_hold": bool(inc["legal_hold"]),
        "retention_until": inc["retention_until"],
        "is_demo": bool(inc["is_demo"]),
        "detected_at": inc["detected_at"],
        "material": {
            "id": m["id"],
            "platform": m["platform"],
            "connector": m["connector"],
            "source_url": m["source_url"],
            "source_name": m["source_name"],
            "source_kind": m["source_kind"],
            "author_kind": m["author_kind"],
            "author_kind_label": AUTHOR_KIND_LABELS.get(m["author_kind"], m["author_kind"]),
            "published_at": m["published_at"],
            "collected_at": m["collected_at"],
            "lang": m["lang"],
            "lang_label": m["lang_label"],
            "lang_confidence": m["lang_confidence"],
            "excerpt": m["excerpt"],
            "has_context": bool(m["context"]),
            "provenance": m["provenance"],
            "legal_basis": m["legal_basis"],
            "evidence_hash": m["evidence_hash"],
            "pii_masked": bool(m["pii_masked"]),
        },
        "assessment": current,
        "history": history,
        "copies": [dict(c) for c in copies],
        "corrections": [dict(c) for c in corrections],
    }


def get_context(db: Database, iid: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT m.context, m.excerpt, m.source_url, m.source_name FROM incidents i JOIN materials m ON m.id = i.material_id WHERE i.id = ?",
            (iid,),
        ).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------ проверка и правка
class ReviewError(ValueError):
    pass


def review(db: Database, iid: str, user, status: str, note: str = "") -> dict:
    if status not in REVIEW_STATUSES:
        raise ReviewError("Неизвестный статус")
    inc = get_incident(db, iid)
    if not inc:
        raise ReviewError("Карточка не найдена")
    critical = inc["priority"] in MANDATORY_REVIEW
    second = None
    if critical and status in ("confirmed", "escalated", "false_positive", "not_threat", "closed"):
        # Критические оценки: решение принимает человек; для подтверждения/передачи
        # и для снятия критической оценки нужна роль руководителя или второй проверяющий.
        if not user.can("confirm_critical"):
            if inc["reviewed_by"] and inc["reviewed_by"] != user.username:
                second = user.username
            elif status in ("confirmed", "escalated"):
                status = "in_review"
                note = (note + " " if note else "") + "[ожидает подтверждения руководителем]"
            else:
                raise ReviewError(
                    "Снять критическую оценку может руководитель или второй аналитик после первичной проверки."
                )
    if not note.strip() and status in ("false_positive", "not_threat", "escalated", "confirmed"):
        raise ReviewError("Укажите обоснование решения")
    policy = retention_policy(db)
    retention = compute_retention(status, inc["is_threat"], now_iso(), policy)
    with db.connect() as conn:
        if second:
            conn.execute(
                "UPDATE incidents SET status=?, analyst_note=?, second_reviewed_by=?, updated_at=?, retention_until=? WHERE id=?",
                (status, note, second, now_iso(), retention, iid),
            )
        else:
            conn.execute(
                "UPDATE incidents SET status=?, analyst_note=?, reviewed_by=?, reviewed_at=?, updated_at=?, retention_until=? WHERE id=?",
                (status, note, user.username, now_iso(), now_iso(), retention, iid),
            )
    audit.log(db, user.username, user.role, "review", "incident", iid, {"status": status, "note": note[:500]})
    return get_incident(db, iid)


def correct(db: Database, iid: str, user, changes: dict, reason: str) -> dict:
    if not reason or len(reason.strip()) < 5:
        raise ReviewError("Укажите причину исправления (не менее 5 символов)")
    inc = get_incident(db, iid)
    if not inc:
        raise ReviewError("Карточка не найдена")
    allowed = {"category": CATEGORIES, "priority": PRIORITIES, "severity": SEVERITIES}
    updates = {}
    for field, value in changes.items():
        if field not in allowed or value in (None, ""):
            continue
        if value not in allowed[field]:
            raise ReviewError(f"Недопустимое значение поля {field}")
        if value != inc[field]:
            updates[field] = value
    if "category" in updates and updates["category"] not in THREAT_CATEGORIES:
        updates["priority"] = None
    if not updates:
        raise ReviewError("Нет изменений")
    is_threat = (updates.get("category", inc["category"]) in THREAT_CATEGORIES)
    if is_threat and updates.get("priority", inc["priority"]) is None:
        updates["priority"] = "insufficient_data"
    with db.connect() as conn:
        for field, value in updates.items():
            conn.execute(
                "INSERT INTO corrections(incident_id, user, created_at, field, old_value, new_value, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (iid, user.username, now_iso(), field, inc[field], value, reason),
            )
        sets = ", ".join(f"{k} = ?" for k in updates)
        requires = 1 if updates.get("priority", inc["priority"]) in MANDATORY_REVIEW else 0
        conn.execute(
            f"UPDATE incidents SET {sets}, is_threat = ?, corrected = 1, requires_review = MAX(requires_review, ?), updated_at = ? WHERE id = ?",
            (*updates.values(), int(is_threat), requires, now_iso(), iid),
        )
    audit.log(db, user.username, user.role, "correct", "incident", iid, {"changes": updates, "reason": reason[:500]})
    return get_incident(db, iid)


def set_legal_hold(db: Database, iid: str, user, hold: bool, reason: str) -> dict:
    if not reason.strip():
        raise ReviewError("Укажите основание")
    with db.connect() as conn:
        conn.execute("UPDATE incidents SET legal_hold = ?, updated_at = ? WHERE id = ?", (int(hold), now_iso(), iid))
    audit.log(db, user.username, user.role, "legal_hold", "incident", iid, {"hold": hold, "reason": reason[:500]})
    return get_incident(db, iid)


def delete_incident(db: Database, iid: str) -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT material_id FROM incidents WHERE id = ?", (iid,)).fetchone()
        if not row:
            return False
        mid = row["material_id"]
        ids = [mid] + [r["id"] for r in conn.execute("SELECT id FROM materials WHERE duplicate_of = ?", (mid,)).fetchall()]
        marks = ", ".join("?" for _ in ids)
        conn.execute(f"DELETE FROM edges WHERE src IN ({marks}) OR dst IN ({marks})", (*ids, *ids))
        conn.execute(f"DELETE FROM materials WHERE id IN ({marks})", ids)
    return True


def purge_expired(db: Database) -> dict:
    policy = retention_policy(db)
    now = now_iso()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM incidents WHERE legal_hold = 0 AND retention_until IS NOT NULL AND retention_until < ?", (now,)
        ).fetchall()
    removed = sum(1 for r in rows if delete_incident(db, r["id"]))
    cutoff_search = (datetime.now(timezone.utc) - timedelta(days=policy["search_history_days"])).replace(microsecond=0).isoformat()
    with db.connect() as conn:
        searches = conn.execute("DELETE FROM searches WHERE created_at < ?", (cutoff_search,)).rowcount
        conn.execute("DELETE FROM connector_runs WHERE started_at < ?", (cutoff_search,))
        # Материалы-сироты (дубли без основной карточки)
        conn.execute("DELETE FROM materials WHERE duplicate_of IS NOT NULL AND duplicate_of NOT IN (SELECT id FROM materials)")
    cutoff_audit = (datetime.now(timezone.utc) - timedelta(days=policy["audit_days"])).replace(microsecond=0).isoformat()
    audit_removed = audit.purge_older_than(db, cutoff_audit)
    return {"incidents": removed, "searches": searches, "audit_entries": audit_removed}


# ------------------------------------------------------------ панели
def timeline(db: Database, f: dict, field: str = "published") -> list[dict]:
    where, args = _filters_sql(f)
    col = "COALESCE(i.published_at, i.detected_at)" if field == "published" else "i.detected_at"
    sql = (
        f"SELECT SUBSTR({col}, 1, 10) AS day, COALESCE(i.priority, 'none') AS priority, COUNT(*) AS n "
        f"FROM incidents i JOIN materials m ON m.id = i.material_id WHERE {where} GROUP BY day, priority ORDER BY day"
    )
    with db.connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    days: dict[str, dict] = {}
    for r in rows:
        d = days.setdefault(r["day"], {"day": r["day"], "total": 0})
        d[r["priority"]] = r["n"]
        d["total"] += r["n"]
    return list(days.values())


def stats(db: Database, demo: str = "include") -> dict:
    where = "1=1" if demo == "include" else ("i.is_demo = 0" if demo == "exclude" else "i.is_demo = 1")
    with db.connect() as conn:
        by_priority = {r[0] or "none": r[1] for r in conn.execute(
            f"SELECT priority, COUNT(*) FROM incidents i WHERE is_threat = 1 AND {where} GROUP BY priority")}
        by_category = {r[0]: r[1] for r in conn.execute(
            f"SELECT category, COUNT(*) FROM incidents i WHERE {where} GROUP BY category")}
        pending = conn.execute(
            f"SELECT COUNT(*) FROM incidents i WHERE requires_review = 1 AND status IN ('new','needs_review','in_review') AND {where}"
        ).fetchone()[0]
        total = conn.execute(f"SELECT COUNT(*) FROM incidents i WHERE {where}").fetchone()[0]
        demo_count = conn.execute("SELECT COUNT(*) FROM incidents WHERE is_demo = 1").fetchone()[0]
        platforms = {r[0]: r[1] for r in conn.execute(
            f"SELECT platform, COUNT(*) FROM incidents i WHERE is_threat = 1 AND {where} GROUP BY platform")}
        langs = {r[0]: r[1] for r in conn.execute(
            f"SELECT lang, COUNT(*) FROM incidents i WHERE is_threat = 1 AND {where} GROUP BY lang")}
    return {
        "total_materials": total,
        "by_priority": by_priority,
        "by_category": by_category,
        "pending_mandatory_review": pending,
        "demo_incidents": demo_count,
        "by_platform": platforms,
        "by_lang": langs,
    }


def graph(db: Database, f: dict, include_similarity: bool = False, focus: str | None = None) -> dict:
    """Граф распространения материалов: узлы — материалы, рёбра — подтверждённые связи."""
    with db.connect() as conn:
        edge_rows = conn.execute(
            "SELECT * FROM edges" + ("" if include_similarity else " WHERE is_evidence = 1")
        ).fetchall()
        edges = [dict(e) for e in edge_rows]
        if focus:
            # Компонента связности вокруг выбранного материала
            keep = {focus}
            changed = True
            while changed:
                changed = False
                for e in edges:
                    if (e["src"] in keep) != (e["dst"] in keep):
                        keep |= {e["src"], e["dst"]}
                        changed = True
            edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
        node_ids = {e["src"] for e in edges} | {e["dst"] for e in edges}
        if focus:
            node_ids.add(focus)
        nodes = []
        for nid in node_ids:
            if nid.startswith("url:"):
                nodes.append({"id": nid, "external": True, "label": nid[4:][:60], "source_url": nid[4:]})
                continue
            m = conn.execute(
                "SELECT m.id, m.platform, m.source_url, m.source_name, m.published_at, m.excerpt, m.is_demo, m.duplicate_of, "
                "COALESCE(i.priority, ip.priority) AS priority, COALESCE(i.category, ip.category) AS category, "
                "COALESCE(i.id, ip.id) AS incident_id, COALESCE(i.is_threat, ip.is_threat) AS is_threat "
                "FROM materials m LEFT JOIN incidents i ON i.material_id = m.id "
                "LEFT JOIN incidents ip ON ip.material_id = m.duplicate_of WHERE m.id = ?",
                (nid,),
            ).fetchone()
            if not m:
                continue
            nodes.append({
                "id": m["id"],
                "external": False,
                "platform": m["platform"],
                "source_name": m["source_name"],
                "source_url": m["source_url"],
                "published_at": m["published_at"],
                "excerpt": (m["excerpt"] or "")[:160],
                "is_demo": bool(m["is_demo"]),
                "priority": m["priority"],
                "category": m["category"],
                "category_label": CATEGORIES.get(m["category"], m["category"]) if m["category"] else "",
                "incident_id": m["incident_id"],
                "is_threat": bool(m["is_threat"]),
                "is_copy": bool(m["duplicate_of"]),
            })
    if f.get("demo") == "exclude":
        nodes = [n for n in nodes if not n.get("is_demo")]
        ids = {n["id"] for n in nodes}
        edges = [e for e in edges if e["src"] in ids and e["dst"] in ids]
    for e in edges:
        e["relation_label"] = RELATION_LABELS.get(e["relation"], e["relation"])
    return {"nodes": nodes, "edges": edges, "relation_labels": RELATION_LABELS}


def propagation_count(db: Database, mid: str) -> int:
    with db.connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM edges WHERE src = ? AND is_evidence = 1 AND relation IN ('repost', 'quote', 'link')", (mid,)
        ).fetchone()[0]
