"""One local task service shared by the desktop and browser clients."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .workspace import atomic_write_json

SERVICE_VERSION = "1.3.0"
PROTOCOL_VERSION = 2


class ServiceInstance:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.instance_id = uuid.uuid4().hex
        self.csrf_token = secrets.token_urlsafe(32)
        self.port = int(os.getenv("LANGBAI_TTS_PORT", "18765"))
        self.started_by = os.getenv("LANGBAI_STARTED_BY", "headless")
        self._handle = None

    def snapshot(self) -> dict:
        return {"instanceId": self.instance_id, "pid": os.getpid(),
                "version": SERVICE_VERSION, "protocolVersion": PROTOCOL_VERSION,
                "schemaVersion": 2, "port": self.port, "startedBy": self.started_by,
                "dataRoot": str(self.root), "webUrl": f"http://127.0.0.1:{self.port}/longform"}

    def acquire(self) -> None:
        if self._handle is not None:
            return
        target = self.root / "v2" / "service.lock"
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = target.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("同一数据目录已有任务服务运行，请连接已有服务或先停止它") from exc
        self._handle = handle
        atomic_write_json(self.root / "v2" / "service.json", self.snapshot())

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass


class LocalServiceMiddleware(BaseHTTPMiddleware):
    """Reject browser cross-site mutations and DNS rebinding, including old APIs.

    CLI/owned Electron main-process requests have no Origin. Browser requests
    must use an exact local origin; opaque file origins are never allowed.
    Cross-origin trusted clients additionally need a service-issued CSRF token.
    """
    def __init__(self, app, *, instance: ServiceInstance, testing: bool = False):
        super().__init__(app)
        self.instance = instance
        self.testing = testing
        self.origins = {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost")
                        for port in (instance.port, 5173, 7862)}

    async def dispatch(self, request, call_next):
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost"} and not (self.testing and host == "testserver"):
            return JSONResponse({"detail": "任务服务只接受本机 Host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in self.origins:
            return JSONResponse({"detail": "不受信任的请求来源"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "拒绝跨站操作"}, status_code=403)
            if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
                token = request.headers.get("x-langbai-csrf", "")
                if not secrets.compare_digest(token, self.instance.csrf_token):
                    return JSONResponse({"detail": "请刷新页面以获取本机会话令牌"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self' http://127.0.0.1:7862"
        return response


class DeviceCoordinator:
    """Process-local GPU lease also used by legacy jobs and training launchers.

    Ownership is logical, not tied to a thread: a training monitor can release
    the lease acquired by the HTTP thread that launched it.
    """
    def __init__(self, adapters: dict[str, Any]):
        self.adapters = adapters
        self._condition = threading.Condition()
        self._owner: dict | None = None

    def snapshot(self):
        with self._condition:
            return dict(self._owner) if self._owner else None

    def reserve(self, owner: str, engine: str, *, stop_event=None, wait=True):
        with self._condition:
            while self._owner and self._owner["id"] != owner:
                if not wait:
                    raise RuntimeError(f"显卡正由 {self._owner['id']} 使用，请等待或暂停该任务")
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("等待显卡时任务已停止")
                self._condition.wait(0.1)
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("等待显卡时任务已停止")
            self._owner = {"id": owner, "engine": engine, "acquiredUtc": time.time()}
        try:
            # Serial execution alone does not unload idle models. Close only
            # adapters owned by this service before switching engines.
            for other, adapter in self.adapters.items():
                if other != engine:
                    adapter.close()
                    snapshot = getattr(adapter, "runtime_snapshot", None)
                    if callable(snapshot) and snapshot().get("exitConfirmed") is False:
                        raise RuntimeError(f"{other} 尚未确认退出，暂不加载另一个模型")
        except Exception:
            self.release(owner)
            raise

    def release(self, owner: str):
        with self._condition:
            if self._owner and self._owner["id"] == owner:
                self._owner = None
                self._condition.notify_all()

    @contextmanager
    def acquire(self, job: dict, stop_event):
        owner = f"longform:{job['id']}"
        self.reserve(owner, job["engine"], stop_event=stop_event)
        try:
            yield
        finally:
            self.release(owner)

    def wire_legacy(self, manager, training):
        original_run = manager._run_job
        def run(job_id):
            job = manager.get(job_id)
            if job is None:
                return
            owner = f"legacy:{job_id}"
            # Cancellation while queued is checked by the original manager.
            self.reserve(owner, job.engine)
            try:
                return original_run(job_id)
            finally:
                self.release(owner)
        manager._run_job = run

        launch, monitor, stop = training._launch, training._monitor, training.stop
        def training_launch(record):
            owner = f"training:{record['id']}"
            try:
                self.reserve(owner, "training", wait=False)
            except RuntimeError as exc:
                from .training import TrainingError
                record.update(status="failed", error=str(exc))
                training._save(record)
                raise TrainingError(str(exc)) from exc
            try:
                return launch(record)
            except Exception:
                self.release(owner)
                raise
        def training_monitor(task_id, process):
            try:
                return monitor(task_id, process)
            finally:
                self.release(f"training:{task_id}")
        def training_stop(task_id, **kwargs):
            result = stop(task_id, **kwargs)
            if training.mock_mode:
                self.release(f"training:{task_id}")
            return result
        training._launch, training._monitor, training.stop = training_launch, training_monitor, training_stop

        start_workbench, monitor_workbench, stop_workbench = (training.start_gpt_workbench,
            training._monitor_gpt_workbench, training.stop_gpt_workbench)
        def workbench_start():
            self.reserve("training:gpt-workbench", "training", wait=False)
            try:
                result = start_workbench()
                if not result.get("running"):
                    self.release("training:gpt-workbench")
                return result
            except Exception:
                self.release("training:gpt-workbench")
                raise
        def workbench_monitor(process):
            try:
                return monitor_workbench(process)
            finally:
                self.release("training:gpt-workbench")
        def workbench_stop(**kwargs):
            result = stop_workbench(**kwargs)
            if not result.get("running"):
                self.release("training:gpt-workbench")
            return result
        training.start_gpt_workbench = workbench_start
        training._monitor_gpt_workbench = workbench_monitor
        training.stop_gpt_workbench = workbench_stop
