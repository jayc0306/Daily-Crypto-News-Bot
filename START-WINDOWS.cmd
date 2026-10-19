@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
title Shortflow - Daily Benefits

echo [1/4] Checking Docker Desktop...
where docker >nul 2>nul
if errorlevel 1 goto need_docker
docker info >nul 2>nul
if errorlevel 1 goto need_docker
docker compose version >nul 2>nul
if errorlevel 1 goto need_docker

echo [2/4] Preparing private settings. Existing .env will be preserved.
docker run --rm --mount "type=bind,source=%CD%,target=/work" --workdir /work python:3.12-slim python tools/desktop_setup.py
if errorlevel 1 goto failed

echo [3/4] Installing and starting. The first run may take several minutes.
docker compose up -d --build --wait --wait-timeout 120
if errorlevel 1 goto failed

echo [4/4] Opening your private login note and dashboard.
start "" notepad.exe "%CD%\.local\dashboard-login.txt"
start "" "http://127.0.0.1:8000"
echo Ready. Use the login note in the browser, then choose Sample Preview.
echo Telegram and publishing accounts still require separate setup.
echo You can close this window. Keep Docker Desktop running.
pause
exit /b 0

:need_docker
echo Install Docker Desktop for Windows, start it, and use Linux containers.
echo Then double-click START-WINDOWS.cmd again.
echo Installation help: docs\windows-quickstart.md
pause
exit /b 1

:failed
echo Setup did not finish. Read the error above and check Docker Desktop.
echo You may share the error text, but NEVER share .env or the login note.
pause
exit /b 1
