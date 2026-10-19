from datetime import datetime, timezone
from array import array
import json
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest

from shortflow import content, render
from shortflow.render import render_video


NOW = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)


def _config(tmp_path: Path, **overrides) -> Path:
    data = {"freshness_hours": 48, "max_items": 3, "sources": [{"url": "https://www.korea.kr/rss/policy.xml", "publisher": "정책브리핑", "category": "생활정책", "include_keywords": ["생활", "청년"]}]}
    data.update(overrides)
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _rss(entries: list[tuple[str, str, str | None]], summary: str = "생활 관련 안내입니다.") -> bytes:
    body = "".join(f"<item><title>{title}</title><link>{url}</link>" + (f"<pubDate>{published}</pubDate>" if published else "") + f"<description><![CDATA[<p>{summary}</p><script>bad()</script>]]></description></item>" for title, url, published in entries)
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Test</title>{body}</channel></rss>'.encode()


def test_collector_rejects_stale_missing_future_local_and_duplicate_sources(tmp_path, monkeypatch):
    feed = _rss([
        ("청년 생활 안내", "https://www.korea.kr/news/policyNewsView.do?newsId=1", "Mon, 19 Oct 2026 09:00:00 GMT"),
        ("같은 생활 안내", "https://www.korea.kr/news/policyNewsView.do?newsId=1#fragment", "Mon, 19 Oct 2026 09:00:00 GMT"),
        ("생활 옛 소식", "https://www.korea.kr/old", "Fri, 16 Oct 2026 09:00:00 GMT"),
        ("생활 미래 소식", "https://www.korea.kr/future", "Tue, 20 Oct 2026 09:00:00 GMT"),
        ("생활 날짜 누락", "https://www.korea.kr/nodate", None),
        ("생활 내부 URL", "https://127.0.0.1/secret", "Mon, 19 Oct 2026 09:00:00 GMT"),
        ("생활 이미 사용", "https://www.korea.kr/seen", "Mon, 19 Oct 2026 09:00:00 GMT"),
    ])
    monkeypatch.setattr(content, "_fetch_feed", lambda _: feed)
    items, diagnostics = content.collect_sources(_config(tmp_path), {"https://www.korea.kr/seen"}, now=NOW)
    assert len(items) == 1
    assert items[0]["title"] == "청년 생활 안내"
    assert items[0]["summary"] == "생활 관련 안내입니다."
    assert items[0]["published_at"] == "2026-10-19T09:00:00+00:00"
    assert diagnostics


def test_collector_excludes_industry_stock_and_future_car_false_positives(tmp_path, monkeypatch):
    feed = _rss([
        ("우주항공 업체 청년 채용과 코스닥 상장 추진", "https://www.korea.kr/space", "Mon, 19 Oct 2026 09:00:00 GMT"),
        ("미래차 소비자 생활 혁신 기술 공개", "https://www.korea.kr/car", "Mon, 19 Oct 2026 09:00:00 GMT"),
        ("청년 창업기업 기술특례 상장 지원금", "https://www.korea.kr/listing", "Mon, 19 Oct 2026 09:00:00 GMT"),
        ("청년 공공임대 늘리고 주거환경 개선해야", "https://www.korea.kr/proposal", "Mon, 19 Oct 2026 09:00:00 GMT"),
        ("청년 월세 지원금 신청 조건 안내", "https://www.korea.kr/benefit", "Mon, 19 Oct 2026 08:00:00 GMT"),
    ])
    monkeypatch.setattr(content, "_fetch_feed", lambda _: feed)
    sources = [{"url": "https://www.korea.kr/rss/policy.xml", "publisher": "정책브리핑", "category": "생활정책"}]
    items, _ = content.collect_sources(_config(tmp_path, sources=sources, max_items=20), set(), now=NOW)
    assert [item["url"] for item in items] == ["https://www.korea.kr/benefit"]


def test_collector_keeps_real_benefit_with_eligibility_terms_in_summary(tmp_path, monkeypatch):
    feed = _rss([("청년 월세 지원금 신청 조건 안내", "https://www.korea.kr/benefit", "Mon, 19 Oct 2026 08:00:00 GMT")], summary="상장기업 근무자도 소득 및 주거 조건을 확인해야 합니다.")
    monkeypatch.setattr(content, "_fetch_feed", lambda _: feed)
    items, _ = content.collect_sources(_config(tmp_path), set(), now=NOW)
    assert len(items) == 1
    assert "상장기업" in items[0]["summary"]


