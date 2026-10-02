"""Хранилище SQLite: материалы, оценки, инциденты, связи, поиски, журнал."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- Собранные публичные материалы. Хранится только необходимый фрагмент текста
-- (с маскированием телефонов и e-mail) и минимальные метаданные. Идентификаторы
-- авторов комментариев не сохраняются (author_kind = 'user_hidden').
CREATE TABLE IF NOT EXISTS materials (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    connector TEXT NOT NULL,
    external_id TEXT,
    source_url TEXT,
    source_name TEXT,
    source_kind TEXT,
    author_kind TEXT,
    published_at TEXT,
    collected_at TEXT NOT NULL,
    lang TEXT,
    lang_label TEXT,
    lang_confidence REAL,
    script TEXT,
    excerpt TEXT NOT NULL,
    context TEXT,
    content_hash TEXT,
    evidence_hash TEXT,
    simhash TEXT,
    duplicate_of TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    provenance TEXT,
    legal_basis TEXT,
    pii_masked INTEGER NOT NULL DEFAULT 0,
    meta TEXT,
    UNIQUE (platform, external_id)
);
CREATE INDEX IF NOT EXISTS ix_materials_hash ON materials(content_hash);
CREATE INDEX IF NOT EXISTS ix_materials_pub ON materials(published_at);
CREATE INDEX IF NOT EXISTS ix_materials_dup ON materials(duplicate_of);

CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY,
    material_id TEXT NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    engine TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    is_threat INTEGER NOT NULL,
    category TEXT NOT NULL,
    priority TEXT,
    severity TEXT,
    confidence REAL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assess_material ON assessments(material_id, is_current);

-- Карточка опасного материала (не досье на человека)
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    material_id TEXT UNIQUE NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    is_threat INTEGER NOT NULL,
    category TEXT NOT NULL,
    priority TEXT,
    severity TEXT,
    confidence REAL,
    auto_category TEXT,
    auto_priority TEXT,
    corrected INTEGER NOT NULL DEFAULT 0,
    requires_review INTEGER NOT NULL DEFAULT 0,
    review_reason TEXT,
    analyst_note TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    second_reviewed_by TEXT,
    legal_hold INTEGER NOT NULL DEFAULT 0,
    retention_until TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    platform TEXT,
    lang TEXT,
    countries TEXT,
    published_at TEXT,
    detected_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_inc_priority ON incidents(priority);
CREATE INDEX IF NOT EXISTS ix_inc_status ON incidents(status);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    user TEXT NOT NULL,
    created_at TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT NOT NULL
);

-- Подтверждённые связи между МАТЕРИАЛАМИ (не между людьми).
-- src — исходный материал, dst — производный (репост, цитата, ссылка, ответ).
-- relation = text_similarity хранится с is_evidence = 0 и не является связью.
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    relation TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_detail TEXT,
    evidence_url TEXT,
    observed_at TEXT NOT NULL,
    is_evidence INTEGER NOT NULL DEFAULT 1,
    similarity REAL,
    is_demo INTEGER NOT NULL DEFAULT 0,
    UNIQUE (src, dst, relation)
);

CREATE TABLE IF NOT EXISTS searches (
    id TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    purpose TEXT,
    params TEXT NOT NULL,
    expansion TEXT,
    status TEXT NOT NULL,
    stats TEXT,
    errors TEXT,
    stages TEXT
);

CREATE TABLE IF NOT EXISTS search_results (
    search_id TEXT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    material_id TEXT NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    matched_terms TEXT,
    PRIMARY KEY (search_id, material_id)
);

CREATE TABLE IF NOT EXISTS connector_runs (
    id INTEGER PRIMARY KEY,
    search_id TEXT,
    connector TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    requests INTEGER DEFAULT 0,
    items INTEGER DEFAULT 0,
    error TEXT,
    queries TEXT
);

-- Журнал действий с хэш-цепочкой (защита от незаметного изменения)
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user TEXT NOT NULL,
    role TEXT,
    action TEXT NOT NULL,
    object_type TEXT,
    object_id TEXT,
    details TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn: sqlite3.Connection | None = None
        if str(self.path) == ":memory:":
            self._memory_conn = self._new_conn()
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._memory_conn or self._new_conn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                if self._memory_conn is None:
                    conn.close()

    # ------------------------------------------------------------ settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return loads(row["value"], default) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, dumps(value)),
            )


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
