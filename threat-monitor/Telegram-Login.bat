@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Дозор - вход в Telegram API
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Сначала один раз запустите Start-Dozor.bat - он создаст окружение .venv.
  pause
  exit /b 1
)

echo Подключение Telegram API (MTProto) - необязательно.
echo Перед запуском введите api_id и api_hash в интерфейсе:
echo Администрирование - Источники и ключи доступа - Telegram API.
echo.
echo [1/2] Устанавливаю пакет telethon...
"%~dp0.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q telethon
if errorlevel 1 goto failed

echo [2/2] Вход в аккаунт аналитика. Введите номер телефона этого аккаунта
echo       и код, который придёт в Telegram. Сессия сохранится в var\telegram.session.
echo.
"%~dp0.venv\Scripts\python.exe" -m dozor telegram-login
if errorlevel 1 goto failed
echo.
echo Готово. В Дозоре обновите страницу: источник «Telegram — официальный клиентский API» станет подключённым.
pause
exit /b 0

:failed
echo.
echo Не получилось. Проверьте интернет и что api_id и api_hash сохранены в Дозоре.
pause
exit /b 1
