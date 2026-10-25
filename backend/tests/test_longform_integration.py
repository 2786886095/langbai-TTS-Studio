"""Cross-module regressions for service ownership, v1 export and GPU leases."""
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.longform import LongformManager
from app.main import create_app
from app.service_runtime import DeviceCoordinator, ServiceInstance


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_second_manager_does_not_recover_active_job_before_service_lock(tmp_path):
    class Blocking(MockAdapter):
        def __init__(self):
            super().__init__("breeze")
            self.entered = threading.Event()
            self.finish = threading.Event()

        def synthesize(self, text, output_path, parameters):
            self.entered.set()
            assert self.finish.wait(5)
            return super().synthesize(text, output_path, parameters)

        def cancel_current(self):
            self.finish.set()

    adapter = Blocking()
    service = ServiceInstance(tmp_path)
    service.acquire()
    first = LongformManager(tmp_path / "v2", {"breeze": adapter})
    first.start()
    second = None
    try:
        job = first.create({"text": "正在运行的任务不能被另一个未获锁实例改写。", "engine": "breeze", "licenseAccepted": True})
        assert adapter.entered.wait(2)
        second = LongformManager(tmp_path / "v2", {"breeze": MockAdapter("breeze")})
        with pytest.raises(RuntimeError, match="已有任务服务"):
            ServiceInstance(tmp_path).acquire()
        assert first.store.get_job(job["id"])["status"] in {"generating", "preparing", "loading", "warming"}
    finally:
        if second:
            second.store.close()
        adapter.finish.set()
        first.close()
        service.release()


def test_native_sample_rate_is_preserved_in_v1_compatibility_export(tmp_path):
    api = create_app(data_dir=tmp_path, mock_mode=True)
    with TestClient(api) as client:
        created = client.post("/api/jobs/v2", json={"text": "保留引擎原始采样率。", "engine": "indextts2",
            "params": {"mock_sample_rate": 22050}, "output": {"sampleRate": "native"}})
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]
        wait_until(lambda: api.state.longform.get(job_id)["status"] == "completed")
        manifest = wait_until(lambda: api.state.manager.store.load(job_id))
        assert api.state.longform.get(job_id)["sampleRate"] == 22050
        assert manifest.long_audio.target_sample_rate == 22050


def test_legacy_and_v2_generation_share_one_gpu_lease(tmp_path):
    guard = threading.Lock()
    active = 0
    maximum = 0

    class Observed(MockAdapter):
        def synthesize(self, text, output_path, parameters):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            try:
                time.sleep(0.035)
                return super().synthesize(text, output_path, parameters)
            finally:
                with guard:
                    active -= 1

    adapters = {key: Observed(key) for key in ("indextts2", "voxcpm", "gpt_sovits")}
    api = create_app(data_dir=tmp_path, mock_mode=True, adapters=adapters)
    with TestClient(api) as client:
        old = client.post("/api/jobs", json={"text": "旧任务第一段。旧任务第二段。旧任务第三段。", "engine": "indextts2", "longAudio": {"maxChars": 8}})
        new = client.post("/api/jobs/v2", json={"text": "新任务第一段。新任务第二段。新任务第三段。", "engine": "voxcpm", "segmentation": {"maxChars": 10}})
        assert old.status_code == 202 and new.status_code == 201
        wait_until(lambda: api.state.manager.get(old.json()["id"]).status.value == "completed")
        wait_until(lambda: api.state.longform.get(new.json()["id"])["status"] == "completed")
        assert maximum == 1


def test_unconfirmed_other_engine_exit_blocks_new_model():
    class Unconfirmed(MockAdapter):
        def close(self):
            return

        def runtime_snapshot(self, lines=20):
            return {"exitConfirmed": False, "phase": "stop_unconfirmed", "running": True}

    devices = DeviceCoordinator({"breeze": Unconfirmed("breeze"), "indextts2": MockAdapter("indextts2")})
    with pytest.raises(RuntimeError, match="尚未确认退出"):
        devices.reserve("new-generation", "indextts2")
    assert devices.snapshot() is None


