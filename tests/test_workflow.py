from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shortflow.app import create_app
from shortflow.service import Service
from shortflow.settings import Settings
from shortflow.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path, {'DATA_DIR': str(tmp_path / 'data'), 'DASHBOARD_PASSWORD': 'local-test-password', 'MEDIA_SIGNING_KEY': 'test-signing-secret', 'SCHEDULE_ENABLED': 'false'})


def add_draft(service, demo=False, day='2026-10-19'):
    folder = service.settings.data_dir / 'videos' / 'test-media'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'video.mp4').write_bytes(b'fixture-video')
    (folder / 'thumbnail.jpg').write_bytes(b'fixture-jpeg')
    content = dict(title='검수할 실제 소식', hook='출처를 확인하세요', caption='원문과 조건 확인', scenes=['하나', '둘', '셋'], sources=[{'url': 'https://www.korea.kr/news/example', 'title': '출처', 'publisher': '정책브리핑'}], review_notes=[], affiliate_links=[], is_demo=demo)
    video = dict(video_path=str(folder / 'video.mp4'), thumbnail_path=str(folder / 'thumbnail.jpg'), duration=18, width=1080, height=1920)
    return service.store.create(day, content, video)


def test_double_approval_and_concurrent_claim_exactly_once(settings):
    service = Service(settings)
    draft = add_draft(service)
    service.review('approve', draft['id'], 1)
    service.review('approve', draft['id'], 1)
    assert len(service.store.get(draft['id'])['uploads']) == 4
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: service.store.claim(draft['id'], 'youtube'), range(12)))
    assert sum(claims) == 1


def test_demo_never_approved(settings):
    service = Service(settings)
    draft = add_draft(service, demo=True)
    with pytest.raises(ValueError, match='샘플'):
        service.review('approve', draft['id'], 1)
    assert service.store.get(draft['id'])['uploads'] == []


def test_old_telegram_revision_cannot_approve_edited_video(settings):
    service = Service(settings)
    draft = add_draft(service)
    service.store.update(draft['id'], draft['content'], draft['video'], 1)
    with pytest.raises(ValueError, match='수정 전'):
        service.telegram_handler('approve', draft['id'] + ':1')
    assert service.store.get(draft['id'])['status'] == 'pending_review'
    service.telegram_handler('approve', draft['id'] + ':2')
    assert service.store.get(draft['id'])['status'] == 'approved'


def test_retry_preserves_ambiguous_and_completed_posts(settings):
    service = Service(settings)
    draft = add_draft(service)
    service.review('approve', draft['id'], 1)
    statuses = dict(youtube='succeeded', tiktok='unknown', instagram='processing', naver='blocked')
    for platform, status in statuses.items():
        service.store.receipt(draft['id'], platform, dict(status=status, stage='container', provider_id='provider-1' if platform != 'naver' else None, privacy='private'))
    assert service.store.retry(draft['id']) == 1
    receipts = {r['platform']: r for r in service.store.get(draft['id'])['uploads']}
    assert receipts['tiktok']['status'] == 'unknown'
    assert receipts['youtube']['status'] == 'succeeded'
    assert receipts['instagram']['stage'] == 'container'
    assert receipts['naver']['status'] == 'queued'


def test_restart_recovery_is_explicit(settings):
    service = Service(settings)
    draft = add_draft(service)
    service.review('approve', draft['id'], 1)
    service.store.claim(draft['id'], 'youtube')
    other = Store(service.store.path)
    assert other.get(draft['id'])['uploads'][3]['status'] == 'uploading'
    other.recover_uploads()
    assert {r['platform']: r for r in other.get(draft['id'])['uploads']}['youtube']['status'] == 'unknown'


def test_no_sources_means_no_real_or_fake_video(settings, monkeypatch):
    service = Service(settings)
    monkeypatch.setattr('shortflow.content.collect_sources', lambda *args: ([], ['network blocked']))
    with pytest.raises(ValueError, match='자료'):
        service.prepare()
    assert service.store.list() == []
    assert 'network blocked' in service.store.meta('last_error')


def test_approved_only_dispatch_and_no_duplicate_upload(settings, monkeypatch):
    calls = []
    class FakePublisher:
        def __init__(self, values):
            pass
        def publish(self, platform, draft, path, **kwargs):
            calls.append(platform)
            return dict(status='succeeded', provider_id=platform, detail='test')
        def close(self):
            pass
    monkeypatch.setattr('shortflow.publishers.Publisher', FakePublisher)
    service = Service(settings)
    draft = add_draft(service)
    service.dispatch()
    assert calls == []
    service.review('approve', draft['id'], 1)
    service.dispatch()
    service.dispatch()
    assert sorted(calls) == ['instagram', 'naver', 'tiktok', 'youtube']


def test_http_auth_csrf_revision_and_demo_guard(settings):
    app = create_app(settings, start_worker=False)
    service = app.state.service
    draft = add_draft(service, demo=True)
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/api/overview').status_code == 401
        client.auth = ('admin', 'local-test-password')
        assert client.get('/').status_code == 200
        assert client.get('/api/overview').json()['counts']['pending_review'] == 1
        url = '/api/drafts/' + draft['id'] + '/approve'
        assert client.post(url, json={'revision': 1}).status_code == 403
        assert client.post(url, json={}, headers={'X-Shortflow-Action':'review'}).status_code == 422
        assert client.post(url, json={'revision':1}, headers={'X-Shortflow-Action':'review'}).status_code == 409
        response = client.get('/api/drafts/' + draft['id'] + '/bundle')
        assert response.status_code == 200
        assert response.content[:2] == b'PK'


def test_public_media_only_signed_and_approved(settings):
    app = create_app(settings, start_worker=False)
    service = app.state.service
    draft = add_draft(service)
    signature = service.public_signature(draft)
    path = f"/public/{draft['id']}/{signature}.mp4"
    with TestClient(app) as client:
        assert client.get(path).status_code == 404
        service.review('approve', draft['id'], 1)
        assert client.get(path).content == b'fixture-video'
        assert client.get(f"/public/{draft['id']}/wrong.mp4").status_code == 404


def test_rejected_draft_has_no_queue_and_cannot_later_approve(settings):
    service = Service(settings)
    draft = add_draft(service)
    service.review('reject', draft['id'], 1)
    with pytest.raises(ValueError, match='이미'):
        service.review('approve', draft['id'], 1)
    assert service.store.get(draft['id'])['uploads'] == []


def test_rejected_material_not_reused_but_new_draft_allowed_same_day(settings):
    service = Service(settings)
    first = add_draft(service)
    service.review('reject', first['id'], 1)
    assert service.store.real_for_day(first['day']) is None
    assert 'https://www.korea.kr/news/example' in service.store.seen_urls()
    second = add_draft(service)
    assert second['id'] != first['id']


def test_blocked_with_known_provider_id_cannot_create_duplicate(settings):
    service = Service(settings)
    draft = add_draft(service)
    service.review('approve', draft['id'], 1)
    service.store.receipt(draft['id'], 'youtube', {'status':'processing', 'provider_id':'already-created', 'privacy':'private'})
    service.store.receipt(draft['id'], 'youtube', {'status':'blocked', 'detail':'credential unavailable'})
    assert service.store.retry(draft['id']) == 0
    receipt = next(r for r in service.store.get(draft['id'])['uploads'] if r['platform'] == 'youtube')
    assert receipt['provider_id'] == 'already-created'
    assert receipt['privacy'] == 'private'
