from __future__ import annotations

import hmac
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field

from .service import Service
from .settings import Settings
from .worker import run_worker


class ReviewRequest(BaseModel):
    revision: int = Field(ge=1)


class TikTokOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    creator_username: str = Field(min_length=1, max_length=100)
    privacy_level: str = Field(min_length=1, max_length=60)
    disable_comment: bool
    disable_duet: bool
    disable_stitch: bool
    consent: Literal[True]


class EditRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=100)
    caption: str = Field(min_length=1, max_length=2000)
    scenes: list[str] = Field(min_length=3, max_length=6)
    tiktok_options: TikTokOptions | None = None


def create_app(settings: Settings | None = None, *, start_worker=True):
    settings = settings or Settings.load()
    service = Service(settings)
    security = HTTPBasic(auto_error=False)

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        thread = None
        if start_worker:
            thread = threading.Thread(target=run_worker, args=(service, stop), daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=2)

    app = FastAPI(title='하루의 이득 · Shortflow', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.service = service

    def auth(credentials: HTTPBasicCredentials | None = Depends(security)):
        expected = settings.get('DASHBOARD_PASSWORD')
        valid = credentials is not None and expected and secrets.compare_digest(credentials.username.encode(), settings.get('DASHBOARD_USERNAME', 'admin').encode()) and secrets.compare_digest(credentials.password.encode(), expected.encode())
        if not valid:
            raise HTTPException(401, '대시보드 계정 설정을 확인하세요.', headers={'WWW-Authenticate': 'Basic realm="Shortflow"'})

    def mutation(x_shortflow_action: Annotated[str | None, Header()] = None):
        if x_shortflow_action != 'review':
            raise HTTPException(403, '검수 요청 헤더가 필요합니다.')

    secured = [Depends(auth)]
    writes = [Depends(auth), Depends(mutation)]

    def get_draft(draft_id):
        draft = service.store.get(draft_id)
        if not draft:
            raise HTTPException(404, '초안을 찾을 수 없습니다.')
        return draft

    def media_file(draft, key):
        path = Path(draft['video'][key]).resolve()
        if not path.is_relative_to(settings.data_dir / 'videos') or not path.is_file():
            raise HTTPException(404, '영상 파일을 찾을 수 없습니다.')
        return path

    @app.get('/health')
    def health():
        return {'status': 'ok', 'service': 'shortflow'}

    @app.get('/', dependencies=secured)
    def index():
        return FileResponse(Path(__file__).parent / 'static/index.html')

    @app.get('/api/overview', dependencies=secured)
    def overview():
        return service.overview()

    @app.get('/api/drafts/{draft_id}', dependencies=secured)
    def draft(draft_id: str):
        return get_draft(draft_id)

    @app.get('/api/integrations/tiktok/creator', dependencies=secured)
    def tiktok_creator():
        from .publishers import Publisher
        publisher = Publisher(settings.values)
        try:
            return publisher.creator_info()
        finally:
            publisher.close()

    def prepare_background(demo):
        try:
            service.prepare(demo=demo)
        except Exception as exc:
            service.store.set_meta('last_error', str(exc) if isinstance(exc, ValueError) else '제작 실패. 수집원 또는 ffmpeg 설정을 확인하세요.')

    @app.post('/api/prepare', dependencies=writes, status_code=202)
    def prepare(tasks: BackgroundTasks):
        tasks.add_task(prepare_background, False)
        return {'message': '오늘의 신규 자료로 제작을 시작했습니다.'}

    @app.post('/api/demo', dependencies=writes, status_code=202)
    def demo(tasks: BackgroundTasks):
        tasks.add_task(prepare_background, True)
        return {'message': '게시할 수 없는 샘플 영상을 제작합니다.'}

    @app.post('/api/drafts/{draft_id}/approve', dependencies=writes)
    def approve(draft_id: str, request: ReviewRequest):
        try:
            return service.review('approve', draft_id, request.revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post('/api/drafts/{draft_id}/reject', dependencies=writes)
    def reject(draft_id: str, request: ReviewRequest):
        try:
            return service.review('reject', draft_id, request.revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.put('/api/drafts/{draft_id}', dependencies=writes)
    def edit(draft_id: str, request: EditRequest):
        if any(not text.strip() or len(text) > 250 for text in request.scenes):
            raise HTTPException(422, '장면별 자막은 1~250자로 입력하세요.')
        try:
            return service.edit(draft_id, request.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post('/api/drafts/{draft_id}/retry', dependencies=writes)
    def retry(draft_id: str):
        get_draft(draft_id)
        return {'message': f"안전하게 재시도할 수 있는 항목 {service.store.retry(draft_id)}개를 등록했습니다."}

    @app.get('/media/{draft_id}/video', dependencies=secured)
    def video(draft_id: str):
        return FileResponse(media_file(get_draft(draft_id), 'video_path'), media_type='video/mp4')

    @app.get('/media/{draft_id}/thumbnail', dependencies=secured)
    def thumbnail(draft_id: str):
        return FileResponse(media_file(get_draft(draft_id), 'thumbnail_path'), media_type='image/jpeg')

    @app.get('/api/drafts/{draft_id}/bundle', dependencies=secured)
    def bundle(draft_id: str):
        get_draft(draft_id)
        return FileResponse(service.bundle(draft_id), media_type='application/zip', filename=f'{draft_id}.zip')

    @app.get('/public/{draft_id}/{signature}.mp4')
    def public_video(draft_id: str, signature: str):
        draft = get_draft(draft_id)
        expected = service.public_signature(draft)
        if draft['status'] != 'approved' or draft['content'].get('is_demo') or not expected or not hmac.compare_digest(signature, expected):
            raise HTTPException(404, '영상 파일을 찾을 수 없습니다.')
        return FileResponse(media_file(draft, 'video_path'), media_type='video/mp4')

    return app
