from __future__ import annotations

import asyncio
import json
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from ..adapters.base import EngineError
from ..workspace import WorkspaceError, WorkspaceNotFound

from .models import ActionRequest, LongformConflict, LongformCreate, PreviewRequest, StorageBlocked
from .segmentation import plan_text

SUMMARY_KEYS = {"id", "title", "engine", "status", "phase", "progress", "acceptedSegments",
                "totalSegments", "totalSegmentRecords", "outputPath", "timing", "error", "version",
                "createdAt", "updatedAt", "segmentsRetained", "segmentsCleanupPending", "counts", "runtime", "stopUnconfirmed",
                "durationSeconds", "sampleRate", "compatibilityExportError", "cleanupWarning"}


async def stream_job_updates(manager, request, interval=1.0):
    """Reconnect starts with current summaries, rather than replaying stale text."""
    known = {}
    heartbeat = time.monotonic()
    while not getattr(manager, "_closed", False) and not getattr(manager, "_draining", False) and not await request.is_disconnected():
        try:
            versions = manager.store.job_versions()
            changed = [identity for identity, version in versions.items() if known.get(identity) != version]
            for offset in range(0, len(changed), 50):
                items = []
                for identity in changed[offset:offset + 50]:
                    summary = manager._payload(manager.store.get_job(identity))
                    items.append({key: value for key, value in summary.items() if key in SUMMARY_KEYS})
                yield "data: " + json.dumps({"type": "jobs", "items": items}, ensure_ascii=False, separators=(",", ":")) + "\n\n"
            known = versions
        except (OSError, sqlite3.Error):
            yield 'data: {"type":"storage_blocked","message":"任务存储暂时不可用，正在重连"}\n\n'
        if time.monotonic() - heartbeat >= 15:
            yield ": heartbeat\n\n"
            heartbeat = time.monotonic()
        await asyncio.sleep(interval)


def build_router(manager):
    router = APIRouter()

    def invoke(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, "任务或片段不存在") from exc
        except WorkspaceNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except LongformConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except StorageBlocked as exc:
            raise HTTPException(503, str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(503, "任务存储暂时不可用：" + str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (WorkspaceError, EngineError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/api/longform/events")
    async def updates(request: Request):
        return StreamingResponse(stream_job_updates(manager, request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/api/longform/capabilities")
    def capabilities():
        return manager.capabilities()

    @router.post("/api/segmentation/preview")
    def preview(request: PreviewRequest):
        return invoke(plan_text, request.text, request.engine, request.segmentation)

    @router.get("/api/jobs/v2")
    def jobs():
        return {"items": invoke(manager.list)}

    @router.post("/api/jobs/v2", status_code=201)
    def create(request: LongformCreate):
        return invoke(manager.create, request)

    @router.get("/api/jobs/v2/{job_id}")
    def detail(job_id: str):
        return invoke(manager.get, job_id)

    @router.get("/api/jobs/v2/{job_id}/segments")
    def segments(job_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500)):
        return invoke(manager.segments, job_id, offset, limit)

    @router.get("/api/jobs/v2/{job_id}/events")
    def events(job_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500), tail: bool = False):
        invoke(manager.store.get_job, job_id)
        return {"items": manager.store.events(job_id, after, limit, tail)}

    @router.get("/api/jobs/v2/{job_id}/output")
    def output(job_id: str):
        path = invoke(manager.output_file, job_id)
        return FileResponse(path, filename=path.name)

    @router.get("/api/jobs/v2/{job_id}/segments/{segment_id}/audio")
    def segment_audio(job_id: str, segment_id: str):
        path = invoke(manager.segment_file, job_id, segment_id)
        return FileResponse(path, media_type="audio/wav")

    @router.post("/api/jobs/v2/{job_id}/{action}")
    def action(job_id: str, action: str, request: ActionRequest | None = None):
        request = request or ActionRequest()
        return invoke(manager.action, job_id, action, version=request.version, mode=request.mode,
                      output=request.output.model_dump(by_alias=True) if request.output else None,
                      segment_ids=request.segment_ids, seed=request.seed)

    return router
