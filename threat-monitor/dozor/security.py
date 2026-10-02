"""Пользователи, роли и разграничение доступа (RBAC)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .db import Database, now_iso

ROLES = {
    "viewer": "Наблюдатель — просмотр карточек и панели",
    "analyst": "Аналитик — поиск, проверка, исправление оценок, импорт",
    "supervisor": "Руководитель — подтверждение критических, экспорт, журнал, удаление",
    "admin": "Администратор — пользователи, источники, сроки хранения",
}
ROLE_ORDER = ["viewer", "analyst", "supervisor", "admin"]

PERMISSIONS = {
    "view": "viewer",
    "view_context": "analyst",
    "search": "analyst",
    "analyze_text": "analyst",
    "review": "analyst",
    "correct": "analyst",
    "import": "analyst",
    "confirm_critical": "supervisor",
    "export": "supervisor",
    "audit": "supervisor",
    "delete": "supervisor",
    "legal_hold": "supervisor",
    "manage_users": "admin",
    "manage_settings": "admin",
    "purge": "admin",
}


@dataclass
class User:
    id: int
    username: str
    role: str

    def can(self, permission: str) -> bool:
        need = PERMISSIONS.get(permission)
        if need is None:
            return False
        return ROLE_ORDER.index(self.role) >= ROLE_ORDER.index(need)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "role_label": ROLES[self.role],
            "permissions": sorted(p for p in PERMISSIONS if self.can(p)),
        }


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    candidate = hash_password(password, bytes.fromhex(salt_hex)).split("$")[2]
    return hmac.compare_digest(candidate, digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user(db: Database, username: str, password: str, role: str) -> User:
    if role not in ROLES:
        raise ValueError(f"Неизвестная роль: {role}")
    if len(password) < 10:
        raise ValueError("Пароль должен содержать не менее 10 символов")
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, pw_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), role, now_iso()),
        )
        return User(cur.lastrowid, username, role)


def list_users(db: Database) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT id, username, role, active, created_at FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def set_user_active(db: Database, user_id: int, active: bool) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def authenticate(db: Database, username: str, password: str) -> User | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
    if row is None:
        # Одинаковое время ответа для существующих и несуществующих пользователей
        verify_password(password, hash_password("timing-equalizer"))
        return None
    if not verify_password(password, row["pw_hash"]):
        return None
    return User(row["id"], row["username"], row["role"])


def create_session(db: Database, user: User, hours: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=hours)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), user.id, now_iso(), expires.replace(microsecond=0).isoformat()),
        )
    return token


def user_for_token(db: Database, token: str | None) -> User | None:
    if not token:
        return None
    with db.connect() as conn:
        row = conn.execute(
            "SELECT u.id, u.username, u.role, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND u.active = 1",
            (_token_hash(token),),
        ).fetchone()
    if row is None:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        drop_session(db, token)
        return None
    return User(row["id"], row["username"], row["role"])


def drop_session(db: Database, token: str) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def purge_sessions(db: Database) -> int:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
        return cur.rowcount