def test_collector_prefers_relevant_headline_to_newer_incidental_summary(tmp_path, monkeypatch):
    feed = _rss([
        ("국내 산업 동향 분석", "https://www.korea.kr/industry", "Mon, 19 Oct 2026 09:00:00 GMT"),
        ("통신비 할인 요금제 확인 안내", "https://www.korea.kr/phone", "Mon, 19 Oct 2026 08:00:00 GMT"),
    ], summary="생활비에 미치는 영향도 살펴봤습니다.")
    monkeypatch.setattr(content, "_fetch_feed", lambda _: feed)
    sources = [{"url": "https://www.korea.kr/rss/policy.xml", "publisher": "정책브리핑"}]
    items, _ = content.collect_sources(_config(tmp_path, sources=sources, max_items=1), set(), now=NOW)
    assert items[0]["url"] == "https://www.korea.kr/phone"


def test_collector_accepts_direct_consumer_headline_without_summary(tmp_path, monkeypatch):
    feed = _rss([("장바구니 할인 행사 안내", "https://www.korea.kr/savings", "Mon, 19 Oct 2026 09:00:00 GMT")], summary="")
    monkeypatch.setattr(content, "_fetch_feed", lambda _: feed)
    sources = [{"url": "https://www.korea.kr/rss/policy.xml", "publisher": "정책브리핑"}]
    items, _ = content.collect_sources(_config(tmp_path, sources=sources), set(), now=NOW)
    assert items[0]["title"] == "장바구니 할인 행사 안내"
    assert items[0]["summary"] == ""


def test_collector_prefers_concrete_savings_program_to_generic_youth_news(tmp_path, monkeypatch):
    feed = _rss([
        ("청년 주거환경에 대한 연구 발표", "https://www.korea.kr/research", "Mon, 19 Oct 2026 09:00:00 GMT"),
        ("청년미래적금 2차 가입 신청자 현황", "https://www.korea.kr/savings", "Mon, 19 Oct 2026 08:00:00 GMT"),
    ], summary="")
    monkeypatch.setattr(content, "_fetch_feed", lambda _: feed)
    sources = [{"url": "https://www.korea.kr/rss/policy.xml", "publisher": "정책브리핑"}]
    items, _ = content.collect_sources(_config(tmp_path, sources=sources, max_items=1), set(), now=NOW)
    assert items[0]["url"] == "https://www.korea.kr/savings"


@pytest.mark.parametrize("url", ["http://www.korea.kr/rss/policy.xml", "https://localhost/feed", "https://127.0.0.1/feed", "https://169.254.169.254/feed", "https://2130706433/feed", "https://0x7f000001/feed", "https://example.com/feed", "https://www.korea.kr:444/feed", "https://user:password@www.korea.kr/feed", "https://www.korea.kr\\@127.0.0.1/feed"])
def test_feed_url_allowlist_blocks_custom_and_local_targets(url):
    with pytest.raises(ValueError):
        content._https_url(url, feed=True)


def test_collector_reports_no_data_without_generating_fallback(tmp_path, monkeypatch):
    def blocked(_):
        raise ValueError("blocked feed")
    monkeypatch.setattr(content, "_fetch_feed", blocked)
    items, diagnostics = content.collect_sources(_config(tmp_path), set(), now=NOW)
    assert items == []
    assert any("대체하지 않습니다" in diagnostic for diagnostic in diagnostics)
    with pytest.raises(ValueError, match="실제 신규 자료"):
        content.compose_content(items, "2026-10-19")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_dtd_and_entities_are_rejected_before_parsing(tmp_path, monkeypatch, encoding):
    malicious = '<!DOCTYPE rss [<!ENTITY injected SYSTEM "file:///etc/passwd">]><rss/>'.encode(encoding)
    monkeypatch.setattr(content, "_fetch_feed", lambda _: malicious)
    items, diagnostics = content.collect_sources(_config(tmp_path), set(), now=NOW)
    assert items == []
    assert any("Unsafe" in diagnostic for diagnostic in diagnostics)


