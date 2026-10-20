from __future__ import annotations

import hashlib
import hmac
import json
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from .settings import Settings
from .store import Store

KST = ZoneInfo('Asia/Seoul')


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.data_dir / 'shortflow.sqlite3')
        self.lock = threading.RLock()

    def prepare(self, demo=False):
        from .content import collect_sources, compose_content, demo_content
        from .render import render_video
        with self.lock:
            day = datetime.now(KST).date().isoformat()
            if not demo:
                existing = self.store.real_for_day(day)
                if existing:
                    return existing
                items, errors = collect_sources(self.settings.sources_path, self.store.seen_urls())
                if not items:
                    message = '새로운 검증 가능 자료가 없습니다. 수집원·날짜·네트워크를 확인하세요.'
                    self.store.set_meta('last_error', message + (' ' + '; '.join(errors) if errors else ''))
                    raise ValueError(message)
                content = compose_content(items, day, self.settings.brand)
                content.setdefault('review_notes', []).extend(errors)
            else:
                content = demo_content(day, self.settings.brand)
            source_title = content['sources'][0]['title'] if content.get('sources') else ''
            # Keep automatic headlines readable; the full claim stays in subtitles
            # and source citations, and the reviewer can write a shorter headline.
            fits_headline = len(source_title) <= 28 and all(len(word) <= 10 for word in source_title.split())
            content.setdefault('headline', '매달 빠져나가는 돈\n그냥 두고 있나요?' if demo else
                               (source_title if source_title and fits_headline else
                                '오늘의 생활비 소식\n원문에서 조건 확인'))
            content.setdefault('accent_color', 'yellow')
            draft_id = uuid4().hex
            output = self.settings.data_dir / 'videos' / draft_id
            from .ai import enrich_content
            try:
                content = enrich_content(content, self.settings.values)
            except ValueError:
                content.setdefault('review_notes', []).append('AI 대본 생성에 실패해 출처 기반 기본 원고를 유지했습니다.')
            self._add_voice(content, output)
            video = render_video(content, output, font_path=self.settings.get('FONT_PATH') or None)
            draft = self.store.create(day, content, video, draft_id)
            self.store.set_meta('last_error', '')
            return draft

    def edit(self, draft_id, changes, expected_revision=None):
        from .render import render_video
        with self.lock:
            draft = self.store.get(draft_id)
            if not draft or draft['status'] != 'pending_review':
                raise ValueError('검수 대기 중인 영상만 수정할 수 있습니다.')
            if expected_revision is not None and draft['revision'] != expected_revision:
                raise ValueError('수정 전 화면입니다. 새로고침한 뒤 다시 시도하세요.')
            content = {**draft['content'], **changes}
            if 'tiktok_options' not in changes:
                content.pop('tiktok_options', None)
            output = self.settings.data_dir / 'videos' / draft_id / f"revision-{draft['revision']+1}-{uuid4().hex[:12]}"
            # A recording made for a previous script is no longer a valid narration.
            content.pop('audio_path', None)
            self._add_voice(content, output)
            video = render_video(content, output, font_path=self.settings.get('FONT_PATH') or None)
            return self.store.update(draft_id, content, video, draft['revision'])

    def _add_voice(self, content, output):
        from .ai import synthesize_voice
        try:
            audio = synthesize_voice(content, output / 'narration.mp3', self.settings.values)
            if audio:
                content['audio_path'] = str(audio)
                content.setdefault('review_notes', []).append('AI 생성 음성이 포함되어 있습니다. 발음·숫자·호흡을 들어보고 검수하세요.')
        except ValueError:
            content.setdefault('review_notes', []).append('음성 생성에 실패했습니다. 현재 영상의 소리는 무음입니다.')

    def review(self, action, draft_id, revision=None):
        if action not in ('approve', 'reject'):
            raise ValueError('허용되지 않은 검수 동작입니다.')
        with self.lock:
            draft = self.store.get(draft_id)
            if not draft:
                raise ValueError('초안을 찾을 수 없습니다.')
            if action == 'approve' and 'tiktok' in self.settings.targets and self.settings.get('TIKTOK_PUBLISHING_MODE', 'manual') == 'approved_app' and not draft['content'].get('tiktok_options', {}).get('consent'):
                raise ValueError('틱톡 게시 설정을 먼저 선택하고 저장하세요.')
            return self.store.review(draft_id, action, self.settings.targets, revision)

    def telegram_handler(self, action, reference):
        if action == 'status':
            overview = self.overview()
            return f"{self.settings.brand}\n검수 대기 {overview['counts']['pending_review']} · 승인 {overview['counts']['approved']} · 제외 {overview['counts']['rejected']}\n" + '\n'.join(f"{i['label']}: {i['detail']}" for i in overview['integrations']) + ('\n' + overview['last_error'][:500] if overview['last_error'] else '')
        if action == 'help':
            return '하루의 이득: 영상의 출처와 내용을 확인한 뒤 승인하면 설정된 플랫폼의 업로드가 시작됩니다. /status'
        draft_id, revision = reference.rsplit(':', 1)
        self.review(action, draft_id, int(revision))
        return '검수 승인 · 업로드 대기열에 등록했습니다.' if action == 'approve' else '보류 처리했습니다.'

    def public_signature(self, draft):
        key = self.settings.get('MEDIA_SIGNING_KEY')
        if not key:
            return None
        return hmac.new(key.encode(), f"{draft['id']}:{draft['revision']}".encode(), hashlib.sha256).hexdigest()

    def public_video_url(self, draft):
        base = self.settings.get('PUBLIC_MEDIA_BASE_URL').rstrip('/')
        signature = self.public_signature(draft)
        if not base.startswith('https://') or not signature:
            return None
        return f"{base}/public/{draft['id']}/{signature}.mp4"

    def bundle(self, draft_id):
        draft = self.store.get(draft_id)
        if not draft:
            raise ValueError('초안을 찾을 수 없습니다.')
        path = self.settings.data_dir / 'bundles' / f"{draft_id}-r{draft['revision']}.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            return path
        temporary = path.with_suffix(f'.{uuid4().hex}.tmp')
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.write(draft['video']['video_path'], 'video.mp4')
            archive.writestr('caption.txt', draft['content']['title'] + '\n\n' + draft['content']['caption'])
            archive.writestr('sources.json', json.dumps(draft['content'].get('sources', []), ensure_ascii=False, indent=2))
            archive.writestr('README.txt', '네이버 클립 등 직접 업로드용 패키지입니다. 이 파일을 내려받았다는 이유로 게시 완료로 처리되지 않습니다.\n' + ('샘플입니다. 실제 소식으로 게시하지 마세요.' if draft['content'].get('is_demo') else '본인 계정에서 최종 게시해 주세요.'))
        temporary.replace(path)
        return path

    def dispatch(self):
        from .publishers import Publisher
        publisher = Publisher(self.settings.values)
        try:
            self._dispatch(publisher)
        finally:
            publisher.close()

    def _dispatch(self, publisher):
        for draft in self.store.list():
            if draft['status'] != 'approved':
                continue
            changed = False
            payload = {**draft['content'], 'duration_seconds': draft['video']['duration']}
            for receipt in draft['uploads']:
                platform = receipt['platform']
                if receipt['status'] in ('processing', 'unknown') and receipt.get('provider_id'):
                    try:
                        result = publisher.reconcile(platform, payload, receipt)
                    except Exception:
                        result = {**receipt, 'detail': '처리 상태 조회 실패. 기존 작업 ID를 유지합니다.'}
                elif receipt['status'] == 'queued' and self.store.claim(draft['id'], platform):
                    try:
                        result = publisher.publish(platform, payload, Path(draft['video']['video_path']), public_video_url=self.public_video_url(draft))
                    except Exception:
                        # An unexpected response after sending a POST is ambiguous, not safe to retry.
                        result = dict(status='unknown', detail='응답을 확인하지 못했습니다. 해당 플랫폼의 게시 여부를 직접 확인하세요.')
                else:
                    continue
                self.store.receipt(draft['id'], platform, result)
                changed = changed or result['status'] != receipt['status']
            if changed:
                self.store.set_meta('last_upload_update', datetime.now(KST).isoformat())
                bot = None
                try:
                    bot = self.telegram()
                    if bot:
                        updated = self.store.get(draft['id'])
                        bot.send_status(updated['content']['title'] + '\n' + '\n'.join(f"{r['platform']}: {r['status']} — {r['detail']}" for r in updated['uploads']))
                except Exception:
                    self.store.set_meta('telegram_error', '게시 상태 알림 전송 실패. 텔레그램 연결을 확인하세요.')
                finally:
                    if bot:
                        bot.close()

    def telegram(self):
        token, chat_id = self.settings.get('TELEGRAM_BOT_TOKEN'), self.settings.get('TELEGRAM_CHAT_ID')
        if not token or not chat_id:
            return None
        from .telegram import TelegramReview
        return TelegramReview(token, chat_id)

    def notify_pending(self):
        bot = self.telegram()
        if not bot:
            return
        try:
            for draft in self.store.list():
                if draft['status'] == 'pending_review' and draft['notified_revision'] < draft['revision']:
                    targets = [dict(i, privacy=self.settings.get('YOUTUBE_PRIVACY_STATUS', 'private') if i['name'] == 'youtube' else '') for i in self.settings.integrations() if i['name'] in self.settings.targets]
                    bot.send_draft({**draft, 'review_targets': targets})
                    self.store.mark_notified(draft['id'], draft['revision'])
            self.store.set_meta('telegram_error', '')
        finally:
            bot.close()

    def overview(self):
        drafts = self.store.list()
        return dict(brand=self.settings.brand, drafts=drafts,
                    counts={s: sum(d['status'] == s for d in drafts) for s in ('pending_review','approved','rejected')},
                    integrations=self.settings.integrations(), last_error=' '.join(filter(None, (self.store.meta('last_error'), self.store.meta('telegram_error')))),
                    telegram_configured=bool(self.settings.get('TELEGRAM_BOT_TOKEN') and self.settings.get('TELEGRAM_CHAT_ID')))
