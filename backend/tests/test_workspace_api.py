import json
import time
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.main import create_app


def make_client(tmp_path):
    adapters = {name: MockAdapter(name) for name in ("indextts2", "voxcpm", "gpt_sovits")}
    return TestClient(create_app(adapters=adapters, data_dir=tmp_path, mock_mode=True))


def wait_completed(client: TestClient, job_id: str) -> dict:
    for _ in range(200):
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] == "completed":
            return payload
        time.sleep(0.02)
    raise AssertionError("job did not complete")


def test_project_crud_copy_search_and_atomic_persistence(tmp_path):
    with make_client(tmp_path) as client:
        created = client.post("/api/projects", json={
            "name": "旁白项目", "description": "商业样片", "engine": "voxcpm",
            "text": "一段正文", "params": {"cfg_value": 2.0},
            "longAudio": {"maxChars": 500},
        })
        assert created.status_code == 201, created.text
        project = created.json()
        assert project["schemaVersion"] == 1
        assert project["params"]["cfg_value"] == 2.0
        project_id = project["id"]
        assert (tmp_path / "projects" / f"{project_id}.json").is_file()
        assert not list((tmp_path / "projects").glob("*.tmp"))

        updated = client.put(f"/api/projects/{project_id}", json={"name": "正式旁白", "text": "新版正文"})
        assert updated.status_code == 200
        assert updated.json()["name"] == "正式旁白"
        assert updated.json()["engine"] == "voxcpm"

        copied = client.post(f"/api/projects/{project_id}/copy", json={"name": "正式旁白 B"})
        assert copied.status_code == 201
        assert copied.json()["sourceProjectId"] == project_id
        listing = client.get("/api/projects", params={"query": "新版", "engine": "voxcpm"}).json()
        assert listing["total"] == 2

        assert client.delete(f"/api/projects/{project_id}").status_code == 204
        assert client.get(f"/api/projects/{project_id}").status_code == 404
        assert client.get("/api/projects/../../settings.json").status_code in {400, 404}


def test_settings_persist_revision_conflict_migration_and_future_version_rejection(tmp_path):
    with make_client(tmp_path) as client:
        initial = client.get("/api/settings").json()
        assert initial["schemaVersion"] == 1
        changed = client.patch("/api/settings", json={
            "expectedRevision": initial["revision"], "theme": "dark",
            "defaultEngine": "gpt_sovits", "outputDirectory": str(tmp_path / "exports"),
        })
        assert changed.status_code == 200, changed.text
        assert changed.json()["revision"] == initial["revision"] + 1
        generated = client.post("/api/jobs", json={"engine": "indextts2", "text": "自定义目录验证。"})
        completed = wait_completed(client, generated.json()["id"])
        assert Path(completed["output_path"]).parent == (tmp_path / "exports").resolve()
        conflict = client.patch("/api/settings", json={
            "expectedRevision": initial["revision"], "theme": "light",
        })
        assert conflict.status_code == 409

    persisted = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert persisted["theme"] == "dark"
    assert not list(tmp_path.glob("*.tmp"))

    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "settings.json").write_text(json.dumps({
        "theme": "light", "default_engine": "indextts2", "revision": 3,
    }), encoding="utf-8")
    with make_client(legacy_root) as client:
        migrated = client.get("/api/settings")
        assert migrated.status_code == 200
        assert migrated.json()["schemaVersion"] == 1
        assert migrated.json()["revision"] == 3
    assert json.loads((legacy_root / "settings.json").read_text(encoding="utf-8"))["schemaVersion"] == 1

    future_root = tmp_path / "future"
    (future_root / "projects").mkdir(parents=True)
    future_id = "a" * 32
    (future_root / "projects" / f"{future_id}.json").write_text(json.dumps({
        "schemaVersion": 999, "id": future_id,
    }), encoding="utf-8")
    with make_client(future_root) as client:
        rejected = client.get("/api/projects")
        assert rejected.status_code == 409
        assert "schemaVersion=999" in rejected.json()["detail"]


