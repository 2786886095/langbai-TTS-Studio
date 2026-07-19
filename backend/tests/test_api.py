import time

from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.main import create_app


def test_engine_metadata_and_job_contract(tmp_path):
    adapters = {name: MockAdapter(name) for name in ("indextts2", "voxcpm", "gpt_sovits")}
    app = create_app(adapters=adapters, data_dir=tmp_path, mock_mode=True)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        engines = client.get("/api/engines").json()
        assert {engine["id"] for engine in engines} == {"indextts2", "voxcpm", "gpt_sovits"}
        for engine in engines:
            assert engine["parameters"]
            assert all(parameter["description_zh"] or parameter["label"] for parameter in engine["parameters"])

        response = client.post("/api/jobs", json={
            "engine": "gpt_sovits", "text": "一段测试文本。另一段测试文本。",
            "params": {"mock_sample_rate": 16000},
            "longAudio": {"maxChars": 8, "targetSampleRate": 24000, "maxRetries": 1},
        })
        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["status"] == "queued"
        assert payload["params"]["mock_sample_rate"] == 16000
        assert payload["longAudio"]["maxChars"] == 8
        job_id = payload["id"]
        for _ in range(200):
            payload = client.get(f"/api/jobs/{job_id}").json()
            if payload["status"] == "completed":
                break
            time.sleep(0.02)
        assert payload["status"] == "completed"
        assert payload["output_path"]
        assert client.get("/api/jobs").json()[0]["id"] == job_id


def test_unknown_engine_parameter_is_rejected(tmp_path):
    adapters = {name: MockAdapter(name) for name in ("indextts2", "voxcpm", "gpt_sovits")}
    app = create_app(adapters=adapters, data_dir=tmp_path, mock_mode=True)
    with TestClient(app) as client:
        response = client.post("/api/jobs", json={
            "engine": "indextts2", "text": "测试", "params": {"not_a_real_parameter": 1}
        })
        assert response.status_code == 400
        assert "未知" in response.json()["detail"]
