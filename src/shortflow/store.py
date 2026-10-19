from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY, day TEXT NOT NULL, status TEXT NOT NULL,
                content TEXT NOT NULL, video TEXT NOT NULL, created_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1, notified_revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS uploads (
                draft_id TEXT NOT NULL, platform TEXT NOT NULL, status TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '', provider_id TEXT, url TEXT,
                updated_at TEXT NOT NULL, receipt_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(draft_id,platform)
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            db.execute('DROP INDEX IF EXISTS daily_real_draft')
            db.execute("CREATE UNIQUE INDEX daily_real_draft ON drafts(day) WHERE json_extract(content,'$.is_demo')=0 AND status!='rejected'")
            if 'receipt_json' not in {row[1] for row in db.execute('PRAGMA table_info(uploads)')}:
                db.execute("ALTER TABLE uploads ADD COLUMN receipt_json TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _decode(self, row, db):
        if row is None:
            return None
        draft = dict(row)
        draft['content'] = json.loads(draft['content'])
        draft['video'] = json.loads(draft['video'])
        draft['uploads'] = []
        for receipt in db.execute("SELECT * FROM uploads WHERE draft_id=? ORDER BY platform", (draft['id'],)):
            fields = dict(receipt)
            extra = json.loads(fields.pop('receipt_json'))
            draft['uploads'].append({**extra, **fields})
        return draft

    def get(self, draft_id):
        with self.connection() as db:
            return self._decode(db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone(), db)

    def list(self):
        with self.connection() as db:
            return [self._decode(r, db) for r in db.execute("SELECT * FROM drafts ORDER BY created_at DESC LIMIT 100")]

    def create(self, day, content, video, draft_id=None):
        draft_id = draft_id or uuid4().hex
        with self.connection() as db:
            db.execute("INSERT INTO drafts(id,day,status,content,video,created_at) VALUES(?,?,'pending_review',?,?,?)", (draft_id, day, json.dumps(content, ensure_ascii=False), json.dumps(video), timestamp()))
        return self.get(draft_id)

    def real_for_day(self, day):
        with self.connection() as db:
            return self._decode(db.execute("SELECT * FROM drafts WHERE day=? AND json_extract(content,'$.is_demo')=0 AND status!='rejected'", (day,)).fetchone(), db)

    def review(self, draft_id, action, platforms, expected_revision=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise ValueError("초안을 찾을 수 없습니다.")
            if expected_revision is not None and row['revision'] != expected_revision:
                raise ValueError("수정 전 검수 메시지입니다. 최신 영상에서 다시 검수하세요.")
            if action == 'approve' and json.loads(row['content']).get('is_demo'):
                raise ValueError("샘플 영상은 실제 업로드할 수 없습니다.")
            target = 'approved' if action == 'approve' else 'rejected'
            if row['status'] == target:
                return self.get(draft_id)
            if row['status'] != 'pending_review':
                raise ValueError("이미 검수가 끝난 초안입니다.")
            db.execute("UPDATE drafts SET status=? WHERE id=?", (target, draft_id))
            if action == 'approve':
                for platform in platforms:
                    db.execute("INSERT OR IGNORE INTO uploads(draft_id,platform,status,updated_at) VALUES(?,?,'queued',?)", (draft_id, platform, timestamp()))
        return self.get(draft_id)

    def update(self, draft_id, content, video, expected_revision):
        with self.connection() as db:
            result = db.execute("UPDATE drafts SET content=?,video=?,revision=revision+1 WHERE id=? AND status='pending_review' AND revision=?", (json.dumps(content, ensure_ascii=False), json.dumps(video), draft_id, expected_revision))
            if result.rowcount != 1:
                raise ValueError("검수 상태가 변경되었습니다. 새로고침 후 다시 시도하세요.")
        return self.get(draft_id)

    def claim(self, draft_id, platform):
        with self.connection() as db:
            result = db.execute("UPDATE uploads SET status='uploading', updated_at=? WHERE draft_id=? AND platform=? AND status='queued' AND EXISTS(SELECT 1 FROM drafts WHERE id=? AND status='approved')", (timestamp(), draft_id, platform, draft_id))
            return result.rowcount == 1

    def recover_uploads(self):
        # Call only after acquiring the exclusive worker lock.
        with self.connection() as db:
            db.execute("UPDATE uploads SET status='unknown', detail='프로세스가 업로드 도중 종료되었습니다. 플랫폼에서 게시 여부를 직접 확인하세요.' WHERE status='uploading'")

    def receipt(self, draft_id, platform, result):
        with self.connection() as db:
            previous = db.execute('SELECT receipt_json FROM uploads WHERE draft_id=? AND platform=?', (draft_id, platform)).fetchone()
            combined = {**(json.loads(previous[0]) if previous else {}), **result}
            db.execute("UPDATE uploads SET status=?,detail=?,provider_id=?,url=?,updated_at=?,receipt_json=? WHERE draft_id=? AND platform=?", (combined['status'], combined.get('detail',''), combined.get('provider_id'), combined.get('url'), timestamp(), json.dumps(combined), draft_id, platform))

    def retry(self, draft_id):
        with self.connection() as db:
            # Unknown/processing uploads are excluded because a post may already exist.
            result = db.execute("UPDATE uploads SET status='queued',updated_at=? WHERE draft_id=? AND status IN ('blocked','failed') AND (provider_id IS NULL OR provider_id='') AND EXISTS(SELECT 1 FROM drafts WHERE id=? AND status='approved')", (timestamp(), draft_id, draft_id))
            return result.rowcount

    def seen_urls(self):
        with self.connection() as db:
            contents = db.execute("SELECT content FROM drafts").fetchall()
        return {s['url'] for r in contents for s in json.loads(r[0]).get('sources',[]) if s.get('url')}

    def meta(self, key, default=''):
        with self.connection() as db:
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_meta(self, key, value):
        with self.connection() as db:
            db.execute("INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def mark_notified(self, draft_id, revision):
        with self.connection() as db:
            db.execute("UPDATE drafts SET notified_revision=? WHERE id=?", (revision, draft_id))
