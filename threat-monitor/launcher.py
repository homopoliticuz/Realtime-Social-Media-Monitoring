"""Запуск «Дозора» одним действием (его вызывает Start-Dozor.bat).

При первом запуске: создаёт окружение Python (.venv), устанавливает
зависимости, создаёт базу с демо-данными и учётными записями (пароли
сохраняются в файл ПАРОЛИ.txt). Затем запускает сервер и открывает браузер.
При следующих запусках сразу стартует сервер.
"""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / ".deps-installed"
DB_FILE = ROOT / "var" / "dozor.sqlite3"
CREDENTIALS = ROOT / "ПАРОЛИ.txt"
PORTS = range(8080, 8100)


def say(message: str = "") -> None:
    print(message, flush=True)


def fail(message: str) -> None:
    say()
    say("ОШИБКА: " + message)
    sys.exit(1)


def _local_open(url: str, timeout: float = 1.0):
    # Прямое подключение к локальному серверу, без системного прокси
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(url, timeout=timeout)


def is_dozor(port: int) -> bool:
    try:
        with _local_open(f"http://127.0.0.1:{port}/") as resp:
            return "Дозор" in resp.read().decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001 — сервер не запущен или занят другим приложением
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


# --------------------------------------------------------------- внешний этап
def outer() -> None:
    """Запускается системным Python: готовит .venv и передаёт управление ему."""
    if sys.version_info < (3, 10):
        fail(f"нужен Python 3.10 или новее, найден {sys.version.split()[0]}. "
             "Установите свежую версию с https://www.python.org/downloads/")
    say("=" * 60)
    say("  ДОЗОР — мониторинг угроз в общедоступном контенте")
    say("=" * 60)
    if not VENV_PY.exists():
        say("[1/4] Создаю окружение Python в папке .venv (один раз)...")
        result = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
        if result.returncode != 0 or not VENV_PY.exists():
            fail("не удалось создать окружение .venv. Удалите папку .venv и запустите снова.")
    else:
        say("[1/4] Окружение Python готово.")
    result = subprocess.run([str(VENV_PY), str(Path(__file__).resolve()), "--inner"], cwd=ROOT)
    sys.exit(result.returncode)


# --------------------------------------------------------------- внутренний этап
def install_dependencies() -> None:
    wanted = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if STAMP.exists() and STAMP.read_text(encoding="utf-8").strip() == wanted:
        say("[2/4] Зависимости установлены.")
        return
    say("[2/4] Устанавливаю зависимости (нужен интернет, 1–2 минуты, один раз)...")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                             "--quiet", "-r", str(REQUIREMENTS)])
    if result.returncode != 0:
        fail("не удалось установить зависимости. Проверьте подключение к интернету и запустите снова.")
    STAMP.write_text(wanted, encoding="utf-8")


def initialize_database() -> None:
    if DB_FILE.exists():
        say("[3/4] База данных найдена.")
        return
    say("[3/4] Создаю базу, учётные записи и демонстрационные данные...")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-m", "dozor", "init", "--demo"], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        say(result.stdout)
        say(result.stderr)
        fail("не удалось создать базу данных.")
    output = result.stdout.strip()
    CREDENTIALS.write_text(
        "ДОЗОР — учётные записи\n"
        "=======================\n\n"
        f"{output}\n\n"
        "Адрес: http://127.0.0.1:8080 (или порт, указанный в окне запуска)\n\n"
        "Храните этот файл в надёжном месте. Лучше перенести пароли в менеджер паролей\n"
        "и удалить файл. Новый пользователь создаётся командой:\n"
        "  .venv\\Scripts\\python -m dozor create-user ИМЯ admin\n",
        encoding="utf-8-sig",
    )
    say()
    say(output)
    say()
    say(f"Пароли сохранены в файл «{CREDENTIALS.name}» в папке программы.")
    if os.name == "nt":
        try:
            os.startfile(str(CREDENTIALS))  # type: ignore[attr-defined]
        except OSError:
            pass


def choose_port() -> tuple[int, bool]:
    for port in PORTS:
        if is_dozor(port):
            return port, True
        if port_free(port):
            return port, False
    fail("все порты 8080–8099 заняты. Закройте лишние программы и запустите снова.")
    raise SystemExit(1)


def open_browser_when_ready(url: str, port: int) -> None:
    for _ in range(120):
        if is_dozor(port):
            webbrowser.open(url)
            return
        time.sleep(0.5)


def inner() -> None:
    """Запускается из .venv: зависимости, база, сервер."""
    install_dependencies()
    initialize_database()
    port, running = choose_port()
    url = f"http://127.0.0.1:{port}/"
    if running:
        say(f"[4/4] Дозор уже запущен: {url} — открываю браузер.")
        webbrowser.open(url)
        return
    os.environ["DOZOR_PORT"] = str(port)
    sys.path.insert(0, str(ROOT))
    import uvicorn

    from dozor.api import create_app
    from dozor.config import load_settings
    from dozor.db import Database

    settings = load_settings()
    app = create_app(settings, Database(settings.db_path))
    say(f"[4/4] Запускаю сервер: {url}")
    say()
    say("  Браузер откроется автоматически. Если нет — откройте адрес вручную.")
    say("  Логины и пароли — в файле ПАРОЛИ.txt.")
    say("  Чтобы остановить Дозор, закройте это окно (или нажмите Ctrl+C).")
    say()
    threading.Thread(target=open_browser_when_ready, args=(url, port), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    try:
        if "--inner" in sys.argv:
            inner()
        else:
            outer()
    except KeyboardInterrupt:
        say("\nДозор остановлен.")
