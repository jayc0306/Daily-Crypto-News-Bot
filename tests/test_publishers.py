import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from shortflow.publishers import Publisher


SECRET = "test-secret-never-return-in-receipt"
TIKTOK_OPTIONS = {
    "creator_username": "reviewed_account", "privacy_level": "SELF_ONLY",
    "disable_comment": True, "disable_duet": True, "disable_stitch": True, "consent": True,
}
CREATOR_INFO = {
    "creator_username": "reviewed_account", "creator_nickname": "검수한 계정",
    "privacy_level_options": ["SELF_ONLY"], "comment_disabled": False,
    "duet_disabled": True, "stitch_disabled": False, "max_video_post_duration_sec": 60,
}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.video = Path(self.directory.name) / "video.mp4"
        self.video.write_bytes(b"mocked-video-content")
        self.draft = {"title": "오늘의 생활 정보", "caption": "검증한 생활 정보와 출처", "is_demo": False,
                      "affiliate_links": [], "duration_seconds": 30, "tiktok_options": dict(TIKTOK_OPTIONS)}

    def publisher(self, responder, **settings):
        client = httpx.Client(transport=httpx.MockTransport(responder))
        self.addCleanup(client.close)
        return Publisher(settings, client)

    def no_network(self, _request):
        self.fail("This configuration must never initiate an HTTP request")

    def tiktok(self, responder, **settings):
        return self.publisher(responder, TIKTOK_PUBLISHING_MODE="approved_app", TIKTOK_APP_APPROVED="true",
                              TIKTOK_ACCESS_TOKEN=SECRET, **settings)

    def instagram(self, responder):
        return self.publisher(responder, IG_USER_ID="1234567", IG_ACCESS_TOKEN=SECRET)

    def test_demo_is_blocked_before_every_provider(self):
        publisher = self.publisher(self.no_network, YOUTUBE_ACCESS_TOKEN=SECRET,
                                   TIKTOK_ACCESS_TOKEN=SECRET, TIKTOK_APP_APPROVED="true",
                                   IG_USER_ID="123", IG_ACCESS_TOKEN=SECRET)
        for platform in ("youtube", "tiktok", "instagram", "naver"):
            with self.subTest(platform=platform):
                receipt = publisher.publish(platform, self.draft | {"is_demo": True}, self.video,
                                            public_video_url="https://media.example.com/video.mp4")
                self.assertEqual(receipt["status"], "blocked")
                self.assertIn("데모", receipt["detail"])

    def test_missing_credentials_and_naver_do_not_make_http_calls(self):
        publisher = self.publisher(self.no_network)
        for platform in ("youtube", "instagram"):
            with self.subTest(platform=platform):
                self.assertEqual(publisher.publish(platform, self.draft, self.video)["status"], "blocked")
        receipt = publisher.publish("naver", self.draft, self.video)
        self.assertEqual(receipt["status"], "manual_required")
        self.assertIn("공식 업로드 API", receipt["detail"])

    def test_tiktok_token_does_not_imply_app_approval(self):
        publisher = self.publisher(self.no_network, TIKTOK_ACCESS_TOKEN=SECRET, TIKTOK_PUBLISHING_MODE="approved_app")
        receipt = publisher.publish("tiktok", self.draft, self.video)
        self.assertEqual(receipt["status"], "blocked")
        self.assertIn("TIKTOK_APP_APPROVED", receipt["detail"])

    def test_tiktok_defaults_to_manual_even_with_approval_flag(self):
        for settings in ({}, {"TIKTOK_ACCESS_TOKEN": SECRET, "TIKTOK_APP_APPROVED": "true"}):
            publisher = self.publisher(self.no_network, **settings)
            receipt = publisher.publish("tiktok", self.draft, self.video)
            self.assertEqual(receipt["status"], "manual_required")
            self.assertIn("허용 용도", receipt["detail"])
            info = publisher.creator_info()
            self.assertEqual(info["status"], "blocked")
            self.assertEqual(info["privacy_level_options"], [])

    def test_tiktok_requires_explicit_per_draft_settings_and_consent(self):
        publisher = self.tiktok(self.no_network)
        for options in (None, {}, TIKTOK_OPTIONS | {"consent": False},
                        TIKTOK_OPTIONS | {"privacy_level": ""}, TIKTOK_OPTIONS | {"disable_duet": "true"}):
            with self.subTest(options=options):
                receipt = publisher.publish("tiktok", self.draft | {"tiktok_options": options}, self.video)
                self.assertEqual(receipt["status"], "blocked")

    def test_tiktok_blocks_affiliate_post_before_upload(self):
        receipt = self.tiktok(self.no_network).publish("tiktok", self.draft | {
            "affiliate_links": [{"url": "https://affiliate.example.com/item"}],
        }, self.video)
        self.assertEqual(receipt["status"], "blocked")
        self.assertIn("상업성", receipt["detail"])

    def test_creator_info_normalizes_controls_without_exposing_vendor_fields(self):
        def responder(request):
            self.assertTrue(request.url.path.endswith("/creator_info/query/"))
            return httpx.Response(200, json={"data": CREATOR_INFO | {
                "access_token": SECRET, "privacy_level_options": ["SELF_ONLY", "SELF_ONLY", "unexpected"],
                "stitch_disabled": "invalid", "creator_nickname": "계정 이름",
            }})

        info = self.tiktok(responder).creator_info()
        self.assertEqual(info["status"], "ok")
        self.assertEqual(info["creator_username"], "reviewed_account")
        self.assertEqual(info["creator_nickname"], "계정 이름")
        self.assertEqual(info["privacy_level_options"], ["SELF_ONLY"])
        self.assertFalse(info["comment_disabled"])
        self.assertTrue(info["duet_disabled"])
        self.assertTrue(info["stitch_disabled"])
        self.assertEqual(info["max_video_post_duration_sec"], 60)
        self.assertNotIn(SECRET, str(info))

    def test_tiktok_revalidates_creator_identity_and_restricted_interactions(self):
        for info, options in (
            (CREATOR_INFO | {"creator_username": "switched_account"}, TIKTOK_OPTIONS),
            (CREATOR_INFO, TIKTOK_OPTIONS | {"disable_duet": False}),
        ):
            calls = []

            def responder(request):
                calls.append(request)
                self.assertTrue(request.url.path.endswith("/creator_info/query/"))
                return httpx.Response(200, json={"data": info})

            receipt = self.tiktok(responder).publish("tiktok", self.draft | {"tiktok_options": options}, self.video)
            self.assertEqual(receipt["status"], "blocked")
            self.assertEqual(len(calls), 1)

    def test_missing_or_non_mp4_file_does_not_make_http_calls(self):
        publisher = self.publisher(self.no_network, YOUTUBE_ACCESS_TOKEN=SECRET)
        for path in (self.video.with_suffix(".missing"), self.video.parent):
            self.assertEqual(publisher.publish("youtube", self.draft, path)["status"], "blocked")

    def test_youtube_refresh_resumable_upload_and_processing_reconciliation(self):
        calls = []

        def responder(request):
            calls.append(request)
            if request.url.host == "oauth2.googleapis.com":
                body = parse_qs(request.read().decode())
                self.assertEqual(body["grant_type"], ["refresh_token"])
                self.assertEqual(body["refresh_token"], [SECRET])
                return httpx.Response(200, json={"access_token": "short-lived-access"})
            if request.method == "POST":
                body = json.loads(request.read())
                self.assertEqual(body["status"]["privacyStatus"], "private")
                self.assertEqual(request.headers["X-Upload-Content-Length"], str(self.video.stat().st_size))
                self.assertEqual(request.url.params["uploadType"], "resumable")
                return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock"})
            if request.method == "PUT":
                self.assertEqual(request.headers["Content-Length"], str(self.video.stat().st_size))
                self.assertEqual(request.headers["Content-Type"], "video/mp4")
                self.assertEqual(request.read(), self.video.read_bytes())
                return httpx.Response(200, json={"id": "yt-video", "status": {"uploadStatus": "uploaded"}})
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.params["id"], "yt-video")
            return httpx.Response(200, json={"items": [{"id": "yt-video", "status": {"uploadStatus": "processed"}}]})

        publisher = self.publisher(responder, YOUTUBE_CLIENT_ID="client", YOUTUBE_CLIENT_SECRET=SECRET,
                                   YOUTUBE_REFRESH_TOKEN=SECRET)
        receipt = publisher.publish("youtube", self.draft, self.video)
        self.assertEqual(receipt["status"], "processing")
        self.assertEqual(receipt["provider_id"], "yt-video")
        self.assertEqual(receipt["privacy"], "private")
        receipt = publisher.reconcile("youtube", self.draft, receipt)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertIn("비공개", receipt["detail"])
        self.assertEqual(sum(r.method == "PUT" for r in calls), 1)
        self.assertEqual(sum(r.method == "POST" and r.url.host != "oauth2.googleapis.com" for r in calls), 1)

    def test_youtube_upload_timeout_is_unknown_and_never_retried(self):
        calls = []

        def responder(request):
            calls.append(request.method)
            if request.method == "POST":
                return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock"})
            raise httpx.ReadTimeout(SECRET, request=request)

        receipt = self.publisher(responder, YOUTUBE_ACCESS_TOKEN=SECRET).publish("youtube", self.draft, self.video)
        self.assertEqual(receipt["status"], "unknown")
        self.assertNotIn(SECRET, str(receipt))
        self.assertEqual(calls, ["POST", "PUT"])

    def test_youtube_does_not_follow_untrusted_session_url(self):
        calls = []

        def responder(request):
            calls.append(request)
            return httpx.Response(200, headers={"Location": "https://attacker.example/upload"})

        receipt = self.publisher(responder, YOUTUBE_ACCESS_TOKEN=SECRET).publish("youtube", self.draft, self.video)
        self.assertEqual(receipt["status"], "unknown")
        self.assertEqual(len(calls), 1)

    def test_youtube_failure_body_does_not_expose_credentials(self):
        def responder(_request):
            return httpx.Response(401, json={"error": {"message": SECRET}})

        receipt = self.publisher(responder, YOUTUBE_ACCESS_TOKEN=SECRET).publish("youtube", self.draft, self.video)
        self.assertEqual(receipt["status"], "blocked")
        self.assertNotIn(SECRET, str(receipt))

    def test_youtube_processing_failure_is_not_success(self):
        def responder(_request):
            return httpx.Response(200, json={"items": [{"status": {"uploadStatus": "rejected"}}]})

        receipt = self.publisher(responder, YOUTUBE_ACCESS_TOKEN=SECRET).reconcile(
            "youtube", self.draft, {"status": "processing", "provider_id": "yt-video"})
        self.assertEqual(receipt["status"], "failed")

    def test_youtube_reports_actual_forced_private_status(self):
        def responder(_request):
            return httpx.Response(200, json={"items": [{"status": {
                "uploadStatus": "processed", "privacyStatus": "private",
            }}]})

        receipt = self.publisher(responder, YOUTUBE_ACCESS_TOKEN=SECRET, YOUTUBE_PRIVACY_STATUS="public").reconcile(
            "youtube", self.draft, {"status": "processing", "provider_id": "yt-video", "privacy": "public"})
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["privacy"], "private")
        self.assertIn("비공개", receipt["detail"])

    def test_tiktok_checks_creator_privacy_and_duration_before_initializing(self):
        for data in ({"privacy_level_options": ["PUBLIC_TO_EVERYONE"], "max_video_post_duration_sec": 60},
                     {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 15}):
            calls = []

            def responder(request):
                calls.append(request)
                self.assertTrue(request.url.path.endswith("/creator_info/query/"))
                return httpx.Response(200, json={"data": CREATOR_INFO | data, "error": {"code": "ok"}})

            receipt = self.tiktok(responder).publish("tiktok", self.draft, self.video)
            self.assertEqual(receipt["status"], "blocked")
            self.assertEqual(len(calls), 1)

    def test_tiktok_acceptance_remains_processing_until_provider_confirms(self):
        status_count = 0
        calls = []

        def responder(request):
            nonlocal status_count
            calls.append(request)
            if request.url.path.endswith("/creator_info/query/"):
                return httpx.Response(200, json={"data": CREATOR_INFO})
            if request.url.path.endswith("/video/init/"):
                body = json.loads(request.read())
                self.assertEqual(body["post_info"]["privacy_level"], "SELF_ONLY")
                self.assertFalse(body["post_info"]["brand_content_toggle"])
                self.assertFalse(body["post_info"]["brand_organic_toggle"])
                self.assertEqual(body["source_info"]["total_chunk_count"], 1)
                return httpx.Response(200, json={"data": {"publish_id": "v_pub_mock", "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=mock"}})
            if request.method == "PUT":
                self.assertNotIn("Authorization", request.headers)
                self.assertEqual(request.read(), self.video.read_bytes())
                size = self.video.stat().st_size
                self.assertEqual(request.headers["Content-Range"], f"bytes 0-{size - 1}/{size}")
                return httpx.Response(201)
            self.assertTrue(request.url.path.endswith("/status/fetch/"))
            status_count += 1
            return httpx.Response(200, json={"data": {"status": "PROCESSING_UPLOAD" if status_count == 1 else "PUBLISH_COMPLETE"}})

        publisher = self.tiktok(responder)
        receipt = publisher.publish("tiktok", self.draft, self.video)
        self.assertEqual(receipt["status"], "processing")
        self.assertEqual(receipt["provider_id"], "v_pub_mock")
        receipt = publisher.reconcile("tiktok", self.draft, receipt)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertIn("비공개", receipt["detail"])
        self.assertEqual(sum(r.url.path.endswith("/video/init/") for r in calls), 1)
        self.assertEqual(sum(r.method == "PUT" for r in calls), 1)

    def test_tiktok_final_chunk_includes_remainder(self):
        size = 25 * 1024 * 1024 + 17
        with self.video.open("wb") as stream:
            stream.truncate(size)
        lengths, ranges = [], []

        def responder(request):
            if request.url.path.endswith("/creator_info/query/"):
                return httpx.Response(200, json={"data": CREATOR_INFO})
            if request.url.path.endswith("/video/init/"):
                source = json.loads(request.read())["source_info"]
                self.assertEqual(source["chunk_size"], 10 * 1024 * 1024)
                self.assertEqual(source["total_chunk_count"], 2)
                return httpx.Response(200, json={"data": {"publish_id": "v_pub_mock", "upload_url": "https://open-upload.tiktokapis.com/video/"}})
            if request.method == "PUT":
                lengths.append(len(request.read()))
                ranges.append(request.headers["Content-Range"])
                return httpx.Response(206 if len(lengths) == 1 else 201)
            return httpx.Response(200, json={"data": {"status": "PROCESSING_UPLOAD"}})

        receipt = self.tiktok(responder).publish("tiktok", self.draft, self.video)
        self.assertEqual(receipt["status"], "processing")
        self.assertEqual(lengths, [10 * 1024 * 1024, 15 * 1024 * 1024 + 17])
        self.assertEqual(ranges, [f"bytes 0-{10 * 1024 * 1024 - 1}/{size}", f"bytes {10 * 1024 * 1024}-{size - 1}/{size}"])

    def test_tiktok_upload_timeout_keeps_publish_id_for_safe_status_check(self):
        def responder(request):
            if request.url.path.endswith("/creator_info/query/"):
                return httpx.Response(200, json={"data": CREATOR_INFO})
            if request.url.path.endswith("/video/init/"):
                return httpx.Response(200, json={"data": {"publish_id": "v_pub_mock", "upload_url": "https://open-upload.tiktokapis.com/video/"}})
            raise httpx.ReadTimeout(SECRET, request=request)

        receipt = self.tiktok(responder).publish("tiktok", self.draft, self.video)
        self.assertEqual(receipt["status"], "unknown")
        self.assertEqual(receipt["provider_id"], "v_pub_mock")
        self.assertNotIn(SECRET, str(receipt))

    def test_tiktok_api_error_is_redacted(self):
        def responder(_request):
            return httpx.Response(200, json={"error": {"code": "access_token_invalid", "message": SECRET}})

        receipt = self.tiktok(responder).publish("tiktok", self.draft, self.video)
        self.assertEqual(receipt["status"], "blocked")
        self.assertNotIn(SECRET, str(receipt))

    def test_instagram_requires_public_https_before_network(self):
        publisher = self.instagram(self.no_network)
        for url in (None, "http://media.example.com/v.mp4", "https://localhost/v.mp4",
                    "https://127.0.0.1/v.mp4", "https://10.0.0.1/v.mp4", "https://user:password@media.example.com/v.mp4"):
            with self.subTest(url=url):
                receipt = publisher.publish("instagram", self.draft, self.video, public_video_url=url)
                self.assertEqual(receipt["status"], "blocked")

    def test_instagram_container_processing_then_publishes_without_recreating(self):
        calls = []
        status_count = 0

        def responder(request):
            nonlocal status_count
            calls.append(request)
            self.assertEqual(request.url.host, "graph.instagram.com")
            self.assertTrue(request.url.path.startswith("/v23.0/"))
            self.assertNotIn("access_token", request.url.params)
            if request.url.path.endswith("/media"):
                data = parse_qs(request.read().decode())
                self.assertEqual(data["media_type"], ["REELS"])
                self.assertEqual(data["video_url"], ["https://media.example.com/v.mp4"])
                return httpx.Response(200, json={"id": "container-1"})
            if request.url.path.endswith("/container-1"):
                status_count += 1
                return httpx.Response(200, json={"status_code": "IN_PROGRESS" if status_count == 1 else "FINISHED"})
            if request.url.path.endswith("/media_publish"):
                self.assertEqual(parse_qs(request.read().decode())["creation_id"], ["container-1"])
                return httpx.Response(200, json={"id": "media-1"})
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/example/"})

        publisher = self.instagram(responder)
        receipt = publisher.publish("instagram", self.draft, self.video, public_video_url="https://media.example.com/v.mp4")
        self.assertEqual(receipt["status"], "processing")
        self.assertEqual(receipt["provider_id"], "container-1")
        self.assertFalse(any(r.url.path.endswith("/media_publish") for r in calls))
        receipt = publisher.reconcile("instagram", self.draft, receipt)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["provider_id"], "media-1")
        self.assertIn("instagram.com", receipt["url"])
        self.assertEqual(sum(r.url.path.endswith("/media") for r in calls), 1)
        self.assertEqual(sum(r.url.path.endswith("/media_publish") for r in calls), 1)
        count = len(calls)
        self.assertEqual(publisher.reconcile("instagram", self.draft, receipt), receipt)
        self.assertEqual(len(calls), count)

    def test_instagram_ambiguous_publish_is_not_repeated(self):
        calls = []
        published = False

        def responder(request):
            calls.append(request)
            if request.url.path.endswith("/media"):
                return httpx.Response(200, json={"id": "container-1"})
            if request.url.path.endswith("/media_publish"):
                raise httpx.ReadTimeout(SECRET, request=request)
            return httpx.Response(200, json={"status_code": "PUBLISHED" if published else "FINISHED"})

        publisher = self.instagram(responder)
        receipt = publisher.publish("instagram", self.draft, self.video, public_video_url="https://media.example.com/v.mp4")
        self.assertEqual(receipt["status"], "unknown")
        self.assertEqual(receipt["stage"], "publishing")
        self.assertNotIn(SECRET, str(receipt))
        receipt = publisher.reconcile("instagram", self.draft, receipt)
        self.assertEqual(receipt["status"], "unknown")
        published = True
        receipt = publisher.reconcile("instagram", self.draft, receipt)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(sum(r.url.path.endswith("/media_publish") for r in calls), 1)

    def test_instagram_failed_container_never_publishes(self):
        def responder(request):
            if request.method == "POST":
                self.assertTrue(request.url.path.endswith("/media"))
                return httpx.Response(200, json={"id": "container-1"})
            return httpx.Response(200, json={"status_code": "ERROR", "status": SECRET})

        receipt = self.instagram(responder).publish("instagram", self.draft, self.video,
                                                   public_video_url="https://media.example.com/v.mp4")
        self.assertEqual(receipt["status"], "failed")
        self.assertNotIn(SECRET, str(receipt))

    def test_server_error_after_mutation_is_ambiguous(self):
        receipt = self.publisher(lambda _: httpx.Response(503, text=SECRET), YOUTUBE_ACCESS_TOKEN=SECRET).publish(
            "youtube", self.draft, self.video)
        self.assertEqual(receipt["status"], "unknown")
        self.assertNotIn(SECRET, str(receipt))

    def test_missing_operation_id_is_not_success(self):
        receipt = self.instagram(lambda _: httpx.Response(200, json={})).publish(
            "instagram", self.draft, self.video, public_video_url="https://media.example.com/v.mp4")
        self.assertEqual(receipt["status"], "unknown")

    def test_reconcile_missing_credentials_preserves_existing_job_and_metadata(self):
        publisher = self.publisher(self.no_network)
        for platform, status, stage, privacy in (
            ("youtube", "processing", "processing", "public"),
            ("tiktok", "unknown", "uploading", "SELF_ONLY"),
            ("instagram", "unknown", "publishing", ""),
        ):
            with self.subTest(platform=platform):
                original = {"status": status, "provider_id": "existing-remote-job", "stage": stage,
                            "privacy": privacy, "detail": "previous detail"}
                receipt = publisher.reconcile(platform, self.draft, original)
                self.assertEqual(receipt["status"], status)
                self.assertEqual(receipt["provider_id"], original["provider_id"])
                self.assertEqual(receipt["stage"], stage)
                self.assertEqual(receipt["privacy"], privacy)
                self.assertNotEqual(receipt["detail"], original["detail"])

    def test_reconcile_auth_failure_preserves_existing_upload(self):
        def responder(_request):
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": SECRET})

        publisher = self.publisher(responder, YOUTUBE_CLIENT_ID="client", YOUTUBE_CLIENT_SECRET=SECRET,
                                   YOUTUBE_REFRESH_TOKEN=SECRET)
        receipt = publisher.reconcile("youtube", self.draft, {
            "status": "processing", "provider_id": "existing-remote-job", "privacy": "public", "stage": "processing",
        })
        self.assertEqual(receipt["status"], "processing")
        self.assertEqual(receipt["provider_id"], "existing-remote-job")
        self.assertEqual(receipt["privacy"], "public")
        self.assertNotIn(SECRET, str(receipt))

    def test_tiktok_unexpected_error_code_never_echoes_provider_body(self):
        def responder(_request):
            return httpx.Response(200, json={"error": {"code": SECRET, "message": SECRET}})

        receipt = self.tiktok(responder).publish("tiktok", self.draft, self.video)
        self.assertEqual(receipt["status"], "failed")
        self.assertNotIn(SECRET, str(receipt))


if __name__ == "__main__":
    unittest.main()
