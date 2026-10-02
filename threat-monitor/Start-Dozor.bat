@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Дозор

if not exist "%~dp0launcher.py" goto notextracted

rem Python пишет в канал в UTF-8 (пути с кириллицей в имени пользователя)
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

rem --- Поиск Python 3.10+ ---
set "PYEXE="
for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable) if sys.version_info>=(3,10) else None" 2^>nul') do set "PYEXE=%%P"
if defined PYEXE goto run
for /f "delims=" %%P in ('python -c "import sys;print(sys.executable) if sys.version_info>=(3,10) else None" 2^>nul') do set "PYEXE=%%P"
if defined PYEXE goto run
call :findlocal
if defined PYEXE goto run
goto nopython

:run
echo Python: %PYEXE%
"%PYEXE%" "%~dp0launcher.py"
if errorlevel 1 goto failed
goto end

:findlocal
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do if exist "%%~D\python.exe" set "PYEXE=%%~D\python.exe"
exit /b 0

:nopython
echo.
echo Python 3.10 или новее не найден на этом компьютере.
where winget >nul 2>nul
if errorlevel 1 goto manual
echo.
echo 1 - установить Python 3.12 автоматически
echo 2 - скачать Python вручную с python.org
choice /c 12 /n /m "Нажмите 1 или 2: "
if errorlevel 2 goto manual
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
call :findlocal
if defined PYEXE goto run
echo.
echo Python установлен. Закройте это окно и снова запустите Start-Dozor.bat.
pause
exit /b 1

:manual
echo.
echo Скачайте Python 3.12 с https://www.python.org/downloads/
echo При установке обязательно отметьте "Add python.exe to PATH",
echo затем снова запустите Start-Dozor.bat.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:notextracted
echo.
echo Сначала распакуйте архив: правый клик по ZIP-файлу - "Извлечь все",
echo затем запустите Start-Dozor.bat из распакованной папки.
pause
exit /b 1

:failed
echo.
echo Дозор завершился с ошибкой. Причина указана выше.
pause
exit /b 1

:end
endlocal