def test_feed_redirect_cannot_target_internal_network(monkeypatch):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://169.254.169.254/latest"})
    client_class = httpx.Client
    monkeypatch.setattr(content.httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(ValueError):
        content._fetch_feed("https://www.korea.kr/rss/policy.xml")
    assert calls == ["https://www.korea.kr/rss/policy.xml"]


def test_compose_preserves_attributed_headline_and_adds_disclosure():
    source = {"title": "청년 월세 지원 대상 안내", "url": "https://www.korea.kr/example", "published_at": "2026-10-19T09:00:00+00:00", "publisher": "정책브리핑", "summary": "지역에 따라 신청 조건이 다릅니다.", "category": "생활정책"}
    draft = content.compose_content([source], "2026-10-19", affiliate_links=[{"label": "예시 제휴", "url": "https://example.com/affiliate"}])
    assert draft["brand"] == "하루의 이득"
    assert 4 <= len(draft["scenes"]) <= 6
    assert draft["is_demo"] is False
    assert source["title"] in "\n".join(draft["scenes"])
    assert "100만원" not in "\n".join(draft["scenes"])
    assert source["url"] in draft["caption"]
    assert "수수료" in draft["caption"]
    assert "제휴 링크 포함" in draft["scenes"][-1]
    assert draft["sources"][0]["summary"] == source["summary"]


def test_demo_is_explicitly_synthetic_and_has_no_real_claims():
    draft = content.demo_content("2026-10-19")
    assert draft["is_demo"] is True
    assert draft["sources"] == []
    assert "게시 금지" in draft["title"]
    assert "합성 샘플" in draft["caption"]


@pytest.mark.parametrize("font_name", ["malgun.ttf", "malgunbd.ttf"])
def test_font_discovery_uses_windows_korean_font(tmp_path, monkeypatch, font_name):
    windows_dir = tmp_path / "Windows"
    fonts = windows_dir / "Fonts"
    fonts.mkdir(parents=True)
    font = fonts / font_name
    font.touch()
    monkeypatch.setenv("WINDIR", str(windows_dir))
    monkeypatch.setattr(render, "FONT_CANDIDATES", ())
    assert render._find_font(None) == str(font)


def test_explicit_font_path_remains_authoritative(tmp_path, monkeypatch):
    windows_fonts = tmp_path / "Windows" / "Fonts"
    windows_fonts.mkdir(parents=True)
    (windows_fonts / "malgun.ttf").touch()
    monkeypatch.setenv("WINDIR", str(windows_fonts.parent))
    custom = tmp_path / "custom.ttf"
    custom.touch()
    assert render._find_font(str(custom)) == str(custom)
    with pytest.raises(RuntimeError, match="FONT_PATH"):
        render._find_font(str(tmp_path / "missing.ttf"))


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg and ffprobe are required")
def test_renderer_creates_actual_h264_audio_and_thumbnail(tmp_path):
    result = render_video(content.demo_content("2026-10-19"), tmp_path, width=270, height=480, fps=12, seconds_per_scene=0.25)
    assert Path(result["video_path"]).is_file()
    assert Path(result["thumbnail_path"]).read_bytes().startswith(b"\xff\xd8")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", result["video_path"]], check=True, capture_output=True, text=True)
    metadata = json.loads(probe.stdout)
    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in metadata["streams"] if stream["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (270, 480)
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert int(video["nb_frames"]) == 15
    assert audio["codec_name"] == "aac"
    assert float(metadata["format"]["duration"]) == pytest.approx(1.25, abs=0.12)


def test_renderer_does_not_crop_long_claims(tmp_path):
    draft = content.demo_content("2026-10-19")
    draft["scenes"] = ["긴 문장 " * 150]
    with pytest.raises(ValueError, match="does not fit"):
        render_video(draft, tmp_path, width=270, height=480, fps=12, seconds_per_scene=0.25)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg and ffprobe are required")
def test_renderer_extends_cards_until_entire_narration_and_tail_finish(tmp_path):
    audio_path = tmp_path / "narration.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "sine=frequency=440:duration=2.1", "-c:a", "pcm_s16le", str(audio_path)], check=True)
    draft = content.demo_content("2026-10-19")
    draft["audio_path"] = str(audio_path)
    # Without duration adaptation these five short cards would total 1.25s.
    result = render_video(draft, tmp_path / "output", width=270, height=480, fps=12, seconds_per_scene=0.25)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", result["video_path"]], check=True, capture_output=True, text=True)
    metadata = json.loads(probe.stdout)
    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in metadata["streams"] if stream["codec_type"] == "audio")
    assert result["duration"] >= 2.1 + 0.4
    assert float(video["duration"]) >= 2.1 + 0.4 - 0.02
    assert float(audio["duration"]) >= 2.1 + 0.4 - 0.02
    # The end of the original recording remains audible beyond the old cutoff.
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", result["video_path"], "-ss", "1.9", "-t", "0.1", "-vn", "-ar", "8000", "-ac", "1", "-f", "f32le", "pipe:1"], check=True, capture_output=True)
    samples = array("f")
    samples.frombytes(decoded.stdout)
    assert samples and sum(sample * sample for sample in samples) / len(samples) > 0.001


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg and ffprobe are required")
def test_renderer_rejects_narration_exceeding_ninety_seconds_without_truncation(tmp_path):
    audio_path = tmp_path / "too-long.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "90", "-c:a", "pcm_s16le", str(audio_path)], check=True)
    draft = content.demo_content("2026-10-19")
    draft["audio_path"] = str(audio_path)
    with pytest.raises(ValueError, match="exceeds 90 seconds"):
        render_video(draft, tmp_path / "output", width=270, height=480, fps=12, seconds_per_scene=0.25)
    assert not list((tmp_path / "output").glob("*.mp4"))
