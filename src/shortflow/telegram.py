"""Private Telegram review delivery and explicit approval callbacks.

This module only communicates when its methods are called with configured
credentials. API errors deliberately omit response bodies and request URLs,
because Telegram includes the bot token in those URLs.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx


MAX_VIDEO_BYTES = 45_000_000
_DRAFT_ID = re.compile(r"[A-Za-z0-9_-]{1,40}\Z")
_CALLBACK = re.compile(r"(approve|reject):([A-Za-z0-9_-]{1,40}):([1-9][0-9]{0,8})\Z")
_TOKEN = re.compile(r"[0-9]+:[A-Za-z0-9_-]+\Z")
_PRIVATE_CHAT = re.compile(r"[1-9][0-9]*\Z")
_PRIVACY_LABELS = {
    "private": "비공개",
    "unlisted": "일부 공개",
    "public": "공개",
    "SELF_ONLY": "나만 보기",
    "PUBLIC_TO_EVERYONE": "전체 공개",
    "MUTUAL_FOLLOW_FRIENDS": "서로 팔로우하는 친구",
    "FOLLOWER_OF_CREATOR": "팔로워",
}


class TelegramError(RuntimeError):
    """A Telegram failure whose message never contains authentication data."""


def _truncate(text: str, limit: int) -> str:
    """Respect Telegram's UTF-16 limits, including non-BMP characters."""
    encoded = text.encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return text
    return encoded[: (limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


class TelegramReview:
    def __init__(
        self, token: str, chat_id: str, client: httpx.Client | None = None
    ) -> None:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise TelegramError("Telegram bot token is missing or malformed.")
        if not isinstance(chat_id, str) or not _PRIVATE_CHAT.fullmatch(chat_id):
            raise TelegramError("Telegram chat ID must be a positive private user ID.")
        self._token = token
        self._chat_id = int(chat_id)
        self._client = client or httpx.Client()
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _safe_text(self, value: Any, limit: int) -> str:
        text = str(value).replace(self._token, "[redacted]")
        return _truncate(text, limit)

    def _request(self, method: str, **kwargs: Any) -> Any:
        timeout = kwargs.pop("timeout", httpx.Timeout(60.0, connect=10.0))
        try:
            response = self._client.post(
                f"https://api.telegram.org/bot{self._token}/{method}",
                timeout=timeout,
                **kwargs,
            )
        except (httpx.HTTPError, OSError):
            raise TelegramError(f"Telegram {method} request failed.") from None
        if not response.is_success:
            raise TelegramError(f"Telegram {method} failed (HTTP {response.status_code}).")
        try:
            payload = response.json()
        except ValueError:
            raise TelegramError(f"Telegram {method} returned an invalid response.") from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            code = payload.get("error_code") if isinstance(payload, dict) else None
            suffix = f" (API {code})" if type(code) is int else ""
            raise TelegramError(f"Telegram {method} failed{suffix}.")
        return payload.get("result")

    @staticmethod
    def _message_id(result: Any) -> int:
        value = result.get("message_id") if isinstance(result, dict) else None
        if type(value) is not int or value <= 0:
            raise TelegramError("Telegram returned an invalid message identifier.")
        return value

    def send_status(self, text: str) -> int:
        """Send plain text only; user content is never interpreted as markup."""
        result = self._request(
            "sendMessage",
            json={
                "chat_id": self._chat_id,
                "text": self._safe_text(text, 4096) or "상태 정보가 없습니다.",
                "link_preview_options": {"is_disabled": True},
            },
        )
        return self._message_id(result)

    def _send_review_text(self, text: str) -> list[int]:
        """Keep the complete review text, even beyond one Telegram message."""
        remaining = text.replace(self._token, "[redacted]")
        message_ids: list[int] = []
        while remaining:
            part = remaining.encode("utf-16-le")[: 4096 * 2].decode("utf-16-le", errors="ignore")
            message_ids.append(self.send_status(part))
            remaining = remaining[len(part):]
        return message_ids

    def send_draft(self, draft: dict) -> list[int]:
        """Send the rendered file and source links for a single private review."""
        draft_id = draft.get("id")
        if not isinstance(draft_id, str) or not _DRAFT_ID.fullmatch(draft_id):
            raise TelegramError("Draft identifier is missing or invalid.")
        revision = draft.get("revision", 1)
        if type(revision) is not int or revision <= 0 or revision > 999_999_999:
            raise TelegramError("Draft revision is missing or invalid.")
        content = draft.get("content")
        content = content if isinstance(content, dict) else draft
        video = draft.get("video")
        video = video if isinstance(video, dict) else {}
        raw_path = video.get("video_path")
        if not isinstance(raw_path, (str, Path)) or not str(raw_path):
            raise TelegramError("Draft video is missing; render it before review.")
        path = Path(raw_path)
        try:
            if not path.is_file():
                raise TelegramError("Draft video is missing; render it before review.")
            size = path.stat().st_size
        except OSError:
            raise TelegramError("Draft video could not be read.") from None
        if size <= 0 or size > MAX_VIDEO_BYTES:
            raise TelegramError("Draft video must be nonempty and at most 45 MB.")

        is_demo = bool(content.get("is_demo") or draft.get("is_demo") or video.get("is_demo"))
        title = str(content.get("title") or "영상 검수")
        summary = content.get("summary") or content.get("caption") or content.get("hook") or ""
        if isinstance(summary, list):
            summary = "\n".join(str(item) for item in summary)
        prefix = "⚠️ 샘플 영상 · 승인 및 업로드 불가\n\n" if is_demo else "영상·출처·아래 게시 설정을 확인한 뒤 승인하세요. 자동 게시 가능한 대상에 전송하며, 수동 대상은 업로드 파일로 제공합니다.\n\n"
        caption = self._safe_text(f"{prefix}{title}\n\n{summary}", 1024)
        data = {
            "chat_id": str(self._chat_id),
            "caption": caption,
            "supports_streaming": "true",
        }
        if not is_demo:
            data["reply_markup"] = json.dumps(
                {
                    "inline_keyboard": [[
                        {"text": "승인 후 업로드", "callback_data": f"approve:{draft_id}:{revision}"},
                        {"text": "반려", "callback_data": f"reject:{draft_id}:{revision}"},
                    ]]
                },
                ensure_ascii=False,
            )
        try:
            with path.open("rb") as stream:
                result = self._request(
                    "sendVideo",
                    data=data,
                    files={"video": ("review.mp4", stream, "video/mp4")},
                )
        except OSError:
            raise TelegramError("Draft video could not be read.") from None
        message_ids = [self._message_id(result)]

        public_caption = content.get("caption")
        if isinstance(public_caption, str) and public_caption:
            public_caption = public_caption.replace(self._token, "[redacted]")
            if public_caption not in caption:
                message_ids.extend(self._send_review_text("게시 캡션 전체 · 검수 대상\n\n" + public_caption))

        source_lines: list[str] = []
        sources = content.get("sources", [])
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    label = str(source.get("title") or source.get("name") or "출처")
                    url = source.get("url")
                elif isinstance(source, str):
                    label, url = "출처", source
                else:
                    continue
                if not isinstance(url, str):
                    continue
                try:
                    parsed = urlsplit(url)
                    valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password
                except ValueError:
                    valid = False
                if valid:
                    source_lines.append(f"{label}\n{url}")
        details: list[str] = []
        targets = draft.get("review_targets")
        if isinstance(targets, list):
            target_lines: list[str] = []
            status_labels = {
                "configured": "설정 입력됨 · 실제 계정 게시 검증 전",
                "not_configured": "연결 설정 필요",
                "approval_required": "앱 심사·게시 권한 확인 필요",
                "manual_required": "직접 업로드 필요",
            }
            for target in targets:
                if not isinstance(target, dict):
                    continue
                label = target.get("label")
                if not isinstance(label, str) or not label.strip():
                    continue
                lines = [label]
                status = target.get("status")
                if isinstance(status, str):
                    lines.append("상태: " + status_labels.get(status, status))
                privacy = target.get("privacy")
                if isinstance(privacy, str) and privacy:
                    lines.append("요청 공개 범위: " + _PRIVACY_LABELS.get(privacy, privacy))
                detail = target.get("detail")
                if isinstance(detail, str) and detail:
                    lines.append(detail)
                target_lines.append("\n".join(lines))
            if target_lines:
                details.append("게시 대상 및 설정\n\n" + "\n\n".join(target_lines))
        tiktok = content.get("tiktok_options")
        if isinstance(tiktok, dict):
            lines = ["저장된 틱톡 게시 설정"]
            username = tiktok.get("creator_username")
            if isinstance(username, str) and username:
                lines.append("게시 계정: @" + username.lstrip("@"))
            privacy = tiktok.get("privacy_level")
            if isinstance(privacy, str) and privacy:
                lines.append("선택 공개 범위: " + _PRIVACY_LABELS.get(privacy, privacy))
            for key, label in (("disable_comment", "댓글"), ("disable_duet", "듀엣"), ("disable_stitch", "이어 붙이기")):
                disabled = tiktok.get(key)
                choice = ("끄기" if disabled else "허용") if type(disabled) is bool else "미선택"
                lines.append(f"{label}: {choice}")
            lines.append("게시 동의: " + ("이 원고·영상에 대한 동의 저장됨" if tiktok.get("consent") is True else "동의 필요"))
            details.append("\n".join(lines))
        notes = content.get("review_notes")
        if isinstance(notes, list):
            notes = "\n".join(str(note) for note in notes if isinstance(note, str))
        if isinstance(notes, str) and notes.strip():
            details.append("검수 메모\n" + notes)
        if source_lines:
            details.append("확인할 출처\n\n" + "\n\n".join(source_lines))
        if details:
            message_ids.extend(self._send_review_text("\n\n".join(details)))
        return message_ids

    def _authorized(self, payload: dict) -> bool:
        sender = payload.get("from")
        message = payload.get("message", payload)
        chat = message.get("chat") if isinstance(message, dict) else None
        return (
            isinstance(sender, dict)
            and type(sender.get("id")) is int
            and sender["id"] == self._chat_id
            and isinstance(chat, dict)
            and type(chat.get("id")) is int
            and chat["id"] == self._chat_id
            and chat.get("type") == "private"
        )

    def _answer_callback(self, callback_id: str, text: str, alert: bool = False) -> None:
        self._request(
            "answerCallbackQuery",
            json={
                "callback_query_id": callback_id,
                "text": self._safe_text(text, 200),
                "show_alert": alert,
            },
        )

    def poll_once(self, offset: int, handler: Callable[[str, str], str]) -> int:
        """Process one long poll; the caller persists the returned offset.

        The handler must make approvals idempotent because Telegram can redeliver
        updates after an interruption. Only the configured private user's sender
        ID *and* private chat ID can authorize an action.
        """
        if type(offset) is not int or offset < 0:
            raise TelegramError("Telegram update offset must be a nonnegative integer.")
        updates = self._request(
            "getUpdates",
            json={
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["callback_query", "message"],
            },
            timeout=40.0,
        )
        if not isinstance(updates, list):
            raise TelegramError("Telegram getUpdates returned an invalid update list.")
        next_offset = offset
        for update in updates:
            if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                continue
            update_id = update["update_id"]
            if update_id < offset:
                continue
            next_offset = max(next_offset, update_id + 1)
            callback = update.get("callback_query")
            if isinstance(callback, dict):
                callback_id = callback.get("id")
                if not isinstance(callback_id, str) or not callback_id or len(callback_id) > 256:
                    continue
                if not self._authorized(callback):
                    self._answer_callback(callback_id, "권한이 없는 요청입니다.", alert=True)
                    continue
                data = callback.get("data")
                match = _CALLBACK.fullmatch(data) if isinstance(data, str) else None
                if not match:
                    self._answer_callback(callback_id, "올바르지 않은 검수 요청입니다.", alert=True)
                    continue
                action, draft_id, revision = match.groups()
                try:
                    reply = handler(action, f"{draft_id}:{revision}")
                except Exception:
                    self._answer_callback(callback_id, "처리하지 못했습니다. 상태를 확인해 주세요.", alert=True)
                    continue
                self._answer_callback(callback_id, str(reply or "처리했습니다."))
                continue
            message = update.get("message")
            if not isinstance(message, dict) or not self._authorized(message):
                continue
            command = message.get("text")
            if not isinstance(command, str):
                continue
            match = re.fullmatch(r"/(status|help|start)(?:@[A-Za-z0-9_]+)?\s*", command)
            if match:
                action = "help" if match[1] == "start" else match[1]
                try:
                    reply = handler(action, "")
                except Exception:
                    reply = "상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."
                self.send_status(str(reply or "상태 정보가 없습니다."))
        return next_offset
