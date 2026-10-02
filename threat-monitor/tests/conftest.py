import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith(("DOZOR_", "VK_", "YOUTUBE_", "TELEGRAM_", "BSKY_", "THREADS_", "INSTAGRAM_", "TIKTOK_", "TWITCH_", "DISCORD_", "X_BEARER")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DOZOR_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("DOZOR_SECRET", "test-secret")
    from dozor.config import load_settings

    return load_settings()
