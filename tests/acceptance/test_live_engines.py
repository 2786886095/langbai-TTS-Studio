from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ENGINE_CASES = [
    ("indextts2", "LANGBAI_INDEXTTS2_PROJECT"),
    ("voxcpm", "LANGBAI_VOXCPM_PROJECT"),
    ("gpt_sovits", "LANGBAI_GPT_SOVITS_PROJECT"),
]
LONG_ACCEPTANCE_TEXT = (
    "你好，这是浪白语音工作室的真实长音频验收。"
    "第一段用于验证引擎加载和中文发音。"
    "第二段用于验证任务切分、逐段生成和失败重试。"
    "第三段用于验证采样率统一、静音拼接和最终 WAV 合并。"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _wav_evidence(path: Path) -> dict[str, object]:
    assert path.is_file(), f"Missing output WAV: {path}"
    with wave.open(str(path), "rb") as audio:
        channels = audio.getnchannels()
        sample_width = audio.getsampwidth()
        sample_rate = audio.getframerate()
        frames = audio.getnframes()
        preview = audio.readframes(min(frames, sample_rate * 2))
    duration = frames / sample_rate if sample_rate else 0
    assert channels in {1, 2}, f"Unexpected channel count: {channels}"
    assert sample_width in {1, 2, 3, 4}, f"Unexpected sample width: {sample_width}"
    assert 8_000 <= sample_rate <= 192_000, f"Unexpected sample rate: {sample_rate}"
    assert duration >= 0.20, f"Output is too short: {duration:.3f}s"
    silence_byte = 128 if sample_width == 1 else 0
    assert preview and any(value != silence_byte for value in preview), "Output WAV contains only digital silence"
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "channels": channels,
        "sampleWidth": sample_width,
        "sampleRate": sample_rate,
        "frames": frames,
        "durationSeconds": round(duration, 3),
    }


def _parameters(engine: str, reference: str) -> dict[str, object]:
    if engine == "indextts2":
        return {
            "speaker_audio": reference,
            "emotion_mode": "audio",
            "device": os.getenv("LANGBAI_ACCEPTANCE_DEVICE", "cuda"),
            "use_fp16": True,
        }
    if engine == "voxcpm":
        return {
            "reference_audio": reference,
            "device": os.getenv("LANGBAI_ACCEPTANCE_DEVICE", "cuda"),
            "local_files_only": True,
            "seed": 20260719,
        }
    return {
        "reference_audio": reference,
        "prompt_text": os.getenv("LANGBAI_ACCEPTANCE_PROMPT_TEXT", "这是一段用于验收的参考语音。"),
        "prompt_language": "zh",
        "text_language": "zh",
        "device": os.getenv("LANGBAI_ACCEPTANCE_DEVICE", "cuda"),
        "seed": 20260719,
    }


@pytest.mark.parametrize(("engine", "project_environment"), ENGINE_CASES)
def test_real_engine_short_audio(
    pytestconfig: pytest.Config,
    project_root: Path,
    tmp_path: Path,
    engine: str,
    project_environment: str,
) -> None:
    if not pytestconfig.getoption("--live-engines"):
        pytest.skip("Real model loading is opt-in; pass --live-engines")

    data_root = tmp_path / engine
    bindings_file = os.getenv("LANGBAI_ACCEPTANCE_BINDINGS_FILE")
    configured_root = os.getenv(project_environment)
    if bindings_file:
        source = Path(bindings_file)
        assert source.is_file(), f"Missing binding file: {source}"
        binding_payload = json.loads(source.read_text(encoding="utf-8"))
        configured_root = binding_payload["bindings"][engine]["sourcePath"]
        data_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, data_root / "engine-bindings.json")
    assert configured_root, f"Set {project_environment} or LANGBAI_ACCEPTANCE_BINDINGS_FILE"
    root = Path(configured_root)
    assert root.is_dir(), f"Missing real engine root: {root}"
    reference = os.environ.get("LANGBAI_ACCEPTANCE_REFERENCE_WAV")
    assert reference and Path(reference).is_file(), (
        "Set LANGBAI_ACCEPTANCE_REFERENCE_WAV to a licensed local reference WAV"
    )

    import sys

    sys.path.insert(0, str(project_root / "backend"))
    from app.main import create_app  # type: ignore

    timeout_seconds = int(os.getenv("LANGBAI_ACCEPTANCE_ENGINE_TIMEOUT", "600"))
    with TestClient(create_app(data_dir=data_root, mock_mode=False)) as client:
        status = client.get("/api/engines").json()
        engine_status = next(item for item in status if item.get("id") == engine)
        assert engine_status.get("status", {}).get("available"), engine_status
        response = client.post(
            "/api/jobs",
            json={
                "title": f"{engine} real acceptance",
                "engine": engine,
                "text": LONG_ACCEPTANCE_TEXT,
                "parameters": _parameters(engine, reference),
                "longAudio": {
                    "maxChars": 28,
                    "silenceMs": 100,
                    "targetSampleRate": 32_000,
                    "keepSegments": True,
                    "maxRetries": 0,
                },
            },
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        deadline = time.monotonic() + timeout_seconds
        job = response.json()
        while time.monotonic() < deadline:
            job_response = client.get(f"/api/jobs/{job_id}")
            assert job_response.status_code == 200, job_response.text
            job = job_response.json()
            if job["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(1)
        assert job["status"] == "completed", job
        assert len(job["segments"]) >= 3, job
        assert all(segment["status"] == "completed" for segment in job["segments"]), job
        generated_path = Path(job["outputPath"])
        _wav_evidence(generated_path)

    evidence_root = Path(os.getenv("LANGBAI_ACCEPTANCE_OUTPUT_DIR", str(project_root / "runtime-smoke")))
    evidence_root.mkdir(parents=True, exist_ok=True)
    durable_audio = evidence_root / f"{engine}-real-acceptance.wav"
    shutil.copy2(generated_path, durable_audio)
    evidence = _wav_evidence(durable_audio)
    (evidence_root / f"{engine}-evidence.json").write_text(
        json.dumps({"engine": engine, "jobId": job_id, "segmentCount": len(job["segments"]), "longAudioMaxChars": 28, **evidence}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
