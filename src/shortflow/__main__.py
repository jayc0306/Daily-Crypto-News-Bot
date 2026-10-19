from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import threading

from .service import Service
from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description='하루의 이득 — 자료 수집·영상 제작·사람 검수·플랫폼 게시')
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve', help='검수 대시보드와 일일 작업자 시작')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', default=8000, type=int)
    sub.add_parser('worker', help='웹 화면 없이 텔레그램·일일 작업자 시작')
    sub.add_parser('demo', help='게시 불가 합성 샘플 영상 제작')
    sub.add_parser('prepare', help='오늘의 실제 자료로 영상 초안 제작')
    sub.add_parser('doctor', help='도구·연동 설정 상태 점검 (비밀 값 출력 없음)')
    sub.add_parser('init', help='기존 파일을 보존하며 비공개 .env 생성')
    args = parser.parse_args()
    settings = Settings.load()
    if args.command == 'init':
        source = (settings.root / '.env.example').read_text(encoding='utf-8')
        source = source.replace('DASHBOARD_PASSWORD=\n', 'DASHBOARD_PASSWORD=' + secrets.token_urlsafe(24) + '\n')
        source = source.replace('MEDIA_SIGNING_KEY=\n', 'MEDIA_SIGNING_KEY=' + secrets.token_urlsafe(32) + '\n')
        path = settings.root / '.env'
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            parser.error('기존 .env가 있어 보존했습니다. 해당 파일을 직접 수정하세요.')
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(source)
        print(f'비공개 설정 파일을 생성했습니다: {path} (비밀 값은 출력하지 않습니다.)')
        return
    if args.command == 'doctor':
        print(json.dumps({'tools': {name: bool(shutil.which(name)) for name in ('ffmpeg', 'ffprobe')}, 'integrations': settings.integrations(), 'telegram_configured': bool(settings.get('TELEGRAM_BOT_TOKEN') and settings.get('TELEGRAM_CHAT_ID')), 'dashboard_password_set': bool(settings.get('DASHBOARD_PASSWORD'))}, ensure_ascii=False, indent=2))
        return
    if args.command == 'serve':
        if len(settings.get('DASHBOARD_PASSWORD')) < 12:
            parser.error('DASHBOARD_PASSWORD를 .env에 12자 이상으로 설정하세요. 키 값은 채팅에 보내지 마세요.')
        import uvicorn
        from .app import create_app
        # Signed public media URLs should never be written to access logs.
        uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)
        return
    service = Service(settings)
    if args.command == 'worker':
        from .worker import run_worker
        try:
            run_worker(service, threading.Event())
        except KeyboardInterrupt:
            pass
        return
    try:
        draft = service.prepare(demo=args.command == 'demo')
        print(json.dumps({'id': draft['id'], 'status': draft['status'], 'is_demo': draft['content']['is_demo'], 'video': draft['video']}, ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
