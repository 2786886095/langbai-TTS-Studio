from __future__ import annotations

import json
import queue
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from .adapters import EngineAdapter
from .audio import merge_wav_files
from .models import JobCreate, JobManifest, JobStatus, SegmentManifest, SegmentStatus, now_iso
from .parameters import ENGINE_PARAMETERS, defaults_for
from .segmenter import split_text
from .storage import JobStore


TEST_ONLY_PARAMETERS = {"mock_fail_segment_once", "mock_segment_delay_ms", "mock_sample_rate"}
INTERNAL_PARAMETERS = {"gpt_sovits": {"sample_steps_auto"}}


def title_from_text(text: str, limit: int = 36) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    collapsed = re.sub(r"^[，。！？、；：,.!?;:\-—_\s]+", "", collapsed)
    if not collapsed:
        return "语音任务"
    first_sentence = re.split(r"[。！？!?\n]", collapsed, maxsplit=1)[0].strip()
    candidate = first_sentence or collapsed
    return candidate[:limit].rstrip("，。！？、；：,.!?;: -—_") or "语音任务"


class EventBroker:
    def __init__(self):
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                pass

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(channel)


class JobManager:
    def __init__(self, store: JobStore, adapters: dict[str, EngineAdapter], *, mock_mode: bool = False):
        self.store = store
        self.adapters = adapters
        self.mock_mode = mock_mode
        self.events = EventBroker()
        self.session_id = uuid.uuid4().hex
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._cancelled: set[str] = set()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker_loop, name="tts-job-worker", daemon=True)
        self._thread.start()
        # A new application session starts with an empty queue. Interrupted work remains
        # in history as cancelled and can still be retried explicitly.
        for job in self.store.list():
            if job.status in (JobStatus.queued, JobStatus.running):
                for segment in job.segments:
                    if segment.status == SegmentStatus.running:
                        segment.status = SegmentStatus.failed
                        segment.error = "软件已关闭"
                job.status = JobStatus.cancelled
                job.error = "上次软件关闭后已从生成队列清除，可在历史记录中重试"
                job.updated_at = now_iso()
                self.store.save(job)

    def close(self) -> None:
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=5)
        for adapter in self.adapters.values():
            adapter.close()

    def _validate_parameters(self, engine: str, supplied: dict[str, Any]) -> dict[str, Any]:
        if engine not in ENGINE_PARAMETERS:
            raise ValueError(f"不支持的引擎: {engine}")
        allowed = {item["name"] for item in ENGINE_PARAMETERS[engine]}
        allowed |= INTERNAL_PARAMETERS.get(engine, set())
        if self.mock_mode:
            allowed |= TEST_ONLY_PARAMETERS
        unknown = sorted(set(supplied) - allowed)
        if unknown:
            raise ValueError(f"未知的 {engine} 参数: {', '.join(unknown)}")
        result = defaults_for(engine)
        result.update(supplied)
        if not self.mock_mode:
            missing = [item["name"] for item in ENGINE_PARAMETERS[engine]
                       if item["required"] and result.get(item["name"]) in (None, "", [])]
            if missing:
                raise ValueError(f"缺少必填参数: {', '.join(missing)}")
        if engine == "indextts2" and result.get("emotion_vector") is not None:
            vector = result["emotion_vector"]
            if not isinstance(vector, list) or len(vector) != 8 or not all(isinstance(v, (int, float)) for v in vector):
                raise ValueError("emotion_vector 必须是包含 8 个数值的数组")
        if engine == "voxcpm" and bool(result.get("prompt_audio")) != bool(result.get("prompt_text")):
            raise ValueError("VoxCPM 的 prompt_audio 与 prompt_text 必须同时填写")
        return result

    def create(self, request: JobCreate) -> JobManifest:
        if request.engine not in self.adapters:
            raise ValueError(f"引擎未配置: {request.engine}")
        parameters = self._validate_parameters(request.engine, request.params)
        runtime_status = self.adapters[request.engine].status()
        runtime_required = runtime_status.get("required_parameters") or []
        runtime_missing = [name for name in runtime_required if parameters.get(name) in (None, "")]
        if runtime_missing:
            raise ValueError(
                "当前托管引擎需要先明确选择参数: " + ", ".join(runtime_missing)
            )
        segments = split_text(request.text, request.long_audio.max_chars)
        if not segments:
            raise ValueError("文本不能为空")
        job_id = uuid.uuid4().hex
        requested_title = (request.title or "").strip()
        job = JobManifest(
            id=job_id, engine=request.engine,
            title=requested_title if requested_title and requested_title not in {"未命名语音项目", "未命名语音任务"} else title_from_text(request.text),
            text=request.text, parameters=parameters, long_audio=request.long_audio,
            session_id=self.session_id,
            segments=[SegmentManifest(id=f"{job_id}:{index}", index=index, text=text) for index, text in enumerate(segments)],
        )
        self.store.save(job)
        self._queue.put(job_id)
        self._emit(job, "job.created")
        return job

    def list(self) -> list[JobManifest]:
        return [job for job in self.store.list() if job.session_id == self.session_id]

    def list_all(self) -> list[JobManifest]:
        return self.store.list()

    def get(self, job_id: str) -> JobManifest | None:
        return self.store.load(job_id)

    def cancel(self, job_id: str) -> JobManifest:
        adapter_to_cancel: EngineAdapter | None = None
        with self._lock:
            job = self._require(job_id)
            if job.status in (JobStatus.completed, JobStatus.cancelled):
                return job
            self._cancelled.add(job_id)
            if job.status == JobStatus.queued:
                job.status = JobStatus.cancelled
                job.error = "用户已取消"
                job.updated_at = now_iso()
                self.store.save(job)
                self._emit(job, "job.cancelled")
            elif job.status == JobStatus.running:
                adapter_to_cancel = self.adapters.get(job.engine)
        if adapter_to_cancel is not None:
            adapter_to_cancel.cancel_current()
        return self._require(job_id)

    def retry(self, job_id: str) -> JobManifest:
        with self._lock:
            job = self._require(job_id)
            if job.status not in (JobStatus.failed, JobStatus.cancelled):
                raise ValueError("只有失败或已取消的任务可以重试")
            self._cancelled.discard(job_id)
            for segment in job.segments:
                path_ok = bool(segment.output_path and Path(segment.output_path).is_file())
                if segment.status != SegmentStatus.completed or not path_ok:
                    segment.status = SegmentStatus.pending
                    segment.error = None
            job.status = JobStatus.queued
            job.error = None
            job.session_id = self.session_id
            job.output_path = None
            job.updated_at = now_iso()
            self.store.save(job)
            self._queue.put(job_id)
            self._emit(job, "job.retried")
            return job

    def delete(self, job_id: str, *, delete_output: bool = False) -> dict[str, Any]:
        from .library import UnsafeOutputPath, safe_output_path

        with self._lock:
            job = self._require(job_id)
            if job.status in (JobStatus.queued, JobStatus.running):
                raise ValueError("请先取消正在排队或生成中的任务")
            try:
                output = safe_output_path(self.store, job)
            except UnsafeOutputPath as exc:
                if delete_output:
                    raise ValueError(f"为保护本地文件，未删除音频：{exc}") from exc
                output = None
            output_existed = bool(output and output.is_file())
            preserved_path: str | None = None
            if output_existed and output is not None:
                if delete_output:
                    output.unlink()
                elif self.store.job_dir(job.id) in output.parents:
                    destination_root = self.store.output_dir()
                    if destination_root == self.store.job_dir(job.id) or self.store.job_dir(job.id) in destination_root.parents:
                        destination_root = (self.store.root.parent / "output").resolve()
                        destination_root.mkdir(parents=True, exist_ok=True)
                    destination = self._available_output_path(destination_root, output.stem, output.suffix)
                    shutil.move(str(output), str(destination))
                    preserved_path = str(destination)
                else:
                    preserved_path = str(output)
            self.store.delete(job_id)
            self._cancelled.discard(job_id)
            return {
                "ok": True,
                "id": job_id,
                "recordDeleted": True,
                "outputDeleted": bool(delete_output and output_existed),
                "preservedOutputPath": preserved_path,
            }

    def _require(self, job_id: str) -> JobManifest:
        job = self.store.load(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def _emit(self, job: JobManifest, event_type: str) -> None:
        self.events.publish({"type": event_type, "job": job.model_dump(mode="json")})

    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            try:
                self._run_job(job_id)
            except Exception as exc:
                # Persist unexpected merge/storage errors instead of leaving a task stuck at running.
                job = self.store.load(job_id)
                if job is not None and job.status not in (JobStatus.completed, JobStatus.cancelled, JobStatus.failed):
                    job.status = JobStatus.failed
                    job.error = f"任务收尾失败：{type(exc).__name__}: {exc}"
                    job.updated_at = now_iso()
                    self.store.save(job)
                    self._emit(job, "job.failed")
                continue

    def _run_job(self, job_id: str) -> None:
        job = self.store.load(job_id)
        if job is None or job.status == JobStatus.cancelled:
            return
        adapter = self.adapters[job.engine]
        _, segment_dir = self.store.create_dirs(job.id)
        job.status = JobStatus.running
        job.error = None
        job.updated_at = now_iso()
        self.store.save(job)
        self._emit(job, "job.started")
        completed_count = sum(segment.status == SegmentStatus.completed for segment in job.segments)
        total_segments = len(job.segments)
        for segment in job.segments:
            if segment.status == SegmentStatus.completed and segment.output_path and Path(segment.output_path).is_file():
                continue
            if job.id in self._cancelled:
                return self._finish_cancelled(job)
            output = segment_dir / f"{segment.index + 1:04d}.wav"
            success = False
            last_error = None
            attempts_this_run = 0
            while attempts_this_run <= job.long_audio.max_retries:
                if job.id in self._cancelled:
                    return self._finish_cancelled(job)
                segment.status = SegmentStatus.running
                segment.attempts += 1
                attempts_this_run += 1
                segment.error = None
                job.updated_at = now_iso()
                self.store.save(job)
                self._emit(job, "segment.started")
                try:
                    call_parameters = job.parameters
                    if self.mock_mode:
                        call_parameters = {**job.parameters, "_segment_index": segment.index}
                    adapter.synthesize(segment.text, output, call_parameters)
                    segment.status = SegmentStatus.completed
                    segment.output_path = str(output)
                    segment.error = None
                    success = True
                    break
                except Exception as exc:
                    if job.id in self._cancelled:
                        return self._finish_cancelled(job)
                    last_error = f"{type(exc).__name__}: {exc}"
                    segment.status = SegmentStatus.failed
                    segment.error = last_error
                    job.updated_at = now_iso()
                    self.store.save(job)
                    self._emit(job, "segment.failed")
            if not success:
                job.status = JobStatus.failed
                job.error = f"第 {segment.index + 1} 段失败：{last_error}"
                job.progress = completed_count / total_segments
                job.updated_at = now_iso()
                self.store.save(job)
                self._emit(job, "job.failed")
                return
            completed_count += 1
            job.progress = completed_count / total_segments
            job.updated_at = now_iso()
            self.store.save(job)
            self._emit(job, "segment.completed")
        if job.id in self._cancelled:
            return self._finish_cancelled(job)
        output_dir = self.store.output_dir()
        final_path = output_dir / f"{job.title or job.id}.wav"
        invalid = '<>:"/\\|?*'
        safe_name = "".join("_" if char in invalid else char for char in final_path.name)
        final_path = final_path.with_name(safe_name)
        final_path = self._available_output_path(output_dir, final_path.stem, final_path.suffix)
        merge_wav_files(
            [segment.output_path for segment in job.segments if segment.output_path], final_path,
            sample_rate=job.long_audio.target_sample_rate, silence_ms=job.long_audio.silence_ms,
        )
        job.output_path = str(final_path)
        job.output_directory = str(output_dir)
        job.status = JobStatus.completed
        job.progress = 1.0
        job.updated_at = now_iso()
        if not job.long_audio.keep_segments:
            shutil.rmtree(segment_dir)
            for segment in job.segments:
                segment.output_path = None
        self.store.save(job)
        self._emit(job, "job.completed")

    @staticmethod
    def _available_output_path(directory: Path, stem: str, suffix: str) -> Path:
        candidate = directory / f"{stem}{suffix}"
        if not candidate.exists():
            return candidate
        index = 2
        while True:
            candidate = directory / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    def _finish_cancelled(self, job: JobManifest) -> None:
        job.status = JobStatus.cancelled
        job.error = "用户已取消；已完成分段保留，可重试续作"
        job.progress = sum(s.status == SegmentStatus.completed for s in job.segments) / len(job.segments)
        job.updated_at = now_iso()
        self.store.save(job)
        self._emit(job, "job.cancelled")
