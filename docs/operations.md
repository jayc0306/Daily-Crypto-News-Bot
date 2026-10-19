# PC 설치와 운영

계속 켜둘 PC를 사용하므로 서버 대여 없이 시작합니다. Windows/macOS는 Docker Desktop의 **Linux 컨테이너**, Linux는 Docker Engine+Compose를 권합니다. Windows의 절전/최대 절전, macOS의 잠자기, Docker 종료 중에는 작업이 실행되지 않습니다. 자동 로그인이나 운영체제 보안 설정을 임의로 변경하지 않습니다.

## PC에서 시작

저장소 루트에 `.env.example`을 복사해 `.env`를 만듭니다. `DASHBOARD_PASSWORD`는 12자 이상의 무작위 값, `MEDIA_SIGNING_KEY`는 별도의 긴 무작위 값으로 설정하고 Git에 넣지 않습니다. Python 실행 환경이 있으면 `shortflow init`이 안전하게 생성할 수 있습니다.

```bash
docker compose build
docker compose up -d
docker compose logs --tail=50
docker compose exec shortflow python -m shortflow doctor
```

Compose 서비스는 `shortflow`, 포트는 로컬 8000입니다. 브라우저로 본인 PC의 해당 로컬 포트를 열어 `admin`과 설정한 암호로 검수합니다. 기본은 외부 접속을 받지 않습니다. 데이터는 named volume에 보관하며 `down -v`는 데이터를 지우므로 사용하지 않습니다. 설정 변경 후 `docker compose up -d --force-recreate`, 코드 변경 후 재빌드합니다. PC 재부팅 후 Docker도 시작되어야 `restart: unless-stopped`가 적용됩니다.

폰에서 최종 검수하는 데는 텔레그램을 사용합니다. PC가 절전하지 않고 프로그램이 실행 중이어야 메시지 전송·승인 처리가 가능합니다.

인스타그램은 Meta가 MP4를 가져갈 **공개 HTTPS 주소**가 필요합니다. 로컬 PC만으로는 이 조건이 충족되지 않습니다. 본인 도메인과 HTTPS 프록시 또는 지원되는 외부 미디어 저장소 연동을 추가해야 합니다. 아직 공개 주소를 준비하지 않았다면 릴스 자동 게시도 차단되며 직접 업로드 패키지를 사용할 수 있습니다. 도메인/호스팅 비용과 설정은 별도입니다.

`PUBLIC_MEDIA_BASE_URL`은 이 앱의 HTTPS origin입니다. `/public/{id}/{signature}.mp4`는 영상별 서명으로 보호하며 승인된 실제 영상만 열립니다. URL을 가진 사람은 영상을 볼 수 있으므로 프록시도 이 경로의 전체 URL을 로그에 기록하지 않습니다. 대시보드와 API는 Basic 인증을 유지하고 외부에는 HTTPS를 사용합니다. 데이터 폴더 전체를 정적 경로로 공개하지 않습니다.

틱톡은 개인/팀 내부 업로더가 Direct Post 허용 용도에서 제외되므로 기본 수동입니다. 네이버도 일반 공개 업로드 API를 확인하지 못해 수동 패키지로 전달합니다. 설정 플래그만 켜는 것으로 권한이나 심사를 대체할 수 없습니다.

## 클라우드 개발 환경

```bash
cd /workspace/Daily-Crypto-News-Bot
UV_CACHE_DIR=/workspace/.cache/uv uv sync --frozen --extra dev
.venv/bin/python -m shortflow doctor
.venv/bin/python -m pytest -q
.venv/bin/python -m shortflow serve
```

작업마다 격리된 환경이므로 기존 체크아웃을 사용하고 별도 Git worktree를 만들지 않습니다. `.env`가 없다면 `init`을 먼저 실행합니다. 기존 비밀 값은 출력하거나 덮어쓰지 않습니다. 환경 설정 초안의 설치/시작 지침 저장은 실행이나 게시와 별개이며, 사용자가 환경 설정에서 검토·저장하고 게시해야 재사용할 수 있습니다. 이 작업의 프로세스가 PC에 자동 설치되거나 24시간 서비스로 유지되는 것은 아닙니다.