def test_history_audio_metadata_and_output_path_confinement(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post("/api/jobs", json={
            "engine": "indextts2", "title": "可检索旁白", "text": "真实音频元数据测试。",
            "params": {"mock_sample_rate": 16000},
        })
        assert response.status_code == 202
        job = wait_completed(client, response.json()["id"])
        job_id = job["id"]

        history = client.get("/api/history", params={"query": "可检索", "hasOutput": True}).json()
        assert history["total"] == 1
        assert history["items"][0]["output"]["sampleRate"] == 44100
        library = client.get("/api/library/audio", params={"engine": "indextts2"}).json()
        assert library["total"] == 1
        assert library["items"][0]["output"]["durationSeconds"] > 0

        resolved = client.get(f"/api/jobs/{job_id}/output")
        assert resolved.status_code == 200
        contract = resolved.json()["openContract"]
        assert contract["open"]["method"] == "shell.openPath"
        assert Path(contract["open"]["path"]).is_file()

        manifest_path = tmp_path / "jobs" / job_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        outside = tmp_path / "outside.wav"
        outside.write_bytes(b"not audio")
        manifest["output_path"] = str(outside)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        unsafe = client.get(f"/api/jobs/{job_id}/output")
        assert unsafe.status_code == 409
        assert "越过任务目录" in unsafe.json()["detail"]


def test_delete_job_can_preserve_or_remove_generated_audio(tmp_path):
    with make_client(tmp_path) as client:
        preserved = client.post("/api/jobs", json={
            "engine": "indextts2", "text": "保留音频文件。",
        })
        preserved_job = wait_completed(client, preserved.json()["id"])
        preserved_path = Path(preserved_job["output_path"])
        assert preserved_path.is_file()

        only_record = client.delete(f"/api/jobs/{preserved_job['id']}", params={"deleteOutput": False})
        assert only_record.status_code == 200, only_record.text
        assert only_record.json()["outputDeleted"] is False
        assert preserved_path.is_file()
        assert client.get(f"/api/jobs/{preserved_job['id']}").status_code == 404

        removed = client.post("/api/jobs", json={
            "engine": "indextts2", "text": "删除音频文件。",
        })
        removed_job = wait_completed(client, removed.json()["id"])
        removed_path = Path(removed_job["output_path"])
        assert removed_path.is_file()

        with_file = client.delete(f"/api/jobs/{removed_job['id']}", params={"deleteOutput": True})
        assert with_file.status_code == 200, with_file.text
        assert with_file.json()["outputDeleted"] is True
        assert not removed_path.exists()


def test_delete_record_preserves_legacy_internal_output_and_rejects_unsafe_file_delete(tmp_path):
    with make_client(tmp_path) as client:
        created = client.post("/api/jobs", json={"engine": "indextts2", "text": "旧版输出兼容。"})
        job = wait_completed(client, created.json()["id"])
        job_id = job["id"]
        manifest_path = tmp_path / "jobs" / job_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        generated_path = Path(job["output_path"])
        legacy_path = manifest_path.parent / generated_path.name
        generated_path.replace(legacy_path)
        manifest["output_path"] = str(legacy_path)
        manifest["output_directory"] = None
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        deleted = client.delete(f"/api/jobs/{job_id}", params={"deleteOutput": False})
        assert deleted.status_code == 200, deleted.text
        preserved_path = Path(deleted.json()["preservedOutputPath"])
        assert preserved_path.is_file()
        assert preserved_path.parent == (tmp_path / "output").resolve()

        unsafe = client.post("/api/jobs", json={"engine": "indextts2", "text": "异常路径记录。"})
        unsafe_job = wait_completed(client, unsafe.json()["id"])
        unsafe_manifest_path = tmp_path / "jobs" / unsafe_job["id"] / "manifest.json"
        unsafe_manifest = json.loads(unsafe_manifest_path.read_text(encoding="utf-8"))
        unsafe_manifest["output_path"] = str(tmp_path / "outside.wav")
        unsafe_manifest["output_directory"] = None
        unsafe_manifest_path.write_text(json.dumps(unsafe_manifest), encoding="utf-8")
        protected = client.delete(f"/api/jobs/{unsafe_job['id']}", params={"deleteOutput": True})
        assert protected.status_code == 409
        record_only = client.delete(f"/api/jobs/{unsafe_job['id']}", params={"deleteOutput": False})
        assert record_only.status_code == 200


def test_diagnostic_export_is_real_zip_and_update_is_electron_contract(tmp_path):
    with make_client(tmp_path) as client:
        exported = client.post("/api/diagnostics/exports")
        assert exported.status_code == 201, exported.text
        metadata = exported.json()
        assert metadata["sizeBytes"] > 0
        assert len(metadata["sha256"]) == 64
        listing = client.get("/api/diagnostics/exports").json()
        assert listing["items"][0]["id"] == metadata["id"]

        downloaded = client.get(f"/api/diagnostics/exports/{metadata['id']}")
        assert downloaded.status_code == 200
        with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
            payload = json.loads(archive.read("diagnostics.json"))
        assert payload["schemaVersion"] == 1
        assert "engines" in payload and "installations" in payload and "jobs" in payload

        update = client.get("/api/update/config").json()
        assert update["handledBy"] == "electron"
        assert update["backendPerformsUpdateChecks"] is False
        assert "updateAvailable" not in update
