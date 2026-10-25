import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.longform import LongformManager, build_router
from app.longform.models import LongformConflict
from app.longform.segmentation import plan_text


class CountingAdapter(MockAdapter):
    def __init__(self, engine_id="breeze"):
        super().__init__(engine_id)
        self.calls = []
        self.revision = "model-1"

    def cache_identity(self, parameters):
        return {"model": self.revision, "mode": "accelerated"}

    def synthesize(self, text, output_path, parameters):
        self.calls.append(text)
        super().synthesize(text, output_path, parameters)
        return {"possibly_truncated": False, "worker_instance_id": "worker-one"}


def request(text="第一段内容。第二段内容。第三段内容。", **kwargs):
    return {"engine": "breeze", "text": text, "params": {"mock_sample_rate": 24000},
            "licenseAccepted": True, "segmentation": {"maxChars": 10}, **kwargs}


def wait(manager, job_id, states=("completed", "failed", "paused", "storage_blocked"), timeout=45):
    # These are state/IO recovery assertions, not speed benchmarks. Shared Windows
    # runners can take longer to fsync rollback-journal transactions and audio.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = manager.get(job_id)
        if result["status"] in states:
            return result
        time.sleep(0.05)
    raise AssertionError(manager.get(job_id))


@pytest.mark.parametrize("text", [
    "  第一段。\r\n\r\n第二段的标点！英文 ABC 123 and mixed words.\n ",
    "无标点长句" * 3000,
    "English only words without Chinese " * 1000,
    "😀a\u0301" * 300,
    "句。" * 50000,
    "甲" * 100000,
], ids=["mixed-crlf", "no-punctuation", "english", "combining-emoji", "100k-chars", "100k-han"])
def test_exact_source_coverage_and_budget(text):
    plan = plan_text(text, "breeze")
    assert "".join(s["text"] for s in plan["segments"]) == text
    from app.longform.segmentation import HAN
    assert all(len(s["requestText"]) <= 300 and len(HAN.findall(s["requestText"])) <= 120 for s in plan["segments"])
    assert all(a["end"] == b["start"] for a, b in zip(plan["segments"], plan["segments"][1:]))
    assert plan["exactCoverage"]


def test_impossible_numeric_budget_rejected():
    with pytest.raises(ValueError, match="连续数字"):
        plan_text("1" * 1000, "breeze")
    with pytest.raises(ValueError, match="至少"):
        plan_text("。！？ \n", "breeze")


def test_chinese_default_matches_proven_110_120_han_sentence_packing():
    a, b, c, d, e = "甲" * 60 + "。", "乙" * 55 + "。", "丙" * 10 + "。", "丁" * 120 + "！", "戊" * 5 + "。"
    source = a + b + c + d + e
    plan = plan_text(source, "breeze")
    assert [s["requestText"] for s in plan["segments"]] == [a + b, c, d, e]
    long_sentence = plan_text("己" * 125 + "。", "breeze")
    assert [s["requestText"] for s in long_sentence["segments"]] == ["己" * 120, "己" * 5 + "。"]


def test_generate_native_rate_and_flac_reexport_without_model_calls(tmp_path):
    adapter = CountingAdapter()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    manager.start()
    try:
        created = manager.create(request())
        result = wait(manager, created["id"])
        assert result["status"] == "completed", result["error"]
        assert result["sampleRate"] == 24000
        assert result["acceptedSegments"] == result["totalSegments"]
        assert result["progress"] == 1
        assert result["timing"]["generationSeconds"] > 0
        assert manager.output_file(result["id"]).is_file()
        calls = len(adapter.calls)
        manager.action(result["id"], "reexport", output={"format": "FLAC", "sampleRate": "native", "silenceMs": 300})
        exported = wait(manager, result["id"])
        assert exported["status"] == "completed", exported["error"]
        assert exported["outputPath"].endswith(".flac")
        assert len(adapter.calls) == calls
        assert sf.info(exported["outputPath"]).samplerate == 24000
    finally:
        manager.close()


