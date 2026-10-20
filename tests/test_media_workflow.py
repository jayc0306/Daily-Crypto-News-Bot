"""Real local footage attachment must invalidate earlier review approvals."""
import json
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from shortflow.app import create_app
from shortflow.media import inspect_footage
from shortflow.settings import Settings

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                                reason='ffmpeg and ffprobe required')
HEADERS = {'X-Shortflow-Action': 'review'}


@pytest.fixture(scope='module')
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp('footage') / 'clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                    'color=c=blue:s=320x180:r=12:d=1', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(path)], check=True)
    return path.read_bytes()


@pytest.fixture
def app(tmp_path, monkeypatch):
    from shortflow.render import render_video
    monkeypatch.setattr('shortflow.render.render_video', lambda content, folder, **kwargs:
        render_video(content, folder, width=270, height=480, fps=12,
                     seconds_per_scene=0.25, **kwargs))
    settings = Settings(tmp_path, {'DASHBOARD_PASSWORD': 'test-password',
                                   'SCHEDULE_ENABLED': 'false'})
    return create_app(settings, start_worker=False)


def new_draft(app):
    # Real news fixture; no publishing credentials or outbound service calls.
    service = app.state.service
    demo = service.prepare(demo=True)
    content = {**demo['content'], 'is_demo': False,
               'sources': [{'title': '생활비 정보', 'url': 'https://www.korea.kr/example'}]}
    return service.store.create('2026-10-20', content, demo['video'])


def test_attach_render_reapprove_and_remove_footage(app, clip):
    draft = new_draft(app)
    base = f"/api/drafts/{draft['id']}"
    with TestClient(app) as client:
        client.auth = ('admin', 'test-password')
        response = client.post(base + '/media', params={'revision': 1, 'filename': r'C:\photos\clip.mp4'},
                               content=clip, headers=HEADERS)
        assert response.status_code == 200, response.text
        attached = response.json()
        assert attached['revision'] == 2 and attached['status'] == 'pending_review'
        assert attached['content']['media_name'] == 'clip.mp4'
        assert Path(attached['content']['media_path']).read_bytes() == clip
        assert attached['video']['video_path'] != draft['video']['video_path']
        assert Path(draft['video']['video_path']).is_file()  # old review immutable
        probe = subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                         '-of', 'json', attached['video']['video_path']])
        assert json.loads(probe)['streams'][0]['codec_name'] == 'h264'
        old_approve = client.post(base + '/approve', json={'revision': 1}, headers=HEADERS)
        assert old_approve.status_code == 409
        stale_remove = client.delete(base + '/media?revision=1', headers=HEADERS)
        assert stale_remove.status_code == 409
        removed = client.delete(base + '/media?revision=2', headers=HEADERS)
        assert removed.status_code == 200, removed.text
        assert removed.json()['revision'] == 3 and not removed.json()['content']['media_path']
        assert client.post(base + '/approve', json={'revision': 3}, headers=HEADERS).status_code == 200
        assert client.post(base + '/media?revision=3&filename=clip.mp4', content=clip,
                           headers=HEADERS).status_code == 409


def test_upload_auth_invalid_files_size_and_stale_cleanup(app, clip, monkeypatch):
    draft = new_draft(app)
    url = f"/api/drafts/{draft['id']}/media?revision=1&filename=clip.mp4"
    uploads = app.state.service.settings.data_dir / 'uploads'
    with TestClient(app) as client:
        assert client.post(url, content=clip, headers=HEADERS).status_code == 401
        client.auth = ('admin', 'test-password')
        assert client.post(url, content=clip).status_code == 403
        assert client.post(url.replace('clip.mp4', 'list.m3u8'), content=clip, headers=HEADERS).status_code == 422
        assert client.post(url, content=b'not a video', headers=HEADERS).status_code == 422
        assert not list(uploads.glob('*'))
        monkeypatch.setattr('shortflow.media.MAX_UPLOAD_BYTES', 12)
        assert client.post(url, content=clip, headers=HEADERS).status_code == 413
        # Missing Content-Length must not bypass the streaming limit.
        assert client.post(url, content=iter([b'x' * 8, b'x' * 8]), headers=HEADERS).status_code == 413
        assert not list(uploads.glob('*'))
        monkeypatch.setattr('shortflow.media.MAX_UPLOAD_BYTES', 100 * 1024 * 1024)
        app.state.service.store.update(draft['id'], draft['content'], draft['video'], 1)
        assert client.post(url, content=clip, headers=HEADERS).status_code == 409
        assert not list(uploads.glob('*'))


