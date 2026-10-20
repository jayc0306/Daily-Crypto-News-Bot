import asyncio
import errno
import socket

import pytest
import uvicorn

from shortflow import server


@pytest.mark.parametrize('host,family', [('127.0.0.1', socket.AF_INET), ('::1', socket.AF_INET6)])
def test_windows_listener_is_nonblocking_and_uses_selector(monkeypatch, host, family):
    observed = {}

    class InspectServer:
        started = True

        def __init__(self, config):
            observed['factory'] = config.get_loop_factory()

        def run(self, sockets):
            listener, = sockets
            observed['socket'] = listener
            observed['family'] = listener.family
            observed['address'] = listener.getsockname()
            observed['timeout'] = listener.gettimeout()

    monkeypatch.setattr(server.uvicorn, 'Server', InspectServer)
    monkeypatch.setattr(server.sys, 'platform', 'win32')
    server.run_server(lambda scope, receive, send: None, host, 0)
    assert observed['factory'] is asyncio.SelectorEventLoop
    assert observed['family'] == family
    assert observed['address'][0] == host and observed['address'][1] > 0
    assert observed['timeout'] == 0
    assert observed['socket'].fileno() == -1


def test_bind_failure_is_identified_and_does_not_start_app(monkeypatch, capsys):
    class BrokenSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            error = OSError(errno.EFAULT, 'internal provider detail')
            error.winerror = 10014
            raise error

    monkeypatch.setattr(server.socket, 'socket', lambda *args: BrokenSocket())
    monkeypatch.setattr(server.sys, 'platform', 'win32')
    with pytest.raises(SystemExit) as failure:
        server.run_server(lambda scope, receive, send: None, '127.0.0.1', 8000)
    assert failure.value.code == 1
    message = capsys.readouterr().err
    assert '로컬 주소 연결' in message and '10014' in message
    assert 'internal provider detail' not in message


def test_linux_keeps_existing_uvicorn_startup(monkeypatch):
    observed = {}
    app = object()
    monkeypatch.setattr(server.sys, 'platform', 'linux')
    monkeypatch.setattr(server.uvicorn, 'run', lambda supplied, **kwargs: observed.update(app=supplied, **kwargs))
    server.run_server(app, '127.0.0.1', 8100)
    assert observed == {'app': app, 'host': '127.0.0.1', 'port': 8100, 'access_log': False}
