@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 goto location_failed
title Shortflow - Daily Benefits

if not exist pyproject.toml goto incomplete_zip
if not exist uv.lock goto incomplete_zip
if not exist .env.example goto incomplete_zip
if not exist tools\desktop_setup.py goto incomplete_zip
if not exist tools\windows_runtime.py goto incomplete_zip

echo Docker 없이 이 PC에서 실행합니다. 관리자 권한이나 가상화 설정이 필요하지 않습니다.
echo 첫 실행에는 Python과 영상 도구 다운로드로 몇 분 걸릴 수 있습니다.
echo.
echo [1/5] 검증된 설치 도구를 준비합니다...
where powershell.exe >nul 2>nul
if errorlevel 1 goto powershell_missing
rem Pinned upstream hashes: https://github.com/astral-sh/uv/releases/tag/0.12.23
rem ZIP hashes are upstream .sha256; executable hashes were computed from those verified ZIPs.
powershell.exe -NoLogo -NoProfile -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $arch=if($env:PROCESSOR_ARCHITEW6432){$env:PROCESSOR_ARCHITEW6432}else{$env:PROCESSOR_ARCHITECTURE}; switch($arch.ToUpperInvariant()) { 'AMD64' { $target='x86_64'; $zipHash='75d05de6762778c31ee183398de7dd15093fad0ed90b1f236d8205ea5ec00c90'; $exeHash='70742ae9bd9f64f984fd69126662cfda5c03558bcb40075b2263833e4075f744' } 'ARM64' { $target='aarch64'; $zipHash='13294e232ececbe709c06b74e6ced06f2a225ea5591476685362f22be56a50d5'; $exeHash='d9c5f88091f0f53f9c26ddd64af5154c853ce69c87410e219180ad2fb0abcf73' } default { throw 'A 64-bit Windows 10 or 11 PC is required.' } }; $bin=Join-Path (Get-Location).Path '.local\bin'; [IO.Directory]::CreateDirectory($bin) | Out-Null; $uv=Join-Path $bin 'uv.exe'; if((Test-Path -LiteralPath $uv) -and ((Get-FileHash -LiteralPath $uv -Algorithm SHA256).Hash -ieq $exeHash)) { Write-Host 'Verified local uv 0.12.23.'; exit 0 }; $stage=Join-Path (Join-Path (Get-Location).Path '.local') ('uv-install-'+[Guid]::NewGuid().ToString('N')); [IO.Directory]::CreateDirectory($stage) | Out-Null; try { $zip=Join-Path $stage 'uv.zip'; $url='https://github.com/astral-sh/uv/releases/download/0.12.23/uv-'+$target+'-pc-windows-msvc.zip'; Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip; if((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ine $zipHash){throw 'uv download checksum mismatch. The download was rejected.'}; Add-Type -AssemblyName System.IO.Compression.FileSystem; $archive=[IO.Compression.ZipFile]::OpenRead($zip); try { $entry=$archive.GetEntry('uv.exe'); if($null -eq $entry){throw 'The verified uv archive is incomplete.'}; $candidate=Join-Path $stage 'uv.exe'; $source=$entry.Open(); try { $destination=[IO.File]::Create($candidate); try { $source.CopyTo($destination) } finally { $destination.Dispose() } } finally { $source.Dispose() } } finally { $archive.Dispose() }; if((Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash -ine $exeHash){throw 'uv executable checksum mismatch. The executable was rejected.'}; Copy-Item -LiteralPath $candidate -Destination $uv -Force; Write-Host 'Verified and installed local uv 0.12.23.' } finally { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue } } catch { Write-Host ('SETUP ERROR: '+$_.Exception.Message); exit 1 }"
if errorlevel 1 goto bootstrap_failed

set "UV_CACHE_DIR=%CD%\.local\uv-cache"
set "UV_PYTHON_INSTALL_DIR=%CD%\.local\python"
set "UV_PROJECT_ENVIRONMENT=%CD%\.venv"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_LINK_MODE=copy"
set "UV_NO_CONFIG=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo [2/5] 이 폴더 전용 Python과 앱을 설치합니다...
".local\bin\uv.exe" sync --frozen --no-dev --python 3.12
if errorlevel 1 goto python_failed

echo [3/5] 영상 제작 도구를 준비합니다...
".venv\Scripts\python.exe" tools\windows_runtime.py
if errorlevel 1 goto runtime_failed
set "PATH=%CD%\.local\ffmpeg\bin;%PATH%"

echo [4/5] 비공개 설정을 준비합니다. 기존 .env는 보존합니다.
".venv\Scripts\python.exe" tools\desktop_setup.py
if errorlevel 1 goto settings_failed
if /I "%~1"=="--setup-only" exit /b 0

echo [5/5] 앱을 시작합니다. 준비가 끝나면 브라우저가 자동으로 열립니다.
start "" notepad.exe "%CD%\.local\dashboard-login.txt"
start "" /b powershell.exe -NoLogo -NoProfile -Command "$deadline=[DateTime]::UtcNow.AddMinutes(2); while([DateTime]::UtcNow -lt $deadline){try{$response=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2; if($response.service -eq 'shortflow' -and $response.status -eq 'ok'){Start-Process 'http://127.0.0.1:8000'; exit 0}}catch{}; Start-Sleep -Seconds 1}; Write-Host 'Browser did not open: see the app error in this window, or open http://127.0.0.1:8000 after startup.'"
echo.
echo 열린 메모장의 로그인 정보로 브라우저에 로그인하세요.
echo 로그인 후 [샘플 미리보기]를 누르면 영상 제작을 확인할 수 있습니다.
echo 브라우저가 열리지 않으면 http://127.0.0.1:8000 에 직접 접속하세요.
echo 이 창을 열어 두어야 앱과 자동 작업이 계속 실행됩니다.
echo 종료하려면 이 창에서 Ctrl+C를 누르세요.
echo 텔레그램과 업로드 계정은 별도로 연결해야 합니다.
echo.
".venv\Scripts\python.exe" -m shortflow serve --host 127.0.0.1 --port 8000
if errorlevel 1 goto app_failed
echo 앱을 종료했습니다.
set /p "SHORTFLOW_CLOSE=Enter 키를 누르면 이 창이 닫힙니다: "
exit /b 0

:location_failed
echo [폴더 접근 실패] 압축을 로컬 PC의 다운로드 폴더 등에 풀고 실행하세요.
goto stopped

:incomplete_zip
echo [압축 해제 필요] 실행에 필요한 파일이 없습니다.
echo 최신 ZIP을 받고 [모두 압축 풀기]를 한 뒤 실행하세요.
goto stopped

:powershell_missing
echo [Windows 도구 없음] 기본 Windows PowerShell을 찾지 못했습니다.
echo 이 PC가 Windows 10 또는 11인지와 위 오류 문구를 알려주세요.
goto stopped

:bootstrap_failed
echo [설치 도구 다운로드 실패] 위 SETUP ERROR 문구를 확인하세요.
echo github.com 연결 또는 다운로드 검증에 실패하면 설치를 중단합니다.
echo 위 오류 문구만 알려주세요. 비밀번호나 토큰은 보내지 마세요.
goto stopped

:python_failed
echo [Python 설치 실패] 위 error 문구를 알려주세요.
echo 인터넷 연결이 필요하며 설치 파일은 이 폴더 안에만 저장됩니다.
goto stopped

:runtime_failed
echo [영상 도구 준비 실패] 바로 위 오류 문구를 알려주세요.
goto stopped

:settings_failed
echo [설정 생성 실패] 바로 위 오류 문구를 알려주세요.
echo .env와 로그인 메모는 공유하지 마세요.
goto stopped

:app_failed
echo [앱 실행 종료] 바로 위 오류 문구를 확인하세요.
echo 다른 실행 창이 있으면 그 창에서 앱이 이미 실행 중인지 확인하세요.
goto stopped

:stopped
echo.
echo 위에 표시된 단계에서 멈춰서 앱이 아직 시작되지 않았습니다.
echo Enter 키는 이 창을 닫는 기능입니다.
if "%SHORTFLOW_NO_PAUSE%"=="1" exit /b 1
set /p "SHORTFLOW_CLOSE=오류 문구를 확인한 후 Enter 키를 누르세요: "
exit /b 1