def test_demo_remains_unpublishable_after_footage_attachment(app, clip):
    draft = app.state.service.prepare(demo=True)
    with TestClient(app) as client:
        client.auth = ('admin', 'test-password')
        base = f"/api/drafts/{draft['id']}"
        result = client.post(base + '/media?revision=1&filename=clip.mp4', content=clip, headers=HEADERS)
        assert result.status_code == 200, result.text
        assert result.json()['content']['is_demo'] is True
        assert client.post(base + '/approve', json={'revision': 2}, headers=HEADERS).status_code == 409


def test_edit_during_upload_validation_discards_unattached_file(app, clip, monkeypatch):
    draft = new_draft(app)
    original = inspect_footage
    def changed_while_probing(path):
        result = original(path)
        app.state.service.store.update(draft['id'], draft['content'], draft['video'], 1)
        return result
    monkeypatch.setattr('shortflow.media.inspect_footage', changed_while_probing)
    with TestClient(app) as client:
        client.auth = ('admin', 'test-password')
        response = client.post(f"/api/drafts/{draft['id']}/media?revision=1&filename=clip.mp4",
                               content=clip, headers=HEADERS)
        assert response.status_code == 409
    current = app.state.service.store.get(draft['id'])
    assert current['revision'] == 2 and not current['content'].get('media_path')
    assert not list((app.state.service.settings.data_dir / 'uploads').glob('*'))


def test_reject_playlist_disguised_as_video(tmp_path):
    path = tmp_path / 'clip.mp4'
    path.write_text('#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://127.0.0.1:9/private.ts\n')
    with pytest.raises(ValueError, match='MP4'):
        inspect_footage(path)


def test_long_source_headline_still_creates_editable_draft(app, monkeypatch):
    title = '청년월세 특별지원 신청기간 연장 및 소득기준 완화 대상자 확인하고 온라인으로 신청하세요 신청 대상 및 제출서류 안내'
    source = {'title': title, 'url': 'https://www.korea.kr/example',
              'published_at': '2026-10-20T00:00:00+00:00', 'publisher': '정책브리핑',
              'category': '생활정책', 'summary': ''}
    monkeypatch.setattr('shortflow.content.collect_sources', lambda *args: ([source], []))
    draft = app.state.service.prepare()
    assert draft['status'] == 'pending_review'
    assert draft['content']['headline'] == '오늘의 생활비 소식\n원문에서 조건 확인'
    assert title in '\n'.join(draft['content']['scenes'])


def test_editor_accepts_visual_fields_but_rejects_client_media_paths(app):
    draft = new_draft(app)
    body = {key: draft['content'][key] for key in ('title', 'caption', 'scenes')}
    body.update(headline='생활비 소식\n조건을 확인하세요', accent_color='green')
    with TestClient(app) as client:
        client.auth = ('admin', 'test-password')
        url = f"/api/drafts/{draft['id']}"
        saved = client.put(url, json=body, headers=HEADERS)
        assert saved.status_code == 200, saved.text
        assert saved.json()['content']['accent_color'] == 'green'
        assert client.put(url, json={**body, 'media_path': '/etc/passwd'}, headers=HEADERS).status_code == 422
        assert client.put(url, json={**body, 'headline': '첫째\n둘째\n셋째'}, headers=HEADERS).status_code == 422
        reset = client.put(url, json={**body, 'headline': ''}, headers=HEADERS)
        assert reset.status_code == 200, reset.text
