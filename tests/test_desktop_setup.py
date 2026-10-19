import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / 'tools/desktop_setup.py'
spec = importlib.util.spec_from_file_location('desktop_setup', MODULE_PATH)
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


@pytest.fixture
def root(tmp_path):
    (tmp_path / '.env.example').write_text('DASHBOARD_USERNAME=admin\nDASHBOARD_PASSWORD=\nMEDIA_SIGNING_KEY=\nTELEGRAM_BOT_TOKEN=\n', encoding='utf-8')
    return tmp_path


def test_new_settings_work_and_no_secrets_are_logged(root, capsys):
    note = desktop.setup(root)
    text = (root / '.env').read_text()
    password = desktop._value(text, 'DASHBOARD_PASSWORD')
    signing = desktop._value(text, 'MEDIA_SIGNING_KEY')
    assert len(password) >= 24 and len(signing) >= 32 and password != signing
    assert password in note.read_text(encoding='utf-8-sig')
    assert signing not in note.read_text(encoding='utf-8-sig')
    output = capsys.readouterr().out
    assert password not in output and signing not in output


def test_repeat_launch_preserves_existing_env_and_tokens(root, capsys):
    original = b'DASHBOARD_USERNAME=admin\r\nDASHBOARD_PASSWORD="already-long-password"\r\nTELEGRAM_BOT_TOKEN=secret-token-sentinel\r\n'
    (root / '.env').write_bytes(original)
    first = desktop.setup(root)
    desktop.setup(root)
    assert (root / '.env').read_bytes() == original
    assert 'already-long-password' in first.read_text(encoding='utf-8-sig')
    assert 'secret-token-sentinel' not in first.read_text(encoding='utf-8-sig')
    assert 'already-long-password' not in capsys.readouterr().out


def test_bad_existing_password_reports_fix_without_overwrite(root):
    original = 'DASHBOARD_PASSWORD=short\nTELEGRAM_BOT_TOKEN=keep-private\n'
    (root / '.env').write_text(original)
    with pytest.raises(ValueError, match='at least 12'):
        desktop.setup(root)
    assert (root / '.env').read_text() == original
    assert not (root / '.local/dashboard-login.txt').exists()
