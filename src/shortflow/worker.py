from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import threading
import time
from datetime import datetime
from typing import Iterator

_WINDOWS = os.name == 'nt'
if _WINDOWS:
    import msvcrt
else:
    import fcntl

from .service import KST, Service


@contextmanager
def worker_lock(path: Path) -> Iterator[bool]:
    """Try to hold one worker lock without replacing or truncating its file."""
    with path.open('a+b') as lock:
        if _WINDOWS:
            # msvcrt locks a byte range from the current position. Give an empty
            # file a real byte, then always lock the same first byte.
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b'\0')
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                yield False
                return
        else:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        try:
            yield True
        finally:
            if _WINDOWS:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def run_worker(service: Service, stop: threading.Event):
    """Single worker per data directory, including Telegram polling and KST schedule."""
    lock_path = service.settings.data_dir / 'worker.lock'
    with worker_lock(lock_path) as acquired:
        if not acquired:
            return
        service.store.recover_uploads()
        offset = int(service.store.meta('telegram_offset', '0'))
        next_prepare = 0.0
        while not stop.is_set():
            now = datetime.now(KST)
            settings = service.settings
            try:
                hour = int(settings.get('DAILY_PREPARE_HOUR', '8'))
                if not 0 <= hour <= 23:
                    raise ValueError('hour')
            except ValueError:
                hour = 24
                service.store.set_meta('last_error', 'DAILY_PREPARE_HOUR는 한국 시간 0~23시로 설정하세요.')
            if settings.get('SCHEDULE_ENABLED', 'true').lower() == 'true' and now.hour >= hour and time.monotonic() >= next_prepare:
                next_prepare = time.monotonic() + 3600
                try:
                    service.prepare()
                except Exception:
                    if not service.store.meta('last_error'):
                        service.store.set_meta('last_error', '일일 제작에 실패했습니다. 수집원/렌더 도구 설정을 확인하세요.')
            try:
                service.notify_pending()
            except Exception:
                service.store.set_meta('telegram_error', '검수 메시지 전송 실패. 봇 권한과 개인 채팅 ID를 확인하세요.')
            try:
                service.dispatch()
            except Exception:
                service.store.set_meta('last_error', '업로드 상태 확인 실패. 대시보드에서 플랫폼별 상태를 확인하세요.')
            bot = None
            try:
                bot = service.telegram()
                if bot:
                    offset = bot.poll_once(offset, service.telegram_handler)
                    service.store.set_meta('telegram_offset', offset)
            except Exception:
                service.store.set_meta('telegram_error', '텔레그램 연결 실패. 봇 설정과 중복 실행 여부를 확인하세요.')
            finally:
                if bot:
                    bot.close()
            stop.wait(5)
