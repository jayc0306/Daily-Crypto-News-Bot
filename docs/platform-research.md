# 플랫폼 연동과 채널 주제 검토

검토 기준일: 2026-10-19 (한국 시간). 이 문서는 구현 범위와 연결 준비에 관한 메모다.

## 조사 상태와 중요한 제한

환경의 네트워크 허용 목록을 저장한 뒤 2026-10-19에 YouTube 업로드 API·가이드, TikTok Content Sharing Guidelines·시작 안내·Direct Post API, Instagram의 두 로그인 방식별 게시 문서, NAVER 공개 API 목록·공통 가이드의 HTTPS 본문을 직접 읽었다. 아래 인용은 그때 확인한 공식 문서 내용이다. 실제 계정의 권한·심사·제한은 계정 연결 뒤 별도로 검증해야 한다.

특히 **네이버 공식 공개 API 목록에 클립 업로드 API는 없으며, 일반 개발자용 공식 업로드 명세를 확인하지 못했다**. 이는 비공개 파트너 API의 존재 여부까지 판단한 결과는 아니다. **TikTok은 본인·팀 계정 전용 업로드 도구를 Direct Post의 허용되지 않는 용도로 명시한다**. 따라서 네 플랫폼의 완전 자동 게시를 약속할 수 없다. 공식 권한이나 승인된 연동 도구가 확인되기 전에는 해당 플랫폼의 MP4·제목·설명·출처를 담은 수동 업로드 패키지를 사용한다. 비공개 API 재현이나 로그인 세션을 사용한 무인 브라우저 게시를 공식 API처럼 취급하지 않는다.

## 플랫폼별 구현 조건

| 플랫폼 | 공식 경로 | 계정·앱 준비 | 주의할 점 |
| --- | --- | --- | --- |
| YouTube Shorts | YouTube Data API `videos.insert` | Google Cloud 프로젝트, YouTube Data API 활성화, 채널 소유자의 OAuth 동의 및 갱신 토큰 | Shorts 전용 업로드 엔드포인트는 없으며 일반 영상 업로드 후 플랫폼이 형식·길이 등에 따라 분류한다. 현재 문서는 미검증 API 프로젝트의 영상이 비공개로 제한되지 않는다고 명시한다. 공개 범위는 명시적으로 지정하고 실제 채널 설정을 확인한다. |
| TikTok | Content Posting API Direct Post | 공개 대상을 위한 적격 서비스, `video.publish` 승인, 크리에이터 OAuth, Direct Post 심사 | 본인·팀 계정 전용 업로더는 공식 Intended Use에서 허용되지 않는다. 미심사 클라이언트는 비공개 계정·`SELF_ONLY` 등 제한을 받는다. 적격 서비스라도 최신 creator info·공개 범위 직접 선택·미리보기·수정·명시적 동의 등 전체 UX 요구를 충족해야 한다. |
| Instagram Reels | Instagram Platform Content Publishing: 컨테이너 생성 → 처리 상태 확인 → publish | Business/Creator 계정과 OAuth. Instagram Login 경로의 권한은 일반적으로 `instagram_business_basic`, `instagram_business_content_publish`; Facebook Login 경로는 계정·페이지 연결과 별도 권한이 필요 | 개인 계정만으로는 불가능하다. Meta가 접근할 수 있는 영상 URL이나 지원되는 업로드 경로가 필요하다. Instagram Login과 Facebook Login의 토큰·권한·호스트를 섞지 않는다. 외부 사용자를 지원하려면 앱 심사/Advanced Access가 필요할 수 있다. |
| 네이버 클립 | 공개 업로드 API 확인 필요 | 클립 계정, 공식 파트너 업로드 권한이 있다면 문서·허용 범위 확인 | 확인 전 자동 게시 완료로 표시하지 않는다. 검수 승인 뒤 업로드 파일을 내려받아 앱/공식 화면에서 수동 게시하고 URL을 기록한다. |

### 직접 확인한 공식 근거

