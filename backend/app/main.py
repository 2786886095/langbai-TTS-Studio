from __future__ import annotations

import json
import os
import queue
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse

from .adapters import MockAdapter, build_default_adapters
from .bindings import EngineBindingRequest, EngineBindingStore, EngineDiscoveryRequest
from .jobs import JobManager
from .installer import InstallerManager, InstallRequest, ModelInstallRequest, ToolRepairRequest
from .installer.manager import InstallConflictError
from .diagnostics import DiagnosticExporter, DiagnosticNotFound
from .library import output_state, search_jobs
from .models import JobCreate
from .parameters import ENGINE_INFO, ENGINE_PARAMETERS, engine_catalog
from .storage import JobStore
from .workspace import (
    PROJECT_SCHEMA_VERSION,
    SETTINGS_SCHEMA_VERSION,
    ProjectCopyRequest,
    ProjectCreate,
    ProjectStore,
    ProjectUpdate,
    SettingsPatch,
    SettingsStore,
    UnsupportedSchema,
    WorkspaceConflict,
    WorkspaceError,
    WorkspaceNotFound,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _job_payload(job) -> dict:
    data = job.model_dump(mode="json")
    # Keep stable manifest names while also serving the Electron camelCase contract.
    data["params"] = data["parameters"]
    data["longAudio"] = job.long_audio.model_dump(mode="json", by_alias=True)
    data["outputPath"] = data["output_path"]
    return data


def create_app(*, adapters=None, data_dir: str | Path | None = None, mock_mode: bool | None = None,
               installer_manager: InstallerManager | None = None) -> FastAPI:
    if mock_mode is None:
        mock_mode = os.getenv("LANGBAI_TTS_MOCK", "0") == "1"
    root = Path(data_dir or os.getenv("LANGBAI_TTS_DATA", BACKEND_ROOT / "data"))
    managed_install_root = Path(os.getenv("LANGBAI_INSTALL_ROOT") or (root / "managed")).resolve()
    bindings = EngineBindingStore(root / "engine-bindings.json")
    if adapters is None:
        if mock_mode:
            adapters = {engine_id: MockAdapter(engine_id) for engine_id in ENGINE_INFO}
        else:
            adapters = build_default_adapters(root / "logs", managed_install_root, bindings)
    manager = JobManager(JobStore(root / "jobs"), adapters, mock_mode=mock_mode)
    installer = installer_manager or InstallerManager(
        root, default_install_root=managed_install_root
    )
    projects = ProjectStore(root / "projects")
    settings_store = SettingsStore(root / "settings.json")
    diagnostics = DiagnosticExporter(root / "diagnostics", root / "logs")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        manager.start()
        installer.start()
        try:
            yield
        finally:
            manager.close()
            installer.close()

    api = FastAPI(title="langbai TTS Studio API", version="1.0.0", lifespan=lifespan)
    api.state.manager = manager
    api.state.installer = installer
    api.state.projects = projects
    api.state.settings = settings_store
    api.state.diagnostics = diagnostics
    api.state.bindings = bindings
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1", "null"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.get("/health")
    def health():
        snapshots = [adapter.status() for adapter in manager.adapters.values()]
        return {
            "status": "ok" if all(item.get("available") for item in snapshots) else "degraded",
            "service": "langbai-TTS-Studio", "engines": snapshots,
        }

    @api.get("/api/engines")
    def engines():
        statuses = {item["id"]: item for item in (adapter.status() for adapter in manager.adapters.values())}
        result = engine_catalog()
        for item in result:
            item["status"] = statuses.get(item["id"], {"available": False, "state": "unconfigured"})
        return result

    @api.get("/api/engines/status")
    def engine_status():
        return [adapter.status() for adapter in manager.adapters.values()]

    @api.get("/api/engines/{engine_id}/parameters")
    def engine_parameters(engine_id: str):
        if engine_id not in ENGINE_PARAMETERS:
            raise HTTPException(status_code=404, detail="引擎不存在")
        return {"engine": engine_id, "parameters": ENGINE_PARAMETERS[engine_id]}

    @api.get("/api/jobs")
    def list_jobs():
        return [_job_payload(job) for job in manager.list()]

    @api.post("/api/jobs", status_code=status.HTTP_202_ACCEPTED)
    def create_job(request: JobCreate):
        try:
            return _job_payload(manager.create(request))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        try:
            job = manager.get(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="无效任务 ID") from exc
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return _job_payload(job)

    @api.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        try:
            return _job_payload(manager.cancel(job_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc

    @api.post("/api/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
    def retry_job(job_id: str):
        try:
            return _job_payload(manager.retry(job_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def workspace_error(exc: Exception):
        if isinstance(exc, (WorkspaceNotFound, DiagnosticNotFound)):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, WorkspaceConflict):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, UnsupportedSchema):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    @api.get("/api/projects")
    def list_projects(
        query: str | None = None,
        engine: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        try:
            items = projects.list()
        except WorkspaceError as exc:
            workspace_error(exc)
        needle = (query or "").strip().casefold()
        filtered = [
            project for project in items
            if (engine is None or project.engine == engine)
            and (not needle or needle in f"{project.id}\n{project.name}\n{project.description}\n{project.text}".casefold())
        ]
        return {
            "items": [item.model_dump(mode="json", by_alias=True) for item in filtered[offset:offset + limit]],
            "total": len(filtered), "offset": offset, "limit": limit,
        }

    @api.post("/api/projects", status_code=status.HTTP_201_CREATED)
    def create_project(request: ProjectCreate):
        try:
            return projects.create(request).model_dump(mode="json", by_alias=True)
        except WorkspaceError as exc:
            workspace_error(exc)

    @api.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        try:
            return projects.get(project_id).model_dump(mode="json", by_alias=True)
        except (ValueError, WorkspaceError) as exc:
            workspace_error(exc)

    @api.put("/api/projects/{project_id}")
    def update_project(project_id: str, request: ProjectUpdate):
        try:
            return projects.update(project_id, request).model_dump(mode="json", by_alias=True)
        except (ValueError, WorkspaceError) as exc:
            workspace_error(exc)

    @api.post("/api/projects/{project_id}/copy", status_code=status.HTTP_201_CREATED)
    def copy_project(project_id: str, request: ProjectCopyRequest):
        try:
            return projects.copy(project_id, request).model_dump(mode="json", by_alias=True)
        except (ValueError, WorkspaceError) as exc:
            workspace_error(exc)

    @api.delete("/api/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_project(project_id: str):
        try:
            projects.delete(project_id)
        except (ValueError, WorkspaceError) as exc:
            workspace_error(exc)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api.get("/api/settings")
    def get_settings():
        try:
            return settings_store.get().model_dump(mode="json", by_alias=True)
        except WorkspaceError as exc:
            workspace_error(exc)

    @api.patch("/api/settings")
    def update_settings(request: SettingsPatch):
        try:
            return settings_store.update(request).model_dump(mode="json", by_alias=True)
        except WorkspaceError as exc:
            workspace_error(exc)

    @api.get("/api/storage/schema")
    def storage_schema():
        return {
            "projects": {"current": PROJECT_SCHEMA_VERSION, "readable": [0, PROJECT_SCHEMA_VERSION]},
            "settings": {"current": SETTINGS_SCHEMA_VERSION, "readable": [0, SETTINGS_SCHEMA_VERSION]},
            "migrationPolicy": "v0 records are migrated on read; future versions are rejected without overwrite",
        }

    @api.get("/api/history")
    def history(
        query: str | None = None,
        engine: str | None = None,
        job_status: str | None = Query(default=None, alias="status"),
        created_after: str | None = Query(default=None, alias="createdAfter"),
        created_before: str | None = Query(default=None, alias="createdBefore"),
        has_output: bool | None = Query(default=None, alias="hasOutput"),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        try:
            return search_jobs(
                manager.store, query=query, engine=engine, job_status=job_status,
                created_after=created_after, created_before=created_before, has_output=has_output,
                offset=offset, limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/api/library/audio")
    def audio_library(
        query: str | None = None,
        engine: str | None = None,
        created_after: str | None = Query(default=None, alias="createdAfter"),
        created_before: str | None = Query(default=None, alias="createdBefore"),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        try:
            return search_jobs(
                manager.store, query=query, engine=engine,
                created_after=created_after, created_before=created_before,
                offset=offset, limit=limit, audio_only=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/api/jobs/{job_id}/output")
    def resolve_job_output(job_id: str):
        try:
            job = manager.get(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="无效任务 ID") from exc
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        output = output_state(manager.store, job)
        if output["state"] == "none":
            raise HTTPException(status_code=409, detail="任务尚无输出")
        if output["state"] == "missing":
            raise HTTPException(status_code=410, detail="任务输出文件已不存在")
        if output["state"] != "available":
            raise HTTPException(status_code=409, detail=output.get("error", "任务输出不可读取"))
        path = output["path"]
        return {
            "jobId": job.id,
            "output": output,
            "openContract": {
                "executor": "electron",
                "open": {"method": "shell.openPath", "path": path},
                "reveal": {"method": "shell.showItemInFolder", "path": path},
            },
        }

    def event_stream(job_id: str | None = None) -> Iterator[str]:
        channel = manager.events.subscribe()
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    event = channel.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if job_id is not None and event.get("job", {}).get("id") != job_id:
                    continue
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            manager.events.unsubscribe(channel)

    @api.get("/api/events")
    def all_events():
        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @api.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str):
        if manager.get(job_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return StreamingResponse(event_stream(job_id), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def install_error(exc: Exception):
        if isinstance(exc, KeyError):
            raise HTTPException(status_code=404, detail="安装任务不存在") from exc
        if isinstance(exc, InstallConflictError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        code = 507 if "磁盘空间不足" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    @api.get("/api/installer/catalog")
    def installer_catalog():
        return installer.catalog_payload()

    @api.get("/api/installer/tools")
    def installer_tools(install_root: str | None = Query(default=None, alias="installRoot")):
        try:
            return installer.tools_payload(install_root)
        except (ValueError, OSError) as exc:
            install_error(exc)

    def merged_installation_status(install_root: str | None = None) -> list[dict]:
        managed_rows = installer.inspect_all(install_root)
        statuses = {item["id"]: item for item in (adapter.status() for adapter in manager.adapters.values())}
        bound = bindings.list()
        for row in managed_rows:
            engine = row["engine"]
            binding = bound.get(engine)
            current = statuses.get(engine, {})
            if not binding:
                row["origin"] = "managed"
                continue
            available = bool(current.get("available"))
            configuration_required = bool(current.get("configuration_required"))
            row.update({
                "origin": "bound",
                "installed": available,
                "detected": True,
                "bound_paths": binding,
                "source_path": binding["sourcePath"],
                "env_path": str(Path(binding["pythonPath"]).parent.parent),
                "source": {"installed": True, "state": "bound", "detail": "已绑定现有本地程序", "path": binding["sourcePath"]},
                "environment": {"installed": True, "state": "bound", "detail": "已绑定现有 Python 环境", "path": str(Path(binding["pythonPath"]).parent.parent), "python_path": binding["pythonPath"]},
                "modelsState": {"installed": available and not configuration_required, "state": "bound" if available else "configuration_required", "detail": "已使用本地模型" if available and not configuration_required else current.get("detail", "需要选择本地模型权重")},
                "runtime_status": current,
            })
            if available and not configuration_required:
                for model in row.get("models", []):
                    model.update({"installed": True, "state": "bound", "path": binding["runtimeRoot"]})
        return managed_rows

    @api.get("/api/installer/status")
    @api.get("/api/installations")
    def installation_status(install_root: str | None = Query(default=None, alias="installRoot")):
        try:
            return merged_installation_status(install_root)
        except (ValueError, OSError) as exc:
            install_error(exc)

    @api.post("/api/installations/scan-local")
    def scan_and_bind_local_engines(request: EngineDiscoveryRequest | None = None):
        bound_rows = []
        errors = []
        for adapter in manager.adapters.values():
            current = adapter.status()
            if not current.get("available") or current.get("managed"):
                continue
            try:
                bound_rows.append(bindings.bind_detected(current))
            except (KeyError, ValueError, OSError) as exc:
                errors.append({"engine": current.get("id"), "error": str(exc)})
        if request and request.roots:
            try:
                discovered = bindings.discover(request.roots, request.max_depth)
                for engine, candidate in discovered.items():
                    try:
                        bound_rows.append(bindings.bind(engine, candidate))
                    except (KeyError, ValueError, OSError) as exc:
                        errors.append({"engine": engine, "error": str(exc)})
            except (ValueError, OSError) as exc:
                errors.append({"engine": None, "error": str(exc)})
        unique_bindings = {item["engine"]: item for item in bound_rows}
        return {"found": len(unique_bindings), "bindings": list(unique_bindings.values()), "errors": errors, "installations": merged_installation_status(None)}

    @api.post("/api/installations/{engine}/bind")
    def bind_local_engine(engine: str, request: EngineBindingRequest):
        previous = bindings.get(engine)
        try:
            binding = bindings.bind(engine, request)
            current = manager.adapters[engine].status()
            if not current.get("available"):
                bindings.restore(engine, previous)
                raise ValueError(current.get("detail") or "绑定路径无法运行")
            return {"binding": binding, "status": current, "installations": merged_installation_status(None)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="引擎不存在") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/api/installer/jobs")
    @api.get("/api/downloads")
    def installer_jobs():
        return [job.model_dump(mode="json") for job in installer.list_jobs()]

    @api.get("/api/installer/jobs/{job_id}")
    @api.get("/api/downloads/{job_id}")
    def installer_job(job_id: str):
        try:
            job = installer.get_job(job_id)
        except ValueError as exc:
            install_error(exc)
        if job is None:
            raise HTTPException(status_code=404, detail="安装任务不存在")
        return job.model_dump(mode="json")

    @api.post("/api/installations/{engine}/setup", status_code=status.HTTP_202_ACCEPTED)
    def setup_engine(engine: str, request: InstallRequest):
        try:
            return installer.setup(engine, request).model_dump(mode="json")
        except (ValueError, OSError, InstallConflictError) as exc:
            install_error(exc)

    @api.post("/api/installations/{engine}/models", status_code=status.HTTP_202_ACCEPTED)
    def install_engine_model(engine: str, request: ModelInstallRequest):
        try:
            return installer.install_model(engine, request).model_dump(mode="json")
        except (ValueError, OSError, InstallConflictError) as exc:
            install_error(exc)

    @api.post("/api/installer/tools/{tool_id}/repair", status_code=status.HTTP_202_ACCEPTED)
    def repair_installer_tool(tool_id: str, request: ToolRepairRequest):
        try:
            return installer.repair_tool(tool_id, request).model_dump(mode="json")
        except (ValueError, OSError, InstallConflictError) as exc:
            install_error(exc)

    @api.post("/api/installer/jobs/{job_id}/{action}")
    @api.post("/api/downloads/{job_id}/{action}")
    def installer_action(job_id: str, action: str):
        if action not in {"pause", "resume", "cancel", "retry"}:
            raise HTTPException(status_code=404, detail="未知安装任务操作")
        try:
            return installer.action(job_id, action).model_dump(mode="json")
        except (KeyError, ValueError) as exc:
            install_error(exc)

    def installer_event_stream() -> Iterator[str]:
        channel = installer.events.subscribe()
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    event = channel.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            installer.events.unsubscribe(channel)

    @api.get("/api/installer/events")
    def installer_events():
        return StreamingResponse(installer_event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def diagnostic_snapshot() -> dict:
        def capture(name: str, callback):
            try:
                return callback()
            except Exception as exc:
                return {"captureError": f"{name}: {type(exc).__name__}: {exc}"}

        jobs = manager.list()
        job_summaries = []
        for job in jobs[:200]:
            job_summaries.append({
                "id": job.id,
                "engine": job.engine,
                "status": job.status.value,
                "progress": job.progress,
                "segmentCount": len(job.segments),
                "createdAt": job.created_at,
                "updatedAt": job.updated_at,
                "error": job.error,
                "output": output_state(manager.store, job),
            })
        settings = capture("settings", lambda: settings_store.get().model_dump(mode="json", by_alias=True))
        if isinstance(settings, dict) and settings.get("outputDirectory"):
            settings["outputDirectory"] = "<configured-local-path>"
        return {
            "service": {"name": "langbai-TTS-Studio", "apiVersion": api.version},
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "engines": capture("engines", lambda: [adapter.status() for adapter in manager.adapters.values()]),
            "installations": capture("installations", lambda: installer.inspect_all(None)),
            "settings": settings,
            "projects": capture("projects", lambda: {"count": len(projects.list())}),
            "jobs": {"total": len(jobs), "items": job_summaries},
        }

    @api.get("/api/diagnostics/exports")
    def diagnostic_exports():
        try:
            return {"items": diagnostics.list()}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"读取诊断导出失败: {exc}") from exc

    @api.post("/api/diagnostics/exports", status_code=status.HTTP_201_CREATED)
    def create_diagnostic_export():
        try:
            return diagnostics.create(diagnostic_snapshot())
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"创建诊断导出失败: {exc}") from exc

    @api.get("/api/diagnostics/exports/{export_id}")
    def download_diagnostic_export(export_id: str):
        try:
            path = diagnostics.get(export_id)
        except (ValueError, DiagnosticNotFound) as exc:
            workspace_error(exc)
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @api.get("/api/update/config")
    def update_config():
        current_settings = settings_store.get()
        return {
            "schemaVersion": 1,
            "handledBy": "electron",
            "backendPerformsUpdateChecks": False,
            "currentVersion": os.getenv("LANGBAI_APP_VERSION") or api.version,
            "channel": current_settings.update_channel,
            "provider": os.getenv("LANGBAI_UPDATE_PROVIDER") or None,
            "feedUrl": os.getenv("LANGBAI_UPDATE_FEED_URL") or None,
            "electronStateContract": {
                "states": ["idle", "checking", "available", "downloading", "downloaded", "error"],
                "requiredFields": ["state", "currentVersion", "checkedAt"],
            },
        }

    return api


app = create_app()
