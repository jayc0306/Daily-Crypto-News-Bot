"""Render original Korean text cards to a vertical H.264/AAC review video."""

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
FONT_CANDIDATES = ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
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
    candidates = (*FONT_CANDIDATES, windows_fonts / "malgun.ttf", windows_fonts / "malgunbd.ttf")
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


def render_video(content: dict, output_dir: Path, *, width: int = 1080, height: int = 1920, fps: int = 24, seconds_per_scene: float = 6, font_path: str | None = None) -> dict:
    """Render local original cards. No external media or remote URLs are fetched.

    Optional ``content['audio_path']`` must point to a local recording the user has
    rights to use. Scene durations extend to retain the entire recording plus a
    short tail, with a 90-second maximum. Otherwise the audio track is silent.
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
        card = _draw_card(content, scene, index, len(scenes), width=width, height=height, font_path=font)
        card_path = cards_dir / f"{index:02d}.png"
        card.save(card_path)
        card_paths.append(card_path)
        if index == 0:
            card.save(thumbnail_path, quality=92, optimize=True)
    # Paths in ffconcat are relative generated names, avoiding quoting user paths.
    concat = ["ffconcat version 1.0"]
    for path in card_paths:
        concat.extend([f"file {path.name}", f"duration {scene_duration:.8f}"])
    concat.append(f"file {card_paths[-1].name}")
    concat_path = cards_dir / "frames.ffconcat"
    concat_path.write_text("\n".join(concat) + "\n", encoding="utf-8")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1", "-i", str(concat_path)]
    if audio_path is not None:
        command += ["-protocol_whitelist", "file,pipe", "-i", str(audio_path)]
    else:
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    # A 1.2% centered push-in resets on each card; margins keep text unobscured.
    video_filter = f"fps={fps},zoompan=z='1+0.012*mod(on,{scene_frames})/{scene_frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps={fps},format=yuv420p"
    command += ["-map", "0:v:0", "-map", "1:a:0", "-vf", video_filter, "-af", "apad", "-t", f"{duration:.8f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-threads", "2", "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(video_path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=max(120, int(duration * 10)))
    if result.returncode:
        video_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg rendering failed: {result.stderr[-2500:]}")
    return {"video_path": str(video_path), "thumbnail_path": str(thumbnail_path), "duration": duration, "width": width, "height": height}
