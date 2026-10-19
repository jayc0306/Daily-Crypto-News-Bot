"""Create local desktop settings without printing or replacing credentials.

Runs with Python's standard library in an official Python container, so Windows
users do not need to install Python or change PowerShell execution policy.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import tempfile


def _value(text: str, name: str, default: str = '') -> str:
    matches = re.findall(rf'(?m)^(?:export\s+)?{re.escape(name)}=(.*)$', text)
    if not matches:
        return default
    value = matches[-1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return value


def setup(root: Path) -> Path:
    root = root.resolve()
    env_path = root / '.env'
    if not env_path.exists():
        template = (root / '.env.example').read_text(encoding='utf-8')
        for key, value in [('DASHBOARD_PASSWORD', secrets.token_urlsafe(24)),
                           ('MEDIA_SIGNING_KEY', secrets.token_urlsafe(32))]:
            template, replacements = re.subn(rf'(?m)^{key}=\r?$', f'{key}={value}', template)
            if replacements != 1:
                raise ValueError('The example settings are incomplete. Download the complete starter ZIP again.')
        try:
            fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # Another launcher already created settings; preserve them.
        else:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(template)
    settings = env_path.read_text(encoding='utf-8-sig')
    username = _value(settings, 'DASHBOARD_USERNAME', 'admin')
    password = _value(settings, 'DASHBOARD_PASSWORD')
    if len(password) < 12 or any(c in username + password for c in '\r\n'):
        raise ValueError('Set DASHBOARD_PASSWORD in .env to at least 12 characters, then run START-WINDOWS.cmd again.')
    local_dir = root / '.local'
    local_dir.mkdir(mode=0o700, exist_ok=True)
    note = local_dir / 'dashboard-login.txt'
    text = ('하루의 이득 — 이 PC의 대시보드 로그인\n\n'
            f'사용자 이름: {username}\n암호: {password}\n\n'
            '자동으로 열린 브라우저 로그인 창에 위 정보를 입력하세요.\n'
            '이 메모와 .env 파일을 채팅에 보내거나 공유하지 마세요.\n'
            '처음에는 [샘플 미리보기]로 영상 제작을 확인하세요.\n'
            '텔레그램과 플랫폼 계정은 별도 연결이 필요합니다.\n')
    fd, temporary = tempfile.mkstemp(prefix='login-', dir=local_dir)
    try:
        # UTF-8 BOM lets older Windows Notepad recognize Korean correctly.
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='\r\n') as stream:
            stream.write(text)
        os.replace(temporary, note)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print('Local settings prepared. Credentials are in .local/dashboard-login.txt, not in this output.')
    return note


if __name__ == '__main__':
    try:
        setup(Path.cwd())
    except (ValueError, OSError) as exc:
        # Never echo .env contents or platform credential values.
        message = str(exc) if isinstance(exc, ValueError) else 'Cannot prepare local settings. Check folder permissions.'
        raise SystemExit(message) from None