def test_second_app_factory_is_rejected_before_touching_active_data(tmp_path):
    first = create_app(data_dir=tmp_path, mock_mode=True)
    with TestClient(first) as client:
        created = client.post("/api/jobs/v2", json={"engine": "indextts2", "text": "当前服务正在生成。",
            "params": {"mock_segment_delay_ms": 300}}).json()
        wait_until(lambda: first.state.longform.store.get_job(created["id"])["status"] == "generating")
        with pytest.raises(RuntimeError, match="已有任务服务"):
            create_app(data_dir=tmp_path, mock_mode=True)
        assert first.state.longform.store.get_job(created["id"])["status"] == "generating"


def test_v1_export_excludes_skipped_and_superseded_rows(tmp_path):
    from types import SimpleNamespace
    from app.legacy_migration import LegacyMigration
    from app.models import now_iso
    from app.storage import JobStore

    job_id = "a" * 32
    legacy = JobStore(tmp_path / "jobs")
    migration = LegacyMigration(tmp_path, legacy)
    accepted = [{"id": "s0.0", "status": "accepted", "requestText": "开头。", "metadata": {"sampleRate": 22050}},
                {"id": "s0.1", "status": "accepted", "requestText": "结尾。", "metadata": {"sampleRate": 22050}}]
    skipped = {"id": "blank", "status": "skipped", "requestText": "", "text": "   "}
    parent = {"id": "s0", "status": "superseded", "requestText": "开头。结尾。"}
    payload = {"id": job_id, "engine": "indextts2", "title": "拆分后的任务", "status": "completed",
               "text": "开头。   结尾。", "params": {}, "createdAt": now_iso(), "updatedAt": now_iso(),
               "sampleRate": 22050, "output": {"format": "WAV", "sampleRate": "native"},
               "segments": [accepted[0], skipped, accepted[1]], "totalSegments": 2}
    migration.longform = SimpleNamespace(get=lambda identity: payload,
        store=SimpleNamespace(segments=lambda identity: [parent, accepted[0], skipped, accepted[1]]))
    migration.compat_export(payload)
    result = legacy.load(job_id)
    assert [segment.text for segment in result.segments] == ["开头。", "结尾。"]
    assert result.long_audio.target_sample_rate == 22050


def test_manual_runtime_start_and_restart_cannot_bypass_gpu_owner(tmp_path):
    class Observed(MockAdapter):
        starts = 0
        restarts = 0

        def start(self):
            self.starts += 1

        def restart(self):
            self.restarts += 1

    adapter = Observed("indextts2")
    api = create_app(data_dir=tmp_path, mock_mode=True, adapters={"indextts2": adapter})
    with TestClient(api) as client:
        api.state.devices.reserve("training:other-task", "training", wait=False)
        try:
            assert client.post("/api/runtime/engines/indextts2/start").status_code == 409
            assert client.post("/api/runtime/engines/indextts2/restart").status_code == 409
            assert adapter.starts == 0 and adapter.restarts == 0
        finally:
            api.state.devices.release("training:other-task")


def test_shutdown_request_rejects_new_work_but_keeps_stop_controls(tmp_path):
    api = create_app(data_dir=tmp_path, mock_mode=True)
    shutdown_requests = []
    api.state.request_shutdown = lambda: shutdown_requests.append(True)
    with TestClient(api) as client:
        manager = api.state.longform
        # Keep an existing recoverable task so every start-work operation is
        # exercised against a real task rather than a nonexistent ID.
        queued = manager.create({"engine": "indextts2", "text": "已有任务需要保留。",
            "params": {"mock_segment_delay_ms": 80}})
        manager.action(queued["id"], "pause")
        wait_until(lambda: manager.get(queued["id"])["status"] == "paused")
        before_count = len(manager.list())
        stopped = client.post("/api/service/stop")
        assert stopped.status_code == 200, stopped.text
        assert shutdown_requests == [True]
        assert client.post("/api/jobs/v2", json={"engine": "indextts2", "text": "不能在关服中提交。"}).status_code == 409
        for action in ("resume", "retry-segments", "regenerate-segment", "reexport"):
            response = client.post(f"/api/jobs/v2/{queued['id']}/{action}", json={})
            assert response.status_code == 409, (action, response.text)
            assert "正在停止" in response.json()["detail"]
        assert len(manager.list()) == before_count
        assert client.post(f"/api/jobs/v2/{queued['id']}/stop", json={"mode": "now"}).status_code == 200
        assert client.post(f"/api/jobs/v2/{queued['id']}/cancel", json={}).status_code == 200


