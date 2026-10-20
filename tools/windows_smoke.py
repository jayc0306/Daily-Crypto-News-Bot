"""Exercise the real native Windows install without publishing or using APIs.

Run START-WINDOWS.cmd --setup-only first. No credentials or generated files are
printed or uploaded; the sample, database, and settings stay on the CI machine.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import httpx


def main() -> None:
    assert sys.platform == "win32", "This smoke check requires a real Windows host"
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    os.environ.update(SCHEDULE_ENABLED="false", LLM_ENABLED="false", TTS_ENABLED="false")
    os.environ["PATH"] = str(root / ".local/ffmpeg/bin") + os.pathsep + os.environ["PATH"]
    for program in ("ffmpeg", "ffprobe"):
        assert shutil.which(program), f"Native install did not provide {program}"

    from shortflow.render import _find_font
    from shortflow.settings import Settings
    from shortflow.worker import worker_lock
    from shortflow.server import WINDOWS_LOOP
    import asyncio
    import uvicorn

    config = uvicorn.Config(lambda scope, receive, send: None, loop=WINDOWS_LOOP)
    assert config.get_loop_factory() is asyncio.SelectorEventLoop

    settings = Settings.load(root)
    assert (root / ".env").is_file() and len(settings.get("DASHBOARD_PASSWORD")) >= 12
    assert not settings.get("TELEGRAM_BOT_TOKEN"), "Use a clean checkout without account settings"
    assert Path(_find_font(settings.get("FONT_PATH") or None)).is_file()
    print("Native Windows tools, Korean font, and Selector event loop are ready.")

    lock_path = root / ".local/windows-smoke.lock"
    child = ("from pathlib import Path; import sys\n"
             "from shortflow.worker import worker_lock\n"
             "with worker_lock(Path(sys.argv[1])) as acquired: print(int(acquired))\n")

    def other_process_locks() -> bool:
        return subprocess.check_output([sys.executable, "-c", child, str(lock_path)],
                                       text=True, timeout=20).strip() == "1"

    with worker_lock(lock_path) as acquired:
        assert acquired and not other_process_locks(), "Windows worker lock allowed a second owner"
    assert other_process_locks(), "Windows worker lock was not released"

    result = subprocess.run([sys.executable, "-m", "shortflow", "demo"], check=True,
                            capture_output=True, text=True, encoding="utf-8", timeout=240)
    draft = json.loads(result.stdout)
    assert draft["is_demo"] and draft["status"] == "pending_review"
    probe = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
         draft["video"]["video_path"]], text=True, encoding="utf-8", timeout=30))
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 1080, 1920)
    assert audio["codec_name"] == "aac" and float(probe["format"]["duration"]) > 0
    print("Real 1080x1920 H.264/AAC sample rendered; Windows process locking passed.")

    footage = root / 'data' / 'windows-footage.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                    'color=c=blue:s=320x180:r=12:d=1', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(footage)], check=True, timeout=30)

    server = subprocess.Popen([sys.executable, "-m", "shortflow", "serve", "--host",
                               "127.0.0.1", "--port", "8787"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        with httpx.Client(base_url="http://127.0.0.1:8787", trust_env=False, timeout=180,
                          auth=(settings.get("DASHBOARD_USERNAME", "admin"),
                                settings.get("DASHBOARD_PASSWORD"))) as client:
            deadline = time.monotonic() + 45
            while True:
                assert server.poll() is None, "Native server exited before becoming ready"
                try:
                    health = client.get("/health")
                    if health.status_code == 200:
                        assert health.json() == {"status": "ok", "service": "shortflow"}
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "Native server did not become ready"
                time.sleep(0.3)
            assert client.get("/api/overview", auth=None).status_code == 401
            overview = client.get("/api/overview")
            overview.raise_for_status()
            # CLI output is a summary; the API carries the current revision.
            draft = next(item for item in overview.json()['drafts'] if item['id'] == draft['id'])
            media = client.get(f"/media/{draft['id']}/video")
            media.raise_for_status()
            assert media.headers["content-type"].startswith("video/mp4")
            assert media.content[4:8] == b"ftyp"
            attached = client.post(f"/api/drafts/{draft['id']}/media",
                                   params={'revision': draft['revision'], 'filename': 'local-footage.mp4'},
                                   headers={'X-Shortflow-Action': 'review',
                                            'Content-Type': 'application/octet-stream'},
                                   content=footage.read_bytes())
            attached.raise_for_status()
            updated = attached.json()
            assert updated['revision'] == draft['revision'] + 1
            assert updated['content']['media_name'] == 'local-footage.mp4'
            assert updated['content']['is_demo']
            revised_video = client.get(f"/media/{draft['id']}/video")
            revised_video.raise_for_status()
            assert revised_video.content[4:8] == b'ftyp'
            print('Uploaded local footage and rendered the revised headline/subtitle video on Windows.')
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    print("Native dashboard, authentication, and real MP4 delivery passed. No publishing was attempted.")


if __name__ == "__main__":
    main()
