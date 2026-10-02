"""Рабочий режим: без демо-данных, источники настраиваются из интерфейса."""

import httpx
import pytest
from fastapi.testclient import TestClient

from dozor import repository as repo
from dozor import security
from dozor.api import create_app
from dozor.connectors.demo import demo_items
from dozor.connectors.registry import build_connectors
from dozor.db import Database
from dozor.pipeline import Pipeline

H = {"X-Requested-With": "dozor"}


def _offline(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, text="offline")


@pytest.fixture
def admin_client(work_settings):
    db = Database(work_settings.db_path)
    security.create_user(db, "admin", "admin-pass-123", "admin")
    security.create_user(db, "analyst", "analyst-pass-123", "analyst")
    app = create_app(work_settings, db, transport=httpx.MockTransport(_offline))
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"}, headers=H)
        token = r.json()["token"]
        c.headers.update({**H, "Authorization": f"Bearer {token}"})
        yield c, db, work_settings


def test_no_demo_anywhere_by_default(admin_client):
    c, db, settings = admin_client
    assert not settings.demo_enabled
    meta = c.get("/api/meta").json()
    assert meta["demo_enabled"] is False
    names = {x["name"] for x in c.get("/api/connectors").json()}
    assert "demo" not in names
    assert c.post("/api/demo/load").status_code == 404
    assert c.get("/api/incidents").json()["total"] == 0


def test_search_include_demo_is_ignored_in_work_mode(admin_client):
    c, db, settings = admin_client
    r = c.post("/api/search", json={"query": "убить", "include_demo": True, "connectors": [],
                                    "purpose": "Мониторинг угроз насилия"})
    assert r.status_code == 200
    import time
    for _ in range(200):
        s = c.get(f"/api/search/{r.json()['search_id']}").json()
        if s["status"] != "running":
            break
        time.sleep(0.05)
    plan = next(e for e in s["events"] if e["stage"] == "plan" and e["status"] == "done")
    assert "demo" not in plan["active"]
    # Источники недоступны (тестовый транспорт без сети) — итог подсказывает, что проверить
    assert "Ни один источник не ответил" in s["events"][-1]["message"]
    assert c.get("/api/incidents").json()["total"] == 0


def test_leftover_demo_data_removed_on_start(work_settings):
    db = Database(work_settings.db_path)
    system = security.User(0, "system", "admin")
    Pipeline(db, work_settings, build_connectors(work_settings)).ingest(demo_items(), system)
    assert repo.stats(db)["demo_incidents"] > 0
    create_app(work_settings, db)
    assert repo.stats(db)["demo_incidents"] == 0
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials WHERE is_demo = 1").fetchone()[0] == 0


def test_sources_configured_from_ui_apply_without_restart(admin_client):
    c, db, settings = admin_client
    before = {x["name"]: x["status"]["state"] for x in c.get("/api/connectors").json()}
    assert before["telegram_web"] == "watchlist_needed" and before["vk"] == "not_configured"
    r = c.put("/api/settings/sources", json={"values": {
        "DOZOR_TELEGRAM_CHANNELS": "@channel_one\nhttps://t.me/channel_two, channel_three",
        "VK_ACCESS_TOKEN": "vk-secret-token-1234",
    }})
    assert r.status_code == 200, r.text
    assert set(r.json()["changed"]) == {"DOZOR_TELEGRAM_CHANNELS", "VK_ACCESS_TOKEN"}
    after = {x["name"]: x["status"]["state"] for x in r.json()["connectors"]}
    assert after["telegram_web"] == "connected" and after["vk"] == "connected"
    assert settings.telegram_channels == ["@channel_one", "https://t.me/channel_two", "channel_three"]
    # Значения записаны в .env, секрет в интерфейс не возвращается
    env_text = settings.env_file.read_text(encoding="utf-8")
    assert "VK_ACCESS_TOKEN=vk-secret-token-1234" in env_text
    data = c.get("/api/settings/sources").json()
    fields = {f["name"]: f for g in data["groups"] for f in g["fields"]}
    assert fields["VK_ACCESS_TOKEN"]["value"] == "" and fields["VK_ACCESS_TOKEN"]["is_set"]
    assert fields["VK_ACCESS_TOKEN"]["hint"].endswith("1234")
    assert fields["DOZOR_TELEGRAM_CHANNELS"]["value"].splitlines()[0] == "@channel_one"
    # Пустой секрет не стирает сохранённый, очистка — явная
    c.put("/api/settings/sources", json={"values": {"VK_ACCESS_TOKEN": ""}})
    assert "vk-secret-token-1234" in settings.env_file.read_text(encoding="utf-8")
    r = c.put("/api/settings/sources", json={"values": {}, "clear": ["VK_ACCESS_TOKEN"]})
    assert {x["name"]: x["status"]["state"] for x in r.json()["connectors"]}["vk"] == "not_configured"
    # В журнал попадают только имена полей
    entries = c.get("/api/audit?action=settings_change").json()
    assert entries and "vk-secret" not in str(entries)


def test_sources_settings_admin_only_and_validated(admin_client):
    c, db, settings = admin_client
    r = c.put("/api/settings/sources", json={"values": {"DOZOR_TELEGRAM_PAGES": "много"}})
    assert r.status_code == 400
    c.headers.pop("Authorization")
    r = c.post("/api/auth/login", json={"username": "analyst", "password": "analyst-pass-123"}, headers=H)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    assert c.get("/api/settings/sources").status_code == 403
