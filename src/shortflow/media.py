"""Inspect user-selected local footage without fetching network resources."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MEDIA_SUFFIXES = frozenset({'.mp4', '.mov', '.webm', '.mkv'})


def inspect_footage(path: Path) -> dict:
    if not shutil.which('ffprobe'):
        raise ValueError('영상 확인 도구가 없습니다. START-WINDOWS.cmd를 다시 실행하세요.')
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
         '-format_whitelist', 'mov,matroska,webm', '-show_entries',
         'format=duration,format_name:stream=codec_type,width,height,duration',
         '-of', 'json', str(path.resolve())],
        capture_output=True, text=True, timeout=30,
    )
    try:
        metadata = json.loads(result.stdout)
        video = next(s for s in metadata['streams'] if s.get('codec_type') == 'video')
        duration = float(metadata['format'].get('duration') or video['duration'])
        width, height = int(video['width']), int(video['height'])
        valid = (result.returncode == 0 and math.isfinite(duration) and
                 0 < duration <= 600 and 16 <= width <= 4096 and 16 <= height <= 4096)
    except (ValueError, KeyError, TypeError, StopIteration):
        valid = False
    if not valid:
        raise ValueError('MP4·MOV·WebM·MKV 영상인지 확인하세요. 최대 10분, 가로·세로 4096px까지 지원합니다.')
    return {'duration': duration, 'width': width, 'height': height}
