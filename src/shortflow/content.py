"""Collect timely public headlines and prepare a conservative human-review draft.

No LLM, scraping, or invented fallback is used here. The review draft quotes short
headlines and links to the original sources; a person must verify the full article.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import feedparser
import httpx


UTC = timezone.utc
APPROVED_FEED_HOSTS = frozenset({"www.korea.kr", "www.hankyung.com", "www.yna.co.kr"})
MAX_FEED_BYTES = 2 * 1024 * 1024
DEFAULT_BRAND = "하루의 이득"
DEFAULT_INCLUDE_KEYWORDS = ("생활비", "교통비", "통신비", "월세", "지원금", "환급", "할인", "장바구니", "장보기", "소비자", "구독료", "요금제", "숙박", "항공권", "주거", "청년", "적금", "예금")
DEFAULT_EXCLUDE_KEYWORDS = ("우주항공", "기술특례", "상장", "코스닥", "거래소", "미래차", "목표주가", "기업공개", "공모주", "유상증자", "촉구", "개선해야", "늘려야", "확대해야")
DIRECT_BENEFIT_KEYWORDS = frozenset({"생활비", "교통비", "통신비", "월세", "지원금", "환급", "할인", "장바구니", "장보기", "구독료", "요금제", "숙박", "항공권", "적금", "예금"})
ACTION_TITLE_KEYWORDS = ("신청", "접수", "가입", "지원", "혜택", "환급", "할인", "출시", "인하", "감면")


def _has_xml_declarations(raw: bytes) -> bool:
    # NUL stripping also detects the ASCII declaration tokens in UTF-16/32 XML.
    return bool(re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw.replace(b"\x00", b""), re.I))


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _plain(value: object, limit: int = 500) -> str:
    parser = _PlainText()
    parser.feed(str(value or ""))
    text = " ".join(unescape(" ".join(parser.parts)).split())
    return text[:limit].strip()


def _https_url(value: str, *, feed: bool = False) -> str:
    """Validate public HTTPS URLs without ever requesting arbitrary article URLs.

    Only the three fixed, public publisher domains can be fetched as feeds.
    Redirect destinations pass the same allowlist. Article and affiliate URLs are
    displayed only, never fetched by the collector or renderer.
    """
    if not isinstance(value, str) or len(value) > 4096 or re.search(r"[\x00-\x20\\]", value):
        raise ValueError("Invalid HTTPS URL")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("Only public HTTPS URLs without credentials are supported")
    if parsed.port not in (None, 443):
        raise ValueError("Custom network ports are not supported")
    if host == "localhost" or "." not in host or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
        raise ValueError("Local network URLs are not supported")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Reject alternate numeric IPv4 spellings and malformed DNS labels.
        if re.fullmatch(r"[0-9.]+", host) or host.startswith("0x"):
            raise ValueError("Numeric network addresses are not supported")
        if not re.fullmatch(r"[a-z0-9.-]+", host) or any(not part or part.startswith("-") or part.endswith("-") for part in host.split(".")):
            raise ValueError("Invalid public hostname")
    else:
        if not address.is_global:
            raise ValueError("Local network URLs are not supported")
    if feed and host not in APPROVED_FEED_HOSTS:
        raise ValueError("Feed host is outside the built-in publisher allowlist")
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _fetch_feed(url: str) -> bytes:
    current = _https_url(url, feed=True)
    # No redirects to arbitrary URLs, and no reading large bodies into memory.
    with httpx.Client(timeout=20.0, follow_redirects=False, headers={"User-Agent": "Shortflow/0.1 (RSS headline reader)"}) as client:
        for _ in range(4):
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Feed redirect has no Location")
                    current = _https_url(urljoin(current, location), feed=True)
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_FEED_BYTES:
                        raise ValueError("RSS feed exceeds the 2 MiB limit")
                    chunks.append(chunk)
                result = b"".join(chunks)
                # RSS needs neither DTDs nor custom entities. Reject before parsing.
                if _has_xml_declarations(result):
                    raise ValueError("XML declarations containing DTD/entities are forbidden")
                return result
    raise ValueError("Too many RSS redirects")


def _entry_time(entry: dict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def collect_sources(config_path: Path, seen_urls: set[str], now: datetime | None = None) -> tuple[list[dict], list[str]]:
    """Return fresh, unseen sources plus diagnostic messages; never fake a result."""
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    hours = float(config.get("freshness_hours", 48))
    if not 0 < hours <= 168:
        raise ValueError("freshness_hours must be between 0 and 168")
    max_items = int(config.get("max_items", 1))
    if not 1 <= max_items <= 20:
        raise ValueError("max_items must be between 1 and 20")
    since = now - timedelta(hours=hours)
    diagnostics: list[str] = []
    output: list[dict] = []
    relevance_scores: dict[str, int] = {}
    seen: set[str] = set()
    for url in seen_urls:
        try:
            seen.add(_https_url(url))
        except ValueError:
            continue
    for source in config.get("sources", []):
        if not source.get("enabled", True):
            continue
        publisher = _plain(source.get("publisher", ""), 80)
        try:
            raw = _fetch_feed(source["url"])
            # This check is repeated so test/custom transport hooks cannot bypass it.
            if len(raw) > MAX_FEED_BYTES or _has_xml_declarations(raw):
                raise ValueError("Unsafe or oversized RSS document")
            parsed = feedparser.parse(raw)
            if not parsed.entries:
                raise ValueError("RSS has no readable entries")
            accepted = 0
            keywords = [str(word).casefold().strip() for word in source.get("include_keywords", DEFAULT_INCLUDE_KEYWORDS) if str(word).strip()]
            excluded = [re.sub(r"\s+", "", str(word).casefold()) for word in source.get("exclude_keywords", DEFAULT_EXCLUDE_KEYWORDS) if str(word).strip()]
            for entry in parsed.entries:
                title = _plain(entry.get("title"), 180)
                published = _entry_time(entry)
                if not title or not published or not since <= published <= now + timedelta(minutes=5):
                    continue
                summary = _plain(entry.get("summary") or entry.get("description"), 500)
                normalized_title = title.casefold()
                # Exclude market/industry headlines only. A genuine benefit's
                # eligibility summary may mention an exchange-listed employer.
                compact_title = re.sub(r"\s+", "", normalized_title)
                if any(word in compact_title for word in excluded):
                    continue
                title_hits = [word for word in keywords if word in normalized_title]
                summary_hits = [word for word in keywords if word in summary.casefold()]
                if keywords and not title_hits and not summary_hits:
                    continue
                try:
                    url = _https_url(entry.get("link", ""))
                except ValueError:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                output.append({"title": title, "url": url, "published_at": published.isoformat(), "summary": summary, "publisher": publisher, "category": _plain(source.get("category", "생활비"), 40)})
                # Prefer explicit consumer benefits in the headline to incidental
                # body matches, then select the newest among equally relevant items.
                direct_hits = sum(word in DIRECT_BENEFIT_KEYWORDS for word in title_hits)
                action_hits = sum(word in normalized_title for word in ACTION_TITLE_KEYWORDS) if title_hits else 0
                relevance_scores[url] = sum((
                    10 if title_hits else 0,
                    min(3, len(title_hits)) * 2,
                    min(2, direct_hits) * 5,
                    min(2, action_hits) * 5,
                    min(2, len(summary_hits)),
                ))
                accepted += 1
            diagnostics.append(f"{publisher}: 최근 {hours:g}시간의 신규 관련 항목 {accepted}건")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            diagnostics.append(f"{publisher or 'RSS'}: 수집 실패 ({type(exc).__name__}: {exc})")
    output.sort(key=lambda item: (relevance_scores[item["url"]], item["published_at"]), reverse=True)
    if not output:
        diagnostics.append("새로운 실제 자료가 없어 제작을 중단했습니다. 데모 자료로 대체하지 않습니다.")
    return output[:max_items], diagnostics


def compose_content(items: list[dict], day: str, brand: str = DEFAULT_BRAND, affiliate_links: list[dict] | None = None) -> dict:
    """Build a reviewable headline digest with explicit source citations."""
    date.fromisoformat(day)
    if not items:
        raise ValueError("실제 신규 자료가 없어 콘텐츠를 생성할 수 없습니다")
    sources: list[dict] = []
    for item in items[:3]:
        title = _plain(item.get("title"), 180)
        if not title:
            raise ValueError("Source title is required")
        published = datetime.fromisoformat(str(item["published_at"]).replace("Z", "+00:00"))
        if published.tzinfo is None:
            raise ValueError("Source date must have a timezone")
        sources.append({"title": title, "url": _https_url(item["url"]), "published_at": published.astimezone(UTC).isoformat(), "summary": _plain(item.get("summary"), 500), "publisher": _plain(item.get("publisher"), 80), "category": _plain(item.get("category", "생활비"), 40)})
    links = [{"label": _plain(link.get("label", "제휴 링크"), 80), "url": _https_url(link["url"])} for link in (affiliate_links or [])]
    hook = "오늘의 생활비 소식, 원문으로 확인해요"
    scenes = [f"{hook}\n새로 나온 소식 {len(sources)}건을 모았어요."]
    scene_indexes: list[list[int]] = [[]]
    for index, source in enumerate(sources):
        # Only the attributed headline appears in video. A full summary is kept
        # for the reviewer, avoiding lossy paraphrases of eligibility/amounts.
        scenes.append(f"{source['category']}\n{source['title']}")
        scene_indexes.append([index])
    if len(scenes) < 3:
        scenes.append("원문에서 꼭 확인해요\n누가 대상인지 · 언제까지인지\n어떤 조건이 있는지")
        scene_indexes.append([])
    scenes.append("신청·구매 전 원문 확인\n내 조건에 맞는 정보인지\n직접 확인하고 결정하세요." + ("\n제휴 링크 포함" if links else ""))
    scene_indexes.append(list(range(len(sources))))
    title = f"{day} 생활비 소식 {len(sources)}가지 | {brand}"
    caption_lines = [title, hook, "", "대상·신청 기간·금액·조건은 원문을 확인하세요.", "", "출처"]
    caption_lines.extend(f"[{i + 1}] {source['publisher']} · {source['title']}\n{source['url']}" for i, source in enumerate(sources))
    if links:
        caption_lines.extend(["", "광고·제휴 안내: 아래 링크를 통한 구매/가입 시 운영자가 수수료를 받을 수 있습니다."])
        caption_lines.extend(f"{link['label']}: {link['url']}" for link in links)
    caption_lines.extend(["", "#하루의이득 #생활비 #생활정보 #직장인"])
    return {"day": day, "brand": brand, "title": title, "hook": hook, "scenes": scenes, "scene_source_indexes": scene_indexes, "caption": "\n".join(caption_lines), "sources": sources, "affiliate_links": links, "review_notes": ["각 출처의 원문을 열어 제목과 사실관계를 검수하세요.", "지원 대상·지역·신청 기간·예산 소진 여부·최종 가격은 원문에서 확인하세요.", "본문 요약은 검수 참고용이며 영상에는 출처의 짧은 제목만 표시합니다.", "기사·이미지·음원 이용 권한과 제휴 표시를 확인한 후 업로드를 승인하세요."], "is_demo": False}


def demo_content(day: str, brand: str = DEFAULT_BRAND) -> dict:
    """A synthetic design/pipeline sample, explicitly prohibited from publishing."""
    date.fromisoformat(day)
    return {"day": day, "brand": brand, "title": f"[데모·게시 금지] {brand} 영상 미리보기", "hook": "생활비를 살피는 작은 습관", "scenes": ["데모 · 게시 금지\n생활비를 살피는\n작은 습관", "예시 01 · 구독 정리\n자주 쓰지 않는 구독이\n있는지 살펴보세요.", "예시 02 · 장보기 메모\n이미 있는 물건부터\n확인해 보세요.", "예시 03 · 비교하기\n필요한 물건의 총비용을\n비교해 보세요.", "실제 혜택 정보가 아닌\n자동화 동작 확인용\n합성 샘플입니다."], "scene_source_indexes": [[], [], [], [], []], "caption": "[데모·게시 금지] 실제 뉴스·가격·할인·정책을 담지 않은 자동화 동작 확인용 합성 샘플입니다.", "sources": [], "affiliate_links": [], "review_notes": ["실제 정보가 아닌 합성 샘플입니다. 외부 플랫폼에 게시할 수 없습니다.", "실제 수집·원문 검수·계정 연결 후 별도 콘텐츠를 제작하세요."], "is_demo": True}
