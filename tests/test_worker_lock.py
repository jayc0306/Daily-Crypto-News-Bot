import errno
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from shortflow import worker


def _other_process_can_lock(path: Path) -> bool:
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from shortflow.worker import worker_lock\n"
        "with worker_lock(Path(sys.argv[1])) as acquired:\n"
        "    print(int(acquired))\n"
    )
    result = subprocess.run([sys.executable, "-c", script, str(path)], check=True, capture_output=True, text=True, timeout=20)
    return result.stdout.strip() == "1"


def test_worker_lock_excludes_other_process_and_releases(tmp_path):
    path = tmp_path / "worker.lock"
    path.write_bytes(b"existing file content")
    with worker.worker_lock(path) as acquired:
        assert acquired
        assert not _other_process_can_lock(path)
        assert path.read_bytes() == b"existing file content"
    assert _other_process_can_lock(path)
    assert path.read_bytes() == b"existing file content"


def test_worker_lock_releases_after_exception(tmp_path):
    path = tmp_path / "worker.lock"
    with pytest.raises(RuntimeError, match="worker failed"):
        with worker.worker_lock(path) as acquired:
            assert acquired
            raise RuntimeError("worker failed")
    assert _other_process_can_lock(path)


def test_only_owner_recovers_uploads(tmp_path):
    recoveries = []
    store = SimpleNamespace(recover_uploads=lambda: recoveries.append(True), meta=lambda key, default: default)
    service = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), store=store)
    stop = threading.Event()
    stop.set()
    with worker.worker_lock(tmp_path / "worker.lock") as acquired:
        assert acquired
        worker.run_worker(service, stop)
        assert recoveries == []
    worker.run_worker(service, stop)
    assert recoveries == [True]


def _mock_windows_lock(monkeypatch, locking):
    msvcrt = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
    monkeypatch.setattr(worker, "_WINDOWS", True)
    monkeypatch.setattr(worker, "msvcrt", msvcrt, raising=False)
    return msvcrt


@pytest.mark.parametrize("initial", [b"", b"preserve existing content"])
def test_windows_lock_uses_first_existing_byte_and_unlocks_on_failure(tmp_path, monkeypatch, initial):
    calls = []

    def locking(fd, mode, size):
        calls.append((mode, size, os.lseek(fd, 0, os.SEEK_CUR), os.fstat(fd).st_size))

    msvcrt = _mock_windows_lock(monkeypatch, locking)
    path = tmp_path / "worker.lock"
    path.write_bytes(initial)
    with pytest.raises(RuntimeError, match="worker failed"):
        with worker.worker_lock(path) as acquired:
            assert acquired
            raise RuntimeError("worker failed")
    expected_content = initial or b"\0"
    assert path.read_bytes() == expected_content
    assert calls == [(msvcrt.LK_NBLCK, 1, 0, len(expected_content)), (msvcrt.LK_UNLCK, 1, 0, len(expected_content))]


@pytest.mark.parametrize("error", [errno.EACCES, errno.EAGAIN, errno.EDEADLK])
def test_windows_lock_contention_never_unlocks_another_owner(tmp_path, monkeypatch, error):
    calls = []

    def locking(fd, mode, size):
        calls.append(mode)
        raise OSError(error, "already locked")

    msvcrt = _mock_windows_lock(monkeypatch, locking)
    with worker.worker_lock(tmp_path / "worker.lock") as acquired:
        assert not acquired
    assert calls == [msvcrt.LK_NBLCK]


def test_windows_lock_reports_unexpected_io_errors(tmp_path, monkeypatch):
    calls = []

    def locking(fd, mode, size):
        calls.append(mode)
        raise OSError(errno.EBADF, "invalid descriptor")

    msvcrt = _mock_windows_lock(monkeypatch, locking)
    with pytest.raises(OSError) as error:
        with worker.worker_lock(tmp_path / "worker.lock"):
            pytest.fail("unexpected lock failure was ignored")
    assert error.value.errno == errno.EBADF
    assert calls == [msvcrt.LK_NBLCK]
