from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path


def content_manifest(root: Path) -> tuple[str, int]:
    manifest = hashlib.sha256()
    total = 0
    files = sorted(
        (item for item in root.rglob("*") if item.is_file() and item.name != "model-manifest.json"),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    for path in files:
        relative = path.relative_to(root).as_posix()
        file_hash = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                total += len(chunk)
                file_hash.update(chunk)
        manifest.update(relative.encode("utf-8"))
        manifest.update(b"\0")
        manifest.update(file_hash.digest())
    return manifest.hexdigest(), total


def metadata_fingerprint(root: Path) -> str:
    """Cheap change detector: paths, sizes and nanosecond mtimes, without reading model bytes."""
    digest = hashlib.sha256()
    files = sorted(
        (item for item in root.rglob("*") if item.is_file() and item.name != "model-manifest.json"),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    for path in files:
        stat = path.stat()
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


class ModelIntegrityVerifier:
    def __init__(self):
        self._cache: dict[str, tuple[tuple, bool]] = {}
        self._lock = threading.RLock()

    def verify(self, path: Path, expected_revision: str) -> bool:
        manifest_path = path / "model-manifest.json"
        try:
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            saved_revision = saved.get("revision")
            expected_digest = saved.get("content_manifest_sha256")
            installed_bytes = int(saved.get("installed_bytes", -1))
            fingerprint = metadata_fingerprint(path)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        cache_key = (fingerprint, saved_revision, expected_digest, installed_bytes, expected_revision)
        path_key = str(path.resolve())
        with self._lock:
            cached = self._cache.get(path_key)
            if cached is not None and cached[0] == cache_key:
                return cached[1]
        valid = False
        if saved_revision == expected_revision and expected_digest and installed_bytes >= 0:
            try:
                actual, total = content_manifest(path)
                valid = actual == expected_digest and total == installed_bytes
            except OSError:
                valid = False
        with self._lock:
            self._cache[path_key] = (cache_key, valid)
        return valid

    def remember_verified(self, path: Path, revision: str, digest: str, installed_bytes: int) -> None:
        try:
            fingerprint = metadata_fingerprint(path)
        except OSError:
            return
        cache_key = (fingerprint, revision, digest, installed_bytes, revision)
        with self._lock:
            self._cache[str(path.resolve())] = (cache_key, True)


MODEL_INTEGRITY_VERIFIER = ModelIntegrityVerifier()
