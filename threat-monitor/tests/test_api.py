import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from dozor import security
from dozor.api import create_app
from dozor.db import Database

H = {"X-Requested-With": "dozor"}
PASSWORDS = {"admin": "admin-pass-123", "analyst": "analyst-pass-123", "supervisor": "super-pass-123", "viewer": "viewer-pass-123", "analyst2": "analyst2-pass-123"}


def _offline(request: httpx.Request) -> httpx.Response:
    # Тесты не ходят в сеть: любой внешний запрос получает 503
    return httpx.Response(503, json={"error": {"message": "offline test transport"}})


@pytest.fixture
def client(settings):
    db = Database(settings.db_path)
    for name, pw in PASSWORDS.items():
        security.create_user(db, name, pw, "analyst" if name == "analyst2" else name)
    app = create_app(settings, db, transport=httpx.MockTransport(_offline))
    with TestClient(app) as c:
        yield c


def login(client, name):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": name, "password": PASSWORDS[name]}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth(token):
    return {**H, "Authorization": f"Bearer {token}"}


def run_demo_search(client, token, **extra):
    body = {"query": "", "topics": ["violence_threats", "terrorism_recruitment", "harassment", "ethnic_religious_incitement", "dangerous_involvement"],
            "include_demo": True, "connectors": ["demo"], "purpose": "Проверка работы системы на демо-данных", **extra}
    r = client.post("/api/search", json=body, headers=auth(token))
    assert r.status_code == 200, r.text
    sid = r.json()["search_id"]
    for _ in range(200):
        s = client.get(f"/api/search/{sid}", headers=auth(token)).json()
        if s["status"] != "running":
            return sid, s
        time.sleep(0.05)
    raise AssertionError("поиск не завершился")


def test_requires_login_and_csrf_header(client):
    assert client.get("/api/incidents").status_code == 401
    r = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORDS["admin"]})
    assert r.status_code == 403  # нет X-Requested-With


def test_login_rate_limit(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "viewer", "password": "wrong-password"}, headers=H)
    r = client.post("/api/auth/login", json={"username": "viewer", "password": PASSWORDS["viewer"]}, headers=H)
    assert r.status_code == 429


def test_rbac(client):
    viewer = login(client, "viewer")
    assert client.get("/api/incidents", headers=auth(viewer)).status_code == 200
    assert client.post("/api/search", json={"query": "убить", "purpose": "тестовая цель"}, headers=auth(viewer)).status_code == 403
    assert client.get("/api/audit", headers=auth(viewer)).status_code == 403
    analyst = login(client, "analyst")
    assert client.get("/api/export", headers=auth(analyst)).status_code == 403
    assert client.get("/api/users", headers=auth(analyst)).status_code == 403
    sup = login(client, "supervisor")
    assert client.get("/api/audit", headers=auth(sup)).status_code == 200
    assert client.get("/api/users", headers=auth(sup)).status_code == 403


def test_search_policy_rejections(client):
    analyst = login(client, "analyst")
    r = client.post("/api/search", json={"query": "+998 90 123 45 67", "purpose": "найти человека"}, headers=auth(analyst))
    assert r.status_code == 400 and "телефон" in r.json()["detail"]
    r = client.post("/api/search", json={"query": "user@example.com", "purpose": "найти человека"}, headers=auth(analyst))
    assert r.status_code == 400
    r = client.post("/api/search", json={"query": "убить", "purpose": ""}, headers=auth(analyst))
    assert r.status_code == 400 and "цель" in r.json()["detail"].lower()
    sup = login(client, "supervisor")
    actions = [e["action"] for e in client.get("/api/audit", headers=auth(sup)).json()]
    assert "search_rejected" in actions


