import time
from pathlib import Path

import soundfile as sf

from app.adapters.mock import MockAdapter
from app.jobs import JobManager
from app.models import JobCreate, JobStatus, LongAudioOptions
from app.storage import JobStore


def wait_terminal(manager: JobManager, job_id: str, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = manager.get(job_id)
        if job.status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_long_job_retries_once_merges_and_persists(tmp_path):
    adapter = MockAdapter("indextts2")
    manager = JobManager(JobStore(tmp_path / "jobs"), {"indextts2": adapter}, mock_mode=True)
    manager.start()
    try:
        request = JobCreate(
            engine="indextts2", text="第一段结束。第二段包含失败标记，然后恢复。第三段结束。",
            params={"mock_fail_segment_once": "失败标记", "mock_sample_rate": 16000},
            longAudio=LongAudioOptions(maxChars=12, targetSampleRate=22050, maxRetries=1, silenceMs=100),
        )
        created = manager.create(request)
        result = wait_terminal(manager, created.id)
        assert result.status == JobStatus.completed
        assert result.progress == 1
        assert Path(result.output_path).is_file()
        assert any(segment.attempts == 2 for segment in result.segments)
        audio, rate = sf.read(result.output_path)
        assert rate == 22050
        assert len(audio) > 0
        loaded = JobStore(tmp_path / "jobs").load(result.id)
        assert loaded.status == JobStatus.completed
        assert len(loaded.segments) == len(result.segments)
    finally:
        manager.close()


def test_failed_job_can_resume_without_regenerating_completed_segments(tmp_path):
    adapter = MockAdapter("voxcpm", fail_text="必败")
    manager = JobManager(JobStore(tmp_path / "jobs"), {"voxcpm": adapter}, mock_mode=True)
    manager.start()
    try:
        created = manager.create(JobCreate(
            engine="voxcpm", text="先成功。这里必败。末尾继续。", params={},
            longAudio={"maxChars": 6, "maxRetries": 0},
        ))
        failed = wait_terminal(manager, created.id)
        assert failed.status == JobStatus.failed
        completed_attempts = {s.index: s.attempts for s in failed.segments if s.status == "completed"}
        adapter.fail_text = None
        manager.retry(created.id)
        completed = wait_terminal(manager, created.id)
        assert completed.status == JobStatus.completed
        assert {s.index: s.attempts for s in completed.segments if s.index in completed_attempts} == completed_attempts
    finally:
        manager.close()