def test_pause_resume_validates_only_damaged_segment(tmp_path):
    adapter = CountingAdapter()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    manager.start()
    try:
        created = manager.create(request("".join(f"第{i}段逐段生成。" for i in range(12)), params={"mock_sample_rate": 24000, "mock_segment_delay_ms": 20}))
        wait(manager, created["id"], ("generating",))
        time.sleep(0.06)
        manager.action(created["id"], "pause")
        paused = wait(manager, created["id"], ("paused",))
        assert 0 < paused["acceptedSegments"] < paused["totalSegments"]
        accepted = [s for s in paused["segments"] if s["status"] == "accepted"]
        damaged = Path(accepted[0]["outputPath"])
        damaged.write_bytes(b"corrupt wav")
        old_calls = len(adapter.calls)
        # Identical texts can share the same render; all references to that file
        # must be validated, but one regeneration supplies the replacement.
        manager.action(created["id"], "resume")
        finished = wait(manager, created["id"])
        assert finished["status"] == "completed", finished["error"]
        assert len(adapter.calls) > old_calls
        assert all(manager.segment_file(created["id"], s["id"]).exists() for s in finished["segments"] if s["status"] == "accepted")
    finally:
        manager.close()


def test_restart_marks_active_interrupted_and_reuses_hashes(tmp_path):
    adapter = CountingAdapter()
    root = tmp_path / "v2"
    manager = LongformManager(root, {"breeze": adapter})
    manager.start()
    created = manager.create(request())
    finished = wait(manager, created["id"])
    assert finished["status"] == "completed"
    manager.close()
    # Simulate a crash after accepted pieces were committed, before completion.
    from app.longform.store import LongformStore
    store = LongformStore(root)
    job = store.get_job(created["id"])
    job.update(status="generating", phase="generating", completedAt=None)
    store.save_job(job)
    store.close()
    adapter2 = CountingAdapter()
    resumed = LongformManager(root, {"breeze": adapter2})
    resumed.start()
    try:
        interrupted = resumed.get(created["id"])
        assert interrupted["status"] == "interrupted"
        resumed.action(created["id"], "resume")
        assert wait(resumed, created["id"])["status"] == "completed"
        assert adapter2.calls == []
    finally:
        resumed.close()


def test_file_publish_sharing_failure_retries_without_model_retry(tmp_path, monkeypatch):
    import app.longform.manager as module
    adapter = CountingAdapter()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    real_replace = module.os.replace
    failed = []

    def blocked(source, destination):
        if "renders" in str(destination) and len(failed) < 2:
            failed.append(str(source))
            raise PermissionError("sharing violation")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", blocked)
    manager.start()
    try:
        created = manager.create(request("一次合成应该保留。"))
        result = wait(manager, created["id"])
        assert result["status"] == "completed", result["error"]
        assert result["counts"]["modelRetries"] == 0
        assert result["counts"]["ioRecoveries"] == 1
    finally:
        manager.close()


def test_blocked_publication_resumes_produced_audio_without_regeneration(tmp_path, monkeypatch):
    import app.longform.manager as module
    adapter = CountingAdapter()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    real_replace = module.os.replace

    def blocked(source, destination):
        raise PermissionError("sharing violation")

    monkeypatch.setattr(module.os, "replace", blocked)
    manager.start()
    try:
        created = manager.create(request("保留当前产物。"))
        result = wait(manager, created["id"])
        assert result["status"] == "storage_blocked"
        assert len(adapter.calls) == 1
        monkeypatch.setattr(module.os, "replace", real_replace)
        manager.action(created["id"], "resume")
        result = wait(manager, created["id"])
        assert result["status"] == "completed", result["error"]
        assert len(adapter.calls) == 1
    finally:
        manager.close()


def test_changed_reference_fingerprint_refuses_resume(tmp_path):
    adapter = CountingAdapter()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    manager.start()
    try:
        created = manager.create(request())
        result = wait(manager, created["id"])
        assert result["status"] == "completed"
        previous = len(adapter.calls)
        adapter.revision = "new-model"
        manager.action(created["id"], "resume")
        result = wait(manager, created["id"])
        assert result["status"] == "failed"
        assert "指纹" in result["error"]
        assert len(adapter.calls) == previous
    finally:
        manager.close()


