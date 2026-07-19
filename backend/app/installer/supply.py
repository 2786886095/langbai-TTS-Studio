from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .catalog import MANAGED_TOOL_CATALOG
from .runner import CommandStopped


ProgressCallback = Callable[[int, int | None], None]
StopCallback = Callable[[], str | None]


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _directory_manifest(root: Path, manifest_name: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    files = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.name != manifest_name),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in files:
        relative = path.relative_to(root).as_posix()
        file_digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                total += len(chunk)
                file_digest.update(chunk)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.digest())
    return digest.hexdigest(), total


def _metadata_fingerprint(root: Path, manifest_name: str) -> str:
    digest = hashlib.sha256()
    files = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.name != manifest_name),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in files:
        stat_result = path.stat()
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat_result.st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat_result.st_mtime_ns).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


class ManagedSupply:
    """Pinned archive downloader and atomic installer; it never invokes a system shell or package tool."""

    def __init__(
        self,
        tool_catalog: dict[str, dict] | None = None,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        retry_attempts: int = 3,
        retry_delay: Callable[[float], None] = time.sleep,
    ):
        self.tool_catalog = tool_catalog or MANAGED_TOOL_CATALOG
        self.urlopen = urlopen
        self.retry_attempts = max(1, retry_attempts)
        self.retry_delay = retry_delay
        self._verify_cache: dict[str, tuple[tuple[Any, ...], bool]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _raise_if_stopped(stop_reason: StopCallback) -> None:
        reason = stop_reason()
        if reason:
            raise CommandStopped(reason)

    def download_verified(
        self,
        url: str,
        destination: Path,
        expected_sha256: str,
        *,
        on_progress: ProgressCallback,
        stop_reason: StopCallback,
    ) -> int:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.unlink(missing_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "langbai-TTS-Studio/installer"})
        last_error: BaseException | None = None
        for attempt in range(1, self.retry_attempts + 1):
            digest = hashlib.sha256()
            downloaded = 0
            partial.unlink(missing_ok=True)
            try:
                self._raise_if_stopped(stop_reason)
                with self.urlopen(request, timeout=60) as response, partial.open("wb") as handle:
                    raw_length = response.headers.get("Content-Length")
                    total = int(raw_length) if raw_length and raw_length.isdigit() else None
                    while True:
                        self._raise_if_stopped(stop_reason)
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        on_progress(downloaded, total)
                    handle.flush()
                    os.fsync(handle.fileno())
                actual = digest.hexdigest()
                if actual != expected_sha256.lower():
                    raise RuntimeError(f"SHA-256 校验失败：期望 {expected_sha256}，实际 {actual}")
                os.replace(partial, destination)
                return downloaded
            except CommandStopped:
                raise
            except RuntimeError:
                # A digest mismatch is deterministic and must never be hidden by
                # repeatedly accepting another response from the same URL.
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_error = exc
                if attempt >= self.retry_attempts:
                    break
                self._raise_if_stopped(stop_reason)
                self.retry_delay(min(2 ** (attempt - 1), 4))
            finally:
                partial.unlink(missing_ok=True)
        raise RuntimeError(f"下载失败，已重试 {self.retry_attempts} 次: {last_error}") from last_error

    def extract_zip(
        self,
        archive_path: Path,
        destination: Path,
        *,
        stop_reason: StopCallback,
        max_expansion_ratio: int = 30,
    ) -> None:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        archive_bytes = max(archive_path.stat().st_size, 1)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                if len(members) > 200_000:
                    raise RuntimeError("归档文件条目过多")
                total_uncompressed = sum(member.file_size for member in members)
                if total_uncompressed > archive_bytes * max_expansion_ratio:
                    raise RuntimeError("归档解压比例异常")
                clean_paths: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
                for member in members:
                    if "\\" in member.filename:
                        raise RuntimeError(f"归档含不安全路径: {member.filename}")
                    pure = PurePosixPath(member.filename)
                    parts = tuple(part for part in pure.parts if part not in ("", "."))
                    if pure.is_absolute() or ".." in parts:
                        raise RuntimeError(f"归档含越界路径: {member.filename}")
                    file_type = (member.external_attr >> 16) & 0o170000
                    if file_type == stat.S_IFLNK:
                        raise RuntimeError(f"归档含符号链接: {member.filename}")
                    if parts:
                        clean_paths.append((member, parts))
                first_parts = {parts[0] for _, parts in clean_paths}
                strip_root = len(first_parts) == 1 and all(len(parts) > 1 or member.is_dir() for member, parts in clean_paths)
                resolved_root = destination.resolve()
                for member, parts in clean_paths:
                    self._raise_if_stopped(stop_reason)
                    relative = parts[1:] if strip_root else parts
                    if not relative:
                        continue
                    target = destination.joinpath(*relative)
                    resolved = target.resolve(strict=False)
                    if resolved_root != resolved and resolved_root not in resolved.parents:
                        raise RuntimeError(f"归档解压路径越界: {member.filename}")
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open("wb") as output:
                        while chunk := source.read(1024 * 1024):
                            self._raise_if_stopped(stop_reason)
                            output.write(chunk)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def _verify_directory(self, path: Path, manifest_name: str, expected: dict[str, Any]) -> bool:
        manifest_path = path / manifest_name
        try:
            if path.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
                return False
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            fingerprint = _metadata_fingerprint(path, manifest_name)
            expected_digest = saved.get("contentManifestSha256")
            installed_bytes = int(saved.get("installedBytes", -1))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        expected_fields = tuple(sorted(expected.items()))
        cache_key = (fingerprint, expected_fields, expected_digest, installed_bytes)
        path_key = str(path.resolve())
        with self._lock:
            cached = self._verify_cache.get(path_key)
            if cached is not None and cached[0] == cache_key:
                return cached[1]
        fields_match = all(saved.get(key) == value for key, value in expected.items())
        valid = False
        if fields_match and expected_digest and installed_bytes >= 0:
            actual, total = _directory_manifest(path, manifest_name)
            valid = actual == expected_digest and total == installed_bytes
        with self._lock:
            self._verify_cache[path_key] = (cache_key, valid)
        return valid

    @staticmethod
    def tool_path(root: Path, tool: dict) -> Path:
        return root / "tools" / tool["id"] / tool["version"]

    def tool_status(self, root: Path, tool_id: str) -> dict[str, Any]:
        item = self.tool_catalog[tool_id]
        path = self.tool_path(root, item)
        expected = {"id": tool_id, "version": item["version"], "archiveSha256": item["sha256"]}
        valid = self._verify_directory(path, "tool-manifest.json", expected) if path.exists() else False
        executables = {name: str(path / name) for name in item["executables"]}
        return {
            "id": tool_id, "name": item["name"], "version": item["version"],
            "installed": valid, "state": "installed" if valid else ("integrity_failed" if path.exists() else "missing"),
            "path": str(path), "executables": executables,
            "archiveUrl": item["archive_url"], "sha256": item["sha256"],
            "checksumUrl": item.get("checksum_url"), "sourcePage": item.get("source_page"),
            "platform": item.get("platform"), "archiveBytes": item.get("archive_bytes"),
            "license": item["license"], "licenseUrl": item["license_url"],
        }

    def ensure_tool(
        self,
        root: Path,
        tool_id: str,
        *,
        license_accepted_at: str,
        job_id: str,
        on_progress: ProgressCallback,
        stop_reason: StopCallback,
    ) -> Path:
        item = self.tool_catalog[tool_id]
        status = self.tool_status(root, tool_id)
        if status["installed"]:
            return Path(status["executables"][item["executables"][0]])
        final = self.tool_path(root, item)
        work = root / ".installer-tmp" / job_id / "tools" / tool_id
        archive = work / "archive.zip"
        extracted = work / "extracted"
        backup = work / "previous"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        try:
            self.download_verified(
                item["archive_url"], archive, item["sha256"],
                on_progress=on_progress, stop_reason=stop_reason,
            )
            self.extract_zip(archive, extracted, stop_reason=stop_reason)
            self._raise_if_stopped(stop_reason)
            missing = [name for name in item["executables"] if not (extracted / name).is_file()]
            if missing:
                raise RuntimeError("托管工具归档缺少可执行文件: " + ", ".join(missing))
            content_digest, installed_bytes = _directory_manifest(extracted, "tool-manifest.json")
            _atomic_json(extracted / "tool-manifest.json", {
                "schemaVersion": 1, "id": tool_id, "version": item["version"],
                "archiveUrl": item["archive_url"], "archiveSha256": item["sha256"],
                "contentManifestSha256": content_digest, "installedBytes": installed_bytes,
                "license": item["license"], "licenseUrl": item["license_url"],
                "licenseAcceptedAt": license_accepted_at,
            })
            self._raise_if_stopped(stop_reason)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                os.replace(final, backup)
            try:
                os.replace(extracted, final)
                if not self.tool_status(root, tool_id)["installed"]:
                    raise RuntimeError(f"托管工具 {tool_id} 安装后完整性校验失败")
            except Exception:
                if final.exists():
                    shutil.rmtree(final, ignore_errors=True)
                if backup.exists():
                    os.replace(backup, final)
                raise
            else:
                shutil.rmtree(backup, ignore_errors=True)
            return final / item["executables"][0]
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def source_valid(self, source: Path, item: dict) -> bool:
        expected = {"revision": item["revision"], "archiveSha256": item["sha256"]}
        return self._verify_directory(source, "source-manifest.json", expected)

    def install_source(
        self,
        item: dict,
        source: Path,
        work: Path,
        *,
        on_progress: ProgressCallback,
        stop_reason: StopCallback,
    ) -> None:
        archive = work / "source.zip"
        extracted = work / "source-extracted"
        self.download_verified(
            item["source_url"], archive, item["sha256"],
            on_progress=on_progress, stop_reason=stop_reason,
        )
        self.extract_zip(archive, extracted, stop_reason=stop_reason)
        self._raise_if_stopped(stop_reason)
        if not any(extracted.iterdir()):
            raise RuntimeError("源码归档解压后为空")
        digest, installed_bytes = _directory_manifest(extracted, "source-manifest.json")
        _atomic_json(extracted / "source-manifest.json", {
            "schemaVersion": 1, "revision": item["revision"],
            "archiveUrl": item["source_url"], "archiveSha256": item["sha256"],
            "contentManifestSha256": digest, "installedBytes": installed_bytes,
        })
        self._raise_if_stopped(stop_reason)
        if source.exists():
            shutil.rmtree(source)
        source.parent.mkdir(parents=True, exist_ok=True)
        os.replace(extracted, source)
        if not self.source_valid(source, item):
            raise RuntimeError("源码归档安装后完整性校验失败")
