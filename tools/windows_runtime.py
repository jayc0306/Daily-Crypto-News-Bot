"""Install the verified, portable Windows FFmpeg build without administrator access.

Gyan's Windows builds are linked by https://ffmpeg.org/download.html. Only
FFmpeg's two command-line programs are extracted; this helper needs no packages.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile


ARCHIVE_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
CHECKSUM_URL = ARCHIVE_URL + ".sha256"
TRUSTED_HOSTS = frozenset({"www.gyan.dev"})
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_BINARY_BYTES = 160 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 600 * 1024 * 1024
BINARIES = ("ffmpeg.exe", "ffprobe.exe")
CHUNK_BYTES = 1024 * 1024
_DIGEST = re.compile(r"[0-9a-fA-F]{64}")


class RuntimeSetupError(RuntimeError):
    """A safe-to-display setup error containing no authentication information."""


def _trusted_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeSetupError("FFmpeg's download URL is invalid.") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in TRUSTED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise RuntimeSetupError("FFmpeg download redirected outside its trusted HTTPS source.")
    return url


class _TrustedRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _trusted_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _open_url(url: str):
    # The default HTTPS handler keeps certificate and hostname verification on.
    request = Request(_trusted_url(url), headers={"User-Agent": "Shortflow-Windows-Setup/1"})
    response = build_opener(_TrustedRedirects()).open(request, timeout=60)
    try:
        _trusted_url(response.geturl())
    except Exception:
        response.close()
        raise
    return response


def _expected_digest() -> tuple[str, str]:
    with _open_url(CHECKSUM_URL) as response:
        final_url = _trusted_url(response.geturl())
        checksum = response.read(4097)
    if len(checksum) > 4096:
        raise RuntimeSetupError("FFmpeg's published checksum is unexpectedly large.")
    try:
        fields = checksum.decode("ascii").strip().split()
    except UnicodeDecodeError as exc:
        raise RuntimeSetupError("FFmpeg's published checksum is not valid ASCII.") from exc
    if not fields or not _DIGEST.fullmatch(fields[0]) or len(fields) > 2:
        raise RuntimeSetupError("FFmpeg's published SHA-256 checksum is invalid.")
    # Pin the archive to the resolved checksum release, avoiding an alias moving
    # to a newer release between the two downloads.
    if not final_url.endswith(".zip.sha256"):
        raise RuntimeSetupError("FFmpeg's checksum came from an unexpected URL.")
    archive_url = _trusted_url(final_url.removesuffix(".sha256"))
    return fields[0].lower(), archive_url


def _download_archive(destination: Path, expected: str, url: str) -> None:
    digest = hashlib.sha256()
    downloaded = 0
    with _open_url(url) as response, destination.open("xb") as output:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError as exc:
                raise RuntimeSetupError("FFmpeg's download size header is invalid.") from exc
            if not 0 < size <= MAX_ARCHIVE_BYTES:
                raise RuntimeSetupError("FFmpeg's download exceeds the supported size limit.")
        while chunk := response.read(CHUNK_BYTES):
            downloaded += len(chunk)
            if downloaded > MAX_ARCHIVE_BYTES:
                raise RuntimeSetupError("FFmpeg's download exceeds the supported size limit.")
            digest.update(chunk)
            output.write(chunk)
    if downloaded == 0 or digest.hexdigest() != expected:
        raise RuntimeSetupError("FFmpeg's SHA-256 check failed. No downloaded program was installed.")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_valid(installation: Path) -> bool:
    try:
        manifest_path = installation / "install.json"
        if installation.is_symlink() or manifest_path.is_symlink() or (installation / "bin").is_symlink():
            return False
        if manifest_path.stat().st_size > 8192:
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("schema") != 1
            or manifest.get("source") != ARCHIVE_URL
            or not isinstance(manifest.get("archive_sha256"), str)
            or not _DIGEST.fullmatch(manifest["archive_sha256"])
            or set(manifest.get("binaries", {})) != set(BINARIES)
        ):
            return False
        for name in BINARIES:
            binary = installation / "bin" / name
            expected = manifest["binaries"][name]
            if (
                binary.is_symlink()
                or not binary.is_file()
                or not 0 < binary.stat().st_size <= MAX_BINARY_BYTES
                or not isinstance(expected, str)
                or not _DIGEST.fullmatch(expected)
                or _file_digest(binary) != expected
            ):
                return False
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _safe_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    binaries = {}
    total_size = 0
    for member in archive.infolist():
        path = PurePosixPath(member.filename)
        mode = member.external_attr >> 16
        if (
            not member.filename
            or "\\" in member.filename
            or "\x00" in member.filename
            or ":" in member.filename
            or path.is_absolute()
            or ".." in path.parts
            or stat.S_ISLNK(mode)
            or member.flag_bits & 1
        ):
            raise RuntimeSetupError("FFmpeg's archive contains an unsafe member.")
        total_size += member.file_size
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise RuntimeSetupError("FFmpeg's expanded archive exceeds the supported size limit.")
        name = path.name.lower()
        if name in BINARIES:
            if (
                len(path.parts) < 2
                or path.parent.name.lower() != "bin"
                or name in binaries
                or member.is_dir()
                or not 0 < member.file_size <= MAX_BINARY_BYTES
                or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
            ):
                raise RuntimeSetupError("FFmpeg's archive has unexpected program entries.")
            binaries[name] = member
    if set(binaries) != set(BINARIES):
        raise RuntimeSetupError("FFmpeg's archive is missing ffmpeg.exe or ffprobe.exe.")
    return binaries


def _extract(archive_path: Path, staging: Path, archive_digest: str) -> None:
    binary_dir = staging / "bin"
    binary_dir.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_members(archive)
        for name in BINARIES:
            count = 0
            with archive.open(members[name]) as source, (binary_dir / name).open("xb") as output:
                while chunk := source.read(CHUNK_BYTES):
                    if count == 0 and not chunk.startswith(b"MZ"):
                        raise RuntimeSetupError("FFmpeg's archive contains an invalid Windows executable.")
                    count += len(chunk)
                    if count > MAX_BINARY_BYTES:
                        raise RuntimeSetupError("FFmpeg's program exceeds the supported size limit.")
                    output.write(chunk)
    manifest = {
        "schema": 1,
        "source": ARCHIVE_URL,
        "archive_sha256": archive_digest,
        "binaries": {name: _file_digest(binary_dir / name) for name in BINARIES},
    }
    (staging / "install.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _activate(staging: Path, installation: Path) -> None:
    backup = None
    if installation.exists():
        if not installation.is_dir() or installation.is_symlink():
            raise RuntimeSetupError("The FFmpeg install location must be a regular directory.")
        # Keep the old runtime outside the temporary download directory. Even a
        # failed rollback must not let temporary-directory cleanup delete it.
        backup = Path(tempfile.mkdtemp(prefix="ffmpeg-previous-", dir=installation.parent))
        backup.rmdir()
        os.replace(installation, backup)
    try:
        os.replace(staging, installation)
    except OSError:
        if backup is not None:
            try:
                os.replace(backup, installation)
            except OSError as restore_error:
                raise RuntimeSetupError(
                    "FFmpeg activation failed. The previous runtime is preserved in a "
                    "ffmpeg-previous-* directory under .local; close running FFmpeg processes and retry."
                ) from restore_error
        raise
    if backup is not None:
        try:
            shutil.rmtree(backup)
        except OSError:
            print("FFmpeg is installed; its previous runtime could not yet be removed.")


def provision(root: Path) -> Path:
    """Return the verified portable FFmpeg bin folder, installing it if needed."""
    if sys.platform != "win32":
        raise RuntimeSetupError("This FFmpeg setup helper supports Windows only.")
    local = Path(root).resolve() / ".local"
    if local.is_symlink():
        raise RuntimeSetupError("The local runtime directory must not be a symbolic link.")
    local.mkdir(exist_ok=True)
    installation = local / "ffmpeg"
    if _cache_valid(installation):
        print("Verified portable FFmpeg is ready (cached Windows x64 build).")
        return installation / "bin"
    if installation.is_symlink():
        raise RuntimeSetupError("The FFmpeg install directory must not be a symbolic link.")
    print("Downloading the official Windows x64 FFmpeg build and its SHA-256 checksum...")
    expected, archive_url = _expected_digest()
    with tempfile.TemporaryDirectory(prefix="ffmpeg-setup-", dir=local) as temporary:
        work = Path(temporary)
        archive_path = work / "ffmpeg.zip"
        _download_archive(archive_path, expected, archive_url)
        staging = work / "verified"
        _extract(archive_path, staging, expected)
        _activate(staging, installation)
    print("Verified portable FFmpeg is ready (Windows x64 build).")
    return installation / "bin"


def main() -> int:
    try:
        provision(Path(__file__).resolve().parents[1])
    except RuntimeSetupError as exc:
        print(f"FFmpeg setup failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Low-level exception strings can include proxy URLs or user paths.
        print(f"FFmpeg setup failed ({type(exc).__name__}). Check network access and available disk space, then retry.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
