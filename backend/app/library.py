from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import soundfile as sf

from .models import JobManifest
from .storage import JobStore


class UnsafeOutputPath(ValueError):
    pass


def parse_iso_filter(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} 必须是 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_output_path(store: JobStore, job: JobManifest) -> Path | None:
    if not job.output_path:
        return None
    job_root = store.job_dir(job.id).resolve()
    candidate = Path(job.output_path).resolve(strict=False)
    if job_root != candidate.parent and job_root not in candidate.parents:
        raise UnsafeOutputPath(f"任务 {job.id} 的输出路径越过任务目录")
    if candidate.suffix.lower() != ".wav":
        raise UnsafeOutputPath(f"任务 {job.id} 的输出不是 WAV 文件")
    return candidate


def audio_metadata(path: Path) -> dict[str, Any]:
    stat = path.stat()
    info = sf.info(str(path))
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    return {
        "path": str(path),
        "filename": path.name,
        "extension": path.suffix.lower(),
        "sizeBytes": stat.st_size,
        "modifiedAt": modified,
        "durationSeconds": round(float(info.duration), 6),
        "sampleRate": int(info.samplerate),
        "channels": int(info.channels),
        "frames": int(info.frames),
        "format": info.format,
        "subtype": info.subtype,
    }


def output_state(store: JobStore, job: JobManifest) -> dict[str, Any]:
    try:
        path = safe_output_path(store, job)
    except UnsafeOutputPath as exc:
        return {"state": "unsafe", "exists": False, "error": str(exc)}
    if path is None:
        return {"state": "none", "exists": False}
    if not path.is_file():
        return {"state": "missing", "exists": False, "path": str(path)}
    try:
        return {"state": "available", "exists": True, **audio_metadata(path)}
    except (OSError, RuntimeError) as exc:
        return {
            "state": "unreadable", "exists": True, "path": str(path),
            "filename": path.name, "error": f"{type(exc).__name__}: {exc}",
        }


def search_jobs(
    store: JobStore,
    *,
    query: str | None = None,
    engine: str | None = None,
    job_status: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    has_output: bool | None = None,
    offset: int = 0,
    limit: int = 50,
    audio_only: bool = False,
) -> dict[str, Any]:
    after = parse_iso_filter(created_after, "createdAfter")
    before = parse_iso_filter(created_before, "createdBefore")
    needle = (query or "").strip().casefold()
    items: list[dict[str, Any]] = []
    for job in store.list():
        if engine is not None and job.engine != engine:
            continue
        if job_status is not None and job.status.value != job_status:
            continue
        created = parse_iso_filter(job.created_at, "createdAt")
        if after is not None and created is not None and created < after:
            continue
        if before is not None and created is not None and created > before:
            continue
        if needle and needle not in f"{job.id}\n{job.title}\n{job.text}".casefold():
            continue
        output = output_state(store, job)
        available = output.get("state") == "available"
        if audio_only and not available:
            continue
        if has_output is not None and available != has_output:
            continue
        payload = job.model_dump(mode="json")
        payload["params"] = payload["parameters"]
        payload["longAudio"] = job.long_audio.model_dump(mode="json", by_alias=True)
        payload["outputPath"] = payload["output_path"]
        payload["output"] = output
        items.append(payload)
    total = len(items)
    return {"items": items[offset:offset + limit], "total": total, "offset": offset, "limit": limit}
