import json
from pathlib import Path
import tempfile
import unittest

import httpx

from shortflow.telegram import MAX_VIDEO_BYTES, TelegramError, TelegramReview


TOKEN = "123456:fake_test_token"
CHAT_ID = "102030"


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.video = Path(self.directory.name) / "test.mp4"
        self.video.write_bytes(b"fake-video-for-mocked-transport-only")

    def review(self, responder):
        client = httpx.Client(transport=httpx.MockTransport(responder))
        self.addCleanup(client.close)
        return TelegramReview(TOKEN, CHAT_ID, client)

    def draft(self, **extra):
        return {
            "id": "draft-1",
            "title": "<title> & example",
            "summary": "Verify the facts before approval.",
            "video": {"video_path": str(self.video)},
            "sources": [{"title": "Official <source>", "url": "https://example.com/news"}],
            **extra,
        }

    def test_sends_actual_video_buttons_and_plain_text_sources(self):
        requests = []

        def responder(request):
            requests.append((request, request.read()))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests)}})

        result = self.review(responder).send_draft(self.draft())
        self.assertEqual(result, [1, 2])
        video_request, body = requests[0]
        self.assertTrue(video_request.url.path.endswith("/sendVideo"))
        self.assertIn("multipart/form-data", video_request.headers["content-type"])
        self.assertIn(self.video.read_bytes(), body)
        self.assertIn(b"approve:draft-1:1", body)
        self.assertIn(b"reject:draft-1:1", body)
        self.assertNotIn(b"parse_mode", body)
        source_payload = json.loads(requests[1][1])
        self.assertIn("Official <source>", source_payload["text"])
        self.assertNotIn("parse_mode", source_payload)
        self.assertEqual(source_payload["chat_id"], int(CHAT_ID))

    def test_demo_is_marked_and_cannot_be_approved(self):
        bodies = []

        def responder(request):
            bodies.append(request.read())
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(bodies)}})

        self.review(responder).send_draft(self.draft(is_demo=True))
        self.assertIn("샘플 영상".encode(), bodies[0])
        self.assertNotIn(b"approve:", bodies[0])
        self.assertNotIn(b"reply_markup", bodies[0])

    def test_nested_content_schema_preserves_revision_and_review_notes(self):
        requests = []

        def responder(request):
            requests.append(request.read())
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests)}})

        draft = {
            "id": "a" * 32,
            "revision": 4,
            "video": {"video_path": str(self.video)},
            "content": {
                "title": "Today's news",
                "caption": "Check benefit eligibility.",
                "is_demo": False,
                "review_notes": ["Verify deadlines against the official page."],
                "sources": [{"title": "Official source", "url": "https://example.com/news"}],
            },
        }
        self.review(responder).send_draft(draft)
        self.assertIn(("approve:" + "a" * 32 + ":4").encode(), requests[0])
        self.assertIn(b"Check benefit eligibility.", requests[0])
        details = json.loads(requests[1])["text"]
        self.assertIn("Verify deadlines", details)
        self.assertIn("https://example.com/news", details)

    def test_nested_demo_flag_disables_approval(self):
        bodies = []

        def responder(request):
            bodies.append(request.read())
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(bodies)}})

        self.review(responder).send_draft({
            "id": "demo", "revision": 1, "video": {"video_path": str(self.video)},
            "content": {"title": "Demo", "is_demo": True},
        })
        self.assertNotIn(b"approve:", bodies[0])

    def test_full_public_caption_is_sent_when_video_caption_truncates(self):
        requests = []

        def responder(request):
            requests.append(request.read())
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests)}})

        caption = "검수할 사실과 조건 " * 100 + "<마지막 문장> 광고·제휴 안내"
        message_ids = self.review(responder).send_draft({
            "id": "full-caption", "video": {"video_path": str(self.video)},
            "content": {"title": "오늘의 영상", "caption": caption},
        })
        self.assertEqual(message_ids, [1, 2])
        self.assertNotIn("<마지막 문장>".encode(), requests[0])
        full_message = json.loads(requests[1])
        self.assertTrue(full_message["text"].endswith(caption))
        self.assertNotIn("parse_mode", full_message)

    def test_review_text_keeps_platforms_requested_privacy_and_tiktok_choices(self):
        requests = []

        def responder(request):
            requests.append(request.read())
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests)}})

        draft = {
            "id": "posting-settings", "video": {"video_path": str(self.video)},
            "content": {"title": "오늘의 영상", "caption": "최종 캡션", "tiktok_options": {
                "creator_username": "reviewed_account", "privacy_level": "SELF_ONLY",
                "disable_comment": True, "disable_duet": False, "disable_stitch": True, "consent": True,
                "access_token": "secret-extra-field-must-not-render",
            }},
            "review_targets": [
                {"name": "youtube", "label": "YouTube Shorts", "status": "configured", "detail": "실제 게시 검증 전", "privacy": "private"},
                {"name": "naver", "label": "네이버 클립", "status": "manual_required", "detail": "MP4를 직접 업로드하세요.", "access_token": "secret-extra-field-must-not-render"},
            ],
        }
        self.review(responder).send_draft(draft)
        details = json.loads(requests[1])["text"]
        self.assertIn("YouTube Shorts", details)
        self.assertIn("요청 공개 범위: 비공개", details)
        self.assertIn("네이버 클립", details)
        self.assertIn("직접 업로드 필요", details)
        self.assertIn("MP4를 직접 업로드하세요.", details)
        self.assertIn("@reviewed_account", details)
        self.assertIn("선택 공개 범위: 나만 보기", details)
        self.assertIn("댓글: 끄기", details)
        self.assertIn("듀엣: 허용", details)
        self.assertIn("이어 붙이기: 끄기", details)
        self.assertIn("동의 저장됨", details)
        self.assertNotIn("secret-extra-field-must-not-render", details)

    def test_full_caption_splits_without_losing_emoji_or_disclosure(self):
        requests = []

        def responder(request):
            requests.append(request.read())
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests)}})

        caption = "한글😀" * 1100 + "광고·제휴 안내 끝"
        self.review(responder).send_draft({
            "id": "long-caption", "video": {"video_path": str(self.video)},
            "content": {"title": "긴 캡션", "caption": caption},
        })
        text_parts = [json.loads(body)["text"] for body in requests[1:]]
        self.assertEqual("".join(text_parts), "게시 캡션 전체 · 검수 대상\n\n" + caption)
        self.assertTrue(all(len(part.encode("utf-16-le")) <= 8192 for part in text_parts))

    def test_unauthorized_sender_or_chat_cannot_approve(self):
        callbacks = [
            {"from": {"id": 999}, "message": {"chat": {"id": int(CHAT_ID), "type": "private"}}},
            {"from": {"id": int(CHAT_ID)}, "message": {"chat": {"id": 999, "type": "private"}}},
            {"from": {"id": int(CHAT_ID)}, "message": {"chat": {"id": int(CHAT_ID), "type": "group"}}},
        ]
        updates = [
            {"update_id": index + 10, "callback_query": {"id": str(index), "data": "approve:draft-1:1", **callback}}
            for index, callback in enumerate(callbacks)
        ]
        acknowledgments = []

        def responder(request):
            if request.url.path.endswith("/getUpdates"):
                return httpx.Response(200, json={"ok": True, "result": updates})
            acknowledgments.append(json.loads(request.read()))
            return httpx.Response(200, json={"ok": True, "result": True})

        handled = []
        offset = self.review(responder).poll_once(10, lambda *args: handled.append(args))
        self.assertEqual(offset, 13)
        self.assertEqual(handled, [])
        self.assertEqual(len(acknowledgments), 3)
        self.assertTrue(all(item["show_alert"] for item in acknowledgments))

    def test_authorized_actions_validate_data_and_skip_old_updates(self):
        def callback(update_id, data):
            return {"update_id": update_id, "callback_query": {
                "id": str(update_id), "data": data, "from": {"id": int(CHAT_ID)},
                "message": {"chat": {"id": int(CHAT_ID), "type": "private"}},
            }}

        acknowledgments = []

        def responder(request):
            if request.url.path.endswith("/getUpdates"):
                payload = json.loads(request.read())
                self.assertEqual(payload["timeout"], 30)
                self.assertEqual(payload["allowed_updates"], ["callback_query", "message"])
                return httpx.Response(200, json={"ok": True, "result": [
                    callback(4, "approve:stale:1"), callback(5, "approve:draft-1:3"),
                    callback(6, "reject:draft-2:7"), callback(7, "approve:../../bad:1"),
                    callback(8, "delete:draft-3:1"), callback(9, "approve:draft-1"),
                ]})
            acknowledgments.append(json.loads(request.read()))
            return httpx.Response(200, json={"ok": True, "result": True})

        handled = []

        def handler(action, draft_id):
            handled.append((action, draft_id))
            return "처리했습니다."

        self.assertEqual(self.review(responder).poll_once(5, handler), 10)
        self.assertEqual(handled, [("approve", "draft-1:3"), ("reject", "draft-2:7")])
        self.assertEqual(len(acknowledgments), 5)

    def test_missing_or_oversize_video_is_rejected_before_network(self):
        review = self.review(lambda _: self.fail("Network must not be called"))
        self.video.unlink()
        with self.assertRaises(TelegramError):
            review.send_draft(self.draft())
        with self.video.open("wb") as stream:
            stream.truncate(MAX_VIDEO_BYTES + 1)
        with self.assertRaises(TelegramError):
            review.send_draft(self.draft())

    def test_group_chat_configuration_is_rejected(self):
        with self.assertRaises(TelegramError):
            TelegramReview(TOKEN, "-12345")

    def test_transport_and_api_errors_do_not_expose_token_or_body(self):
        def transport_error(request):
            raise httpx.ConnectError(f"Secret URL: {request.url}", request=request)

        with self.assertRaises(TelegramError) as caught:
            self.review(transport_error).send_status("hello")
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))

        def api_error(request):
            return httpx.Response(401, json={"ok": False, "description": TOKEN})

        with self.assertRaises(TelegramError) as caught:
            self.review(api_error).send_status("hello")
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIn("401", str(caught.exception))

    def test_handler_errors_are_not_sent_to_chat(self):
        acknowledgments = []

        def responder(request):
            if request.url.path.endswith("/getUpdates"):
                return httpx.Response(200, json={"ok": True, "result": [{
                    "update_id": 1, "callback_query": {
                        "id": "one", "data": "approve:draft-1:1", "from": {"id": int(CHAT_ID)},
                        "message": {"chat": {"id": int(CHAT_ID), "type": "private"}},
                    },
                }]})
            acknowledgments.append(json.loads(request.read()))
            return httpx.Response(200, json={"ok": True, "result": True})

        def handler(action, draft_id):
            raise RuntimeError(f"Do not publish credentials {TOKEN}")

        self.assertEqual(self.review(responder).poll_once(0, handler), 2)
        self.assertEqual(len(acknowledgments), 1)
        self.assertNotIn(TOKEN, acknowledgments[0]["text"])
        self.assertTrue(acknowledgments[0]["show_alert"])

    def test_private_status_command_requires_both_sender_and_chat(self):
        replies = []

        def message(update_id, sender, chat, text):
            return {"update_id": update_id, "message": {
                "from": {"id": sender}, "chat": {"id": chat, "type": "private"}, "text": text,
            }}

        def responder(request):
            if request.url.path.endswith("/getUpdates"):
                return httpx.Response(200, json={"ok": True, "result": [
                    message(1, 999, int(CHAT_ID), "/status"),
                    message(2, int(CHAT_ID), 999, "/help"),
                    message(3, int(CHAT_ID), int(CHAT_ID), "/status"),
                    message(4, int(CHAT_ID), int(CHAT_ID), "/start"),
                ]})
            replies.append(json.loads(request.read()))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(replies)}})

        handled = []

        def handler(action, identifier):
            handled.append((action, identifier))
            return "Ready"

        self.assertEqual(self.review(responder).poll_once(0, handler), 5)
        self.assertEqual(handled, [("status", ""), ("help", "")])
        self.assertEqual(len(replies), 2)


if __name__ == "__main__":
    unittest.main()