def test_bounded_truncation_retries_then_needs_attention(tmp_path):
    class Truncated(CountingAdapter):
        def synthesize(self, text, output_path, parameters):
            super().synthesize(text, output_path, parameters)
            return {"possibly_truncated": True}

    adapter = Truncated()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    manager.start()
    try:
        created = manager.create(request("始终截断的片段。"))
        result = wait(manager, created["id"])
        assert result["status"] == "paused"
        assert any(s["status"] == "needs_attention" for s in result["segments"])
        calls = len(adapter.calls)
        time.sleep(0.1)
        assert len(adapter.calls) == calls
        assert calls <= 21
        assert "".join(s["text"] for s in result["segments"]) == result["text"]
    finally:
        manager.close()


def test_api_optimistic_controls_and_summary_does_not_copy_document(tmp_path):
    manager = LongformManager(tmp_path / "v2", {"breeze": CountingAdapter()})
    app = FastAPI()
    app.include_router(build_router(manager))
    try:
        with TestClient(app) as client:
            created = client.post("/api/jobs/v2", json=request()).json()
            assert created["status"] == "queued"
            summaries = client.get("/api/jobs/v2").json()["items"]
            assert "text" not in summaries[0] and "segments" not in summaries[0]
            paused = client.post(f"/api/jobs/v2/{created['id']}/pause", json={"version": created["version"]})
            assert paused.status_code == 200
            stale = client.post(f"/api/jobs/v2/{created['id']}/resume", json={"version": created["version"]})
            assert stale.status_code == 409
            preview = client.post("/api/segmentation/preview", json={"text": "  原文。\n", "engine": "breeze"})
            assert preview.json()["exactCoverage"]
            assert client.get(f"/api/jobs/v2/{created['id']}/output").status_code == 409
    finally:
        manager.close()


def test_gpu_callback_owns_entire_run_and_can_cancel_wait(tmp_path):
    held = threading.Event()
    release = threading.Event()

    @contextmanager
    def lease(job, stopped):
        held.set()
        while not release.wait(0.01):
            if stopped.is_set():
                raise InterruptedError()
        yield

    adapter = CountingAdapter()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter}, acquire_gpu=lease)
    manager.start()
    try:
        created = manager.create(request())
        assert held.wait(2)
        manager.action(created["id"], "pause")
        assert wait(manager, created["id"])["status"] == "paused"
        assert adapter.calls == []
    finally:
        manager.close()


def test_cross_document_reuse_only_renders_changed_text(tmp_path):
    adapter = CountingAdapter()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    manager.start()
    try:
        first = manager.create(request("第一固定片段。第二原始片段。"))
        assert wait(manager, first["id"])["status"] == "completed"
        before = list(adapter.calls)
        second = manager.create(request("第一固定片段。第二修改片段。"))
        assert wait(manager, second["id"])["status"] == "completed"
        assert adapter.calls[len(before):] == ["第二修改片段。"]
    finally:
        manager.close()


def test_keep_segments_false_does_not_delete_another_jobs_audio(tmp_path):
    adapter = CountingAdapter()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    manager.start()
    try:
        first = manager.create(request("共享音频需要保留。"))
        original = wait(manager, first["id"])
        assert original["status"] == "completed"
        paths = [Path(s["outputPath"]) for s in original["segments"]]
        second = manager.create(request("共享音频需要保留。", output={"keepSegments": False}))
        discarded = wait(manager, second["id"])
        deadline = time.monotonic() + 3
        while discarded.get("segmentsCleanupPending") and time.monotonic() < deadline:
            time.sleep(0.01)
            discarded = manager.get(second["id"])
        assert discarded["status"] == "completed"
        assert discarded["segmentsRetained"] is False
        assert discarded["progress"] == 1
        assert all(s["outputPath"] is None for s in discarded["segments"])
        assert all(p.is_file() for p in paths)
        assert manager.output_file(second["id"]).is_file()
        with pytest.raises(LongformConflict, match="关闭了保留分段"):
            manager.action(second["id"], "reexport")
    finally:
        manager.close()


def test_immediate_stop_discards_unaccepted_attempt(tmp_path):
    class Delayed(CountingAdapter):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.released = threading.Event()

        def synthesize(self, text, output_path, parameters):
            self.started.set()
            assert self.released.wait(2)
            return super().synthesize(text, output_path, parameters)

        def cancel_current(self):
            self.released.set()

    adapter = Delayed()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    manager.start()
    try:
        created = manager.create(request("当前片段应该丢弃。"))
        assert adapter.started.wait(2)
        manager.action(created["id"], "stop", mode="now")
        paused = wait(manager, created["id"])
        assert paused["status"] == "paused"
        assert paused["acceptedSegments"] == 0
        manager.action(created["id"], "resume")
        assert wait(manager, created["id"])["status"] == "completed"
    finally:
        manager.close()