- **YouTube `videos.insert`**: `status.privacyStatus` 설명은 “Videos uploaded from unverified API projects are not restricted to private viewing mode.”라고 명시한다. 과거의 ‘미심사 프로젝트는 무조건 비공개’ 안내를 현재 제한으로 옮기지 않는다. 같은 문서의 현재 할당량 설명은 “100 calls per day”, “1 unit in the Video Uploads quota bucket”이다. 실제 콘솔에서 프로젝트 할당량을 확인한다.
- **TikTok Content Sharing Guidelines** (본문 최종 수정 표기: 2026-08-04): “API Clients must not be limited to test applications and should be intended for a wide audience, not limited to internal groups/private use.” 바로 다음의 불허 예시는 “A utility tool to help upload contents to the account(s) you or your team manages.”이다. 따라서 개인 전용 도구의 심사 통과를 단순한 계정 연결 절차로 안내하면 안 된다.
- **TikTok 검수 화면**: “Users must manually select the privacy status from a dropdown and there should be no default value.” 닉네임·영상 미리보기 표시, 최신 creator info의 길이·상호작용 제한 적용, 캡션 수정, 음악 이용 확인 동의, 상업 콘텐츠 표시, 명시 동의 후 전송과 처리 상태 확인도 요구된다. 제휴 영상을 붙일 때 `Your brand`/`Branded content` 설정 요구도 따로 확인한다.
- **Instagram 게시 문서** (Instagram Login 페이지의 본문 수정 표기: 2026-06-30): 프로페셔널 계정의 이미지·동영상·릴스 게시를 지원하며, 게시 시 Meta가 가져갈 수 있는 공개 서버의 미디어가 필요하다고 명시한다. Instagram Login 호스트·권한은 `graph.instagram.com`, `instagram_business_basic`, `instagram_business_content_publish`; Facebook Login은 `graph.facebook.com`, `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`로 구분한다.
- **NAVER 공개 API 목록**: 검색, 네이버 로그인, 회원 프로필, 데이터랩, 캡차, 캘린더, 카페 가입·게시글, 공유하기를 열거한다. **클립 영상 업로드 항목은 이 목록에서 확인되지 않는다.** 카페 글쓰기·블로그 공유 API를 클립 업로드 API로 취급하지 않는다.

### 공식 문서 URL

- YouTube 업로드 API: <https://developers.google.com/youtube/v3/docs/videos/insert>
- YouTube 업로드 가이드: <https://developers.google.com/youtube/v3/guides/uploading_a_video>
- TikTok 시작 안내: <https://developers.tiktok.com/doc/content-posting-api-get-started/>
- TikTok UX·심사 요구 사항: <https://developers.tiktok.com/doc/content-sharing-guidelines/>
- 위 UX 문서의 실제 링크에서 확인한 Music Usage Confirmation: <https://www.tiktok.com/legal/page/global/music-usage-confirmation/en>
- 위 UX 문서의 실제 링크에서 확인한 Branded Content Policy: <https://www.tiktok.com/legal/page/global/bc-policy/en>
- TikTok Direct Post endpoint: <https://developers.tiktok.com/doc/content-posting-api-reference-direct-post/>
- Instagram Login을 통한 게시: <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing/>
- Facebook Login을 통한 게시: <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/content-publishing/>
- NAVER 개발자 공개 API 안내: <https://developers.naver.com/products/intro/plan/plan.md>
- NAVER Open API 가이드: <https://developers.naver.com/docs/common/openapiguide/>

추가 확인용 URL(이번 재검증에서 본문을 읽지는 않음): YouTube 3분 Shorts 안내 <https://support.google.com/youtube/answer/15424877>, NAVER 고객센터 <https://help.naver.com/>.

## 권장 운영 흐름

1. 한국 시간 매일 일정에 맞춰 신뢰할 수 있는 원문을 수집한다. 날짜·가격·혜택 조건·마감일을 기록한다.
2. 짧은 대본과 직접 만든 그래픽으로 세로 영상 하나를 만든다. 현재 기본 출력은 H.264/AAC MP4, 1080×1920, 24fps, 장면당 6초다. 음성이 없는 4장면 영상은 24초이며 장면 수에 따라 길이가 달라진다. 내레이션을 켜면 음성 전체가 들어가도록 길이를 늘린다. 실제 각 계정의 API 제한은 업로드 전에 확인한다.
3. 텔레그램의 지정된 운영자 **개인 대화**로 영상, 원문 링크와 검수 안내를 전달한다. 현재 그룹 검수는 지원하지 않으며 대시보드에서 대본·플랫폼별 설정도 확인한다.
4. 허용된 사용자가 명시적으로 승인하면 승인된 버전을 연결된 공식 API에 게시한다. 미연결·미심사·수동 대상은 따로 표시한다.
5. 게시 처리 상태를 조회하고 플랫폼별 성공 URL 또는 구체적인 실패 원인을 보여준다. 일부 플랫폼만 성공하면 완료된 게시를 중복 생성하지 않는다.
6. 네이버 클립과 본인 전용 TikTok은 적격 공식 연동 권한이 확인될 때까지 수동 게시 후 URL을 기록한다. 실패나 보류를 성공으로 취급하지 않는다.

