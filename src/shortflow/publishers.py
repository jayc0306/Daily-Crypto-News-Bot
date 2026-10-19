"""Official publishing adapters. No browser automation and no implicit retries.

Every upload is initiated by the caller after human review. ``unknown`` means a
request may have reached the provider; callers must not blindly submit it again.
Tokens, response bodies and exception messages never appear in receipts.
"""

from __future__ import annotations

import ipaddress
import math
import re
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx


def _receipt(status: str, detail: str, **extra: Any) -> dict:
    return {"status": status, "detail": detail, **extra}


def _json(response: httpx.Response) -> dict:
    try:
        result = response.json()
    except (ValueError, UnicodeError):
        return {}
    return result if isinstance(result, dict) else {}


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _chunks(path: Path, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            yield chunk


class Publisher:
    """Synchronous, injectable HTTP adapters for the supported official APIs."""

    def __init__(self, settings: dict, client: httpx.Client | None = None):
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(60, read=300), follow_redirects=False
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _value(self, key: str, default: str = "") -> str:
        value = self.settings.get(key, default)
        return str(value).strip() if value is not None else default

    def _missing(self, names: tuple[str, ...]) -> dict | None:
        missing = [name for name in names if not self._value(name)]
        if missing:
            return _receipt("blocked", "환경 설정이 필요합니다: " + ", ".join(missing))
        return None

    @staticmethod
    def _platform(platform: str) -> str:
        return {
            "shorts": "youtube", "reels": "instagram", "naver_clip": "naver",
            "naverclip": "naver", "clip": "naver",
        }.get(platform.lower(), platform.lower())

    def _request(self, method: str, url: str, *, operation: str,
                 mutation: bool = False, **kwargs: Any) -> tuple[httpx.Response | None, dict | None]:
        try:
            response = self.client.request(method, url, follow_redirects=False, **kwargs)
        except (httpx.HTTPError, OSError):
            status = "unknown" if mutation else "failed"
            return None, _receipt(
                status,
                f"{operation}: 통신 결과를 확인할 수 없습니다. "
                + ("중복 업로드를 막기 위해 자동 재시도하지 않습니다." if mutation else "연결 상태를 확인하세요."),
            )
        if not 200 <= response.status_code < 300:
            status = (
                "blocked" if response.status_code in (401, 403)
                else "unknown" if mutation and response.status_code >= 500
                else "failed"
            )
            return response, _receipt(status, f"{operation}: API 요청 실패 (HTTP {response.status_code}).")
        return response, None

    def publish(self, platform: str, draft: dict, video_path: Path,
                *, public_video_url: str | None = None) -> dict:
        if draft.get("is_demo"):
            return _receipt("blocked", "데모 콘텐츠는 업로드할 수 없습니다. 실제 자료로 새 초안을 만드세요.")
        platform = self._platform(platform)
        if platform == "naver":
            return self._naver()
        if platform not in {"youtube", "tiktok", "instagram"}:
            return _receipt("blocked", "지원하지 않는 플랫폼입니다.")
        path = Path(video_path)
        try:
            valid = path.is_file() and path.stat().st_size > 0 and path.suffix.lower() == ".mp4"
        except OSError:
            valid = False
        if not valid:
            return _receipt("blocked", "업로드할 MP4 영상 파일을 확인하세요.")
        if platform == "youtube":
            return self._youtube(draft, path)
        if platform == "tiktok":
            return self._tiktok(draft, path)
        return self._instagram(draft, public_video_url)

    def reconcile(self, platform: str, draft: dict, receipt: dict) -> dict:
        """Poll an existing upload without creating another upload.

        Instagram may publish a *known, finished* container created earlier.
        An ambiguous media_publish request is never repeated.
        """
        if draft.get("is_demo"):
            return _receipt("blocked", "데모 콘텐츠의 업로드 상태를 조회할 수 없습니다.")
        if receipt.get("status") not in {"processing", "unknown"}:
            return receipt
        platform = self._platform(platform)
        if platform == "naver":
            return self._naver()
        provider_id = str(receipt.get("provider_id", ""))
        if not provider_id:
            return {**receipt, **_receipt("unknown", "원격 작업 ID가 없어 자동 복구할 수 없습니다. 플랫폼에서 업로드 내역을 확인하세요.")}
        if platform == "youtube":
            token, error = self._youtube_token()
            if error:
                # An auth/configuration failure does not erase a prior upload.
                # Retain its state and metadata so callers cannot queue it as a
                # fresh, safely retryable upload after reconnecting credentials.
                return {**receipt, "detail": error["detail"]}
            return self._youtube_status(provider_id, token or "", receipt.get("privacy", "private"))
        if platform == "tiktok":
            error = self._tiktok_credentials()
            if error:
                return {**receipt, "detail": error["detail"]}
            return self._tiktok_status(provider_id, receipt.get("privacy", "SELF_ONLY"))
        if platform == "instagram":
            error = self._instagram_credentials()
            if error:
                return {**receipt, "detail": error["detail"]}
            return self._instagram_finish(provider_id, receipt.get("stage", "container"))
        return _receipt("blocked", "지원하지 않는 플랫폼입니다.")

    @staticmethod
    def _naver() -> dict:
        return _receipt(
            "manual_required",
            "네이버 클립은 이 앱에서 사용할 수 있는 공식 업로드 API를 확인하지 못했습니다. "
            "제공된 영상과 캡션을 네이버 클립 앱에서 직접 업로드하세요.",
        )

    # YouTube Data API: OAuth refresh -> resumable session -> streamed MP4.
    def _youtube_token(self) -> tuple[str | None, dict | None]:
        if token := self._value("YOUTUBE_ACCESS_TOKEN"):
            return token, None
        error = self._missing(("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))
        if error:
            return None, error
        response, error = self._request(
            "POST", "https://oauth2.googleapis.com/token", operation="유튜브 인증",
            data={
                "client_id": self._value("YOUTUBE_CLIENT_ID"),
                "client_secret": self._value("YOUTUBE_CLIENT_SECRET"),
                "refresh_token": self._value("YOUTUBE_REFRESH_TOKEN"),
                "grant_type": "refresh_token",
            },
        )
        if error:
            return None, error
        token = _json(response).get("access_token")
        if not isinstance(token, str) or not token:
            return None, _receipt("blocked", "유튜브 인증 응답에 액세스 토큰이 없습니다. OAuth 연결을 확인하세요.")
        return token, None

    def _youtube(self, draft: dict, path: Path) -> dict:
        token, error = self._youtube_token()
        if error:
            return error
        privacy = self._value("YOUTUBE_PRIVACY_STATUS", "private")
        if privacy not in {"private", "unlisted", "public"}:
            return _receipt("blocked", "YOUTUBE_PRIVACY_STATUS는 private, unlisted, public 중 하나여야 합니다.")
        size = path.stat().st_size
        response, error = self._request(
            "POST", "https://www.googleapis.com/upload/youtube/v3/videos",
            operation="유튜브 업로드 세션 생성", mutation=True,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Length": str(size),
                     "X-Upload-Content-Type": "video/mp4"},
            json={
                "snippet": {"title": str(draft.get("title", ""))[:100],
                            "description": str(draft.get("caption", ""))[:5000], "categoryId": "22"},
                "status": {"privacyStatus": privacy,
                           "selfDeclaredMadeForKids": self._value("YOUTUBE_MADE_FOR_KIDS", "false").lower() == "true"},
            },
        )
        if error:
            return error
        upload_url = response.headers.get("location", "")
        if not self._trusted_upload_url(upload_url, ("googleapis.com", "youtube.com")):
            return _receipt("unknown", "유튜브가 올바른 업로드 주소를 반환하지 않았습니다. 자동 재시도하지 않습니다.")
        response, error = self._request(
            "PUT", upload_url, operation="유튜브 영상 전송", mutation=True,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4", "Content-Length": str(size)},
            content=_chunks(path),
        )
        if error:
            return error
        body = _json(response)
        video_id = body.get("id")
        if not isinstance(video_id, str) or not video_id:
            return _receipt("unknown", "유튜브 전송 응답에 영상 ID가 없습니다. 플랫폼에서 업로드 내역을 확인하세요.")
        return self._youtube_result(body, video_id, privacy)

    def _youtube_status(self, video_id: str, token: str, privacy: str) -> dict:
        response, error = self._request(
            "GET", "https://www.googleapis.com/youtube/v3/videos", operation="유튜브 처리 상태 확인",
            params={"part": "status,processingDetails", "id": video_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        if error:
            return _receipt("processing", "유튜브 영상 처리 상태 조회가 지연되었습니다.", provider_id=video_id, privacy=privacy)
        items = _json(response).get("items", [])
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            return _receipt("unknown", "유튜브 영상 ID를 조회할 수 없습니다. 계정에서 직접 확인하세요.", provider_id=video_id, privacy=privacy)
        return self._youtube_result(items[0], video_id, privacy)

    @staticmethod
    def _youtube_result(body: dict, video_id: str, privacy: str) -> dict:
        status_fields = _mapping(body.get("status"))
        actual_privacy = status_fields.get("privacyStatus")
        if isinstance(actual_privacy, str) and actual_privacy in {"private", "unlisted", "public"}:
            privacy = actual_privacy
        upload_status = status_fields.get("uploadStatus")
        processing_status = _mapping(body.get("processingDetails")).get("processingStatus")
        upload_status = upload_status if isinstance(upload_status, str) else ""
        processing_status = processing_status if isinstance(processing_status, str) else ""
        extra = {"provider_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}", "privacy": privacy}
        if upload_status in {"failed", "rejected", "deleted"} or processing_status in {"failed", "terminated"}:
            return _receipt("failed", "유튜브가 영상 처리를 거부하거나 중단했습니다. YouTube Studio에서 사유를 확인하세요.", **extra)
        if upload_status == "processed" or processing_status == "succeeded":
            visibility = {"private": "비공개", "unlisted": "일부 공개", "public": "공개"}.get(privacy, privacy)
            return _receipt("succeeded", f"유튜브 영상 처리가 완료되었습니다 ({visibility}).", **extra)
        return _receipt("processing", "유튜브가 영상을 접수했습니다. 영상 처리 완료를 기다리는 중입니다.", **extra)

    # TikTok Content Posting API. Approval flag is an operator attestation, not
    # a workaround for TikTok's app review/audit and internal-tool restrictions.
    def _tiktok_credentials(self) -> dict | None:
        mode = self._value("TIKTOK_PUBLISHING_MODE", "manual")
        if mode != "approved_app":
            return _receipt("blocked", "틱톡은 직접 업로드 모드입니다. 개인·팀 계정용 업로드 유틸리티는 "
                            "Direct Post 허용 용도에 해당하지 않습니다. 영상·캡션 패키지를 틱톡 앱에서 업로드하세요. "
                            "적격 외부 앱의 승인을 확인한 경우에만 TIKTOK_PUBLISHING_MODE=approved_app을 사용하세요.")
        if self._value("TIKTOK_APP_APPROVED", "false").lower() != "true":
            return _receipt("blocked", "틱톡 Direct Post 앱 심사·사용 목적 승인이 필요합니다. "
                            "개인 계정용 내부 업로드 도구는 심사에서 허용되지 않을 수 있습니다. "
                            "승인 사실을 확인한 운영자만 TIKTOK_APP_APPROVED=true를 설정하세요.")
        return self._missing(("TIKTOK_ACCESS_TOKEN",))

    @staticmethod
    def _tiktok_api_error(body: dict, operation: str) -> dict | None:
        code = _mapping(body.get("error")).get("code", "ok")
        if code in ("ok", "", None, 0):
            return None
        # Never include vendor messages: they may echo request tokens/URLs.
        known_codes = {"access_token_invalid", "scope_not_authorized", "invalid_param", "rate_limit_exceeded",
                       "internal_error", "unaudited_client_can_only_post_to_private_accounts",
                       "privacy_level_option_mismatch", "spam_risk_too_many_posts",
                       "spam_risk_user_banned_from_posting", "reached_active_user_cap"}
        safe_code = code if isinstance(code, str) and code in known_codes else "unknown"
        blocked = safe_code in {"access_token_invalid", "scope_not_authorized", "unaudited_client_can_only_post_to_private_accounts"}
        return _receipt("blocked" if blocked else "failed", f"{operation}: 틱톡 API 오류 ({safe_code}).")

    def _tiktok_request(self, route: str, body: dict, *, operation: str,
                        mutation: bool = False) -> tuple[dict | None, dict | None]:
        response, error = self._request(
            "POST", f"https://open.tiktokapis.com/v2/post/publish/{route}/",
            operation=operation, mutation=mutation,
            headers={"Authorization": f"Bearer {self._value('TIKTOK_ACCESS_TOKEN')}"}, json=body,
        )
        if error:
            return None, error
        result = _json(response)
        error = self._tiktok_api_error(result, operation)
        return _mapping(result.get("data")), error

    def creator_info(self) -> dict:
        """Read current TikTok identity and controls for an explicit user choice.

        Missing restrictions default to disabled. No credential or raw vendor
        message is returned to the dashboard.
        """
        result = {
            "status": "blocked", "detail": "틱톡 계정 연결을 확인하세요.",
            "creator_username": "", "creator_nickname": "", "privacy_level_options": [],
            "comment_disabled": True, "duet_disabled": True, "stitch_disabled": True,
            "max_video_post_duration_sec": None,
        }
        if error := self._tiktok_credentials():
            return result | error
        info, error = self._tiktok_request("creator_info/query", {}, operation="틱톡 계정 업로드 권한 확인")
        if error:
            return result | {"status": "blocked" if error["status"] == "blocked" else "failed", "detail": error["detail"]}
        username = info.get("creator_username")
        options = info.get("privacy_level_options")
        known_privacy = {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"}
        if not isinstance(username, str) or not username.strip() or not isinstance(options, list):
            return result | {"status": "failed", "detail": "틱톡 계정 식별자 또는 공개 범위를 확인할 수 없습니다."}
        privacy = list(dict.fromkeys(value for value in options if isinstance(value, str) and value in known_privacy))
        if not privacy:
            return result | {"status": "blocked", "detail": "틱톡 계정에서 사용할 수 있는 공개 범위가 없습니다."}
        maximum = info.get("max_video_post_duration_sec")
        if type(maximum) is not int or maximum <= 0:
            maximum = None
        result.update({
            "status": "ok", "detail": "연결된 틱톡 계정과 게시 가능 설정을 확인했습니다.",
            "creator_username": username.strip(),
            "creator_nickname": info.get("creator_nickname") if isinstance(info.get("creator_nickname"), str) else "",
            "privacy_level_options": privacy,
            "max_video_post_duration_sec": maximum,
        })
        for key in ("comment_disabled", "duet_disabled", "stitch_disabled"):
            result[key] = info[key] if type(info.get(key)) is bool else True
        return result

    @staticmethod
    def _chunk_plan(size: int) -> tuple[int, int]:
        # TikTok merges a remainder smaller than a chunk into the final chunk.
        # Standard chunks are 10 MiB (allowed range: 5-64 MB); the final chunk
        # can be larger, up to 128 MB. A small video uses one complete chunk.
        chunk_size = min(size, 10 * 1024 * 1024)
        return chunk_size, max(1, size // chunk_size)

    def _tiktok(self, draft: dict, path: Path) -> dict:
        if self._value("TIKTOK_PUBLISHING_MODE", "manual") == "manual":
            return _receipt("manual_required", "틱톡은 직접 업로드 모드입니다. 개인·팀 계정용 업로드 유틸리티는 "
                            "Direct Post 허용 용도에 해당하지 않습니다. 제공된 영상·캡션을 틱톡 앱에서 직접 업로드하세요.")
        if error := self._tiktok_credentials():
            return error
        selected = draft.get("tiktok_options")
        if not isinstance(selected, dict) or selected.get("consent") is not True:
            return _receipt("blocked", "초안별 틱톡 계정·공개 범위·상호작용 설정을 직접 선택하고 게시에 동의해야 합니다.")
        if (not isinstance(selected.get("creator_username"), str) or not selected["creator_username"].strip()
                or not isinstance(selected.get("privacy_level"), str) or not selected["privacy_level"]
                or any(type(selected.get(key)) is not bool for key in ("disable_comment", "disable_duet", "disable_stitch"))):
            return _receipt("blocked", "초안별 틱톡 계정·공개 범위·상호작용 설정을 모두 확인하세요.")
        if draft.get("affiliate_links"):
            return _receipt("blocked", "제휴 링크가 있는 틱톡 영상은 상업성 콘텐츠 표시·동의 기능을 준비한 뒤 게시할 수 있습니다.")
        info = self.creator_info()
        if info["status"] != "ok":
            return _receipt(info["status"], info["detail"])
        if selected["creator_username"] != info["creator_username"]:
            return _receipt("blocked", "검수 때 선택한 틱톡 계정과 현재 연결 계정이 다릅니다. 계정과 게시 설정을 다시 확인하세요.")
        privacy = selected["privacy_level"]
        if privacy not in info["privacy_level_options"]:
            return _receipt("blocked", "선택한 틱톡 공개 범위를 현재 계정이 허용하지 않습니다. 게시 설정을 다시 선택하세요.")
        for user_key, creator_key in (("disable_comment", "comment_disabled"), ("disable_duet", "duet_disabled"), ("disable_stitch", "stitch_disabled")):
            if info[creator_key] and not selected[user_key]:
                return _receipt("blocked", "현재 틱톡 계정이 허용하지 않는 상호작용을 활성화할 수 없습니다. 게시 설정을 다시 확인하세요.")
        duration = draft.get("duration_seconds", draft.get("video_duration_seconds"))
        maximum = info.get("max_video_post_duration_sec")
        try:
            if duration is not None:
                duration = float(duration)
                if not math.isfinite(duration) or duration <= 0:
                    return _receipt("blocked", "틱톡 영상 길이를 확인할 수 없습니다.")
                if maximum is not None:
                    maximum = float(maximum)
                    if not math.isfinite(maximum) or maximum <= 0:
                        return _receipt("blocked", "틱톡 계정의 제한 시간을 확인할 수 없습니다.")
                    if duration > maximum:
                        return _receipt("blocked", "영상 길이가 틱톡 계정의 업로드 허용 시간을 초과합니다.")
        except (TypeError, ValueError):
            return _receipt("blocked", "틱톡 영상 길이 또는 계정의 제한 시간을 확인할 수 없습니다.")
        size = path.stat().st_size
        if size > 4 * 1024 * 1024 * 1024:
            return _receipt("blocked", "틱톡 영상은 4 GB 이하여야 합니다.")
        chunk_size, count = self._chunk_plan(size)
        data, error = self._tiktok_request(
            "video/init", {
                "post_info": {"title": str(draft.get("caption") or draft.get("title", ""))[:2200],
                              "privacy_level": privacy, "disable_comment": selected["disable_comment"],
                              "disable_duet": selected["disable_duet"], "disable_stitch": selected["disable_stitch"],
                              "brand_content_toggle": False, "brand_organic_toggle": False},
                "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                "chunk_size": chunk_size, "total_chunk_count": count},
            }, operation="틱톡 업로드 생성", mutation=True,
        )
        if error:
            return error
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url", "")
        if not isinstance(publish_id, str) or not publish_id:
            return _receipt("unknown", "틱톡 응답에 업로드 ID가 없습니다. 플랫폼에서 업로드 내역을 확인하세요.")
        extra = {"provider_id": publish_id, "privacy": privacy, "stage": "uploading"}
        if not self._trusted_upload_url(upload_url, ("tiktokapis.com", "tiktok.com")):
            return _receipt("unknown", "틱톡이 올바른 업로드 주소를 반환하지 않았습니다.", **extra)
        with path.open("rb") as stream:
            for index in range(count):
                start = index * chunk_size
                length = size - start if index == count - 1 else chunk_size
                response, error = self._request(
                    "PUT", upload_url, operation="틱톡 영상 전송", mutation=True,
                    headers={"Content-Type": "video/mp4", "Content-Length": str(length),
                             "Content-Range": f"bytes {start}-{start + length - 1}/{size}"},
                    content=stream.read(length),
                )
                if error:
                    return error | extra
                if response.status_code not in {200, 201, 206}:
                    return _receipt("unknown", "틱톡의 영상 전송 응답을 확인할 수 없습니다.", **extra)
        return self._tiktok_status(publish_id, privacy)

    def _tiktok_status(self, publish_id: str, privacy: str) -> dict:
        data, error = self._tiktok_request("status/fetch", {"publish_id": publish_id}, operation="틱톡 게시 상태 확인")
        extra = {"provider_id": publish_id, "privacy": privacy, "stage": "processing"}
        if error:
            return _receipt("processing", "틱톡 게시 상태 조회가 지연되었습니다. 같은 영상을 다시 전송하지 마세요.", **extra)
        status = data.get("status")
        if status == "PUBLISH_COMPLETE":
            visibility = "비공개" if privacy == "SELF_ONLY" else privacy
            return _receipt("succeeded", f"틱톡 업로드가 완료되었습니다 ({visibility}).", **extra)
        if status == "FAILED":
            return _receipt("failed", "틱톡이 영상 게시를 거부했습니다. 틱톡 계정에서 사유를 확인하세요.", **extra)
        return _receipt("processing", "틱톡이 영상을 접수했습니다. 게시 완료를 기다리는 중입니다.", **extra)

    # Instagram API with Instagram Login: professional account + public MP4.
    def _instagram_credentials(self) -> dict | None:
        if error := self._missing(("IG_USER_ID", "IG_ACCESS_TOKEN")):
            return error
        if not self._value("IG_USER_ID").isdigit():
            return _receipt("blocked", "IG_USER_ID는 인스타그램 프로페셔널 계정의 숫자 ID여야 합니다.")
        if not re.fullmatch(r"v\d+\.\d+", self._value("IG_GRAPH_VERSION", "v23.0")):
            return _receipt("blocked", "IG_GRAPH_VERSION 형식을 확인하세요 (예: v23.0).")
        return None

    def _instagram_url(self, resource: str) -> str:
        return f"https://graph.instagram.com/{self._value('IG_GRAPH_VERSION', 'v23.0')}/{resource}"

    @staticmethod
    def _public_https_url(url: str | None) -> bool:
        if not isinstance(url, str):
            return False
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
                return False
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return False
            try:
                return ipaddress.ip_address(host).is_global
            except ValueError:
                return "." in host
        except ValueError:
            return False

    @staticmethod
    def _trusted_upload_url(url: str, domains: tuple[str, ...]) -> bool:
        if not isinstance(url, str):
            return False
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            return (parsed.scheme == "https" and parsed.port in (None, 443)
                    and not parsed.username and not parsed.password
                    and any(host == domain or host.endswith("." + domain) for domain in domains))
        except ValueError:
            return False

    def _instagram(self, draft: dict, public_url: str | None) -> dict:
        if error := self._instagram_credentials():
            return error
        if not self._public_https_url(public_url):
            return _receipt("blocked", "인스타그램 릴스에는 Meta 서버가 읽을 수 있는 공개 HTTPS MP4 주소가 필요합니다. "
                            "PUBLIC_MEDIA_BASE_URL 또는 공개 영상 저장소를 설정하세요.")
        response, error = self._request(
            "POST", self._instagram_url(self._value("IG_USER_ID") + "/media"),
            operation="인스타그램 릴스 컨테이너 생성", mutation=True,
            headers={"Authorization": f"Bearer {self._value('IG_ACCESS_TOKEN')}"},
            data={"media_type": "REELS", "video_url": public_url,
                  "caption": str(draft.get("caption", ""))[:2200], "share_to_feed": "true"},
        )
        if error:
            return error
        container_id = _json(response).get("id")
        if not isinstance(container_id, str) or not container_id:
            return _receipt("unknown", "인스타그램 응답에 컨테이너 ID가 없습니다. 계정에서 업로드 내역을 확인하세요.")
        return self._instagram_finish(container_id, "container")

    def _instagram_finish(self, container_id: str, stage: str) -> dict:
        headers = {"Authorization": f"Bearer {self._value('IG_ACCESS_TOKEN')}"}
        extra = {"provider_id": container_id, "stage": stage}
        response, error = self._request(
            "GET", self._instagram_url(container_id), operation="인스타그램 영상 처리 상태 확인",
            headers=headers, params={"fields": "status_code,status"},
        )
        if error:
            return _receipt("unknown" if stage == "publishing" else "processing",
                            "인스타그램 처리 상태 조회가 지연되었습니다.", **extra)
        status = _json(response).get("status_code")
        status = status if isinstance(status, str) else ""
        if status == "PUBLISHED":
            return _receipt("succeeded", "인스타그램 릴스 게시가 완료되었습니다.", provider_id=container_id, stage="published")
        if status in {"ERROR", "EXPIRED"}:
            return _receipt("failed", "인스타그램 컨테이너 처리에 실패했거나 만료되었습니다. 계정에서 확인하세요.", **extra)
        if stage == "publishing":
            return _receipt("unknown", "인스타그램 게시 요청 결과를 아직 확인할 수 없습니다. 중복 요청하지 않습니다.", **extra)
        if status != "FINISHED":
            return _receipt("processing", "인스타그램이 영상을 처리하고 있습니다. 완료 후 기존 컨테이너를 게시합니다.", **extra)
        response, error = self._request(
            "POST", self._instagram_url(self._value("IG_USER_ID") + "/media_publish"),
            operation="인스타그램 릴스 게시", mutation=True, headers=headers,
            data={"creation_id": container_id},
        )
        if error:
            return error | {"provider_id": container_id, "stage": "publishing"}
        media_id = _json(response).get("id")
        if not isinstance(media_id, str) or not media_id:
            return _receipt("unknown", "인스타그램 게시 응답에 미디어 ID가 없습니다. 계정에서 확인하세요.",
                            provider_id=container_id, stage="publishing")
        result = _receipt("succeeded", "인스타그램 릴스 게시가 완료되었습니다.", provider_id=media_id, stage="published")
        response, error = self._request(
            "GET", self._instagram_url(media_id), operation="인스타그램 게시물 주소 확인",
            headers=headers, params={"fields": "permalink"},
        )
        if not error:
            permalink = _json(response).get("permalink")
            if isinstance(permalink, str) and permalink.startswith("https://"):
                result["url"] = permalink
        return result
