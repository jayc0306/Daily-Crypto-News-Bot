"""Desktop serving with a Windows loop and an explicit local listener."""
from __future__ import annotations

import errno
import socket
import sys

import uvicorn


WINDOWS_LOOP = 'asyncio:SelectorEventLoop'


def run_server(app, host: str, port: int) -> None:
    if sys.platform != 'win32':
        uvicorn.run(app, host=host, port=port, access_log=False)
        return

    # This desktop app runs synchronous FFmpeg commands in worker threads and
    # does not require Proactor's asynchronous subprocess support. Selector
    # avoids the default Windows IOCP accept path on affected Winsock providers.
    config = uvicorn.Config(app, host=host, port=port, loop=WINDOWS_LOOP, access_log=False)
    server = uvicorn.Server(config)
    phase = '로컬 소켓 생성'
    try:
        family = socket.AF_INET6 if ':' in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as listener:
            phase = '로컬 주소 연결'
            listener.bind((host, port))
            phase = '로컬 접속 대기'
            listener.listen(config.backlog)
            listener.setblocking(False)
            phase = 'Windows 호환 서버 시작'
            print(f'Windows 호환 모드로 서버를 시작합니다: {host}:{listener.getsockname()[1]}', flush=True)
            server.run(sockets=[listener])
    except KeyboardInterrupt:
        return
    except OSError as exc:
        code = getattr(exc, 'winerror', None) or exc.errno
        if code in (10048, errno.EADDRINUSE):
            message = f'{port}번 포트가 사용 중입니다. 이미 실행한 프로그램 창을 확인하세요.'
        else:
            message = f'{phase} 단계에서 오류가 발생했습니다. 오류 코드: {code}.'
        print(f'[서버 시작 실패] {message}', file=sys.stderr)
        raise SystemExit(1) from None
    if not server.started:
        raise SystemExit(1)
