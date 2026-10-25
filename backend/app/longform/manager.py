from __future__ import annotations

import hashlib
import os
import queue
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path

from ..audio import merge_wav_files
from .models import LongformConflict, LongformCreate, OutputOptions, SegmentRejected, StorageBlocked
from .segmentation import digest, plan_text, split_segment
from .store import LongformStore

ACTIVE = {"queued", "acquiring_gpu", "preparing", "loading", "warming", "generating", "pausing", "merging", "exporting", "verifying"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def utc_delta(start, end):
    try:
        value = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        return value if value >= 0 else None
    except (TypeError, ValueError):
        return None


def file_sha(path):
    result = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def validate_audio(path: Path, metadata=None):
    import numpy as np
    import soundfile as sf

    metadata = metadata or {}
    if metadata.get("possibly_truncated") or metadata.get("possiblyTruncated"):
        raise SegmentRejected("引擎标记此片段可能被截断")
    info = sf.info(str(path))
    if info.frames <= 0 or info.samplerate <= 0 or info.channels != 1:
        raise SegmentRejected("音频为空、采样率非法或不是单声道")
    if info.duration < 0.02 or info.duration > 300:
        raise SegmentRejected("单段音频时长不在安全范围内")
    peak = 0.0
    frames = 0
    with sf.SoundFile(str(path)) as source:
        for block in source.blocks(blocksize=65536, dtype="float32"):
            if not np.isfinite(block).all():
                raise SegmentRejected("音频包含非法浮点数")
            peak = max(peak, float(np.max(np.abs(block))))
            frames += len(block)
    if frames != info.frames or peak < 1e-7:
        raise SegmentRejected("音频被截断或没有有效声音")
    return {"sampleRate": info.samplerate, "frames": info.frames,
            "durationSeconds": info.duration, "channels": info.channels,
            "bytes": path.stat().st_size, "sha256": file_sha(path)}


class LongformManager:
    def __init__(self, root, adapters, voice_store=None, legacy_manager=None, acquire_gpu=None, *, ffmpeg_path=None, on_terminal=None):
        self.root = Path(root).resolve()
        self.store = LongformStore(self.root)
        self.adapters = adapters
        self.voice_store = voice_store
        self.legacy_manager = legacy_manager
        self.acquire_gpu = acquire_gpu
        self.ffmpeg_path = Path(ffmpeg_path).resolve() if ffmpeg_path else None
        self.on_terminal = on_terminal
        self.compatibility_errors = {}
        self.lock = threading.RLock()
        self.pending = queue.Queue()
        self.enqueued = set()
        self.stop_event = threading.Event()
        self.interrupts = {}
        self.current_id = None
        self.thread = None
        self._closed = False
        self._draining = False
        self.heartbeat_thread = None
        self.storage_errors = {}
        self.cleanup_warnings = {}
        self._committed_completions = set()
        self.eta_samples = {}
        self.active_sessions = {}
        self._created_ids = set()
        self._recovered = False
        for directory in ("documents", "staging", "renders", "outputs", "backups"):
            (self.root / directory).mkdir(exist_ok=True)
    def _recover_interrupted(self):
        if self._recovered:
            return
        for job in self.store.list_jobs():
            if job["status"] in ACTIVE and job["id"] not in self._created_ids:
                job.update(status="interrupted", phase="interrupted", error="上次服务在任务完成前退出，已有片段将在继续时校验", requestedAction=None)
                job["interruptedAt"] = job.get("lastSeenAt") or job["updatedAt"]
                self._save(job)
        self._recovered = True
        for job in self.store.list_jobs():
            if job["status"] == "completed" and job.get("segmentsCleanupPending"):
                self._cleanup_after_completion(job["id"])

    def start(self):
        if self._closed:
            raise RuntimeError("任务服务已关闭")
        if self.thread and self.thread.is_alive():
            return
        self._recover_interrupted()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._worker, name="longform-queue", daemon=True)
        self.thread.start()
        self.heartbeat_thread = threading.Thread(target=self._heartbeat, name="longform-heartbeat", daemon=True)
        self.heartbeat_thread.start()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.stop_event.set()
        if self.current_id:
            self.interrupts.setdefault(self.current_id, threading.Event()).set()
            try:
                job = self.store.get_job(self.current_id)
                self.adapters[job["engine"]].cancel_current()
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=5)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2)
        # If an engine ignores cancellation, leave DB open for its daemon thread.
        # Startup recovery will still treat the persisted active state as interrupted.
        for job in self.store.list_jobs() if self._recovered or self._created_ids else []:
            if job["status"] in ACTIVE:
                job.update(status="interrupted", phase="interrupted", interruptedAt=utc(), requestedAction=None)
                self._save(job)
        if not self.thread or not self.thread.is_alive():
            self.store.close()

    def _save(self, job):
        job["updatedAt"] = utc()
        job["version"] = job.get("version", 0) + 1
        self.store.save_job(job)
        self._notify_terminal(job)

    def _notify_terminal(self, job):
        if callable(self.on_terminal) and job["status"] in {"completed", "failed", "cancelled"} and not job.get("segmentsCleanupPending"):
            try:
                self.on_terminal(self._payload(job, True))
                self.compatibility_errors.pop(job["id"], None)
            except Exception as exc:
                # A legacy compatibility export must not invalidate v2 output.
                self.compatibility_errors[job["id"]] = str(exc)

    def _phase(self, job_id, phase, error=None):
        with self.lock:
            job = self.store.get_job(job_id)
            job.update(status=phase, phase=phase, error=error, lastSeenAt=utc())
            self._save(job)
            self.store.event(job_id, utc(), phase, {"error": error} if error else {})
            return job

    def _params(self, request):
        params = dict(request.params)
        voice_revision = None
        if request.voice_profile_id:
            if self.voice_store is None:
                raise ValueError("声音资料库不可用")
            profile = self.voice_store.get(request.voice_profile_id)
            engine = profile.get("engine") if isinstance(profile, dict) else profile.engine
            if engine != request.engine:
                raise ValueError("声音资料属于另一个引擎，不能混用")
            values = profile.get("parameters", {}) if isinstance(profile, dict) else profile.parameters
            if request.engine == "breeze":
                params.update(values)
            else:
                from ..parameters import ENGINE_PARAMETERS
                allowed = {item["name"] for item in ENGINE_PARAMETERS[request.engine]}
                mapped = {key: value for key, value in values.items() if key in allowed}
                if request.engine == "indextts2":
                    aliases = {"spk_audio_prompt": "speaker_audio", "emo_audio_prompt": "emotion_audio", "emo_alpha": "emotion_alpha", "emo_text": "emotion_text"}
                elif request.engine == "voxcpm":
                    aliases = {"reference_wav_path": "reference_audio", "prompt_wav_path": "prompt_audio", "voice_instruction": "control"}
                else:
                    from ..voices import gpt_voice_parameters_to_api
                    aliases = {}
                    mapped.update(gpt_voice_parameters_to_api(values))
                mapped.update({target: values[key] for key, target in aliases.items() if key in values})
                params.update(mapped)
            revision = profile.get("updatedAt") if isinstance(profile, dict) else getattr(profile, "updated_at", None)
            voice_revision = [request.voice_profile_id, revision]
        if request.engine == "breeze":
            params.setdefault("performance_mode", "accelerated")
            params.setdefault("cfg_scale", 1.0)
            params.setdefault("seed", 42)
            params.setdefault("temperature", 0.9)
            params.setdefault("top_k", 50)
            params.setdefault("top_p", 1.0)
            params.setdefault("repetition_penalty", 1.1)
            params.setdefault("max_new_tokens", 600)
        elif self.legacy_manager is not None:
            params = self.legacy_manager._validate_parameters(request.engine, params)
            self.legacy_manager._validate_runtime_requirements(request.engine, params)
        return params, voice_revision

    def _identity(self, engine, params, plan_id, voice_revision=None):
        adapter = self.adapters[engine]
        identity_fn = getattr(adapter, "cache_identity", None)
        runtime_fn = getattr(adapter, "runtime_fingerprint", None)
        if callable(identity_fn):
            runtime = identity_fn(params)
            scope = None
        elif callable(runtime_fn):
            runtime = runtime_fn()
            scope = None if engine == "breeze" else plan_id
        else:
            runtime = {"adapter": type(adapter).__module__ + "." + type(adapter).__name__}
            scope = plan_id
        references = {}
        for key in ("ref_audio_path", "reference_audio", "ref_audio", "speaker_audio", "spk_audio_prompt", "reference_wav_path", "gpt_weights_path", "sovits_weights_path"):
            value = params.get(key)
            if value:
                path = Path(str(value))
                references[key] = file_sha(path) if path.is_file() else {"unavailablePath": str(value)}
        return {"engine": engine, "adapterVersion": 1, "runtime": runtime,
                "params": {k: v for k, v in params.items() if not k.startswith("_segment")},
                "referenceHashes": references, "voiceRevision": voice_revision,
                "reuseScope": scope, "contextDependency": "none" if engine == "breeze" else "same_plan_only"}

    def begin_draining(self):
        """Serialize the shutdown gate with the complete create/action commit."""
        with self.lock:
            previous = self._draining
            self._draining = True
            return previous

    def restore_draining(self, previous):
        with self.lock:
            self._draining = previous

    def _assert_accepting_work(self):
        if self._draining or self._closed:
            raise LongformConflict("任务服务正在停止，不接受新任务、继续或重新导出；请等待服务关闭后重新启动")

    @contextmanager
    def accepting_work(self):
        with self.lock:
            self._assert_accepting_work()
            yield

    def create(self, request):
        # Holding the same RLock until enqueue prevents a create which passed
        # preflight from committing after begin_draining has taken its snapshot.
        with self.accepting_work():
            return self._create_locked(request)

    def _create_locked(self, request):
        request = request if isinstance(request, LongformCreate) else LongformCreate.model_validate(request)
        if request.engine not in self.adapters:
            raise ValueError("未知引擎")
        if request.engine == "breeze" and not request.license_accepted:
            raise ValueError("请先确认 Breeze 模型的研究/非商业使用许可")
        status = self.adapters[request.engine].status()
        if not status.get("available"):
            raise ValueError(status.get("detail") or "引擎尚未配置")
        if request.output.format == "MP3" and not self._ffmpeg():
            raise ValueError("MP3 需要受管理的 FFmpeg 组件")
        plan = plan_text(request.text, request.engine, request.segmentation)
        params, voice_revision = self._params(request)
        identity = self._identity(request.engine, params, plan["planId"], voice_revision)
        document = self.root / "documents" / f"{plan['documentId']}.txt"
        if not document.exists():
            try:
                with document.open("xb") as destination:
                    destination.write(request.text.encode("utf-8"))
                    destination.flush()
                    os.fsync(destination.fileno())
            except FileExistsError:
                pass
        if file_sha(document) != plan["documentId"]:
            raise StorageBlocked("原文文件校验失败")
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "title": request.title or "长文配音", "engine": request.engine,
               "status": "queued", "phase": "queued", "version": 0,
               "documentId": plan["documentId"], "planId": plan["planId"], "documentPath": str(document),
               "sourceCharacters": len(request.text), "params": params, "voiceProfileId": request.voice_profile_id,
               "voiceRevision": voice_revision, "renderIdentity": identity,
               "output": request.output.model_dump(by_alias=True), "segmentation": request.segmentation.model_dump(by_alias=True),
               "createdAt": utc(), "updatedAt": utc(), "startedAt": None, "completedAt": None,
               "lastSeenAt": None, "requestedAction": None, "outputPath": None, "outputSha256": None,
               "error": None, "timing": {"activeSeconds": 0.0, "generationSeconds": 0.0,
               "loadingSeconds": 0.0, "warmingSeconds": 0.0, "preparingSeconds": 0.0,
               "pauseSeconds": 0.0, "unknownSeconds": 0.0, "clockAnomaly": False,
               "totalWallSeconds": None, "etaLowSeconds": None, "etaHighSeconds": None},
               "counts": {"scheduled": 0, "modelCalls": 0, "modelRetries": 0, "ioRecoveries": 0}}
        with self.store.transaction() as conn:
            self.store.save_job(job, conn)
            for segment in plan["segments"]:
                segment.update(attempts=0, outputPath=None, sha256=None, error=None)
                self.store.save_segment(job_id, segment, conn)
        self.store.event(job_id, utc(), "created", {"exactCoverage": True, "segments": plan["totalSegments"]})
        self._created_ids.add(job_id)
        self._enqueue(job_id)
        return self.get(job_id)

    def _enqueue(self, job_id):
        with self.lock:
            if job_id not in self.enqueued:
                self.enqueued.add(job_id)
                self.interrupts[job_id] = threading.Event()
                self.pending.put(job_id)

    def _segments(self, job_id):
        return [s for s in self.store.segments(job_id) if s["status"] != "superseded"]

    def _payload(self, job, detail=False):
        segments = self._segments(job["id"]) if detail else None
        counts = self.store.segment_counts(job["id"])
        accepted, total = counts["accepted"], counts["total"]
        result = dict(job)
        result.update(acceptedSegments=accepted, totalSegments=total,
                      progress=accepted / total if total else 0.0)
        timing = dict(job["timing"])
        session = self.active_sessions.get(job["id"])
        if session:
            timing["activeSeconds"] += max(0.0, time.monotonic() - session["last"])
        timing["totalWallSeconds"] = utc_delta(job["startedAt"], job.get("completedAt") or utc()) if job["startedAt"] else None
        if timing["totalWallSeconds"] is None and job["startedAt"]:
            timing["clockAnomaly"] = True
        result["timing"] = timing
        if self.current_id == job["id"] and job["status"] in {"preparing", "generating", "loading", "warming"}:
            snapshot = getattr(self.adapters[job["engine"]], "runtime_snapshot", None)
            if callable(snapshot):
                runtime = snapshot(20)
                if runtime.get("phase") in {"validating", "loading", "warming", "generating"}:
                    result["phase"] = "preparing" if runtime["phase"] == "validating" else runtime["phase"]
                    result["status"] = result["phase"]
                result["runtime"] = {k: runtime.get(k) for k in ("phase", "instanceToken", "warmed", "exitConfirmed")}
        result["storage"] = {"sqliteVersion": __import__("sqlite3").sqlite_version, "journalMode": self.store.journal_mode}
        if job.get("stopUnconfirmed"):
            snapshot = getattr(self.adapters[job["engine"]], "runtime_snapshot", None)
            runtime = snapshot(20) if callable(snapshot) else {}
            if runtime.get("phase") == "stop_unconfirmed":
                result.update(status="pausing", phase="stop_unconfirmed", stopUnconfirmed=True,
                              error="已请求停止，尚未确认 worker 退出；请在引擎诊断中检查")
            else:
                result["stopUnconfirmed"] = False
        if job["id"] in self.compatibility_errors:
            result["compatibilityExportError"] = self.compatibility_errors[job["id"]]
        if job["id"] in self.cleanup_warnings:
            result["cleanupWarning"] = self.cleanup_warnings[job["id"]]
        if job["id"] in self.storage_errors:
            result.update(status="storage_blocked", phase="storage_blocked", error=self.storage_errors[job["id"]])
        if detail:
            result["text"] = self._document(job)
            result["segments"] = segments
        result["totalSegmentRecords"] = counts["records"]
        for private in ("renderIdentity", "documentPath", "requestedAction"):
            result.pop(private, None)
        return result

    def _document(self, job):
        with open(job["documentPath"], "r", encoding="utf-8", newline="") as source:
            text = source.read()
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != job["documentId"]:
            raise StorageBlocked("原文文件已被修改，拒绝继续")
        return text

    def get(self, job_id):
        return self._payload(self.store.get_job(job_id), True)

    def list(self):
        return [self._payload(j) for j in self.store.list_jobs()]

    def segments(self, job_id, offset=0, limit=100):
        self.store.get_job(job_id)
        items = self._segments(job_id)
        return {"items": items[offset:offset + limit], "total": len(items)}

    def action(self, job_id, action, *, version=None, mode="after_segment", output=None, segment_ids=None, seed=None):
        with self.lock:
            if action in {"resume", "retry-segments", "reexport", "regenerate-segment"}:
                self._assert_accepting_work()
            job = self.store.get_job(job_id)
            if version is not None and job["version"] != version:
                raise LongformConflict("任务已被其他操作更新，请刷新后重试")
            if action in {"pause", "stop", "cancel"}:
                if job["status"] == "completed":
                    raise LongformConflict("已完成任务不能暂停或取消")
                if job["status"] in {"merging", "exporting", "verifying"}:
                    raise LongformConflict("正在合并、导出或校验，请等待当前发布阶段结束")
                if action in {"pause", "stop"} and job["status"] in {"paused", "interrupted", "failed", "storage_blocked"}:
                    return self.get(job_id)
                if action == "cancel" and job["status"] == "cancelled":
                    return self.get(job_id)
                requested = "cancel" if action == "cancel" else ("pause_now" if action == "stop" and mode == "now" else "pause")
                job["requestedAction"] = requested
                if self.current_id == job_id:
                    job.update(status="pausing", phase="pausing")
                else:
                    state = "cancelled" if requested == "cancel" else "paused"
                    job.update(status=state, phase=state, pausedAt=utc())
                self._save(job)
                self.interrupts.setdefault(job_id, threading.Event()).set()
                if self.current_id == job_id and requested in {"pause_now", "cancel"}:
                    self.adapters[job["engine"]].cancel_current()
            elif action in {"resume", "retry-segments", "reexport", "regenerate-segment"}:
                if job["status"] in ACTIVE:
                    raise LongformConflict("任务仍在运行")
                if job["status"] == "cancelled":
                    raise LongformConflict("已取消任务请复制为新任务，验收音频仍可复用")
                if job.get("segmentsRetained") is False or job["status"] == "completed" and not job["output"]["keepSegments"]:
                    raise LongformConflict("创建任务时关闭了保留分段，无法续跑、局部重做或重新导出；请复制为新任务")
                if job.get("stopUnconfirmed"):
                    snapshot = getattr(self.adapters[job["engine"]], "runtime_snapshot", None)
                    if callable(snapshot) and snapshot(20).get("phase") == "stop_unconfirmed":
                        raise LongformConflict("尚未确认旧 worker 退出，不能继续或重做")
                    job["stopUnconfirmed"] = False
                self._document(job)
                if action == "reexport":
                    if output:
                        options = OutputOptions.model_validate(output)
                        if options.format == "MP3" and not self._ffmpeg():
                            raise ValueError("MP3 需要受管理的 FFmpeg")
                        job["output"] = options.model_dump(by_alias=True)
                if action == "regenerate-segment":
                    if not segment_ids or seed is None:
                        raise ValueError("单段重做需要 segmentIds 和显式 seed")
                    for segment in self._segments(job_id):
                        if segment["id"] in segment_ids:
                            segment.update(status="pending", seedOverride=seed, outputPath=None, sha256=None)
                            self.store.save_segment(job_id, segment)
                if job.get("pausedAt"):
                    delta = utc_delta(job["pausedAt"], utc())
                    if delta is None:
                        job["timing"]["clockAnomaly"] = True
                    else:
                        job["timing"]["pauseSeconds"] += delta
                    job["pausedAt"] = None
                if job.get("interruptedAt"):
                    delta = utc_delta(job["interruptedAt"], utc())
                    if delta is None:
                        job["timing"]["clockAnomaly"] = True
                        job["timing"]["unknownSeconds"] = None
                    elif job["timing"].get("unknownSeconds") is not None:
                        job["timing"]["unknownSeconds"] += delta
                    job["interruptedAt"] = None
                self._reset_eta_if_cold(job)
                job.update(status="queued", phase="queued", requestedAction=None, error=None, completedAt=None)
                self.storage_errors.pop(job_id, None)
                self._save(job)
                self._enqueue(job_id)
            else:
                raise ValueError("未知任务操作")
            self.store.event(job_id, utc(), action, {"mode": mode})
        return self.get(job_id)

    def _requested(self, job_id):
        if self.stop_event.is_set():
            return "shutdown"
        return self.store.get_job(job_id).get("requestedAction")

    def _finish_requested(self, job_id):
        requested = self._requested(job_id)
        if not requested:
            return False
        state = "interrupted" if requested == "shutdown" else "cancelled" if requested == "cancel" else "paused"
        job = self._phase(job_id, state)
        if requested in {"pause_now", "cancel", "shutdown"}:
            snapshot = getattr(self.adapters[job["engine"]], "runtime_snapshot", None)
            if callable(snapshot):
                job["stopUnconfirmed"] = snapshot(20).get("phase") == "stop_unconfirmed"
        job.update(requestedAction=None, pausedAt=utc() if state == "paused" else None,
                   interruptedAt=utc() if state == "interrupted" else None)
        self._save(job)
        return True

    def _worker(self):
        while not self.stop_event.is_set():
            try:
                job_id = self.pending.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                job = self.store.get_job(job_id)
                if job["status"] != "queued":
                    continue
                self.current_id = job_id
                self._run(job_id)
            except (OSError, __import__("sqlite3").Error) as exc:
                if job_id in self._committed_completions:
                    self.cleanup_warnings[job_id] = "成品已保存，后续记账或清理暂未完成：" + str(exc)
                else:
                    self.storage_errors[job_id] = str(exc)
                    try:
                        self._phase(job_id, "storage_blocked", str(exc))
                    except Exception:
                        pass
            except Exception as exc:
                try:
                    if not self._finish_requested(job_id):
                        self._phase(job_id, "failed", str(exc))
                except Exception:
                    pass
            finally:
                self.current_id = None
                with self.lock:
                    self.enqueued.discard(job_id)
                    if not self.stop_event.is_set() and self.store.get_job(job_id)["status"] == "queued":
                        self._enqueue(job_id)
                self.pending.task_done()

    def _heartbeat(self):
        while not self.stop_event.wait(10):
            if self.current_id:
                try:
                    with self.lock:
                        job = self.store.get_job(self.current_id)
                        job["lastSeenAt"] = utc()
                        self._add_active(job)
                        self.store.save_job(job)
                except Exception:
                    pass
            # Adapter owns idle unloading: it can identify its exact worker and
            # avoid closing a shared legacy adapter while another job uses it.

    def _run(self, job_id):
        job = self._phase(job_id, "acquiring_gpu")
        context = self.acquire_gpu(job, self.interrupts[job_id]) if self.acquire_gpu else nullcontext()
        try:
            with context:
                if self._finish_requested(job_id):
                    return
                self._run_acquired(job_id)
        except InterruptedError:
            if not self._finish_requested(job_id):
                self._phase(job_id, "interrupted", "等待显卡时中断")

    def _run_acquired(self, job_id):
        self._committed_completions.discard(job_id)
        job = self.store.get_job(job_id)
        source = self._document(job)
        identity = self._identity(job["engine"], job["params"], job["planId"], job.get("voiceRevision"))
        if identity != job["renderIdentity"]:
            raise ValueError("引擎、模型或参考素材指纹发生变化，请复制为新任务")
        session_id = uuid.uuid4().hex
        session_started, monotonic_start = utc(), time.monotonic()
        with self.lock:
            job = self.store.get_job(job_id)
            job["startedAt"] = job["startedAt"] or utc()
            job["sessionId"] = session_id
            self._reset_eta_if_cold(job)
            self.active_sessions[job_id] = {"id": session_id, "start": monotonic_start, "last": monotonic_start, "startedAt": session_started}
            self._save(job)
        try:
            self._phase(job_id, "generating")
            verified = set()
            while True:
                if self._finish_requested(job_id):
                    return
                segments = self._segments(job_id)
                pending = []
                for segment in segments:
                    if segment["status"] == "skipped":
                        continue
                    params = dict(job["params"])
                    if "seedOverride" in segment:
                        params["seed"] = segment["seedOverride"]
                    key = digest([identity, segment["requestText"], params.get("seed")])
                    if segment["status"] == "accepted" and segment.get("renderKey") == key:
                        token = (segment["id"], segment.get("sha256"))
                        if token in verified or self._valid_saved(segment):
                            verified.add(token)
                            continue
                    if segment["status"] == "accepted":
                        segment.update(status="damaged" if segment.get("renderKey") == key else "stale", error="续跑校验未通过")
                        self.store.save_segment(job_id, segment)
                    pending.append((segment, params, key))
                if not pending:
                    break
                segment, params, key = pending[0]
                if segment["status"] == "produced" and segment.get("renderKey") == key and self._valid_saved(segment):
                    self._publish(job_id, segment, key, segment.get("metadata", {}))
                    continue
                cached = self.store.render(key)
                if cached and self._valid_saved({"outputPath": cached["path"], "sha256": cached["sha256"]}):
                    segment.update(status="accepted", renderKey=key, outputPath=cached["path"], sha256=cached["sha256"],
                                   durationSeconds=cached["metadata"].get("durationSeconds"), metadata=cached["metadata"], error=None)
                    self.store.save_segment(job_id, segment)
                    self.store.event(job_id, utc(), "segment_reused", {"segmentId": segment["id"]})
                    continue
                self._synthesize(job_id, segment, params, key, source)
                if self.store.get_job(job_id)["status"] in {"paused", "interrupted", "cancelled"}:
                    return
            if self._finish_requested(job_id):
                return
            self._export(job_id)
        finally:
            elapsed = time.monotonic() - monotonic_start
            ended = utc()
            with self.lock:
                latest = self.store.get_job(job_id)
                self._add_active(latest)
                delta = utc_delta(session_started, ended)
                if delta is None or abs(delta - elapsed) > 10:
                    latest["timing"]["clockAnomaly"] = True
                self._save(latest)
                self.store.record("sessions", session_id, job_id, {"startedAt": session_started, "endedAt": ended, "activeSeconds": elapsed})
                self.active_sessions.pop(job_id, None)

    def _valid_saved(self, segment):
        try:
            path = Path(segment.get("outputPath") or "")
            return path.is_file() and file_sha(path) == segment.get("sha256")
        except OSError:
            return False

    def _synthesize(self, job_id, segment, params, key, source):
        job = self.store.get_job(job_id)
        adapter = self.adapters[job["engine"]]
        for retry in range(3):
            if self._finish_requested(job_id):
                return
            attempt_id = uuid.uuid4().hex
            output = self.root / "staging" / f"{key}.{attempt_id}.wav.part"
            segment.update(status="running", attempts=segment.get("attempts", 0) + 1, renderKey=key, error=None)
            self.store.save_segment(job_id, segment)
            with self.lock:
                job = self.store.get_job(job_id)
                job["counts"]["scheduled"] += 1
                job["counts"]["modelCalls"] += 1
                job["counts"]["modelRetries"] += int(retry > 0)
                self._save(job)
            start_utc, started = utc(), time.monotonic()
            attempt = {"startedAt": start_utc, "status": "running", "path": str(output)}
            self.store.record("attempts", attempt_id, job_id, attempt, segment["id"])
            monitor_stop, phase_times = threading.Event(), {}
            monitor = threading.Thread(target=self._track_phases, args=(job_id, adapter, monitor_stop, phase_times), daemon=True)
            monitor.start()
            try:
                metadata = adapter.synthesize(segment["requestText"], output, {**params, "_segment_index": segment["index"]}) or {}
                if self._requested(job_id) in {"pause_now", "cancel", "shutdown"}:
                    segment.update(status="pending", error="当前尝试已停止，不作为完成音频")
                    self.store.save_segment(job_id, segment)
                    self._finish_requested(job_id)
                    return
                quality = validate_audio(output, metadata)
                segment.update(status="produced", outputPath=str(output), sha256=quality["sha256"],
                               durationSeconds=quality["durationSeconds"], metadata={**metadata, **quality}, error=None)
                self.store.save_segment(job_id, segment)
                self._publish(job_id, segment, key, segment["metadata"])
                attempt.update(status="accepted", metadata=segment["metadata"])
                self._update_eta(job_id, segment, time.monotonic() - started, metadata)
                return
            except (OSError, __import__("sqlite3").Error):
                raise
            except Exception as exc:
                if self._requested(job_id):
                    segment.update(status="pending", error="当前尝试已停止")
                    self.store.save_segment(job_id, segment)
                    self._finish_requested(job_id)
                    return
                message = str(exc)
                if getattr(exc, "gpu_busy", False) or any(value in message.lower() for value in ("显存不足", "显卡空闲显存不足", "out of memory")):
                    segment.update(status="pending", error=message)
                    self.store.save_segment(job_id, segment)
                    paused = self._phase(job_id, "paused", message + "；请释放显卡后继续")
                    paused["pausedAt"] = utc()
                    self._save(paused)
                    attempt.update(status="resource_blocked", error=message)
                    return
                segment.update(status="rejected", error=str(exc))
                attempt.update(status="rejected", error=str(exc))
                self.store.save_segment(job_id, segment)
                budget_failure = getattr(exc, "budget_exceeded", False) or "上下文预算" in message or "context budget" in message.lower()
                if retry == 2 or budget_failure:
                    children = split_segment(segment, source)
                    if children:
                        with self.store.transaction() as conn:
                            segment["status"] = "superseded"
                            self.store.save_segment(job_id, segment, conn)
                            for child in children:
                                child.update(attempts=0, outputPath=None, sha256=None, error=None)
                                self.store.save_segment(job_id, child, conn)
                        self.store.event(job_id, utc(), "segment_split", {"segmentId": segment["id"]})
                    else:
                        segment["status"] = "needs_attention"
                        self.store.save_segment(job_id, segment)
                        paused = self._phase(job_id, "paused", "自动重试和拆分已达上限，请检查待处理片段")
                        paused["pausedAt"] = utc()
                        self._save(paused)
                    return
            finally:
                elapsed = time.monotonic() - started
                monitor_stop.set()
                monitor.join(timeout=2)
                attempt.update(endedAt=utc(), elapsedSeconds=elapsed, hostPhaseSeconds=phase_times)
                self.store.record("attempts", attempt_id, job_id, attempt, segment["id"])
                with self.lock:
                    latest = self.store.get_job(job_id)
                    latest["timing"]["generationSeconds"] += phase_times.get("generating", elapsed)
                    for phase, key_name in (("loading", "loadingSeconds"), ("warming", "warmingSeconds"), ("preparing", "preparingSeconds")):
                        latest["timing"][key_name] = latest["timing"].get(key_name, 0.0) + phase_times.get(phase, 0.0)
                    self._add_active(latest)
                    self._save(latest)

    def _add_active(self, job):
        session = self.active_sessions.get(job["id"])
        if not session:
            return
        now = time.monotonic()
        job["timing"]["activeSeconds"] += max(0.0, now - session["last"])
        session["last"] = now
        job["lastSeenAt"] = utc()
        self.store.record("sessions", session["id"], job["id"],
                          {"startedAt": session["startedAt"], "lastSeenAt": job["lastSeenAt"],
                           "activeSeconds": now - session["start"], "endedAt": None})

    def _track_phases(self, job_id, adapter, stopped, durations):
        """Measure worker-reported phase boundaries using the host monotonic clock.

        Polling uncertainty is at most one sample interval; worker perf counters
        remain auxiliary metadata and are never added to host measurements.
        """
        snapshot = getattr(adapter, "runtime_snapshot", None)
        phase, last = "generating", time.monotonic()
        while True:
            try:
                runtime = snapshot(20) if callable(snapshot) else {}
                reported = runtime.get("phase", "generating")
                next_phase = reported if reported in {"loading", "warming", "generating"} else "preparing"
            except Exception:
                next_phase = phase
            now = time.monotonic()
            durations[phase] = durations.get(phase, 0.0) + now - last
            phase, last = next_phase, now
            try:
                with self.lock:
                    job = self.store.get_job(job_id)
                    if job["status"] in {"preparing", "generating", "loading", "warming"} and job["phase"] != next_phase:
                        self._phase(job_id, next_phase)
            except Exception:
                pass
            if stopped.wait(0.1):
                durations[phase] = durations.get(phase, 0.0) + time.monotonic() - last
                return

    def _publish(self, job_id, segment, key, metadata):
        source = Path(segment["outputPath"])
        destination = self.root / "renders" / f"{segment['sha256']}.wav"
        if source != destination:
            self._replace(source, destination, job_id)
        segment.update(status="accepted", outputPath=str(destination), error=None)
        self.store.save_render(key, destination, segment["sha256"], metadata, segment, job_id)
        self.store.event(job_id, utc(), "segment_accepted", {"segmentId": segment["id"], "sha256": segment["sha256"]})

    def _replace(self, source, destination, job_id):
        for retry in range(8):
            try:
                os.replace(source, destination)
                if retry:
                    with self.lock:
                        job = self.store.get_job(job_id)
                        job["counts"]["ioRecoveries"] += 1
                        self._save(job)
                return
            except PermissionError as exc:
                if retry == 7:
                    raise StorageBlocked(f"文件被占用，发布失败：{destination.name}") from exc
                time.sleep(min(0.025 * 2 ** retry, 0.5))

    def _ffmpeg(self):
        return self.ffmpeg_path if self.ffmpeg_path and self.ffmpeg_path.is_file() else None

    def _export(self, job_id):
        import soundfile as sf

        job = self._phase(job_id, "merging")
        segments = [s for s in self._segments(job_id) if s["status"] != "skipped"]
        if not segments or any(s["status"] != "accepted" or not self._valid_saved(s) for s in segments):
            raise SegmentRejected("合并前分段完整性校验失败")
        output = job["output"]
        rate = sf.info(segments[0]["outputPath"]).samplerate if output["sampleRate"] == "native" else output["sampleRate"]
        export_key = digest([[s["sha256"] for s in segments], output, "soxr-hq-v1", str(rate)])
        output_directory = self.root / "outputs" / job_id
        output_directory.mkdir(exist_ok=True)
        token = uuid.uuid4().hex
        wav_part = output_directory / f"{token}.wav.part"
        merge_wav_files([s["outputPath"] for s in segments], wav_part, sample_rate=rate, silence_ms=output["silenceMs"])
        self._phase(job_id, "exporting")
        fmt = output["format"]
        publish_source = wav_part
        if fmt == "FLAC":
            publish_source = output_directory / f"{token}.flac.part"
            with sf.SoundFile(str(wav_part)) as source, sf.SoundFile(str(publish_source), "w", samplerate=rate, channels=1, format="FLAC", subtype="PCM_16") as destination:
                for block in source.blocks(blocksize=65536, dtype="int16"):
                    destination.write(block)
        elif fmt == "MP3":
            ffmpeg = self._ffmpeg()
            if not ffmpeg:
                raise ValueError("受管理的 FFmpeg 不可用，分段已保留，可改格式重新导出")
            publish_source = output_directory / f"{token}.mp3.part"
            subprocess.run([str(ffmpeg), "-v", "error", "-nostdin", "-i", str(wav_part), "-codec:a", "libmp3lame", "-b:a", "192k", "-f", "mp3", str(publish_source)], check=True, capture_output=True, timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._phase(job_id, "verifying")
        info = sf.info(str(publish_source))
        expected_frames = sum(sf.info(s["outputPath"]).frames * rate / sf.info(s["outputPath"]).samplerate for s in segments)
        expected_frames += round(rate * output["silenceMs"] / 1000) * (len(segments) - 1)
        if info.channels != 1 or info.samplerate != rate or abs(info.frames - expected_frames) > max(rate * 0.15, len(segments) * 2):
            raise SegmentRejected("成品时长、采样率或声道校验失败")
        with publish_source.open("r+b") as source:
            os.fsync(source.fileno())
        sha = file_sha(publish_source)
        destination = output_directory / f"{export_key}.{fmt.lower()}"
        self._replace(publish_source, destination, job_id)
        with self.lock:
            latest = self.store.get_job(job_id)
            latest.update(status="completed", phase="completed", completedAt=utc(), outputPath=str(destination),
                          outputSha256=sha, exportKey=export_key, durationSeconds=info.duration, sampleRate=rate,
                          segmentsRetained=True, segmentsCleanupPending=not output["keepSegments"], error=None)
            self._save(latest)
            self._committed_completions.add(job_id)
        try:
            self.store.event(job_id, utc(), "completed", {"sha256": sha, "exportKey": export_key})
        except (OSError, __import__("sqlite3").Error) as exc:
            self.cleanup_warnings[job_id] = "成品已保存，完成事件暂未写入：" + str(exc)
        if not output["keepSegments"]:
            self._cleanup_after_completion(job_id)
        if wav_part.exists() and wav_part != destination:
            wav_part.unlink(missing_ok=True)

    def _discard_private_segments(self, job_id):
        """Drop only this job's references; never collect another job's audio."""
        with self.lock:
            job = self.store.get_job(job_id)
            if job["status"] != "completed" or not job.get("outputPath"):
                raise RuntimeError("只有持久化完成的成品可以清理片段")
            segments = self._segments(job_id)
            owned = {s["outputPath"] for s in segments if s.get("outputPath")}
            other_paths = set()
            for other in self.store.list_jobs():
                if other["id"] != job_id:
                    other_paths.update(s["outputPath"] for s in self.store.segments(other["id"]) if s.get("outputPath"))
            with self.store.transaction() as conn:
                for segment in segments:
                    if segment.get("outputPath"):
                        segment.update(outputPath=None, retained=False)
                        self.store.save_segment(job_id, segment, conn)
                for raw in owned - other_paths:
                    conn.execute("DELETE FROM renders WHERE path=?", (raw,))
                job.update(segmentsRetained=False, segmentsCleanupPending=False, updatedAt=utc(),
                           version=job.get("version", 0) + 1)
                job.pop("cleanupWarning", None)
                self.store.save_job(job, conn)
            self._notify_terminal(job)
            self.cleanup_warnings.pop(job_id, None)
            for raw in owned - other_paths:
                path = Path(raw)
                # Deletion is limited to verified direct children of our render root.
                if path.parent.resolve() == (self.root / "renders").resolve():
                    try:
                        path.unlink(missing_ok=True)
                    except OSError as exc:
                        raise OSError(f"分段引用已移除，暂时无法回收未引用文件：{exc}") from exc

    def _cleanup_after_completion(self, job_id):
        try:
            self._discard_private_segments(job_id)
        except Exception as exc:
            message = "完整成品已保存，分段清理未完成：" + str(exc)
            self.cleanup_warnings[job_id] = message
            try:
                with self.lock:
                    job = self.store.get_job(job_id)
                    job["cleanupWarning"] = message
                    self._save(job)
                    self.store.event(job_id, utc(), "cleanup_warning", {"message": message})
            except Exception:
                # The already committed completed row/output remains authoritative.
                pass

    def _update_eta(self, job_id, segment, elapsed, metadata):
        import statistics
        stored = self.store.get_job(job_id)
        snapshot = getattr(self.adapters[stored["engine"]], "runtime_snapshot", None)
        runtime = snapshot(20) if callable(snapshot) else {}
        identity = metadata.get("worker_instance_id") or metadata.get("instance_id") or runtime.get("instanceToken") or stored["sessionId"]
        previous_id, samples = self.eta_samples.get(job_id, (identity, []))
        if identity != previous_id:
            samples = []
        samples = (samples + [elapsed / max(1, len(segment["requestText"]))])[-20:]
        self.eta_samples[job_id] = (identity, samples)
        if len(samples) < 5:
            with self.lock:
                job = self.store.get_job(job_id)
                job["timing"].update(etaLowSeconds=None, etaHighSeconds=None)
                self._save(job)
            return
        remaining = sum(len(s["requestText"]) for s in self._segments(job_id) if s["status"] not in {"accepted", "skipped"})
        quantiles = statistics.quantiles(samples, n=4, method="inclusive")
        with self.lock:
            job = self.store.get_job(job_id)
            job["timing"].update(etaLowSeconds=remaining * quantiles[0], etaHighSeconds=remaining * quantiles[2])
            self._save(job)

    def _reset_eta_if_cold(self, job):
        """An ETA belongs to a measured hot worker, not to the document."""
        snapshot = getattr(self.adapters[job["engine"]], "runtime_snapshot", None)
        runtime = snapshot(20) if callable(snapshot) else {}
        identity = runtime.get("instanceToken")
        previous_id, samples = self.eta_samples.get(job["id"], (None, []))
        if runtime.get("warmed") and identity and previous_id == identity:
            if len(samples) < 5:
                job["timing"].update(etaLowSeconds=None, etaHighSeconds=None)
            return
        self.eta_samples.pop(job["id"], None)
        job["timing"].update(etaLowSeconds=None, etaHighSeconds=None)

    def output_file(self, job_id):
        job = self.store.get_job(job_id)
        if job["status"] != "completed" or not job.get("outputPath"):
            raise LongformConflict("成品尚未完成")
        path = Path(job["outputPath"])
        if not path.is_file() or file_sha(path) != job["outputSha256"]:
            raise StorageBlocked("成品文件缺失或已损坏，请重新导出")
        return path

    def segment_file(self, job_id, segment_id):
        for segment in self._segments(job_id):
            if segment["id"] == segment_id and segment["status"] == "accepted" and self._valid_saved(segment):
                return Path(segment["outputPath"])
        raise KeyError(segment_id)

    def capabilities(self):
        return {"formats": ["WAV", "FLAC"] + (["MP3"] if self._ffmpeg() else []), "maxHan": 100000,
                "maxCharacters": 500000, "nativeSampleRate": True, "resumeUnit": "segment",
                "sqliteVersion": __import__("sqlite3").sqlite_version, "journalMode": self.store.journal_mode}
