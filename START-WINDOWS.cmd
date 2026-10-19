@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 goto location_failed
title Shortflow - Daily Benefits

if not exist compose.yaml goto incomplete_zip
if not exist .env.example goto incomplete_zip
if not exist tools\desktop_setup.py goto incomplete_zip

echo [1/5] Docker Desktop 설치 확인 중...
where docker >nul 2>nul
if not errorlevel 1 goto check_engine
if exist "%ProgramFiles%\Docker\Docker\resources\bin\docker.exe" set "PATH=%ProgramFiles%\Docker\Docker\resources\bin;%PATH%"
if exist "%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin\docker.exe" set "PATH=%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin;%PATH%"
where docker >nul 2>nul
if errorlevel 1 goto docker_missing

:check_engine
echo [2/5] Docker 엔진 실행 확인 중...
docker info >nul 2>nul
if not errorlevel 1 goto engine_ready
set "SHORTFLOW_DESKTOP_EXE=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if not exist "%SHORTFLOW_DESKTOP_EXE%" set "SHORTFLOW_DESKTOP_EXE=%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe"
if not exist "%SHORTFLOW_DESKTOP_EXE%" goto engine_stopped
echo 설치된 Docker Desktop을 시작합니다. 엔진이 준비될 때까지 기다려 주세요.
start "" "%SHORTFLOW_DESKTOP_EXE%"
set /a SHORTFLOW_ENGINE_TRIES=0 >nul

:wait_engine
docker info >nul 2>nul
if not errorlevel 1 goto engine_ready
set /a SHORTFLOW_ENGINE_TRIES+=1 >nul
if %SHORTFLOW_ENGINE_TRIES% GEQ 30 goto engine_stopped
timeout /t 2 /nobreak >nul
goto wait_engine

:engine_ready
set "SHORTFLOW_DOCKER_OS="
for /f "delims=" %%T in ('docker info --format "{{.OSType}}" 2^>nul') do set "SHORTFLOW_DOCKER_OS=%%T"
if /I "%SHORTFLOW_DOCKER_OS%"=="windows" goto windows_containers
if /I not "%SHORTFLOW_DOCKER_OS%"=="linux" goto engine_stopped
docker compose version >nul 2>nul
if errorlevel 1 goto compose_missing

echo [3/5] 최초 설정에 필요한 Python 이미지를 확인합니다...
docker image inspect python:3.12-slim >nul 2>nul
if not errorlevel 1 goto prepare_settings
docker pull python:3.12-slim
if errorlevel 1 goto image_failed

:prepare_settings
echo [4/5] 비공개 설정을 준비합니다. 기존 .env는 보존합니다.
docker run --rm --mount "type=bind,source=%CD%,target=/work" --workdir /work python:3.12-slim python tools/desktop_setup.py
if errorlevel 1 goto settings_failed

echo [5/5] 앱 설치 및 시작 중입니다. 첫 실행에는 몇 분 걸릴 수 있습니다.
docker compose up -d --build --wait --wait-timeout 120
if errorlevel 1 goto app_failed

start "" notepad.exe "%CD%\.local\dashboard-login.txt"
start "" "http://127.0.0.1:8000"
echo.
echo 실행 완료. 열린 메모장의 로그인 정보로 브라우저에 로그인하세요.
echo 로그인 후 [샘플 미리보기]를 누르면 영상 제작을 확인할 수 있습니다.
echo 이 창은 닫아도 됩니다. Docker Desktop은 켜 두세요.
echo 텔레그램과 업로드 계정은 다음 단계에서 별도로 연결합니다.
set /p "SHORTFLOW_CLOSE=확인 후 Enter 키를 누르면 이 창이 닫힙니다: "
exit /b 0

:location_failed
echo [폴더 접근 실패] 압축을 로컬 PC의 다운로드 폴더 등에 풀고 실행하세요.
goto stopped

:incomplete_zip
echo [압축 해제 필요] 실행에 필요한 파일이 없습니다.
echo ZIP 안에서 바로 실행하지 말고 [모두 압축 풀기]를 한 뒤 실행하세요.
goto stopped

:docker_missing
echo [Docker 미설치] Docker 실행 파일을 찾지 못했습니다.
echo 지금 열리는 공식 페이지에서 Windows용 Docker Desktop을 설치하세요.
echo 설치 후 재부팅 안내가 있으면 따르고 Docker Desktop을 실행하세요.
echo 그 다음 START-WINDOWS.cmd를 다시 더블클릭하세요.
start "" "https://www.docker.com/products/docker-desktop/"
goto stopped

:engine_stopped
echo [Docker 엔진 미실행] Docker가 아직 준비되지 않았습니다.
echo Docker Desktop을 직접 열고 Engine running 상태가 될 때까지 기다리세요.
echo WSL 또는 가상화 오류가 나오면 그 오류 문구를 알려주세요.
echo 준비가 끝난 뒤 START-WINDOWS.cmd를 다시 실행하세요.
goto stopped

:windows_containers
echo [컨테이너 모드] 현재 Windows 컨테이너 모드입니다.
echo 작업 표시줄의 Docker 아이콘 메뉴에서 Switch to Linux containers를 선택하세요.
echo 전환이 끝난 뒤 START-WINDOWS.cmd를 다시 실행하세요.
goto stopped

:compose_missing
echo [Compose 없음] Docker 엔진은 켜졌지만 Compose를 찾지 못했습니다.
echo Docker Desktop의 설치 또는 업데이트를 완료하고 다시 실행하세요.
goto stopped

:image_failed
echo [이미지 다운로드 실패] Python 이미지를 받지 못했습니다.
echo 위의 다운로드 오류 문구를 알려주세요. 인터넷 또는 Docker Hub 연결을 확인해야 합니다.
goto stopped

:settings_failed
echo [설정 생성 실패] 비공개 설정을 준비하지 못했습니다.
echo 바로 위 오류 문구를 알려주세요. .env와 로그인 메모는 공유하지 마세요.
goto stopped

:app_failed
echo [앱 시작 실패] Docker는 준비됐지만 앱 설치 또는 시작에 실패했습니다.
echo 바로 위 ERROR 또는 failed 문구를 알려주세요. .env 파일은 보내지 마세요.
goto stopped

:stopped
echo.
echo 위에 표시된 단계에서 멈춰서 앱이 아직 시작되지 않았습니다.
echo 안내된 조치를 진행한 뒤 이 실행 파일을 다시 열어 주세요.
echo Enter 키는 이 창을 닫는 기능입니다.
set /p "SHORTFLOW_CLOSE=오류 문구를 확인한 후 Enter 키를 누르세요: "
exit /b 1
