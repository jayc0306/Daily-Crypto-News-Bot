# Windows PC에서 시작하기

## 처음에는 이것만 하세요

1. 시작 ZIP을 PC에 내려받아 **압축을 풉니다**.
2. [Docker Desktop](https://www.docker.com/products/docker-desktop/)을 설치하고 실행합니다. Linux 컨테이너를 사용하며, 설치 중 재부팅 안내가 있으면 따릅니다.
3. 압축 푼 폴더의 **`START-WINDOWS.cmd`를 더블클릭**합니다. 최초 설치는 몇 분 걸릴 수 있습니다. Docker가 꺼져 있으면 켜고 다시 실행합니다.
4. 자동으로 열리는 메모장의 **사용자 이름·암호**를 브라우저 로그인 창에 입력합니다.
5. 화면에서 **샘플 미리보기**를 눌러 영상이 만들어지는지 확인합니다.

여기까지는 PC 실행과 미리보기입니다. Telegram 봇과 YouTube/Instagram 계정은 그 다음에 연결합니다. 이름·소개 문구는 [채널 운영안](channel-plan.md)에 있습니다. 로그인 메모와 `.env`는 공유하지 마세요.

실행 파일은 기존 `.env`를 보존하고 처음에만 무작위 암호를 만듭니다. 기본 설정 생성 도구는 테스트했으며 Windows에서 직접 더블클릭 실행한 결과는 아직 확인하지 못했습니다. Docker 컨테이너와 앱은 Linux 환경에서 검증했습니다.

## 수동 실행과 계정 연결

사용자의 구성: 계속 켜둘 Windows PC, 기존 YouTube/TikTok/Instagram 계정, Instagram 비즈니스 계정, NAVER 미개설. 채널 표시 이름과 소개는 [운영안](channel-plan.md)의 문구로 소유자가 변경합니다. 계정 변경이나 외부 게시를 대신 실행한 상태는 아닙니다.

1. [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)를 설치합니다. 공식 안내에 따라 WSL2와 Linux 컨테이너를 사용합니다. Windows/macOS에서 직접 검증한 것은 아니며, 같은 Docker 이미지는 이 Linux 클라우드에서 빌드·실행 검증했습니다.
2. 시작 ZIP을 풀고 해당 폴더에서 PowerShell을 엽니다. `docker version`과 `docker compose version`이 정상인지 확인합니다.
3. 다음 코드는 기존 `.env`를 보존하고, 없을 때만 로컬 암호를 무작위로 만듭니다. 비밀 값은 터미널에 출력하지 않습니다.

```powershell
if (!(Test-Path .env)) {
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $passwordBytes = New-Object byte[] 24
    $signingBytes = New-Object byte[] 32
    $rng.GetBytes($passwordBytes)
    $rng.GetBytes($signingBytes)
    $config = [IO.File]::ReadAllText((Join-Path $PWD '.env.example'))
    $config = $config -replace '(?m)^DASHBOARD_PASSWORD=\r?$', ('DASHBOARD_PASSWORD=' + [Convert]::ToBase64String($passwordBytes))
    $config = $config -replace '(?m)^MEDIA_SIGNING_KEY=\r?$', ('MEDIA_SIGNING_KEY=' + [Convert]::ToBase64String($signingBytes))
    [IO.File]::WriteAllText((Join-Path $PWD '.env'), $config, [Text.UTF8Encoding]::new($false))
    $rng.Dispose()
}
docker compose up -d --build
docker compose exec shortflow python -m shortflow doctor
```

4. 본인 PC 브라우저에서 로컬 8000 포트로 접속합니다. 사용자 이름은 `admin`, 암호는 `.env`의 `DASHBOARD_PASSWORD`입니다. `notepad .env`로 본인 PC에서만 확인합니다. 파일을 채팅에 첨부하거나 Git에 올리지 않습니다.
5. 먼저 **샘플 미리보기**로 제작 화면을 확인하고 **오늘 콘텐츠 만들기**로 실제 자료를 수집합니다. 이 단계에서는 플랫폼 계정에 게시하지 않습니다.
6. `@BotFather`에서 Telegram 봇을 만들고 본인과 봇 사이에 개인 대화를 시작합니다. 봇 토큰·본인의 양수 개인 chat ID를 `.env`에 넣고 `docker compose up -d --force-recreate`로 적용합니다. 봇 토큰을 브라우저 URL이나 채팅에 붙여 넣지 않습니다.
7. YouTube OAuth를 연결하고 비공개 게시부터 검증합니다. Instagram은 비즈니스 계정이므로 유형 조건은 충족하지만, Instagram Login 앱 권한과 공개 HTTPS 영상 주소가 별도로 필요합니다. 아직 준비하지 않았다면 릴스 직접 업로드 파일을 사용합니다.

텔레그램 검수 승인 후에만 자동 게시 가능한 플랫폼에 전송합니다. 틱톡은 개인/팀 전용 내부 Direct Post 도구가 허용되지 않아 기본 수동 패키지입니다. 네이버클립도 일반 공개 업로드 API를 확인하지 못해 계정 개설 후 직접 발행합니다. 네 곳 동시 자동 게시가 완료됐다고 표시하지 않습니다.

PC 절전 중에는 매일 제작·봇 응답이 중단됩니다. PC와 Docker가 실행 중이어야 하며 재부팅 뒤 Docker 시작도 확인합니다. `restart: unless-stopped`는 Docker가 실행된 뒤 컨테이너 재시작에 적용됩니다. 클라우드에서 만든 데모·DB는 시작 ZIP에 복사하지 않으므로 PC에서는 새로 생성됩니다.

무료 카드형 영상부터 시작하며 유료 AI는 기본 비활성입니다. 월 3만 원을 목표로 AI를 켜려면 별도 API 결제와 공급자 사용량 제한을 설정합니다. 앱 자체에 원화 결제 차단 기능은 없습니다.
