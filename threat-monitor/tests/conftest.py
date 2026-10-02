import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


import os

from dozor.envfile import FIELDS

ENV_PREFIXES = ("DOZOR_", "VK_", "YOUTUBE_", "TELEGRAM_", "BSKY_", "THREADS_", "INSTAGRAM_", "TIKTOK_", "TWITCH_",
                "DISCORD_", "X_BEARER", "ANTHROPIC_")


def _clean_env(monkeypatch, tmp_path):
    keys = {k for k in os.environ if k.startswith(ENV_PREFIXES)} | {f.name for f in FIELDS}
    for key in keys:
        # setenv + delenv: monkeypatch восстановит исходное состояние после теста,
        # даже если код (сохранение настроек) изменит os.environ напрямую
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)
    monkeypatch.setenv("DOZOR_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("DOZOR_SECRET", "test-secret")
    monkeypatch.setenv("DOZOR_ENV_FILE", str(tmp_path / "test.env"))


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Учебный режим (демо-данные) — для проверки классификатора и интерфейса API."""
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DOZOR_DEMO", "1")
    from dozor.config import load_settings

    return load_settings()


@pytest.fixture
def work_settings(tmp_path, monkeypatch):
    """Рабочий режим: без демо-данных, как у пользователя после Start-Dozor.bat."""
    _clean_env(monkeypatch, tmp_path)
    from dozor.config import load_settings

    return load_settings()
