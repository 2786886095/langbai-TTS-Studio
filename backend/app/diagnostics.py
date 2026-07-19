from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DIAGNOSTIC_SCHEMA_VERSION = 1
_EXPORT_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
_SECRET_PATTERNS = (
    re.compile(r"(?im)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s]+"),
    re.compile(r"(?im)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r'(?i)("(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"\s*:\s*")[^"]*(")'),
)


def _redact_log(data: bytes) -> bytes:
    text = data.decode("utf-8", errors="replace")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 2:
            text = pattern.sub(r"\1[REDACTED]\2", text)
        else:
            text = pattern.sub(r"\1[REDACTED]", text)
    return text.encode("utf-8")


class DiagnosticNotFound(FileNotFoundError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class DiagnosticExporter:
    def __init__(self, root: str | Path, log_root: str | Path):
        self.root = Path(root).resolve()
        self.log_root = Path(log_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, export_id: str) -> Path:
        if not _EXPORT_ID.fullmatch(export_id):
            raise ValueError("无效诊断导出 ID")
        return self.root / f"{export_id}.zip"

    def _safe_existing_path(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        if path.is_symlink() or resolved.parent != self.root:
            raise ValueError("诊断导出路径越过诊断目录")
        return resolved

    @staticmethod
    def _metadata(path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "id": path.stem,
            "filename": path.name,
            "path": str(path),
            "sizeBytes": stat.st_size,
            "createdAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "sha256": _sha256(path),
        }

    def create(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        created = datetime.now(timezone.utc)
        export_id = created.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        destination = self._path(export_id)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        manifest = {
            "schemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
            "createdAt": created.isoformat(),
            "backend": {"python": sys.version, "platform": platform.platform()},
            **snapshot,
        }
        try:
            with self._lock, zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "diagnostics.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                )
                if self.log_root.is_dir():
                    logs = sorted(
                        (path for path in self.log_root.rglob("*.log") if path.is_file()),
                        key=lambda path: path.stat().st_mtime,
                        reverse=True,
                    )[:20]
                    for path in logs:
                        try:
                            resolved = path.resolve(strict=True)
                            if path.is_symlink() or self.log_root not in resolved.parents:
                                continue
                            data = _redact_log(path.read_bytes()[-256 * 1024:])
                            relative = path.relative_to(self.log_root).as_posix()
                            archive.writestr(f"logs/{relative}", data)
                        except OSError:
                            continue
            os.replace(temporary, destination)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return self._metadata(destination)

    def get(self, export_id: str) -> Path:
        path = self._path(export_id)
        if not path.is_file():
            raise DiagnosticNotFound("诊断导出不存在")
        return self._safe_existing_path(path)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            result = [
                self._metadata(self._safe_existing_path(path))
                for path in self.root.glob("*.zip") if _EXPORT_ID.fullmatch(path.stem)
            ]
        return sorted(result, key=lambda item: item["createdAt"], reverse=True)