def test_legacy_parameters_are_validated_and_voice_is_mapped(tmp_path):
    from types import SimpleNamespace

    class Legacy:
        def __init__(self):
            self.seen = None

        def _validate_parameters(self, engine, params):
            assert "spk_audio_prompt" not in params
            assert params["speaker_audio"] == "voice.wav"
            self.seen = params
            return params

        def _validate_runtime_requirements(self, engine, params):
            assert self.seen is params

    class Voices:
        def get(self, identity):
            return SimpleNamespace(engine="indextts2", parameters={"spk_audio_prompt": "voice.wav", "emo_alpha": 0.6}, updated_at="revision")

    legacy = Legacy()
    manager = LongformManager(tmp_path / "v2", {"indextts2": CountingAdapter("indextts2")}, voice_store=Voices(), legacy_manager=legacy)
    try:
        created = manager.create({"engine": "indextts2", "text": "声音资料应映射。", "voiceProfileId": "voice-one"})
        assert created["params"]["speaker_audio"] == "voice.wav"
        assert created["params"]["emotion_alpha"] == 0.6
    finally:
        manager.close()


def test_gpu_busy_is_recoverable_without_model_retry(tmp_path):
    class BusyError(RuntimeError):
        gpu_busy = True

    class Busy(CountingAdapter):
        def synthesize(self, text, output_path, parameters):
            self.calls.append(text)
            raise BusyError("显卡正由外部任务使用")

    adapter = Busy()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    manager.start()
    try:
        created = manager.create(request())
        result = wait(manager, created["id"])
        assert result["status"] == "paused"
        assert len(adapter.calls) == 1
        assert result["counts"]["modelRetries"] == 0
        assert result["segments"][0]["status"] == "pending"
    finally:
        manager.close()


def test_real_database_lock_fails_bounded_then_recovers(tmp_path):
    import sqlite3
    from app.longform.models import StorageBlocked

    manager = LongformManager(tmp_path / "v2", {"breeze": CountingAdapter()})
    external = sqlite3.connect(manager.store.path)
    try:
        external.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(StorageBlocked):
            manager.create(request())
        assert time.monotonic() - started < 5
        external.rollback()
        created = manager.create(request())
        assert created["status"] == "queued"
        backup = tmp_path / "backup.db"
        manager.store.backup(backup)
        with sqlite3.connect(backup) as saved:
            assert saved.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    finally:
        external.close()
        manager.close()


def test_sse_pushes_only_changed_summaries_and_exits_on_disconnect(tmp_path):
    import asyncio
    import json
    from app.longform.api import stream_job_updates

    manager = LongformManager(tmp_path / "v2", {"breeze": CountingAdapter()})
    created = manager.create(request())

    class Connection:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    async def consume():
        connection = Connection()
        stream = stream_job_updates(manager, connection, interval=0)
        first = await anext(stream)
        body = json.loads(first.removeprefix("data: ").strip())
        assert body["type"] == "jobs"
        assert body["items"][0]["id"] == created["id"]
        assert not ({"text", "segments", "params", "renderIdentity"} & body["items"][0].keys())
        manager.action(created["id"], "pause")
        second = json.loads((await anext(stream)).removeprefix("data: ").strip())
        assert second["items"][0]["status"] == "paused"
        connection.disconnected = True
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    try:
        asyncio.run(consume())
    finally:
        manager.close()


def test_new_worker_clears_persisted_eta_until_five_samples(tmp_path):
    class RuntimeAdapter(CountingAdapter):
        token = "new-worker"
        warmed = False

        def runtime_snapshot(self, lines=20):
            return {"instanceToken": self.token, "warmed": self.warmed}

    adapter = RuntimeAdapter()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    try:
        created = manager.create(request())
        manager.action(created["id"], "pause")
        job = manager.store.get_job(created["id"])
        job["timing"].update(etaLowSeconds=100, etaHighSeconds=200)
        manager.store.save_job(job)
        manager.eta_samples[job["id"]] = ("old-worker", [1.0] * 5)
        resumed = manager.action(job["id"], "resume")
        assert resumed["timing"]["etaLowSeconds"] is None
        segment = manager._segments(job["id"])[0]
        for _ in range(4):
            manager._update_eta(job["id"], segment, 1.0, {})
            assert manager.get(job["id"])["timing"]["etaHighSeconds"] is None
        manager._update_eta(job["id"], segment, 1.0, {})
        assert manager.get(job["id"])["timing"]["etaHighSeconds"] is not None
    finally:
        manager.close()


