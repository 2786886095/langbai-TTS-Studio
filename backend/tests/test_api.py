import time

from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.main import create_app


def test_local_dev_origin_preflight_is_allowed(tmp_path):
    adapters = {name: MockAdapter(name) for name in ("indextts2", "voxcpm", "gpt_sovits")}
    app = create_app(adapters=adapters, data_dir=tmp_path, mock_mode=True)
    with TestClient(app) as client:
        response = client.options("/api/jobs", headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


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


def test_runtime_console_and_model_scan_contract(tmp_path):
    adapters = {name: MockAdapter(name) for name in ("indextts2", "voxcpm", "gpt_sovits")}
    app = create_app(adapters=adapters, data_dir=tmp_path / "data", mock_mode=True)
    model_root = tmp_path / "downloaded-model"
    model_root.mkdir()
    (model_root / "demo-e10.ckpt").write_bytes(b"gpt")
    (model_root / "demo-s20.pth").write_bytes(b"sovits")

    with TestClient(app) as client:
        runtime = client.get("/api/runtime/engines?lines=40")
        assert runtime.status_code == 200
        assert {item["id"] for item in runtime.json()["items"]} == set(adapters)
        assert all("logLines" in item and "command" in item for item in runtime.json()["items"])

        assert client.post("/api/runtime/engines/gpt_sovits/start").json()["ok"] is True
        assert client.post("/api/runtime/engines/gpt_sovits/restart").json()["ok"] is True
        assert client.post("/api/runtime/engines/gpt_sovits/stop").json()["ok"] is True
        assert client.post("/api/runtime/engines/unknown/start").status_code == 404

        scan = client.post("/api/community-models/scan", json={"paths": [str(model_root)]})
        assert scan.status_code == 200
        assert len(scan.json()["items"]) == 1
        assert scan.json()["items"][0]["gptWeightsPath"].endswith(".ckpt")

        activity = client.get("/api/runtime/activity")
        assert activity.status_code == 200
        assert activity.json()["active"] is False

        capabilities = client.get("/api/training/capabilities")
        assert capabilities.status_code == 200
        assert {"voxcpm", "gptSovits"}.issubset(capabilities.json())
        workbench = client.post("/api/training/gpt-sovits/workbench/start")
        assert workbench.status_code == 200
        assert workbench.json()["running"] is True
        assert client.get("/api/runtime/activity").json()["active"] is True
        assert client.post("/api/training/gpt-sovits/workbench/stop").json()["running"] is False

        terminated = client.post("/api/runtime/terminate-active")
        assert terminated.status_code == 200
        assert terminated.json()["ok"] is True
