from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def require_commercial_gate(commercial_release: bool) -> None:
    if not commercial_release:
        pytest.skip("strict commercial backend checks run with --commercial-release")


def make_client(project_root: Path, data_dir: Path) -> TestClient:
    import sys
    sys.path.insert(0, str(project_root / "backend"))
    from app.main import create_app  # type: ignore
    return TestClient(create_app(data_dir=data_dir, mock_mode=True))


def test_project_and_settings_survive_backend_restart(project_root: Path, tmp_path: Path, commercial_release: bool) -> None:
    require_commercial_gate(commercial_release)
    payload = {"name": "持久化验收", "engine": "indextts2", "text": "重启后仍应存在", "params": {"temperature": 0.8}}
    with make_client(project_root, tmp_path) as first:
        created = first.post("/api/projects", json=payload)
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        settings = first.get("/api/settings").json()
        changed = first.patch("/api/settings", json={"expectedRevision": settings["revision"], "defaultEngine": "voxcpm"})
        assert changed.status_code == 200, changed.text
    with make_client(project_root, tmp_path) as second:
        restored = second.get(f"/api/projects/{project_id}")
        assert restored.status_code == 200, restored.text
        assert restored.json()["text"] == payload["text"]
        assert second.get("/api/settings").json()["defaultEngine"] == "voxcpm"


def test_documented_workspace_api_contract(project_root: Path, tmp_path: Path, commercial_release: bool) -> None:
    require_commercial_gate(commercial_release)
    with make_client(project_root, tmp_path) as client:
        schema = client.get("/api/storage/schema")
        assert schema.status_code == 200
        assert schema.json()["projects"]["current"] == 1

        created = client.post("/api/projects", json={"name": "API 契约", "engine": "voxcpm", "text": "项目内容", "params": {}})
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        copied = client.post(f"/api/projects/{project_id}/copy", json={"name": "API 契约副本"})
        assert copied.status_code == 201, copied.text
        assert copied.json()["sourceProjectId"] == project_id
        listed = client.get("/api/projects", params={"query": "副本", "engine": "voxcpm", "limit": 10}).json()
        assert listed["total"] == 1 and listed["items"][0]["id"] == copied.json()["id"]

        settings = client.get("/api/settings").json()
        changed = client.patch("/api/settings", json={"expectedRevision": settings["revision"], "updateChannel": "beta"})
        assert changed.status_code == 200, changed.text
        conflict = client.patch("/api/settings", json={"expectedRevision": settings["revision"], "theme": "dark"})
        assert conflict.status_code == 409, conflict.text

        created_job = client.post("/api/jobs", json={"title": "音频库契约", "engine": "indextts2", "text": "音频库契约验证。", "parameters": {}})
        assert created_job.status_code in {200, 201, 202}, created_job.text
        job_id = created_job.json()["id"]
        deadline = time.monotonic() + 15
        job = None
        while time.monotonic() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert job and job["status"] == "completed", job

        history = client.get("/api/history", params={"query": "音频库契约", "hasOutput": True}).json()
        assert history["total"] == 1 and history["items"][0]["id"] == job_id
        library = client.get("/api/library/audio", params={"engine": "indextts2"}).json()
        item = next(entry for entry in library["items"] if entry["id"] == job_id)
        assert item["output"]["state"] == "available"
        assert item["output"]["durationSeconds"] > 0
        assert item["output"]["format"] == "WAV"
        output = client.get(f"/api/jobs/{job_id}/output")
        assert output.status_code == 200, output.text
        assert output.json()["openContract"]["executor"] == "electron"

        update = client.get("/api/update/config")
        assert update.status_code == 200
        assert update.json()["handledBy"] == "electron"
        assert update.json()["backendPerformsUpdateChecks"] is False


def test_diagnostic_export_redacts_secret_like_log_values(project_root: Path, tmp_path: Path, commercial_release: bool) -> None:
    require_commercial_gate(commercial_release)
    sentinel = "LANGBAI_ACCEPTANCE_TOKEN_DO_NOT_EXPORT_7f32"
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / "acceptance.log").write_text(f"Authorization: Bearer {sentinel}\n", encoding="utf-8")
    with make_client(project_root, tmp_path) as client:
        created = client.post("/api/diagnostics/exports")
        assert created.status_code == 201, created.text
        downloaded = client.get(f"/api/diagnostics/exports/{created.json()['id']}")
        assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        combined = b"\n".join(archive.read(name) for name in archive.namelist()).decode("utf-8", errors="replace")
    assert sentinel not in combined, "诊断包原样导出了疑似令牌/Authorization 日志内容"
