import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/windows_runtime.py"
spec = importlib.util.spec_from_file_location("windows_runtime", MODULE_PATH)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def archive_bytes(extra=None, binaries=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in (binaries or {
            "release/bin/ffmpeg.exe": b"MZ-test-ffmpeg",
            "release/bin/ffprobe.exe": b"MZ-test-ffprobe",
        }).items():
            archive.writestr(name, content)
        for member, content in extra or []:
            archive.writestr(member, content)
    return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self, body, url, headers=None):
        super().__init__(body)
        self.url = url
        self.headers = headers or {}

    def geturl(self):
        return self.url


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "win32")


def mock_download(monkeypatch, payload, *, checksum=None, headers=None):
    calls = []
    digest = checksum or hashlib.sha256(payload).hexdigest()
    versioned = "https://www.gyan.dev/ffmpeg/builds/packages/release.zip"

    def fetch(url):
        calls.append(url)
        if url == runtime.CHECKSUM_URL:
            return Response(digest.encode("ascii"), versioned + ".sha256")
        assert url == versioned
        return Response(payload, versioned, headers)

    monkeypatch.setattr(runtime, "_open_url", fetch)
    return calls


def test_install_extracts_only_programs_and_repeated_launch_verifies_offline(tmp_path, monkeypatch, windows):
    payload = archive_bytes(extra=[("release/README.txt", b"ignore"), ("release/bin/other.exe", b"ignore")])
    calls = mock_download(monkeypatch, payload)
    target = runtime.provision(tmp_path)
    assert target == tmp_path / ".local/ffmpeg/bin"
    assert sorted(path.name for path in target.iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]
    assert (target / "ffmpeg.exe").read_bytes() == b"MZ-test-ffmpeg"
    manifest = json.loads((target.parent / "install.json").read_text())
    assert manifest["archive_sha256"] == hashlib.sha256(payload).hexdigest()
    assert manifest["binaries"]["ffprobe.exe"] == hashlib.sha256(b"MZ-test-ffprobe").hexdigest()
    assert len(calls) == 2

    def offline(url):
        pytest.fail("A verified local installation must not need a network request")

    monkeypatch.setattr(runtime, "_open_url", offline)
    assert runtime.provision(tmp_path) == target
    assert sorted(path.name for path in target.parent.parent.iterdir()) == ["ffmpeg"]


def test_corrupt_archive_is_never_installed_and_temp_files_are_removed(tmp_path, monkeypatch, windows):
    mock_download(monkeypatch, archive_bytes(), checksum="0" * 64)
    with pytest.raises(runtime.RuntimeSetupError, match="SHA-256 check failed"):
        runtime.provision(tmp_path)
    assert list((tmp_path / ".local").iterdir()) == []


def test_tampered_binary_triggers_verified_redownload(tmp_path, monkeypatch, windows):
    calls = mock_download(monkeypatch, archive_bytes())
    target = runtime.provision(tmp_path)
    (target / "ffmpeg.exe").write_bytes(b"tampered")
    assert runtime.provision(tmp_path) == target
    assert (target / "ffmpeg.exe").read_bytes() == b"MZ-test-ffmpeg"
    assert len(calls) == 4


def test_failed_repair_preserves_existing_files(tmp_path, monkeypatch, windows):
    mock_download(monkeypatch, archive_bytes())
    target = runtime.provision(tmp_path)
    (target / "ffmpeg.exe").write_bytes(b"changed-before-attempt")
    mock_download(monkeypatch, archive_bytes(), checksum="0" * 64)
    with pytest.raises(runtime.RuntimeSetupError, match="SHA-256"):
        runtime.provision(tmp_path)
    assert (target / "ffmpeg.exe").read_bytes() == b"changed-before-attempt"
    assert (target / "ffprobe.exe").read_bytes() == b"MZ-test-ffprobe"


def test_activation_failure_restores_previous_runtime(tmp_path, monkeypatch, windows):
    mock_download(monkeypatch, archive_bytes())
    target = runtime.provision(tmp_path)
    (target / "ffmpeg.exe").write_bytes(b"original-before-repair")
    replace = runtime.os.replace

    def fail_activation(source, destination):
        if Path(source).name == "verified":
            raise OSError("program busy")
        return replace(source, destination)

    monkeypatch.setattr(runtime.os, "replace", fail_activation)
    with pytest.raises(OSError):
        runtime.provision(tmp_path)
    assert (target / "ffmpeg.exe").read_bytes() == b"original-before-repair"
    assert sorted(path.name for path in target.parent.parent.iterdir()) == ["ffmpeg"]


