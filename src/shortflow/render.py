"""Render Korean headline-and-footage shorts using local, user supplied media."""

from __future__ import annotations

from datetime import date
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from PIL import Image, ImageDraw, ImageFont


BG = "#111916"
PAPER = "#F2EEDD"
LIME = "#D6FE69"
MUTED = "#B5BFAF"
FONT_CANDIDATES = ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf")
ACCENT_COLORS = {"yellow": "#FFE500", "green": "#B4F542", "purple": "#B693FF", "red": "#FF5C62"}
MAX_NARRATED_DURATION = 90.0
NARRATION_TAIL = 0.4


def _audio_duration(audio_path: Path) -> float:
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe is required to measure narration before rendering")
    result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries", "format=duration:stream=codec_type,duration", "-of", "json", str(audio_path)], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError(f"Unable to inspect narration audio: {result.stderr[-500:]}")
    try:
        metadata = json.loads(result.stdout)
        audio = next(stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "audio")
    except (ValueError, TypeError, StopIteration) as exc:
        raise ValueError("Narration must contain an audio stream with a readable duration") from exc
    for candidate in (audio.get("duration"), metadata.get("format", {}).get("duration")):
        try:
            duration = float(candidate)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0:
            return duration
    raise ValueError("Narration duration must be finite and greater than zero")


def _find_font(font_path: str | None) -> str:
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = (*FONT_CANDIDATES, windows_fonts / "malgunbd.ttf", windows_fonts / "malgun.ttf")
    for path in (font_path,) if font_path else candidates:
        if path and Path(path).is_file():
            return str(path)
    raise RuntimeError("Korean font not found; install fonts-noto-cjk, use Windows Malgun Gothic, or set FONT_PATH")


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(8, size))


