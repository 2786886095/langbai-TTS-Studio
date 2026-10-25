"""Verified v1 snapshot and deliberately limited v1 visibility of v2 tasks."""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
import uuid
from pathlib import Path

from .models import JobManifest, JobStatus, LongAudioOptions, SegmentManifest, SegmentStatus, now_iso
from .workspace import atomic_write_json


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


class LegacyMigration:
    def __init__(self, root: Path, legacy_store):
        self.root, self.legacy_store = root.resolve(), legacy_store
        self.path = self.root / "v2" / "migration.json"
        self._lock = threading.RLock()
        self.longform = None

    def prepare(self):
        with self._lock:
            if self.path.is_file():
                record = json.loads(self.path.read_text(encoding="utf-8"))
                changed = []
                for relative, old_hash in record.get("legacyJsonHashes", {}).items():
                    path = self.root / relative
                    if not path.is_file() or sha(path) != old_hash:
                        changed.append(relative)
                record["changedSinceSnapshot"] = changed
                record["checkedAt"] = now_iso()
                atomic_write_json(self.path, record)
                return record
            files = []
            for directory in ("jobs", "projects", "voice-profiles"):
                source = self.root / directory
                if source.is_dir():
                    for path in source.rglob("*"):
                        if path.is_file():
                            if path.is_symlink() or self.root not in path.resolve().parents:
                                raise RuntimeError(f"旧数据包含越界链接，迁移已停止：{path}")
                            files.append(path)
            needed = sum(p.stat().st_size for p in files)
            if shutil.disk_usage(self.root).free < needed + 64 * 1024 * 1024:
                raise RuntimeError("旧数据备份空间不足；未修改旧数据")
            backup = self.root / "v2" / "backups" / f"v1-{uuid.uuid4().hex}"
            hashes, json_hashes = {}, {}
            for source in files:
                relative = source.relative_to(self.root)
                expected = sha(source)
                target = backup / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if sha(target) != expected or sha(source) != expected:
                    raise RuntimeError("旧数据在备份期间发生变化，迁移已停止；请退出旧版本后重试")
                hashes[str(relative)] = expected
                if source.suffix == ".json":
                    json_hashes[str(relative)] = expected
            record = {"schemaVersion": 2, "status": "verified", "createdAt": now_iso(),
                      "backupPath": str(backup) if files else None, "files": len(files),
                      "bytes": needed, "hashes": hashes, "legacyJsonHashes": json_hashes,
                      "changedSinceSnapshot": [],
                      "rollback": "旧版只能看到旧三引擎的v1资料和受限兼容任务；Breeze与暂停/中断任务仍保存在v2，旧版不可见"}
            atomic_write_json(self.path, record)
            return record

    def snapshot(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {"status": "not_started"}

    def compat_export(self, payload):
        # Formats and states which v1 cannot represent never enter its scans.
        if payload.get("engine") not in {"indextts2", "voxcpm", "gpt_sovits"}:
            return
        if payload.get("status") not in {"completed", "failed", "cancelled"}:
            return
        if payload.get("output", {}).get("format", "WAV").upper() != "WAV":
            return
        if self.longform is None:
            return
        job_id = payload["id"]
        detail = self.longform.get(job_id)
        segments = [s for s in detail.get("segments", [])
                    if s.get("status") not in {"skipped", "superseded"}]
        output = detail.get("output", {})
        sample_rate = output.get("sampleRate")
        if not isinstance(sample_rate, int):
            sample_rate = detail.get("sampleRate")
            if detail.get("outputPath") and Path(detail["outputPath"]).is_file():
                import soundfile as sf
                sample_rate = sf.info(detail["outputPath"]).samplerate
            if not isinstance(sample_rate, int):
                sample_rate = next((s.get("metadata", {}).get("sampleRate") for s in segments
                                    if isinstance(s.get("metadata", {}).get("sampleRate"), int)), None)
            if sample_rate is None:
                # Failed jobs without an observed native rate cannot be
                # represented honestly in v1's mandatory numeric field.
                return
        manifest = JobManifest(id=job_id, engine=detail["engine"], title=detail["title"],
            status=JobStatus(detail["status"]), text=detail.get("text", ""),
            progress=1.0 if detail["status"] == "completed" else detail.get("progress", 0),
            parameters=detail.get("params", {}), error=detail.get("error"),
            output_path=detail.get("outputPath"), created_at=detail["createdAt"], updated_at=detail["updatedAt"],
            long_audio=LongAudioOptions(targetSampleRate=sample_rate or 24000,
                silenceMs=output.get("silenceMs", 150), keepSegments=output.get("keepSegments", True)),
            segments=[SegmentManifest(index=i, text=s.get("requestText", s.get("text", "")),
                attempts=s.get("attempts", 0), output_path=s.get("outputPath"),
                status=SegmentStatus.completed if s.get("status") == "accepted" else SegmentStatus.pending)
                for i, s in enumerate(segments)])
        self.legacy_store.save(manifest)
