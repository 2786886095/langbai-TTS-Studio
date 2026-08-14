import time
from pathlib import Path

import soundfile as sf
from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.main import create_app


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