def test_rejected_shutdown_restores_acceptance_gate(tmp_path):
    api = create_app(data_dir=tmp_path, mock_mode=True)
    api.state.request_shutdown = lambda: None
    with TestClient(api) as client:
        manager = api.state.longform
        created = manager.create({"engine": "indextts2", "text": "已完成的测试片段。"})
        wait_until(lambda: manager.get(created["id"])["status"] == "completed")
        # Inject the publish phase to exercise the real shutdown endpoint's
        # safety refusal without interrupting any file writer.
        manager._phase(created["id"], "verifying")
        response = client.post("/api/service/stop")
        assert response.status_code == 409
        assert manager._draining is False
        assert client.post("/api/jobs/v2", json={"engine": "indextts2", "text": "仍允许继续创作。"}).status_code == 201


def test_draining_gate_serializes_with_create_commit(tmp_path):
    from app.longform.models import LongformConflict

    class StatusBarrier(MockAdapter):
        def __init__(self):
            super().__init__("indextts2")
            self.inside = threading.Event()
            self.proceed = threading.Event()

        def status(self):
            self.inside.set()
            assert self.proceed.wait(3)
            return super().status()

    adapter = StatusBarrier()
    manager = LongformManager(tmp_path / "v2", {"indextts2": adapter})
    committed, drained, errors = [], threading.Event(), []

    def create():
        try:
            committed.append(manager.create({"engine": "indextts2", "text": "关服前已经开始的提交。"}))
        except Exception as exc:
            errors.append(exc)

    def drain():
        manager.begin_draining()
        drained.set()

    creator = threading.Thread(target=create)
    draining = threading.Thread(target=drain)
    try:
        creator.start()
        assert adapter.inside.wait(1)
        draining.start()
        assert not drained.wait(0.05)
        adapter.proceed.set()
        creator.join(3)
        draining.join(3)
        assert not errors and len(committed) == 1 and drained.is_set()
        with pytest.raises(LongformConflict, match="正在停止"):
            manager.create({"engine": "indextts2", "text": "关服后不能再提交。"})
        assert len(manager.list()) == 1
    finally:
        adapter.proceed.set()
        creator.join(3)
        if draining.ident is not None:
            draining.join(3)
        manager.close()


def test_shutdown_gate_blocks_legacy_start_routes_before_handlers(tmp_path, monkeypatch):
    api = create_app(data_dir=tmp_path, mock_mode=True)
    api.state.request_shutdown = lambda: None
    invoked = []

    def forbidden(*args, **kwargs):
        invoked.append(True)
        raise AssertionError("a new-work handler ran after the shutdown gate")

    with TestClient(api) as client:
        assert client.post("/api/service/stop").status_code == 200
        for owner, methods in ((api.state.manager, ("create", "create_multi_speaker", "retry")),
                               (api.state.training, ("create", "resume", "start_gpt_workbench")),
                               (api.state.adapters["indextts2"], ("start", "restart"))):
            for method in methods:
                monkeypatch.setattr(owner, method, forbidden)
        requests = [
            ("/api/jobs", {"engine": "indextts2", "text": "停止后不能提交。"}),
            ("/api/jobs/multi-speaker", {"script": "【旁白】：停止后不能提交。", "assignments": {"旁白": {"voiceProfileId": "test"}}}),
            ("/api/jobs/test/retry", {}),
            ("/api/training/gpt-sovits/workbench/start", {}),
            ("/api/training/tasks", {"pretrainedPath": "test", "trainManifest": "test", "outputDir": "test"}),
            ("/api/training/tasks/test/resume", {}),
            ("/api/runtime/engines/indextts2/start", {}),
            ("/api/runtime/engines/indextts2/restart", {}),
        ]
        for route, body in requests:
            response = client.post(route, json=body)
            assert response.status_code == 409, (route, response.text)
            assert "正在停止" in response.json()["detail"]
        assert invoked == []
        assert client.get("/health").status_code == 200