## 스케줄과 승인

- `SCHEDULE_ENABLED=true`, `DAILY_PREPARE_HOUR=8`: 한국 시간 오전 8시 이후 초안이 없으면 제작합니다. 늦게 켜도 당일 작업을 시도하며 수집 실패는 한 시간 뒤 재시도합니다.
- `serve`는 웹과 작업자를 함께 시작합니다. `worker`는 Telegram만 운영할 때 사용합니다. 같은 데이터 폴더의 작업자는 파일 잠금으로 한 개만 동작합니다.
- 승인 대기에는 게시 요청을 보내지 않습니다. 중복 Telegram 메시지나 버튼을 눌러도 승인/게시 claim을 트랜잭션으로 보호합니다. 수정 전 버튼은 거부합니다.
- 제외한 원문은 재선정하지 않으며 같은 날 새 초안을 만들 수 있습니다. 이미 승인한 초안은 편집할 수 없습니다.
- 재시작 시 중단된 `uploading`은 `unknown`으로 바꿉니다. 원격 ID를 아는 작업은 기존 작업을 조회합니다. ID를 모르면 플랫폼에서 직접 확인해야 합니다.
- 재시도는 `failed/blocked` 중 원격 ID가 없는 항목만 처리합니다. 성공·진행·불확실·이미 원격 작업이 있는 항목은 새로 올리지 않습니다.
- Telegram은 봇 토큰과 본인 양수 개인 chat ID가 필요합니다. 그룹은 지원하지 않습니다. 같은 봇에 별도 getUpdates 작업자를 중복 실행하면 안 됩니다.

## 수집원·예산·제휴

`config/sources.json`에서 최근 48시간의 자료를 선택합니다. 고정 허용된 세 뉴스 호스트만 읽으며 본문·상품 이미지를 복제하지 않습니다. 자료가 없으면 제작하지 않습니다. 원문의 가격·기간·자격은 최종 검수합니다. 기본 원고는 제목 중심이고, 더 풍부한 설명은 사람이 수정하거나 선택적 AI로 작성합니다.

새 수집처는 환경 허용 도메인과 `content.py`의 호스트 제한을 함께 검토해야 합니다. 제휴 API가 있다고 가정해 임의 엔드포인트를 호출하지 않습니다.

기본 카드 영상·Telegram·자체 그래픽은 별도 API 사용료가 없습니다. 보유 PC의 전기·네트워크 비용은 별도입니다. 유료 AI는 기본 꺼져 있으며 공급자의 사용량 한도를 설정합니다. 이 앱에 원화 청구 차단 기능은 없습니다.

제휴는 각 프로그램 가입과 매체 자격을 확인한 뒤 추가합니다. `compose_content`에 링크·캡션 고지용 필드가 있지만 자동 가입·추천 링크 생성·가격 조회는 없습니다. 틱톡 제휴 게시 API 경로는 상업 콘텐츠 UX를 별도 구현하기 전까지 차단합니다.

## 네트워크와 백업

수집: `www.korea.kr`, `www.hankyung.com`, `www.yna.co.kr`. Telegram: `api.telegram.org`. Google: `oauth2.googleapis.com`, `www.googleapis.com`. Instagram: `graph.instagram.com`. 선택적 AI: `api.openai.com`. 적격 TikTok만 `open.tiktokapis.com`과 공식 반환 업로드 호스트를 사용합니다. 개발자 문서는 각 플랫폼 공식 문서 도메인을, Python 설치는 PyPI와 files.pythonhosted.org를 사용합니다.

서버 중지 후 SQLite·영상·오디오 데이터 볼륨과 `.env`를 안전한 별도 위치에 백업합니다. 실행 중 SQLite 복사에는 SQLite backup API를 사용합니다. 오래된 영상 정리는 게시·검수·백업 여부를 확인한 뒤 직접 합니다.