텔레그램의 승인 버튼 하나가 각 플랫폼의 모든 UX 요구 사항을 자동으로 충족하지는 않는다. 특히 TikTok은 설정 파일의 기본값으로 공개 범위를 결정하지 말고, 필요한 설정과 명시적 동의를 실제 게시 화면에 노출해야 한다. 일반·Your Brand 게시의 동의 문구에는 Music Usage Confirmation을 연결하며, Branded Content 또는 두 브랜드 유형을 모두 선택한 게시에는 Branded Content Policy와 Music Usage Confirmation을 모두 연결한다. 승인된 앱용 고급 설정을 제공하더라도 전체 플랫폼 준수와 실제 심사는 별도이며, 본인 전용 도구의 기본 TikTok 경로는 수동 게시다. 현재 텔레그램은 설정된 운영자의 사용자 ID와 개인 chat ID가 모두 일치할 때만 검수 동작을 허용한다.

## 추천 주제: “오늘의 생활비 1분”

**20~30대 직장인·자취생을 위한 오늘의 소비·혜택·여행 비용 정보**를 권한다. 하루에 한 가지 선택을 도와주는 채널로 정체성을 고정한다. 광범위한 경제·정치·연예 뉴스보다 상품, 생활 서비스, 패션, 여행으로 연결되는 맥락이 자연스럽다.

- 월·화: 정부·지자체 생활 지원, 통신·구독 비용, 생활용품의 단위 가격과 구매 기준.
- 수·목: 계절 의류·신발의 구매 기준, 배송·반품 조건, 확인 가능한 기간 한정 행사.
- 금·주말: 국내외 짧은 여행의 교통·숙박 비용과 예약 조건, 한 주에 마감되는 생활 혜택.

각 영상은 “오늘 확인한 사실 1개 → 적용 대상과 조건 → 무엇을 확인하거나 비교할지 → 원문과 확인 시각” 순서로 구성한다. 보도자료나 상품 페이지의 원문 링크를 보관하고, 실제 사용하지 않은 상품을 사용 후기처럼 표현하지 않는다. 다른 채널의 영상을 내려받아 재사용하는 방식에 의존하지 않고 직접 만든 그래픽·직접 촬영·사용 권한이 확인된 자료로 만든다.

매일 새 할인 행사가 보장되는 것은 아니다. 최근 확인한 공식 자료를 우선하고 최신 소재가 부족하면 미리 검수한 상시 콘텐츠(단위 가격 계산, 환불 조건 읽기, 수하물 추가 비용 비교)를 사용한다. 오래된 정보를 새 소식으로 표시하거나 확인하지 않은 할인율을 만들어내지 않는다. 시의성이 중요한 가격·마감일은 게시 승인 직전에 다시 확인한다.

### 향후 제휴 연결

| 제휴 후보 | 자연스러운 영상 소재 | 선행 확인 |
| --- | --- | --- |
| 쿠팡 | 자취 생활용품, 계절 준비물, 단위 가격 비교 | 파트너스 승인, 링크·가격 표시 규칙, 경제적 이해관계 표시 |
| 무신사·에이블리 | 계절 코디, 사이즈·소재·반품 조건 | 사용 가능한 제휴 프로그램의 실제 승인과 상품 이미지 사용 권한 |
| 트립닷컴 | 주말 여행, 호텔·교통·취소 조건 비교 | 제휴 승인, 예약 시점별 가격 변동, 세금·수수료 포함 여부 |
| 토스 | 생활 결제 혜택·공식 이벤트 | 현재 제공되는 추천 프로그램과 노출 규칙 확인. 금융 상품의 수익·혜택을 보장하지 않는다. |

브랜드마다 상시 공개 추천 프로그램이 있다고 가정하지 않는다. 제휴가 실제로 승인된 항목만 붙이고 영상·캡션·링크 랜딩 페이지에 필요한 경제적 이해관계 표시를 유지한다.

## 사용자에게 받을 연결 정보

프로젝트에 비밀 값을 기록하지 않고 환경 설정이나 비밀 저장소로 받는다.

- 텔레그램 봇 토큰과 운영자의 개인 chat ID. 현재 구현에서는 승인자 user ID와 같은 양의 정수 ID를 사용하며 그룹은 지원하지 않는다.
- YouTube 채널 및 OAuth 앱 연결, TikTok 앱/권한/심사 상태, Instagram Professional 계정과 선택한 로그인 방식.
- 네이버 클립 공식 업로드 API 또는 계약된 파트너 도구 접근 권한의 유무.
- 실행할 서버, 공개 영상 URL을 제공할 저장소, 운영 시간(기본 매일 한국 시간), 목소리와 채널 이름.

실제 API 게시와 알림 발송은 연결 정보와 권한이 준비된 뒤 검증할 수 있다. 로컬에서 영상 생성과 승인 상태 전이는 별도로 검증할 수 있다.
