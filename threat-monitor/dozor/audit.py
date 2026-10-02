"""Журнал действий с хэш-цепочкой.

Каждая запись содержит SHA-256 от предыдущего хэша и канонического
представления записи. Изменение или удаление записи в середине журнала
обнаруживается проверкой ``verify``. При удалении записей по сроку
хранения последний удалённый хэш сохраняется как «якорь» цепочки.

В журнал не пишется текст материалов — только идентификаторы и параметры
действий (что сделано, кем, когда и с каким обоснованием).
"""

from __future__ import annotations

import hashlib

from .db import Database, dumps, loads, now_iso

GENESIS = "0" * 64

ACTION_LABELS = {
    "login": "Вход в систему",
    "login_failed": "Неудачная попытка входа",
    "logout": "Выход",
    "search": "Поиск",
    "search_rejected": "Поиск отклонён политикой",
    "analyze_text": "Разовый анализ текста",
    "view_incident": "Просмотр карточки",
    "view_context": "Просмотр контекста",
    "review": "Изменение статуса проверки",
    "correct": "Исправление оценки",
    "legal_hold": "Изменение режима хранения (legal hold)",
    "import": "Импорт материалов",
    "export": "Экспорт",
    "delete": "Удаление материала",
    "purge": "Удаление по сроку хранения",
    "user_create": "Создание пользователя",
    "user_update": "Изменение пользователя",
    "settings_change": "Изменение настроек",
    "connector_test": "Проверка подключения источника",
    "demo_load": "Загрузка демонстрационных данных",
}


def _entry_hash(prev_hash: str, entry: dict) -> str:
    canonical = dumps({k: entry[k] for k in ("ts", "user", "role", "action", "object_type", "object_id", "details")})
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def log(db: Database, user: str, role: str | None, action: str, object_type: str | None = None,
        object_id: str | None = None, details: dict | None = None) -> None:
    entry = {
        "ts": now_iso(),
        "user": user,
        "role": role,
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "details": dumps(details or {}),
    }
    with db.connect() as conn:
        row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            prev = row["hash"]
        else:
            anchor = conn.execute("SELECT value FROM settings WHERE key = 'audit_anchor'").fetchone()
            prev = loads(anchor["value"], GENESIS) if anchor else GENESIS
        entry_hash = _entry_hash(prev, entry)
        conn.execute(
            "INSERT INTO audit_log(ts, user, role, action, object_type, object_id, details, prev_hash, hash) "
            "VALUES (:ts, :user, :role, :action, :object_type, :object_id, :details, :prev, :hash)",
            {**entry, "prev": prev, "hash": entry_hash},
        )


def entries(db: Database, limit: int = 200, offset: int = 0, object_id: str | None = None,
            action: str | None = None, user: str | None = None) -> list[dict]:
    where, args = [], []
    if object_id:
        where.append("object_id = ?")
        args.append(object_id)
    if action:
        where.append("action = ?")
        args.append(action)
    if user:
        where.append("user = ?")
        args.append(user)
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    with db.connect() as conn:
        rows = conn.execute(sql, (*args, limit, offset)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["details"] = loads(d["details"], {})
        d["action_label"] = ACTION_LABELS.get(d["action"], d["action"])
        out.append(d)
    return out


def verify(db: Database) -> dict:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        anchor = conn.execute("SELECT value FROM settings WHERE key = 'audit_anchor'").fetchone()
    prev = loads(anchor["value"], GENESIS) if anchor else GENESIS
    for r in rows:
        d = dict(r)
        if d["prev_hash"] != prev or _entry_hash(prev, d) != d["hash"]:
            return {"ok": False, "checked": len(rows), "broken_at": d["id"],
                    "message": f"Цепочка журнала нарушена на записи #{d['id']}"}
        prev = d["hash"]
    return {"ok": True, "checked": len(rows), "message": f"Цепочка журнала цела ({len(rows)} записей)"}


def purge_older_than(db: Database, cutoff_iso: str) -> int:
    with db.connect() as conn:
        last = conn.execute(
            "SELECT id, hash FROM audit_log WHERE ts < ? ORDER BY id DESC LIMIT 1", (cutoff_iso,)
        ).fetchone()
        if not last:
            return 0
        cur = conn.execute("DELETE FROM audit_log WHERE id <= ?", (last["id"],))
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('audit_anchor', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (dumps(last["hash"]),),
        )
        return cur.rowcount
