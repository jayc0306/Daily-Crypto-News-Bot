from array import array
from io import BytesIO
import json
import math
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageChops, ImageStat
import pytest

from shortflow import content, render


FFMPEG_REQUIRED = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg and ffprobe are required")


def _clip(path: Path) -> Path:
    """Half-second alternating footage with a square and an audible source tone."""
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=12:d=0.25",
        "-f", "lavfi", "-i", "color=c=red:s=320x180:r=12:d=0.25",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=0.5",
        "-filter_complex_threads", "1", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,drawbox=x=140:y=40:w=40:h=40:color=white:t=fill[v]",
        "-map", "[v]", "-map", "2:a:0", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True, capture_output=True)
    return path


def _frame(path: str, second: float) -> Image.Image:
    decoded = subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-ss", str(second), "-i", path,
        "-frames:v", "1", "-threads", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1",
    ], check=True, capture_output=True)
    return Image.open(BytesIO(decoded.stdout)).convert("RGB")


def _audio(path: str, *, start: float = 0, duration: float = 0.2) -> array:
    decoded = subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-i", path, "-ss", str(start), "-t", str(duration),
        "-vn", "-ar", "8000", "-ac", "1", "-f", "f32le", "pipe:1",
    ], check=True, capture_output=True)
    samples = array("f")
    samples.frombytes(decoded.stdout)
    return samples


@FFMPEG_REQUIRED
def test_reference_composes_looped_footage_changes_subtitles_and_mutes_source(tmp_path):
    source = _clip(tmp_path / "uploaded clip.mp4")
    draft = content.demo_content("2026-10-19")
    draft.update(headline="오늘의 작은 습관\n가벼워지는 생활비", media_path=str(source), scenes=["첫 번째 자막", "다음 장면의 다른 자막"])
    result = render.render_video(draft, tmp_path / "output", width=270, height=480, fps=12, seconds_per_scene=0.75)
    first = _frame(result["video_path"], 1 / 12)
    red = _frame(result["video_path"], 4 / 12)
    looped = _frame(result["video_path"], 13 / 12)
    late = _frame(result["video_path"], 16 / 12)
    # The black canvas remains fixed; footage occupies the full-width middle.
    assert max(first.getpixel((5, 20))) < 8
    assert max(first.getpixel((5, 390))) < 8
    for frame in (first, looped):
        r, g, b = frame.getpixel((12, 205))
        assert b > 180 and r < 30 and g < 30
    for frame in (red, late):
        r, g, b = frame.getpixel((12, 205))
        assert r > 180 and b < 30 and g < 30
    # The source's square stays square after aspect-preserving fill and crop.
    white = [(x, y) for y in range(175, 265) for x in range(80, 190) if min(first.getpixel((x, y))) > 220]
    assert white
    square_width = max(x for x, _ in white) - min(x for x, _ in white)
    square_height = max(y for _, y in white) - min(y for _, y in white)
    assert abs(square_width - square_height) <= 2
    header_difference = ImageChops.difference(first.crop((0, 0, 270, 150)), looped.crop((0, 0, 270, 150)))
    assert max(ImageStat.Stat(header_difference).mean) < 0.5
    assert ImageChops.difference(first.crop((10, 290, 260, 350)), looped.crop((10, 290, 260, 350))).getbbox() is not None
    headline = first.crop((10, 50, 260, 140))
    assert any(r > 210 and g > 210 and b < 70 for r, g, b in (headline.getpixel((x, y)) for y in range(headline.height) for x in range(headline.width)))
    assert any(min(headline.getpixel((x, y))) > 220 for y in range(headline.height) for x in range(headline.width))
    thumbnail = Image.open(result["thumbnail_path"]).convert("RGB")
    assert thumbnail.getpixel((12, 205))[2] > 180
    samples = _audio(result["video_path"])
    assert samples and max(abs(sample) for sample in samples) < 0.0001
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", result["video_path"]], check=True, capture_output=True, text=True)
    streams = json.loads(probe.stdout)["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    assert int(video["nb_frames"]) == 18
    assert next(stream for stream in streams if stream["codec_type"] == "audio")["codec_name"] == "aac"


@FFMPEG_REQUIRED
def test_reference_keeps_entire_narration_over_looped_muted_footage(tmp_path):
    source = _clip(tmp_path / "footage.mp4")
    narration = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "sine=frequency=440:duration=2.1", str(narration)], check=True, capture_output=True)
    draft = content.demo_content("2026-10-19")
    draft.update(headline="작은 습관으로\n가벼워지는 하루", media_path=str(source), audio_path=str(narration))
    result = render.render_video(draft, tmp_path / "output", width=270, height=480, fps=12, seconds_per_scene=0.25)
    assert result["duration"] >= 2.5
    samples = _audio(result["video_path"], start=1.9, duration=0.1)
    assert sum(sample * sample for sample in samples) / len(samples) > 0.001
    def tone_power(frequency):
        real = sum(sample * math.cos(2 * math.pi * frequency * index / 8000) for index, sample in enumerate(samples))
        imaginary = sum(sample * math.sin(2 * math.pi * frequency * index / 8000) for index, sample in enumerate(samples))
        return real * real + imaginary * imaginary
    assert tone_power(440) > tone_power(880) * 20
    # Narration outlasts the source clip, and the footage still reaches the tail.
    assert _frame(result["video_path"], result["duration"] - 0.15).getpixel((12, 205))[0] > 180


@pytest.mark.parametrize("accent", list(render.ACCENT_COLORS))
def test_reference_drawn_fallback_is_explicit_and_accepts_each_accent(accent):
    font = render._find_font(None)
    draft = content.demo_content("2026-10-19")
    draft.update(headline="오늘의 작은 습관\n생활비를 가볍게", accent_color=accent)
    frame = render._draw_reference_frame(draft, "필요한 것부터 확인해요", width=270, height=480, font_path=font, has_media=False)
    _, top, _, _ = render._media_box(270, 480)
    # The sample label area contains text and has an opaque illustrated backdrop.
    assert frame.getpixel((12, top + 10))[3] == 255
    assert any(min(frame.getpixel((x, y))[:3]) > 150 for y in range(top + 10, top + 22) for x in range(14, 88))
    assert frame.getpixel((12, top + 60))[:3] != (0, 0, 0)


def test_reference_blank_headline_resets_to_title_and_long_headline_is_not_cut():
    font = render._find_font(None)
    draft = content.demo_content("2026-10-19")
    draft.update(title="작은 습관\n가벼운 하루", headline="  \n ")
    render._draw_reference_frame(draft, "짧은 자막", width=270, height=480, font_path=font, has_media=False)
    draft["headline"] = "아주 긴 제목 " * 100
    with pytest.raises(ValueError, match="does not fit"):
        render._draw_reference_frame(draft, "짧은 자막", width=270, height=480, font_path=font, has_media=False)


def test_reference_rejects_non_video_and_missing_media(tmp_path):
    missing = tmp_path / "missing.mp4"
    with pytest.raises(ValueError, match="existing local"):
        render._validate_media(missing)
    invalid = tmp_path / "invalid.mp4"
    invalid.write_text("not a video", encoding="utf-8")
    if shutil.which("ffprobe"):
        with pytest.raises(ValueError, match="readable local video"):
            render._validate_media(invalid)