def test_failed_rollback_keeps_previous_runtime_outside_temporary_cleanup(tmp_path, monkeypatch, windows):
    mock_download(monkeypatch, archive_bytes())
    target = runtime.provision(tmp_path)
    (target / "ffmpeg.exe").write_bytes(b"original-before-repair")
    replace = runtime.os.replace

    def fail_activation_and_rollback(source, destination):
        if Path(source).name == "verified" or Path(source).name.startswith("ffmpeg-previous-"):
            raise OSError("program busy")
        return replace(source, destination)

    monkeypatch.setattr(runtime.os, "replace", fail_activation_and_rollback)
    with pytest.raises(runtime.RuntimeSetupError, match="previous runtime is preserved"):
        runtime.provision(tmp_path)
    backups = list((tmp_path / ".local").glob("ffmpeg-previous-*"))
    assert len(backups) == 1
    assert (backups[0] / "bin/ffmpeg.exe").read_bytes() == b"original-before-repair"


@pytest.mark.parametrize("name", ["../escape.exe", "/escape.exe", "release/../../escape.exe", r"release\..\escape.exe", "C:/escape.exe", "release/file.exe:stream"])
def test_unsafe_zip_member_rejected_even_if_not_extracted(tmp_path, monkeypatch, windows, name):
    mock_download(monkeypatch, archive_bytes(extra=[(name, b"malicious")]))
    with pytest.raises(runtime.RuntimeSetupError, match="unsafe member"):
        runtime.provision(tmp_path)
    assert not (tmp_path / "escape.exe").exists()
    assert list((tmp_path / ".local").iterdir()) == []


def test_symlink_zip_member_rejected(tmp_path, monkeypatch, windows):
    link = zipfile.ZipInfo("release/bin/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    mock_download(monkeypatch, archive_bytes(extra=[(link, b"/outside")]))
    with pytest.raises(runtime.RuntimeSetupError, match="unsafe member"):
        runtime.provision(tmp_path)


@pytest.mark.parametrize("binaries", [
    {"release/bin/ffmpeg.exe": b"MZ-only-one"},
    {"release/bin/ffmpeg.exe": b"MZ-one", "release/bin/ffprobe.exe": b"bad executable"},
    {"release/bin/ffmpeg.exe": b"MZ-one", "other/bin/FFMPEG.exe": b"MZ-duplicate", "release/bin/ffprobe.exe": b"MZ-two"},
])
def test_incomplete_invalid_or_duplicate_programs_rejected(tmp_path, monkeypatch, windows, binaries):
    mock_download(monkeypatch, archive_bytes(binaries=binaries))
    with pytest.raises(runtime.RuntimeSetupError):
        runtime.provision(tmp_path)
    assert not (tmp_path / ".local/ffmpeg").exists()


@pytest.mark.parametrize("url", [
    "http://www.gyan.dev/ffmpeg.zip",
    "https://untrusted.example/ffmpeg.zip",
    "https://www.gyan.dev.evil.example/ffmpeg.zip",
    "https://user:secret@www.gyan.dev/ffmpeg.zip",
    "https://www.gyan.dev:444/ffmpeg.zip",
])
def test_download_redirects_cannot_leave_trusted_https_host(url):
    with pytest.raises(runtime.RuntimeSetupError, match="trusted HTTPS"):
        runtime._trusted_url(url)
    redirect = runtime._TrustedRedirects()
    with pytest.raises(runtime.RuntimeSetupError, match="trusted HTTPS"):
        redirect.redirect_request(None, None, 302, "", {}, url)


def test_streaming_download_limit_applies_without_content_length(tmp_path, monkeypatch, windows):
    monkeypatch.setattr(runtime, "MAX_ARCHIVE_BYTES", 10)
    mock_download(monkeypatch, archive_bytes())
    with pytest.raises(runtime.RuntimeSetupError, match="size limit"):
        runtime.provision(tmp_path)
    assert list((tmp_path / ".local").iterdir()) == []


def test_non_windows_main_returns_clear_error_without_network(monkeypatch, capsys):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    assert runtime.main() == 1
    assert "Windows only" in capsys.readouterr().err


def test_low_level_error_does_not_print_proxy_credentials(monkeypatch, capsys, windows):
    def fail(root):
        raise OSError("https://secret-user:secret-password@proxy.example")

    monkeypatch.setattr(runtime, "provision", fail)
    assert runtime.main() == 1
    error = capsys.readouterr().err
    assert "OSError" in error
    assert "secret" not in error