def test_full_demo_search_flow(client):
    analyst = login(client, "analyst")
    sid, status = run_demo_search(client, analyst)
    stages = [e["stage"] for e in status["events"]]
    for st in ("validate", "expand", "plan", "fetch", "normalize", "filter", "dedup", "classify", "propagation", "store", "done"):
        assert st in stages
    plan = next(e for e in status["events"] if e["stage"] == "plan" and e["status"] == "done")
    assert plan["active"] == ["demo"]
    assert "signal" not in plan["active"]
    done = status["events"][-1]
    assert done["coverage"]["statement"].startswith("Поиск охватывает только")
    data = client.get(f"/api/incidents?search_id={sid}&limit=100", headers=auth(analyst)).json()
    assert data["total"] >= 12
    assert all(i["is_demo"] for i in data["items"])
    # Критические — на обязательной проверке
    crit = [i for i in data["items"] if i["priority"] in ("critical", "very_critical")]
    assert crit and all(i["requires_review"] and i["status"] == "needs_review" for i in crit)

    # Дубли присоединены как копии, есть подтверждённые связи
    copies = [i for i in data["items"] if i["copies"]]
    assert copies
    g = client.get("/api/graph", headers=auth(analyst)).json()
    assert g["edges"] and all(e["is_evidence"] == 1 for e in g["edges"])
    rels = {e["relation"] for e in g["edges"]}
    assert {"repost", "link"} <= rels
    g2 = client.get("/api/graph?similarity=true", headers=auth(analyst)).json()
    sims = [e for e in g2["edges"] if e["relation"] == "text_similarity"]
    assert sims and all(e["is_evidence"] == 0 for e in sims)

    # Временная шкала и статистика
    tl = client.get("/api/timeline", headers=auth(analyst)).json()
    assert tl and all("day" in d for d in tl)
    stats = client.get("/api/stats", headers=auth(analyst)).json()
    assert stats["pending_mandatory_review"] >= 1


def test_card_review_correction_and_two_person_rule(client):
    analyst = login(client, "analyst")
    sid, _ = run_demo_search(client, analyst)
    items = client.get("/api/incidents?priority=very_critical", headers=auth(analyst)).json()["items"]
    iid = items[0]["id"]
    card = client.get(f"/api/incidents/{iid}", headers=auth(analyst)).json()
    assert card["material"]["source_url"].startswith("https://demo.invalid/")
    assert card["assessment"]["explanation"]
    assert card["material"]["evidence_hash"]
    assert "author" not in json.dumps(card["material"]).lower() or card["material"]["author_kind"]

    # Аналитик не может снять критическую оценку единолично
    r = client.post(f"/api/incidents/{iid}/review", json={"status": "false_positive", "note": "ошибка"}, headers=auth(analyst))
    assert r.status_code == 400
    # Подтверждение аналитиком уходит руководителю
    r = client.post(f"/api/incidents/{iid}/review", json={"status": "confirmed", "note": "угроза конкретна"}, headers=auth(analyst))
    assert r.status_code == 200 and r.json()["status"] == "in_review"
    # Второй аналитик может завершить проверку
    analyst2 = login(client, "analyst2")
    r = client.post(f"/api/incidents/{iid}/review", json={"status": "confirmed", "note": "подтверждаю"}, headers=auth(analyst2))
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert r.json()["second_reviewed_by"] == "analyst2"

    # Исправление ошибочной оценки
    other = client.get("/api/incidents?category=ambiguous", headers=auth(analyst2)).json()["items"][0]["id"]
    r = client.post(f"/api/incidents/{other}/correct", json={"category": "no_threat", "reason": "спортивный жаргон"}, headers=auth(analyst2))
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "no_threat" and body["priority"] is None and body["corrected"]
    assert body["auto"]["category"] == "ambiguous"
    assert body["corrections"][0]["reason"] == "спортивный жаргон"
    r = client.post(f"/api/incidents/{other}/correct", json={"category": "harassment", "reason": ""}, headers=auth(analyst2))
    assert r.status_code == 400


def test_export_withholds_unreviewed_critical(client):
    analyst = login(client, "analyst")
    run_demo_search(client, analyst)
    sup = login(client, "supervisor")
    r = client.get("/api/export", headers=auth(sup)).json()
    assert r["withheld_unreviewed_critical"] >= 1
    for item in r["items"]:
        assert "facts" in item and "assessment" in item and item["facts"]["source_url"]
    csv_resp = client.get("/api/export?format=csv", headers=auth(sup))
    assert csv_resp.status_code == 200 and "incident_id" in csv_resp.text


def test_audit_chain_and_tamper_detection(client, settings):
    analyst = login(client, "analyst")
    run_demo_search(client, analyst)
    sup = login(client, "supervisor")
    v = client.get("/api/audit/verify", headers=auth(sup)).json()
    assert v["ok"] and v["checked"] >= 3
    db = Database(settings.db_path)
    with db.connect() as conn:
        conn.execute("UPDATE audit_log SET user = 'intruder' WHERE id = 2")
    v = client.get("/api/audit/verify", headers=auth(sup)).json()
    assert not v["ok"] and v["broken_at"] == 2


