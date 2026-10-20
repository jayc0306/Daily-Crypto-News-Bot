# 하루의 이득 · Shortflow

**2030 직장인의 생활비·소비 혜택을 매일 한 편으로 만들고, 사람이 검수한 뒤 발행하는 도구입니다.** 기존 저장소 이름은 유지했습니다. [채널 주제·이름·소개·예산](docs/channel-plan.md)도 준비했습니다.

흐름: 최근 RSS 수집 → 생활비 주제 1개 선정 → 한국어 세로 영상 → 텔레그램/대시보드 검수 → 승인된 버전만 플랫폼별 처리.

| 플랫폼 | 현재 구현 | 실제 운영 조건 |
| --- | --- | --- |
| 유튜브 쇼츠 | OAuth·MP4 업로드·처리 상태 확인 | 본인 채널 OAuth 연결과 실제 게시 확인. 기본 **비공개** |
| 인스타 릴스 | 영상 컨테이너·게시·상태 확인 | Business/Creator 계정, Instagram Login 권한, 공개 HTTPS 영상 주소 |
| 틱톡 | **기본 수동 업로드 패키지**, 적격 승인 앱용 어댑터 별도 | 본인/팀 내부 업로더는 Direct Post 허용 용도에서 제외. 승인된 적격 외부 연동이 있어야 자동 게시 검토 가능 |
| 네이버 클립 | **수동 업로드 패키지** | 계정 생성. 일반 공개 업로드 API 미확인으로 공식 파트너 연동 확인 전에는 직접 게시 |

**네 플랫폼 동시 자동 게시가 완료된 상태는 아닙니다.** 계정과 API 권한은 별도입니다. 실제 플랫폼 게시·텔레그램 발송은 아직 검증하지 않았습니다. [공식 문서 근거](docs/platform-research.md)를 참고하세요.

## 실행

**Windows 첫 실행:** ZIP 압축을 풀고 `START-WINDOWS.cmd`를 더블클릭하세요. **Docker 설치가 필요 없습니다.** 필요한 Python과 영상 도구를 프로그램 폴더에 준비한 뒤 검수 화면을 엽니다. Telegram·플랫폼 계정은 다음 단계에 연결합니다. [Windows 안내](docs/windows-quickstart.md).

Python 3.12+, FFmpeg/FFprobe, 한글 Noto 폰트가 필요합니다. 현재 클라우드 환경에는 준비되어 있습니다. Windows 10/11 64비트는 전용 실행 파일이 필요한 도구를 준비하며 Docker·WSL2를 사용하지 않습니다. PC 설치는 [Windows 안내](docs/windows-quickstart.md)에, 선택적 서버/Docker 운영은 [운영 안내](docs/operations.md)에 있습니다.

```bash
cd /workspace/Daily-Crypto-News-Bot
UV_CACHE_DIR=/workspace/.cache/uv uv sync --frozen --extra dev
.venv/bin/python -m shortflow init
.venv/bin/python -m shortflow demo
.venv/bin/python -m shortflow serve
```

`init`은 권한 0600의 `.env`에 무작위 대시보드 암호와 미디어 서명 키를 만들며 기존 파일은 덮어쓰지 않습니다. **현재 환경에는 이미 생성했습니다.** 파일에서 암호를 확인해 사용자 `admin`으로 로컬 8000 포트에 접속합니다. 키를 채팅이나 로그에 복사하지 마세요. 서버 기본 바인딩은 로컬이며 외부 서비스에는 HTTPS가 필요합니다.

```bash
.venv/bin/python -m shortflow doctor   # 비밀 값 없이 설정 상태 확인
.venv/bin/python -m shortflow prepare  # 실제 최신 자료로 제작
.venv/bin/python -m shortflow worker   # 웹 없이 텔레그램/스케줄 실행
.venv/bin/python -m pytest -q
```

`serve`에 작업자가 포함되어 있으므로 `worker`를 따로 띄우지 않아도 됩니다. 같은 데이터 폴더의 작업자는 한 개만 동작합니다. `.env` 변경 후에는 프로세스를 재시작합니다.

## 매일 검수하는 방법

1. 한국 시간 **오전 8시 이후** 일일 작업자가 당일 실제 초안 한 개를 만듭니다. PC/서버가 계속 켜져 있어야 합니다.
2. 텔레그램으로 영상·게시 문구·원문·검수 메모·플랫폼 상태를 받습니다. 봇 토큰과 본인 개인 채팅 ID를 먼저 연결합니다.
3. 대시보드 **원고 수정**에서 두 줄 화면 제목·강조색·자막·게시 문구를 수정하고 자료 영상을 첨부합니다. 큰 제목과 영상 위 자막을 검은 세로 화면에 배치합니다. 다시 제작한 버전은 새로 검수합니다.
4. 텔레그램 **승인 후 업로드** 또는 대시보드 **검수 완료 · 업로드 요청**을 누릅니다. 그 전에는 게시 요청을 보내지 않습니다.
5. 자동 게시가 가능한 플랫폼은 업로드 후 처리 상태를 확인합니다. 수동 대상은 영상·게시 문구 또는 ZIP 패키지로 직접 발행합니다.

