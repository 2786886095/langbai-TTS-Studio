import time
import math
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.main import create_app
from app.gpu_scheduler import GpuSnapshot


class RecordingAdapter(MockAdapter):
    def __init__(self):
        super().__init__("gpt_sovits")
        self.calls: list[dict] = []

    def synthesize(self, text: str, output_path: Path, parameters: dict) -> None:
        self.calls.append(dict(parameters))
        super().synthesize(text, output_path, parameters)


class QualityRetryAdapter(MockAdapter):
    def __init__(self):
        super().__init__("gpt_sovits")
        self.seeds: list[int] = []

    def synthesize(self, text: str, output_path: Path, parameters: dict) -> None:
        self.seeds.append(int(parameters["seed"]))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 16_000
        if len(self.seeds) == 1:
            sf.write(output_path, np.zeros(sample_rate, dtype=np.float32), sample_rate)
            return
        t = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
        sf.write(output_path, 0.15 * np.sin(2 * math.pi * 220 * t), sample_rate)


class PersistentLongSilenceAdapter(MockAdapter):
    def __init__(self):
        super().__init__("gpt_sovits")
        self.seeds: list[int] = []

    def synthesize(self, text: str, output_path: Path, parameters: dict) -> None:
        self.seeds.append(int(parameters["seed"]))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 16_000
        t = np.arange(sample_rate, dtype=np.float32) / sample_rate
        tone = 0.15 * np.sin(2 * math.pi * 220 * t)
        sf.write(output_path, np.concatenate([tone, np.zeros(sample_rate * 3), tone]), sample_rate)


class ParallelState:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls: list[tuple[str, str]] = []


class ParallelAdapter(MockAdapter):
    def __init__(self, state: ParallelState, label: str = "primary", *, oom=False):
        super().__init__("gpt_sovits")
        self.state = state
        self.label = label
        self.oom = oom

    def spawn_parallel_worker(self, worker_index: int):
        return ParallelAdapter(self.state, f"worker-{worker_index}", oom=self.oom)

    def synthesize(self, text: str, output_path: Path, parameters: dict) -> None:
        if self.oom and self.label != "primary":
            with self.state.lock:
                self.state.calls.append((self.label, text))
            raise RuntimeError("torch.OutOfMemoryError: CUDA out of memory")
        with self.state.lock:
            self.state.active += 1
            self.state.max_active = max(self.state.max_active, self.state.active)
            self.state.calls.append((self.label, text))
        try:
            time.sleep(0.04)
            super().synthesize(text, output_path, parameters)
        finally:
            with self.state.lock:
                self.state.active -= 1


def roomy_gpu_snapshots():
    rows = iter([
        GpuSnapshot(0, "RTX Test 24GB", 24_576, 2_000, 8, 50),
        GpuSnapshot(0, "RTX Test 24GB", 24_576, 6_500, 12, 56),
    ])
    last = GpuSnapshot(0, "RTX Test 24GB", 24_576, 6_500, 40, 60)

    def read():
        return next(rows, last)

    return read


def wait_completed(client: TestClient, job_id: str) -> dict:
    for _ in range(200):
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] == "completed":
            return payload
        if payload["status"] == "failed":
            raise AssertionError(payload.get("error"))
        time.sleep(0.02)
    raise AssertionError("multi-speaker job did not complete")