def test_same_hot_worker_keeps_eta_across_pause_resume(tmp_path):
    class RuntimeAdapter(CountingAdapter):
        def runtime_snapshot(self, lines=20):
            return {"instanceToken": "same-worker", "warmed": True}

    manager = LongformManager(tmp_path / "v2", {"breeze": RuntimeAdapter()})
    try:
        created = manager.create(request())
        manager.action(created["id"], "pause")
        job = manager.store.get_job(created["id"])
        job["timing"].update(etaLowSeconds=100, etaHighSeconds=200)
        manager.store.save_job(job)
        manager.eta_samples[job["id"]] = ("same-worker", [1.0] * 5)
        resumed = manager.action(job["id"], "resume")
        assert resumed["timing"]["etaLowSeconds"] == 100
        assert len(manager.eta_samples[job["id"]][1]) == 5
        manager.action(job["id"], "pause")
        manager.eta_samples[job["id"]] = ("same-worker", [1.0] * 2)
        resumed = manager.action(job["id"], "resume")
        assert resumed["timing"]["etaLowSeconds"] is None
        assert len(manager.eta_samples[job["id"]][1]) == 2
    finally:
        manager.close()


def test_failed_completion_commit_never_discards_recoverable_renders(tmp_path, monkeypatch):
    from app.longform.models import StorageBlocked

    adapter = CountingAdapter()
    manager = LongformManager(tmp_path / "v2", {"breeze": adapter})
    original_save = manager.store.save_job
    failed = []

    def fail_completed_once(job, connection=None):
        if job["status"] == "completed" and not failed:
            failed.append(True)
            raise StorageBlocked("completion commit blocked")
        return original_save(job, connection)

    monkeypatch.setattr(manager.store, "save_job", fail_completed_once)
    manager.start()
    try:
        created = manager.create(request("成品状态保存失败时不能删片段。", output={"keepSegments": False}))
        result = wait(manager, created["id"])
        assert result["status"] == "storage_blocked"
        assert failed
        assert all(s["outputPath"] and Path(s["outputPath"]).is_file() for s in result["segments"] if s["status"] == "accepted")
        assert any(s["status"] == "accepted" for s in result["segments"])
        calls = len(adapter.calls)
        manager.action(created["id"], "resume")
        assert wait(manager, created["id"])["status"] == "completed"
        assert len(adapter.calls) == calls
    finally:
        manager.close()


def test_cleanup_database_failure_keeps_published_result_completed(tmp_path, monkeypatch):
    from app.longform.models import StorageBlocked

    manager = LongformManager(tmp_path / "v2", {"breeze": CountingAdapter()})
    original_save = manager.store.save_segment
    attempted = []

    def block_ref_cleanup(job_id, segment, connection=None):
        if segment.get("retained") is False:
            attempted.append(True)
            raise StorageBlocked("reference cleanup blocked")
        return original_save(job_id, segment, connection)

    monkeypatch.setattr(manager.store, "save_segment", block_ref_cleanup)
    manager.start()
    try:
        created = manager.create(request("清理失败不能撤销完整成品。", output={"keepSegments": False}))
        deadline = time.monotonic() + 5
        while not attempted and time.monotonic() < deadline:
            time.sleep(0.01)
        assert attempted
        result = wait(manager, created["id"])
        assert result["status"] == "completed"
        assert result["segmentsRetained"] is True and result["segmentsCleanupPending"] is True
        assert manager.output_file(created["id"]).is_file()
        assert all(s["outputPath"] and Path(s["outputPath"]).is_file() for s in result["segments"] if s["status"] == "accepted")
        with pytest.raises(LongformConflict, match="关闭了保留分段"):
            manager.action(created["id"], "reexport")
    finally:
        manager.close()