def _wrap(text: str, draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Character wrapping also handles Korean text with no spaces."""
    lines: list[str] = []
    for paragraph in str(text).split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            if current and draw.textlength(current + char, font=font) > width:
                lines.append(current.rstrip())
                current = char.lstrip()
            else:
                current += char
        if current:
            lines.append(current.rstrip())
    return lines


def _text_block(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], *, font_path: str, size: int, min_size: int, width: int, height: int, fill: str, spacing: float = 1.38) -> int:
    while size >= min_size:
        font = _font(font_path, size)
        lines = _wrap(text, draw, font, width)
        line_height = round(size * spacing)
        if line_height * len(lines) <= height:
            break
        size -= 2
    if line_height * len(lines) > height:
        # Never silently crop editorial content into a different claim.
        raise ValueError("Card text does not fit; shorten the scene text before rendering")
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill, anchor="lt")
        y += line_height
    return y


def _draw_card(content: dict, scene: str, index: int, count: int, *, width: int, height: int, font_path: str) -> Image.Image:
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    scale = width / 1080
    px = lambda value: max(1, round(value * scale))
    margin = px(86)
    usable = width - 2 * margin
    # Keep critical content within mobile players' central safe area.
    top = round(height * 0.12)
    draw.rounded_rectangle((margin, top, margin + px(28), top + px(28)), radius=px(6), fill=LIME)
    draw.text((margin + px(45), top - px(5)), str(content.get("brand", "하루의 이득")), font=_font(font_path, px(34)), fill=PAPER, anchor="lt")
    draw.text((margin, top + px(80)), "생활비 브리핑" if not content.get("is_demo") else "DEMO · 게시 금지", font=_font(font_path, px(27)), fill=LIME, anchor="lt")
    draw.text((margin, top + px(170)), f"{index + 1:02d}", font=_font(font_path, px(136)), fill=LIME, anchor="lt")
    body_top = top + px(370)
    body_bottom = round(height * 0.72)
    _text_block(draw, scene, (margin, body_top), font_path=font_path, size=px(70), min_size=px(34), width=usable, height=body_bottom - body_top, fill=PAPER)
    footer_top = round(height * 0.76)
    draw.line((margin, footer_top, width - margin, footer_top), fill="#465044", width=px(2))
    source_indexes = content.get("scene_source_indexes", [])
    sources = content.get("sources", [])
    indexes = source_indexes[index] if index < len(source_indexes) else list(range(len(sources)))
    if content.get("is_demo"):
        footer = "합성 샘플 · 실제 혜택 정보 아님"
    elif indexes:
        footer = "출처 " + " · ".join(f"[{i + 1}] {sources[i].get('publisher', '')}" for i in indexes if isinstance(i, int) and 0 <= i < len(sources))
    else:
        footer = "출처 링크와 자세한 조건은 본문에서"
    _text_block(draw, footer, (margin, footer_top + px(28)), font_path=font_path, size=px(24), min_size=px(18), width=usable, height=px(100), fill=MUTED)
    draw.text((margin, footer_top + px(125)), str(content.get("day", date.today().isoformat())), font=_font(font_path, px(25)), fill=MUTED, anchor="lt")
    draw.text((width - margin, footer_top + px(125)), f"{index + 1} / {count}", font=_font(font_path, px(25)), fill=LIME, anchor="rt")
    # Slim segmented progress line is kept above platform bottom controls.
    segment = (usable - px(14) * (count - 1)) / count
    bar_y = round(height * 0.88)
    for position in range(count):
        left = margin + round(position * (segment + px(14)))
        draw.rounded_rectangle((left, bar_y, left + round(segment), bar_y + px(5)), radius=px(2), fill=LIME if position <= index else "#465044")
    return image


def _media_box(width: int, height: int) -> tuple[int, int, int, int]:
    """A wide, edge-to-edge picture within the vertical black canvas."""
    top = round(height * 0.335)
    panel_height = round(height * 0.405)
    # Even dimensions also keep the H.264 crop aligned to chroma samples.
    return 0, top - top % 2, width, panel_height - panel_height % 2


def _headline_lines(text: str, draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont) -> list[str]:
    explicit = [line.strip() for line in text.splitlines() if line.strip()]
    if len(explicit) > 2:
        raise ValueError("Headline does not fit; use at most two lines")
    if len(explicit) == 2:
        return explicit
    if not explicit:
        raise ValueError("Headline must include nonempty text")
    text = explicit[0]
    if len(text) == 1:
        return [text]
    # Prefer word boundaries; unspaced Korean can split between syllables.
    breaks = [index for index in range(1, len(text)) if text[index].isspace()]
    if not breaks:
        breaks = list(range(1, len(text)))
    split = min(breaks, key=lambda index: max(draw.textlength(text[:index].strip(), font=font), draw.textlength(text[index:].strip(), font=font)))
    return [text[:split].strip(), text[split:].strip()]


def _draw_headline(draw: ImageDraw.ImageDraw, content: dict, *, width: int, height: int, font_path: str, accent: str) -> None:
    text = str(content.get("headline") or "").strip() or str(content.get("title") or "").strip() or "오늘의 생활비\n가볍게 살펴보기"
    if len(text) > 400:
        raise ValueError("Headline does not fit; shorten it before rendering")
    scale = width / 1080
    usable_width = width - round(80 * scale)
    max_height = round(height * 0.17)
    for size in range(max(8, round(112 * scale)), max(7, round(48 * scale)) - 1, -1):
        font = _font(font_path, size)
        lines = _headline_lines(text, draw, font)
        line_height = round(size * 1.36)
        if max(draw.textlength(line, font=font) for line in lines) <= usable_width and len(lines) * line_height <= max_height:
            break
    else:
        raise ValueError("Headline does not fit; shorten it before rendering")
    top = round(height * 0.115) + (max_height - len(lines) * line_height) // 2
    for index, line in enumerate(lines):
        draw.text((width // 2, top + index * line_height), line, font=font, fill="#FFFFFF" if index == 0 else accent, anchor="mt")


def _draw_information_panel(image: Image.Image, content: dict, *, box: tuple[int, int, int, int], font_path: str, accent: str) -> None:
    """Original geometric illustration, explicitly identified as a layout sample."""
    x, top, width, panel_height = box
    scale = width / 1080
    px = lambda value: max(1, round(value * scale))
    draw = ImageDraw.Draw(image)
    # Quiet blue-green gradient keeps the drawn fallback distinct from footage.
    for row in range(panel_height):
        progress = row / max(1, panel_height - 1)
        color = (round(20 - 8 * progress), round(42 - 15 * progress), round(46 - 15 * progress), 255)
        draw.line((x, top + row, width, top + row), fill=color)
    center_x, center_y = round(width * 0.83), top + round(panel_height * 0.48)
    radius = px(295)
    draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), outline="#315756", width=px(2))
    radius = px(220)
    draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill="#21413F")
    label = "화면 구성 샘플" if content.get("is_demo") else "자료 영상 미첨부"
    draw.rounded_rectangle((px(48), top + px(36), px(355), top + px(91)), radius=px(28), fill="#0B201F", outline="#557774", width=px(1))
    draw.text((px(201), top + px(47)), label, font=_font(font_path, px(28)), fill="#D4E9DC", anchor="mt")
    draw.text((px(64), top + px(173)), "작은 습관이", font=_font(font_path, px(64)), fill="#F3F6F2", anchor="lt")
    draw.text((px(64), top + px(260)), "하루의 이득", font=_font(font_path, px(64)), fill=accent, anchor="lt")
    # A simple checklist and coin are drawn here; no stock image is implied.
    left, card_top = px(668), top + px(165)
    draw.rounded_rectangle((left, card_top, left + px(264), card_top + px(304)), radius=px(28), fill="#EFF2DE")
    for index in range(3):
        row_y = card_top + px(55 + index * 72)
        draw.rounded_rectangle((left + px(27), row_y, left + px(57), row_y + px(30)), radius=px(7), fill="#C1DAC5")
        draw.line((left + px(33), row_y + px(15), left + px(41), row_y + px(22), left + px(53), row_y + px(8)), fill="#325B46", width=px(4))
        draw.rounded_rectangle((left + px(78), row_y + px(8), left + px(222), row_y + px(19)), radius=px(5), fill="#6E8B7D")
    coin_x, coin_y, coin_radius = left + px(221), card_top + px(268), px(78)
    draw.ellipse((coin_x - coin_radius, coin_y - coin_radius, coin_x + coin_radius, coin_y + coin_radius), fill=accent)
    draw.ellipse((coin_x - px(58), coin_y - px(58), coin_x + px(58), coin_y + px(58)), outline="#365044", width=px(3))
    draw.text((coin_x, coin_y - px(37)), "₩", font=_font(font_path, px(62)), fill="#21342D", anchor="mt")


def _draw_subtitle(draw: ImageDraw.ImageDraw, scene: str, *, width: int, box: tuple[int, int, int, int], font_path: str) -> None:
    _, top, _, panel_height = box
    scale = width / 1080
    px = lambda value: max(1, round(value * scale))
    max_height = round(panel_height * 0.34)
    for size in range(px(52), px(32) - 1, -1):
        font = _font(font_path, size)
        lines = _wrap(scene.strip(), draw, font, width - px(130))
        line_height = round(size * 1.35)
        if len(lines) * line_height + px(32) <= max_height and all(draw.textlength(line, font=font) <= width - px(130) for line in lines):
            break
    else:
        raise ValueError("Subtitle text does not fit; shorten the scene text before rendering")
    bottom = top + panel_height - px(40)
    subtitle_top = bottom - len(lines) * line_height - px(28)
    text_width = max(draw.textlength(line, font=font) for line in lines)
    left = max(px(30), round((width - text_width) / 2) - px(24))
    draw.rounded_rectangle((left, subtitle_top, width - left, bottom), radius=px(5), fill=(0, 0, 0, 240))
    for index, line in enumerate(lines):
        draw.text((width // 2, subtitle_top + px(14) + index * line_height), line, font=font, fill="#FFFFFF", anchor="mt")


def _draw_reference_frame(content: dict, scene: str, *, width: int, height: int, font_path: str, has_media: bool) -> Image.Image:
    image = Image.new("RGBA", (width, height), "#000000")
    draw = ImageDraw.Draw(image)
    scale = width / 1080
    px = lambda value: max(1, round(value * scale))
    accent = ACCENT_COLORS[content.get("accent_color", "yellow")]
    _draw_headline(draw, content, width=width, height=height, font_path=font_path, accent=accent)
    box = _media_box(width, height)
    x, top, panel_width, panel_height = box
    if has_media:
        draw.rectangle((x, top, panel_width - 1, top + panel_height - 1), fill=(0, 0, 0, 0))
    else:
        _draw_information_panel(image, content, box=box, font_path=font_path, accent=accent)
    _draw_subtitle(draw, scene, width=width, box=box, font_path=font_path)
    brand = str(content.get("brand", "하루의 이득"))
    brand_font = _font(font_path, px(33))
    brand_width = draw.textlength(brand, font=brand_font)
    if brand_width > width - px(150):
        raise ValueError("Brand text does not fit; shorten it before rendering")
    brand_y = round(height * 0.805)
    brand_left = round((width - brand_width - px(48)) / 2)
    draw.rounded_rectangle((brand_left, brand_y + px(5), brand_left + px(29), brand_y + px(34)), radius=px(8), fill=accent)
    draw.line((brand_left + px(8), brand_y + px(20), brand_left + px(13), brand_y + px(25), brand_left + px(22), brand_y + px(13)), fill="#000000", width=px(3))
    draw.text((brand_left + px(48), brand_y), brand, font=brand_font, fill="#F4F4F4", anchor="lt")
    if content.get("is_demo"):
        footer = "화면 구성 샘플 · 실제 혜택 정보 아님"
    else:
        publishers = list(dict.fromkeys(str(source.get("publisher", "")) for source in content.get("sources", []) if source.get("publisher")))
        footer = "출처 · " + " · ".join(publishers) if publishers else "자세한 조건과 출처는 본문에서"
    footer_font = _font(font_path, px(23))
    footer_lines = _wrap(footer, draw, footer_font, width - px(130))
    if len(footer_lines) > 2:
        raise ValueError("Source text does not fit; shorten publisher names before rendering")
    for index, line in enumerate(footer_lines):
        draw.text((width // 2, brand_y + px(66 + index * 34)), line, font=footer_font, fill="#858585", anchor="mt")
    return image


def _validate_media(media_path: Path) -> None:
    if not media_path.is_file():
        raise ValueError("media_path must be an existing local video file")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe is required to inspect local video footage")
    result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", str(media_path)], capture_output=True, text=True, timeout=20)
    try:
        metadata = json.loads(result.stdout)
        video = next(stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "video" and stream.get("width", 0) > 0 and stream.get("height", 0) > 0)
        duration = float(metadata.get("format", {}).get("duration", 0))
        if result.returncode or not video or not math.isfinite(duration) or duration <= 0:
            raise ValueError
    except (ValueError, TypeError, StopIteration) as exc:
        raise ValueError("media_path must contain a readable local video with a finite duration") from exc


def render_video(content: dict, output_dir: Path, *, width: int = 1080, height: int = 1920, fps: int = 24, seconds_per_scene: float = 6, font_path: str | None = None) -> dict:
    """Render a black vertical canvas with a two-line headline and local footage.

    ``media_path`` is an optional trusted local video; it is cropped without
    distortion, looped to the subtitle duration, and its original audio is muted.
    Without it an explicitly labeled original illustration fills the picture.
    ``headline`` defaults to ``title``; ``accent_color`` defaults to yellow.
    Optional ``audio_path`` narration is retained in full plus a short tail,
    with a 90-second maximum. Otherwise an AAC silent audio track is added.
    The previous card layout remains available as ``template='cards'``.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to render videos")
    if width < 180 or height < 320 or width % 2 or height % 2:
        raise ValueError("Video dimensions must be even and at least 180×320")
    if not 1 <= fps <= 60 or not 0.1 <= seconds_per_scene <= 30:
        raise ValueError("Use fps 1–60 and seconds_per_scene 0.1–30")
    scenes = content.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 10 or any(not isinstance(scene, str) or not scene.strip() or len(scene) > 1000 for scene in scenes):
        raise ValueError("Content must include 1–10 nonempty text scenes")
    if content.get("accent_color", "yellow") not in ACCENT_COLORS:
        raise ValueError("accent_color must be yellow, green, purple, or red")
    template = content.get("template", "reference")
    if template not in ("reference", "cards"):
        raise ValueError("template must be reference or cards")
    media_path = None
    if content.get("media_path"):
        media_path = Path(content["media_path"]).resolve()
        _validate_media(media_path)
        if template == "cards":
            raise ValueError("Local footage requires the reference template")
    scene_frames = max(1, round(seconds_per_scene * fps))
    audio_path = None
    if content.get("audio_path"):
        audio_path = Path(content["audio_path"]).resolve()
        if not audio_path.is_file():
            raise ValueError("audio_path must be an existing local file")
        audio_duration = _audio_duration(audio_path)
        # Round up in whole frames per card so neither audio nor its tail is cut.
        required_frames = math.ceil((audio_duration + NARRATION_TAIL) * fps / len(scenes) - 1e-9)
        scene_frames = max(scene_frames, required_frames)
        if scene_frames * len(scenes) / fps > MAX_NARRATED_DURATION:
            raise ValueError("Narrated video exceeds 90 seconds; shorten the narration or scenes")
    scene_duration = scene_frames / fps
    duration = scene_duration * len(scenes)
    font = _find_font(font_path)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    cards_dir = output_dir / f"cards-{run_id}"
    cards_dir.mkdir()
    video_path = output_dir / f"video-{run_id}.mp4"
    thumbnail_path = output_dir / f"thumbnail-{run_id}.jpg"
    card_paths: list[Path] = []
    for index, scene in enumerate(scenes):
        if template == "cards":
            card = _draw_card(content, scene, index, len(scenes), width=width, height=height, font_path=font)
        else:
            card = _draw_reference_frame(content, scene, width=width, height=height, font_path=font, has_media=media_path is not None)
        card_path = cards_dir / f"{index:02d}.png"
        card.save(card_path)
        card_paths.append(card_path)
    # Paths in ffconcat are relative generated names, avoiding quoting user paths.
    concat = ["ffconcat version 1.0"]
    for path in card_paths:
        concat.extend([f"file {path.name}", f"duration {scene_duration:.8f}"])
    concat.append(f"file {card_paths[-1].name}")
    concat_path = cards_dir / "frames.ffconcat"
    concat_path.write_text("\n".join(concat) + "\n", encoding="utf-8")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1", "-i", str(concat_path)]
    if media_path is not None:
        command += ["-protocol_whitelist", "file,pipe", "-stream_loop", "-1", "-i", str(media_path)]
    audio_index = 2 if media_path is not None else 1
    if audio_path is not None:
        command += ["-protocol_whitelist", "file,pipe", "-i", str(audio_path)]
    else:
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    if media_path is not None:
        _, media_top, media_width, media_height = _media_box(width, height)
        video_filter = (
            f"[0:v]fps={fps},setpts=PTS-STARTPTS[design];"
            f"[1:v]scale={media_width}:{media_height}:force_original_aspect_ratio=increase,"
            f"crop={media_width}:{media_height},setsar=1,fps={fps},setpts=PTS-STARTPTS,"
            f"pad={width}:{height}:0:{media_top}:color=black[footage];"
            "[footage][design]overlay=0:0:shortest=1:format=auto,format=yuv420p[video]"
        )
        command += ["-filter_complex_threads", "1", "-filter_complex", video_filter, "-map", "[video]"]
    else:
        video_filter = f"fps={fps},format=yuv420p"
        if template == "cards":
            video_filter = f"fps={fps},zoompan=z='1+0.012*mod(on,{scene_frames})/{scene_frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps={fps},format=yuv420p"
        command += ["-map", "0:v:0", "-vf", video_filter]
    command += ["-map", f"{audio_index}:a:0", "-af", "apad", "-t", f"{duration:.8f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-threads", "2", "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(video_path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=max(120, int(duration * 10)))
    if result.returncode:
        video_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg rendering failed: {result.stderr[-2500:]}")
    # Extract from the encoded composition so uploaded footage is in the poster.
    poster = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", "-threads", "1", str(thumbnail_path)], capture_output=True, text=True, timeout=30)
    if poster.returncode:
        raise RuntimeError(f"ffmpeg thumbnail extraction failed: {poster.stderr[-1000:]}")
    return {"video_path": str(video_path), "thumbnail_path": str(thumbnail_path), "duration": duration, "width": width, "height": height}