def create_voice(client: TestClient, tmp_path: Path, name: str) -> str:
    response = client.post("/api/voice-profiles", json={
        "name": name,
        "engine": "gpt_sovits",
        "parameters": {
            "gpt_weights_path": str(tmp_path / f"{name}.ckpt"),
            "sovits_weights_path": str(tmp_path / f"{name}.pth"),
            "ref_audio_path": str(tmp_path / f"{name}.wav"),
            "prompt_text": f"{name}参考文本",
            "prompt_lang": "中文",
            "version": "v4",
        },
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_script_parser_reports_roles_and_unlabelled_lines(tmp_path):
    app = create_app(
        adapters={"gpt_sovits": MockAdapter("gpt_sovits")},
        data_dir=tmp_path / "data",
        mock_mode=True,
    )
    with TestClient(app) as client:
        response = client.post("/api/multi-speaker/parse", json={
            "script": "【旁白】：夜幕降临。\n这行没有标签。\n【小明】: 我回来了。",
        })
        assert response.status_code == 200
        payload = response.json()
        assert payload["speakers"] == ["旁白", "小明"]
        assert payload["invalidLines"] == [2]
        assert payload["valid"] is False


def test_multi_speaker_job_uses_saved_voices_presets_and_line_gaps(tmp_path):
    app = create_app(
        adapters={"gpt_sovits": MockAdapter("gpt_sovits")},
        data_dir=tmp_path / "data",
        mock_mode=True,
    )
    with TestClient(app) as client:
        narrator_id = create_voice(client, tmp_path, "旁白声线")
        actor_id = create_voice(client, tmp_path, "小明声线")
        response = client.post("/api/jobs/multi-speaker", json={
            "title": "多人测试",
            "script": "【旁白】：夜幕降临。\n【小明】：我回来了。\n【小明】：门外很冷。",
            "lineIntervalMs": 280,
            "longAudio": {"maxChars": 100, "targetSampleRate": 16000, "silenceMs": 0},
            "assignments": {
                "旁白": {"voiceProfileId": narrator_id, "params": {"top_p": 0.7, "mock_sample_rate": 16000}},
                "小明": {"voiceProfileId": actor_id, "params": {"top_p": 0.65, "mock_sample_rate": 16000}},
            },
        })
        assert response.status_code == 202, response.text
        completed = wait_completed(client, response.json()["id"])
        assert completed["mode"] == "multi_speaker"
        assert [segment["speaker"] for segment in completed["segments"]] == ["旁白", "小明", "小明"]
        assert [segment["script_line_number"] for segment in completed["segments"]] == [1, 2, 3]
        assignments = completed["multi_speaker"]["assignments"]
        assert assignments["旁白"]["parameters"]["reference_audio"].endswith("旁白声线.wav")
        assert assignments["旁白"]["parameters"]["top_p"] == 0.7
        assert assignments["小明"]["parameters"]["top_p"] == 0.65
        info = sf.info(completed["output_path"])
        assert info.samplerate == 16000
        assert info.frames == 3 * 1280 + 2 * 4480


def test_multi_speaker_rejects_missing_mapping_and_voice_overrides(tmp_path):
    app = create_app(
        adapters={"gpt_sovits": MockAdapter("gpt_sovits")},
        data_dir=tmp_path / "data",
        mock_mode=True,
    )
    with TestClient(app) as client:
        actor_id = create_voice(client, tmp_path, "角色声线")
        missing = client.post("/api/jobs/multi-speaker", json={
            "script": "【旁白】：测试。",
            "assignments": {"其他": {"voiceProfileId": actor_id}},
        })
        assert missing.status_code == 400
        assert "尚未匹配声音" in missing.json()["detail"]

        override = client.post("/api/jobs/multi-speaker", json={
            "script": "【旁白】：测试。",
            "assignments": {
                "旁白": {
                    "voiceProfileId": actor_id,
                    "params": {"reference_audio": str(tmp_path / "other.wav")},
                },
            },
        })
        assert override.status_code == 400
        assert "不能覆盖声音模型与参考配置" in override.json()["detail"]


def test_stable_quality_preset_overrides_risky_parameters_and_reuses_role_seed(tmp_path):
    adapter = RecordingAdapter()
    app = create_app(
        adapters={"gpt_sovits": adapter}, data_dir=tmp_path / "data", mock_mode=True,
    )
    with TestClient(app) as client:
        actor_id = create_voice(client, tmp_path, "稳定声线")
        response = client.post("/api/jobs/multi-speaker", json={
            "script": "【角色】：第一句。\n【角色】：第二句。",
            "qualityPreset": "stable",
            "assignments": {"角色": {"voiceProfileId": actor_id, "params": {
                "temperature": 1.6, "speed_factor": 1.2,
                "text_split_method": "cut5", "seed": -1,
            }}},
        })
        assert response.status_code == 202, response.text
        completed = wait_completed(client, response.json()["id"])
        assert completed["multiSpeaker"]["qualityPreset"] == "stable"
        assert len(adapter.calls) == 2
        assert {call["seed"] for call in adapter.calls} == {adapter.calls[0]["seed"]}
        assert adapter.calls[0]["seed"] > 0
        assert adapter.calls[0]["temperature"] == 0.75
        assert adapter.calls[0]["speed_factor"] == 1.1
        assert adapter.calls[0]["text_split_method"] == "cut5"
        assert adapter.calls[0]["parallel_infer"] is True
        assert all(segment["quality"]["preset"] == "stable" for segment in completed["segments"])


def test_all_quality_presets_use_1_1_speed():
    from app.jobs import MULTI_SPEAKER_QUALITY_PARAMETERS

    assert {
        preset: parameters["speed_factor"]
        for preset, parameters in MULTI_SPEAKER_QUALITY_PARAMETERS.items()
    } == {
        "stable": 1.1,
        "balanced": 1.1,
        "expressive": 1.1,
    }


def test_quality_gate_retries_silent_segment_with_a_new_seed(tmp_path):
    adapter = QualityRetryAdapter()
    app = create_app(
        adapters={"gpt_sovits": adapter}, data_dir=tmp_path / "data", mock_mode=True,
    )
    with TestClient(app) as client:
        actor_id = create_voice(client, tmp_path, "重试声线")
        response = client.post("/api/jobs/multi-speaker", json={
            "script": "【角色】：这一句话用于验证异常静音自动重试。",
            "qualityPreset": "stable",
            "longAudio": {"maxRetries": 2},
            "assignments": {"角色": {"voiceProfileId": actor_id}},
        })
        assert response.status_code == 202, response.text
        completed = wait_completed(client, response.json()["id"])
        assert completed["segments"][0]["attempts"] == 2
        assert len(adapter.seeds) == 2
        assert adapter.seeds[0] != adapter.seeds[1]
        assert completed["segments"][0]["quality"]["durationSeconds"] == 2.0


def test_persistent_internal_silence_is_retried_then_safely_compressed(tmp_path):
    adapter = PersistentLongSilenceAdapter()
    app = create_app(
        adapters={"gpt_sovits": adapter}, data_dir=tmp_path / "data", mock_mode=True,
    )
    with TestClient(app) as client:
        actor_id = create_voice(client, tmp_path, "长静音声线")
        response = client.post("/api/jobs/multi-speaker", json={
            "script": "【角色】：前半句结束后还要继续说后半句。",
            "qualityPreset": "stable",
            "longAudio": {"maxRetries": 1, "targetSampleRate": 16000},
            "assignments": {"角色": {"voiceProfileId": actor_id}},
        })
        assert response.status_code == 202, response.text
        completed = wait_completed(client, response.json()["id"])
        segment = completed["segments"][0]
        assert segment["attempts"] == 2
        assert len(adapter.seeds) == 2
        assert adapter.seeds[0] != adapter.seeds[1]
        assert segment["quality"]["compressedSilenceCount"] == 1
        assert segment["quality"]["removedSilenceMs"] == 2350
        assert sf.info(completed["output_path"]).duration < 2.7


def test_multi_speaker_adaptive_workers_generate_concurrently_and_merge_in_script_order(tmp_path):
    state = ParallelState()
    app = create_app(
        adapters={"gpt_sovits": ParallelAdapter(state)},
        data_dir=tmp_path / "data",
        mock_mode=True,
    )
    app.state.manager.gpu_snapshot_provider = roomy_gpu_snapshots()
    with TestClient(app) as client:
        voices = {
            role: create_voice(client, tmp_path, f"{role}声线")
            for role in ("甲", "乙", "丙")
        }
        response = client.post("/api/jobs/multi-speaker", json={
            "script": "\n".join([
                "【甲】：甲一。", "【乙】：乙一。", "【丙】：丙一。",
                "【甲】：甲二。", "【乙】：乙二。", "【丙】：丙二。",
            ]),
            "longAudio": {"targetSampleRate": 16000, "silenceMs": 0},
            "assignments": {
                role: {"voiceProfileId": voice, "params": {"mock_sample_rate": 16000}}
                for role, voice in voices.items()
            },
        })
        assert response.status_code == 202, response.text
        completed = wait_completed(client, response.json()["id"])
        scheduling = completed["multiSpeaker"]
        assert scheduling["aggressiveConcurrency"] is True
        assert scheduling["schedulingMode"] == "aggressive_gpu"
        assert scheduling["plannedWorkers"] == 3
        assert scheduling["maxWorkersUsed"] == 3
        assert scheduling["activeWorkers"] == 0
        assert scheduling["gpuName"] == "RTX Test 24GB"
        assert state.max_active >= 2
        assert [segment["index"] for segment in completed["segments"]] == list(range(6))
        assert [Path(segment["output_path"]).name for segment in completed["segments"]] == [
            f"{index:04d}.wav" for index in range(1, 7)
        ]
        assert sf.info(completed["output_path"]).frames == 6 * 1280 + 5 * 4480


def test_parallel_cuda_oom_retires_clone_and_continues_on_primary(tmp_path):
    state = ParallelState()
    app = create_app(
        adapters={"gpt_sovits": ParallelAdapter(state, oom=True)},
        data_dir=tmp_path / "data",
        mock_mode=True,
    )
    app.state.manager.gpu_snapshot_provider = roomy_gpu_snapshots()
    with TestClient(app) as client:
        voices = {
            role: create_voice(client, tmp_path, f"{role}回退声线")
            for role in ("甲", "乙")
        }
        response = client.post("/api/jobs/multi-speaker", json={
            "script": "【甲】：第一句。\n【乙】：第二句。\n【甲】：第三句。\n【乙】：第四句。",
            "longAudio": {"targetSampleRate": 16000, "silenceMs": 0, "maxRetries": 0},
            "assignments": {
                role: {"voiceProfileId": voice, "params": {"mock_sample_rate": 16000}}
                for role, voice in voices.items()
            },
        })
        assert response.status_code == 202, response.text
        completed = wait_completed(client, response.json()["id"])
        assert completed["multiSpeaker"]["plannedWorkers"] == 2
        assert "退回单 Worker" in completed["multiSpeaker"]["schedulerDetail"]
        assert all(segment["status"] == "completed" for segment in completed["segments"])
        assert any(label.startswith("worker-") for label, _ in state.calls)
        assert sum(label == "primary" for label, _ in state.calls) >= 3


def test_single_worker_groups_roles_to_avoid_reloading_weights_for_every_line(tmp_path):
    adapter = RecordingAdapter()
    app = create_app(
        adapters={"gpt_sovits": adapter}, data_dir=tmp_path / "data", mock_mode=True,
    )
    app.state.manager.gpu_snapshot_provider = lambda: None
    with TestClient(app) as client:
        first_voice = create_voice(client, tmp_path, "甲分组声线")
        second_voice = create_voice(client, tmp_path, "乙分组声线")
        response = client.post("/api/jobs/multi-speaker", json={
            "script": "【甲】：甲一。\n【乙】：乙一。\n【甲】：甲二。\n【乙】：乙二。",
            "assignments": {
                "甲": {"voiceProfileId": first_voice},
                "乙": {"voiceProfileId": second_voice},
            },
        })
        assert response.status_code == 202, response.text
        completed = wait_completed(client, response.json()["id"])
        generated_weight_order = [Path(call["t2s_weights_path"]).name for call in adapter.calls]
        assert generated_weight_order == [
            "甲分组声线.ckpt", "甲分组声线.ckpt", "乙分组声线.ckpt", "乙分组声线.ckpt",
        ]
        assert [segment["speaker"] for segment in completed["segments"]] == ["甲", "乙", "甲", "乙"]