def test_retention_purge(client, settings):
    analyst = login(client, "analyst")
    run_demo_search(client, analyst)
    admin = login(client, "admin")
    db = Database(settings.db_path)
    with db.connect() as conn:
        conn.execute("UPDATE incidents SET retention_until = '2000-01-01T00:00:00+00:00' WHERE is_threat = 0")
        held = conn.execute("SELECT id FROM incidents WHERE is_threat = 0 LIMIT 1").fetchone()["id"]
        conn.execute("UPDATE incidents SET legal_hold = 1 WHERE id = ?", (held,))
        before = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    r = client.post("/api/retention/purge", headers=auth(admin)).json()
    assert r["incidents"] >= 1
    with db.connect() as conn:
        after = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM incidents WHERE id = ?", (held,)).fetchone()[0] == 1
    assert after == before - r["incidents"]


def test_import_requires_legal_basis_and_marks_provenance(client):
    analyst = login(client, "analyst")
    payload = json.dumps([{"text": "Ертең мектепке мылтықпен барамын, бәрін атып тастаймын", "platform": "Signal"}])
    r = client.post("/api/import", json={"content": payload, "format": "json", "legal_basis": ""}, headers=auth(analyst))
    assert r.status_code == 400
    r = client.post("/api/import", json={"content": payload, "format": "json", "legal_basis": "Жалоба пользователя №42 с копией сообщения"}, headers=auth(analyst))
    assert r.status_code == 200 and r.json()["counts"]["threats"] == 1
    item = client.get("/api/incidents?platform=Signal", headers=auth(analyst)).json()["items"][0]
    card = client.get(f"/api/incidents/{item['id']}", headers=auth(analyst)).json()
    assert card["material"]["provenance"] == "user_provided"
    assert card["material"]["legal_basis"].startswith("Жалоба")
    assert card["priority"] == "critical"


def test_analyze_endpoint_and_meta(client):
    analyst = login(client, "analyst")
    a = client.post("/api/analyze", json={"text": "I'm going to shoot everyone at school tomorrow"}, headers=auth(analyst)).json()
    assert a["category"] == "direct_threat" and a["requires_human_review"]
    meta = client.get("/api/meta", headers=auth(analyst)).json()
    codes = {l["code"] for l in meta["languages"]}
    assert {"ru", "uz", "en", "kk", "ky", "tg", "tk", "fr", "de", "es", "it", "ko", "ja", "zh", "fa", "ar"} <= codes
    assert set(meta["priorities"]) == {"insufficient_data", "moderate", "high", "critical", "very_critical"}


def test_security_headers_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "Дозор" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/static/data/land-dots.json").status_code == 200


def test_unconfigured_sources_are_not_polled_and_errors_are_shown(client):
    analyst = login(client, "analyst")
    sid, status = run_demo_search(client, analyst, connectors=[])
    plan = next(e for e in status["events"] if e["stage"] == "plan" and e["status"] == "done")
    actions = {p["connector"]: p["action"] for p in plan["plan"]}
    # Без ключей и списков наблюдения источники не опрашиваются и не изображаются охваченными
    for name in ("vk", "youtube", "threads", "instagram", "x", "discord", "telegram_web", "rss"):
        assert actions[name].startswith("пропущен"), (name, actions[name])
    for name in ("signal", "max", "facebook", "odnoklassniki", "tiktok"):
        assert actions[name].startswith("пропущен")
    # Bluesky и Mastodon (mastodon.social по умолчанию) работают без ключей — опрашиваются,
    # ошибка подключения показана
    assert "bluesky" in plan["active"] and "mastodon" in plan["active"]
    done = status["events"][-1]
    assert "Bluesky" in done["coverage"]["failed"]
    assert any(e["status"] == "error" and e.get("connector") == "bluesky" for e in status["events"])
    assert "Signal" in done["coverage"]["unavailable"]


def test_search_progress_visible_only_to_owner_and_supervisor(client):
    analyst = login(client, "analyst")
    sid, _ = run_demo_search(client, analyst)
    other = login(client, "analyst2")
    assert client.get(f"/api/search/{sid}", headers=auth(other)).status_code == 403
    sup = login(client, "supervisor")
    assert client.get(f"/api/search/{sid}", headers=auth(sup)).status_code == 200
