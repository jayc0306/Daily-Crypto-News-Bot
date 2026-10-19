import copy
import json

import httpx
import pytest

from shortflow.ai import enrich_content, synthesize_voice
from shortflow.content import compose_content, demo_content


KEY = "sk-private-example-do-not-log"
SETTINGS = {"LLM_ENABLED": "true", "TTS_ENABLED": "true", "LLM_API_KEY": KEY}


def _content():
    return compose_content([{"title": "청년 생활 지원 안내", "summary": "지역마다 지원 대상과 신청 조건이 다릅니다. 예산은 1,000만원입니다.",
                             "url": "https://www.korea.kr/source", "publisher": "정책브리핑", "published_at": "2026-10-19T01:00:00+00:00"}], "2026-10-19")


def _generated():
    return {"title": "청년 생활 지원, 조건부터 확인해요", "hook": "지원 소식을 찾았다면?",
            "scenes": ["청년 생활 지원 안내가 나왔어요.", "지역마다 지원 대상이 달라요.", "신청 조건도 지역마다 달라요.", "신청 전 원문에서 내 조건을 확인해요."],
            "caption_excerpt": "지역별 지원 대상과 신청 조건을 원문에서 확인해 보세요."}


def _response(generated=None):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(generated or _generated(), ensure_ascii=False)}}]})


@pytest.mark.parametrize("settings", [{}, {"LLM_API_KEY": KEY}, {"LLM_ENABLED": "true", "TTS_ENABLED": "true"}, {"LLM_API_KEY": KEY, "LLM_ENABLED": "false", "TTS_ENABLED": "false"}])
def test_external_ai_requires_key_and_explicit_opt_in(settings, tmp_path):
    def fail(_):
        pytest.fail("External call without explicit opt-in")
    content = _content()
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        assert enrich_content(content, settings, client) is content
        assert synthesize_voice(content, tmp_path / "voice.mp3", settings, client) is None


def test_demo_never_incurs_external_ai_cost(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("Demo called external AI"))) as client:
        content = demo_content("2026-10-19")
        assert enrich_content(content, SETTINGS, client) is content
        assert synthesize_voice(content, tmp_path / "voice.mp3", SETTINGS, client) is None


def test_writer_preserves_sources_disclosure_and_sends_only_bounded_source_text():
    content = _content()
    content["sources"][0]["summary"] += "가" * 20_000
    original = copy.deepcopy(content)
    def handle(request):
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer " + KEY
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-4.1-mini"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["max_tokens"] <= 1200
        assert "신뢰할 수 없는" in payload["messages"][0]["content"]
        data = json.loads(payload["messages"][1]["content"])
        assert set(data) == {"sources"}
        assert all(set(source) == {"title", "summary"} for source in data["sources"])
        assert sum(len(source["title"]) + len(source["summary"]) for source in data["sources"]) <= 90
        assert KEY not in request.content.decode()
        assert "https://www.korea.kr/source" not in request.content.decode()
        return _response()
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        enriched = enrich_content(content, {**SETTINGS, "LLM_MAX_INPUT_CHARS": "90"}, client)
    assert content == original
    assert enriched["sources"] == content["sources"]
    assert enriched["is_demo"] is False
    assert enriched["ai_enriched"] is True
    assert len(enriched["scene_source_indexes"]) == len(enriched["scenes"])
    assert "https://www.korea.kr/source" in enriched["caption"]
    assert _generated()["caption_excerpt"] in enriched["caption"]
    assert "검수용" not in enriched["caption"]


@pytest.mark.parametrize("change", [
    {"scenes": ["장면이 부족해요"]}, {"scenes": ["영상이에요"] * 7}, {"extra": "field"},
    {"title": "환급액은 500만원이에요"}, {"hook": "지원금을 100원 받아요"},
    {"caption_excerpt": "더 알아보려면 https://example.com 으로 가요"},
    {"caption_excerpt": "더 알아보려면 example.com. 을 확인해요"},
    {"caption_excerpt": "검수용 초안이에요"}, {"hook": 123}, {"title": "No Korean title"},
])
def test_writer_rejects_invalid_shapes_unfounded_numbers_and_model_links(change):
    generated = {**_generated(), **change}
    with httpx.Client(transport=httpx.MockTransport(lambda _: _response(generated))) as client:
        with pytest.raises(ValueError):
            enrich_content(_content(), SETTINGS, client)


def test_writer_allows_source_number_with_equivalent_comma_format():
    generated = {**_generated(), "caption_excerpt": "예산은 1000만원이며 지역별 신청 조건을 원문에서 확인하세요."}
    with httpx.Client(transport=httpx.MockTransport(lambda _: _response(generated))) as client:
        assert "1000만원" in enrich_content(_content(), SETTINGS, client)["caption"]


@pytest.mark.parametrize("response", [httpx.Response(401, text=KEY), httpx.Response(200, text=KEY), httpx.Response(200, json={"choices": []})])
def test_writer_provider_failures_do_not_expose_key(response, capsys):
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(ValueError) as exc:
            enrich_content(_content(), SETTINGS, client)
    assert KEY not in str(exc.value)
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err


def test_tts_posts_bounded_text_and_writes_audio(tmp_path):
    content = _content()
    def handle(request):
        assert str(request.url) == "https://api.openai.com/v1/audio/speech"
        payload = json.loads(request.content)
        assert payload == {"model": "gpt-4o-mini-tts", "voice": "coral", "input": "\n".join(content["scenes"]), "response_format": "mp3"}
        assert KEY not in request.content.decode()
        return httpx.Response(200, content=b"ID3example-mp3", headers={"Content-Type": "audio/mpeg"})
    output = tmp_path / "voice.mp3"
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert synthesize_voice(content, output, SETTINGS, client) == output
    assert output.read_bytes() == b"ID3example-mp3"


@pytest.mark.parametrize("settings,text", [(SETTINGS, "가" * 1601), ({**SETTINGS, "TTS_MAX_CHARS": 10}, "가" * 11), (SETTINGS, " ")])
def test_tts_rejects_empty_or_over_budget_text_before_request(tmp_path, settings, text):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("Over-budget request"))) as client:
        with pytest.raises(ValueError):
            synthesize_voice({"scenes": [text]}, tmp_path / "voice.mp3", settings, client)
    assert not (tmp_path / "voice.mp3").exists()


@pytest.mark.parametrize("audio", [b"", b"a" * (10 * 1024 * 1024 + 1)])
def test_tts_rejects_empty_or_oversized_audio_without_writing(tmp_path, audio):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=audio))) as client:
        with pytest.raises(ValueError):
            synthesize_voice(_content(), tmp_path / "voice.mp3", SETTINGS, client)
    assert not (tmp_path / "voice.mp3").exists()


def test_tts_error_redacts_provider_body(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429, text=KEY))) as client:
        with pytest.raises(ValueError) as exc:
            synthesize_voice(_content(), tmp_path / "voice.mp3", SETTINGS, client)
    assert KEY not in str(exc.value)