데모는 합성 샘플로 표시하며 게시할 수 없습니다. 수정 전 버튼은 새 영상을 승인할 수 없습니다. 제외한 원문은 다시 선정하지 않고, 같은 날 다른 초안을 만들 수 있습니다. 승인한 영상은 수정할 수 없습니다.

기본 원고는 **출처의 제목 중심인 브리핑**입니다. 자료 영상은 직접 첨부하며 미첨부 상태는 안내 그림을 씁니다. 원본 소리는 제외하고 선택적 음성만 합성합니다. 유튜브 링크나 다른 채널 영상을 자동 수집하지 않습니다. 신청 기간·가격·대상을 추측하지 않습니다. 자세한 설명은 검수자가 원문을 보고 편집하거나 선택적 AI로 작성합니다. 자료가 없으면 가짜 소식으로 채우지 않고 제작을 중단합니다. 스케줄은 매일 발행을 보장하지 않습니다.

## 계정과 음성 연결

[.env.example](.env.example)은 설정 이름만 담습니다. 비밀 값은 Git에서 무시하는 `.env` 또는 서버의 비밀 설정에 넣습니다.

- **텔레그램:** `@BotFather`에서 봇을 만들고 본인이 봇과 개인 대화를 시작합니다. `TELEGRAM_BOT_TOKEN`, 양수인 `TELEGRAM_CHAT_ID`를 설정합니다. 지정된 개인의 발신 ID와 채팅 ID를 모두 확인하며 그룹 검수는 지원하지 않습니다.
- **유튜브:** Google Cloud에서 YouTube Data API를 활성화하고 OAuth 동의를 받습니다. `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`을 연결합니다. 범위는 `youtube.upload`, 처리 조회용 `youtube.readonly`입니다. 먼저 비공개로 확인하고 `YOUTUBE_PRIVACY_STATUS`를 정합니다.
- **인스타그램:** 프로페셔널 계정의 Instagram Login 토큰과 숫자 계정 ID를 `IG_ACCESS_TOKEN`, `IG_USER_ID`에 넣습니다. `instagram_business_basic`, `instagram_business_content_publish` 권한이 필요합니다. `PUBLIC_MEDIA_BASE_URL`에는 이 앱을 제공하는 공개 HTTPS origin을, `MEDIA_SIGNING_KEY`에는 독립적인 긴 키를 설정합니다. 서명 URL은 승인된 실제 영상에만 열립니다. Facebook Login 토큰을 혼용하지 않습니다.
- **틱톡:** `TIKTOK_PUBLISHING_MODE=manual`이 기본입니다. `approved_app`와 `TIKTOK_APP_APPROVED=true`는 별도 적격 서비스의 실제 승인을 확인한 경우에만 사용합니다. 설정값은 심사를 대신하지 않습니다. 매 게시별 계정·공개 범위·상호작용·동의 선택이 필요하며 제휴 콘텐츠는 아직 차단합니다. 전체 앱 심사 적합성을 보증하지 않습니다.
- **네이버:** 본인 계정과 클립 발행 준비가 필요합니다. 비공개 API나 로그인 세션을 재현한 무인 게시는 포함하지 않습니다.

선택적으로 `LLM_API_KEY`를 OpenAI API 키에 연결하고 `LLM_ENABLED=true`로 AI 대본을, `TTS_ENABLED=true`로 AI 음성을 사용합니다. 기본은 모두 꺼져 있습니다. ChatGPT 구독과 API 비용은 별도입니다. 출처에 없는 숫자·생성 링크를 검사하지만 사실 검증을 대신하지 않습니다. 전체 음성을 보존하며 영상이 90초를 넘으면 중단합니다.

**월 3만 원은 운영 목표이며 자동 결제 상한이 아닙니다.** 보유 PC와 무료 카드 영상으로 시작하고 AI 공급자에 사용량 제한을 별도로 설정하세요. 재생성·수정도 비용이 발생할 수 있습니다.

## 검증과 운영

[운영 안내](docs/operations.md)에 Docker 배포·HTTPS·백업·재시작 방법을 적었습니다. 클라우드 개발 세션이 24시간 운영 서버로 유지된다고 가정하면 안 됩니다.

- 실제 경제 RSS 수집과 한국어 H.264/AAC 영상 생성 확인. 정책브리핑 피드는 503 응답이 있어 다른 수집원을 사용했습니다.
- FFmpeg/FFprobe로 크기·코덱·프레임·음성 길이 검증.
- 지정 검수자·버전·중복 승인·재시작·안전한 재시도·인증·서명 URL·AI 입력 제한 테스트.
- 업로드 API는 MockTransport로 검증했습니다. 실제 계정 게시와 유료 AI 호출은 하지 않았습니다.
- 데스크톱/모바일 화면은 Chromium으로 동작을 확인했습니다.

데이터는 기본 `data/`의 SQLite·영상·음성·패키지에 보관합니다. 통신이 끊겨 결과를 모르는 업로드는 `unknown`으로 남깁니다. 원격 작업 ID가 있으면 그 작업만 조회하며 새 업로드를 만들지 않습니다.
