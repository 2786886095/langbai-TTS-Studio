import copy
import json
import time
from collections import namedtuple
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.main import create_app
from app.installer.catalog import (
    ENGINE_TOOL_REQUIREMENTS,
    INSTALLER_CATALOG,
    MANAGED_TOOL_CATALOG,
    OFFICIAL_SOURCE_URLS,
)
from app.installer.manager import InstallConflictError, InstallerManager
from app.installer.models import InstallRequest, InstallStatus, ModelInstallRequest, ToolRepairRequest
from app.installer.runner import CommandResult, CommandStopped


Disk = namedtuple("Disk", "total used free")


class FakeRunner:
    def __init__(self):
        self.commands = []

    def run(self, argv, *, cwd, env, stop_reason, on_output):
        self.commands.append((list(argv), str(cwd), dict(env or {})))
        if stop_reason():
            raise CommandStopped(stop_reason())
        on_output("50% fixture progress")
        if "clone" in argv:
            destination = Path(argv[-1])
            destination.mkdir(parents=True)
            (destination / ".git").mkdir()
            (destination / "extra-req.txt").write_text("", encoding="utf-8")
            (destination / "requirements.txt").write_text("", encoding="utf-8")
        if "checkout" in argv and "-C" in argv:
            source = Path(argv[argv.index("-C") + 1])
            (source / ".git" / "HEAD").write_text(argv[-1] + "\n", encoding="utf-8")
        if "venv" in argv:
            env_dir = Path(argv[-1])
            python = env_dir / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"fixture")
        if "download" in argv and "--local-dir" in argv:
            model_dir = Path(argv[argv.index("--local-dir") + 1])
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "weights.bin").write_bytes(b"official fixture weights")
        if "rev-parse" in argv:
            revision = next(value["revision"] for value in INSTALLER_CATALOG.values()
                            if value["source_url"] in [cmd for command, _, _ in self.commands for cmd in command])
            return CommandResult(0, revision)
        return CommandResult(0, "")


class FakeSupply:
    def __init__(self, *, fail_source_once=False):
        self.fail_source_once = fail_source_once
        self.source_calls = 0
        self.tool_calls = []

    @staticmethod
    def _tool_path(root, tool_id):
        item = MANAGED_TOOL_CATALOG[tool_id]
        return Path(root) / "tools" / tool_id / item["version"]

    def tool_status(self, root, tool_id):
        item = MANAGED_TOOL_CATALOG[tool_id]
        path = self._tool_path(root, tool_id)
        executables = {name: str(path / name) for name in item["executables"]}
        installed = all(Path(value).is_file() for value in executables.values())
        return {
            "id": tool_id, "name": item["name"], "version": item["version"],
            "installed": installed, "state": "installed" if installed else "missing",
            "path": str(path), "executables": executables,
            "archiveUrl": item["archive_url"], "sha256": item["sha256"],
            "license": item["license"], "licenseUrl": item["license_url"],
        }

    def ensure_tool(self, root, tool_id, *, license_accepted_at, job_id, on_progress, stop_reason):
        reason = stop_reason()
        if reason:
            raise CommandStopped(reason)
        self.tool_calls.append((tool_id, license_accepted_at, job_id))
        item = MANAGED_TOOL_CATALOG[tool_id]
        path = self._tool_path(root, tool_id)
        for relative in item["executables"]:
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"managed fixture")
        (path / "tool-manifest.json").write_text(json.dumps({
            "archiveSha256": item["sha256"],
        }), encoding="utf-8")
        on_progress(item["archive_bytes"], item["archive_bytes"])
        return path / item["executables"][0]

    def install_source(self, item, source, work, *, on_progress, stop_reason):
        self.source_calls += 1
        if self.fail_source_once and self.source_calls == 1:
            work.mkdir(parents=True, exist_ok=True)
            (work / "partial.zip.part").write_bytes(b"partial")
            raise RuntimeError("fixture network failure")
        reason = stop_reason()
        if reason:
            raise CommandStopped(reason)
        source.mkdir(parents=True, exist_ok=True)
        (source / "extra-req.txt").write_text("", encoding="utf-8")
        (source / "requirements.txt").write_text("", encoding="utf-8")
        (source / "source-manifest.json").write_text(json.dumps({
            "revision": item["revision"], "archiveSha256": item["sha256"],
        }), encoding="utf-8")
        on_progress(item["estimated_source_bytes"], item["estimated_source_bytes"])

    @staticmethod
    def source_valid(source, item):
        try:
            data = json.loads((source / "source-manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return data == {"revision": item["revision"], "archiveSha256": item["sha256"]}


class FailOnceRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.failed = False

    def run(self, argv, **kwargs):
        if "clone" in argv and not self.failed:
            self.failed = True
            raise RuntimeError("fixture network failure")
        return super().run(argv, **kwargs)


class PausableModelRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.download_calls = 0

    def run(self, argv, *, cwd, env, stop_reason, on_output):
        if "download" in argv:
            self.download_calls += 1
            if self.download_calls == 1:
                deadline = time.time() + 3
                while time.time() < deadline:
                    reason = stop_reason()
                    if reason:
                        raise CommandStopped(reason)
                    time.sleep(0.01)
                raise AssertionError("pause was not delivered")
        return super().run(argv, cwd=cwd, env=env, stop_reason=stop_reason, on_output=on_output)


def wait_job(manager, job_id, states=(InstallStatus.completed, InstallStatus.failed, InstallStatus.cancelled), timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = manager.get_job(job_id)
        if job.status in states:
            return job
        time.sleep(0.02)
    raise AssertionError("installer job timeout")


def make_manager(tmp_path, runner=None, free=100_000_000_000, supply=None):
    catalog = copy.deepcopy(INSTALLER_CATALOG)
    for item in catalog.values():
        item["estimated_source_bytes"] = 100
        item["estimated_environment_bytes"] = 100
        for model in item["models"]:
            model["estimated_download_bytes"] = 100
            model["estimated_installed_bytes"] = 100
    return InstallerManager(
        tmp_path / "state", default_install_root=tmp_path / "managed", runner=runner or FakeRunner(),
        disk_usage=lambda _: Disk(100_000_000_000, 0, free),
        which=lambda _: None, catalog=catalog, supply=supply or FakeSupply(),
    )


def setup_request(engine, root, **changes):
    payload = {
        "installRoot": str(root), "acceptLicense": True, "acceptPythonLicense": True,
        "acceptedToolLicenses": ENGINE_TOOL_REQUIREMENTS[engine],
    }
    payload.update(changes)
    return InstallRequest(**payload)


def test_catalog_is_pinned_and_official():
    assert len(INSTALLER_CATALOG) == 3
    for item in INSTALLER_CATALOG.values():
        assert item["source_url"] in OFFICIAL_SOURCE_URLS
        assert len(item["revision"]) == 40
        assert item["code_license"] and item["code_license_url"]
        assert item["revision"] in item["code_license_url"]
        assert "sha256" in item
        assert len(item["sha256"]) == 64
        assert item["revision"] in item["source_url"]
        assert "latest" not in item["source_url"].lower()
        for model in item["models"]:
            assert model["estimated_download_bytes"] > 0
            assert model["estimated_installed_bytes"] >= model["estimated_download_bytes"]
            assert len(model["revision"]) == 40
            assert model["revision"] in model["license_url"]
    for tool in MANAGED_TOOL_CATALOG.values():
        assert tool["version"] in tool["archive_url"]
        assert len(tool["sha256"]) == 64
        assert tool["checksum_url"].startswith("https://")
        assert "latest" not in tool["archive_url"].lower()


def test_default_managed_root_is_scoped_to_app_data_not_developer_drive(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGBAI_INSTALL_ROOT", raising=False)
    data_dir = tmp_path / "app-data"
    app = create_app(data_dir=data_dir, mock_mode=True)
    assert app.state.installer.default_install_root == (data_dir / "managed").resolve()


def test_setup_requires_code_python_and_each_tool_license(tmp_path):
    manager = make_manager(tmp_path)
    root = tmp_path / "managed"
    with pytest.raises(ValueError, match="官方代码许可证"):
        manager.setup("gpt_sovits", InstallRequest(
            installRoot=str(root), acceptPythonLicense=True, acceptedToolLicenses=["uv", "ffmpeg"]
        ))
    with pytest.raises(ValueError, match="Python Software Foundation License"):
        manager.setup("gpt_sovits", InstallRequest(
            installRoot=str(root), acceptLicense=True, acceptedToolLicenses=["uv", "ffmpeg"]
        ))
    with pytest.raises(ValueError, match="ffmpeg"):
        manager.setup("gpt_sovits", InstallRequest(
            installRoot=str(root), acceptLicense=True, acceptPythonLicense=True,
            acceptedToolLicenses=["uv"],
        ))


def test_rejects_relative_root_and_insufficient_disk(tmp_path):
    manager = make_manager(tmp_path, free=10)
    with pytest.raises(ValueError, match="绝对路径"):
        manager.setup("indextts2", setup_request("indextts2", "relative"))
    with pytest.raises(ValueError, match="磁盘空间不足"):
        manager.setup("indextts2", setup_request("indextts2", tmp_path / "managed"))


def test_setup_and_model_are_atomic_and_separate(tmp_path):
    runner = FakeRunner()
    manager = make_manager(tmp_path, runner)
    manager.start()
    try:
        setup = manager.setup("voxcpm", setup_request("voxcpm", tmp_path / "managed"))
        done = wait_job(manager, setup.id)
        assert done.status == InstallStatus.completed, done.error
        source = Path(done.source_path)
        env = Path(done.env_path)
        assert source.is_dir() and env.is_dir()
        assert source.parent == env.parent
        assert not (tmp_path / "managed" / "models" / "voxcpm").exists()
        status = manager.inspect_engine("voxcpm", tmp_path / "managed")
        assert status["installed"]

        model_job = manager.install_model("voxcpm", ModelInstallRequest(
            installRoot=str(tmp_path / "managed"), modelId="voxcpm2-official", acceptLicense=True
        ))
        model_done = wait_job(manager, model_job.id)
        assert model_done.status == InstallStatus.completed, model_done.error
        model_path = Path(model_done.model_path)
        assert model_path.is_dir() and model_path.parent.parent.name == "models"
        manifest = json.loads((model_path / "model-manifest.json").read_text(encoding="utf-8"))
        assert manifest["revision"] == INSTALLER_CATALOG["voxcpm"]["models"][0]["revision"]
        assert model_done.computed_sha256
        assert all(isinstance(command, list) for command, _, _ in runner.commands)
        flattened = [part for command, _, _ in runner.commands for part in command]
        assert "git" not in flattened and "clone" not in flattened
        assert any("managed" in command[0] and command[0].endswith("uv.exe") for command, _, _ in runner.commands)
    finally:
        manager.close()


def test_existing_unrecognized_directory_is_never_overwritten(tmp_path):
    manager = make_manager(tmp_path)
    existing = tmp_path / "managed" / "installations" / "gpt_sovits"
    existing.mkdir(parents=True)
    sentinel = existing / "user-file.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(InstallConflictError):
        manager.setup("gpt_sovits", setup_request("gpt_sovits", tmp_path / "managed"))
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_safe_child_rejects_escape(tmp_path):
    root = (tmp_path / "managed").resolve()
    with pytest.raises(ValueError, match="逃逸"):
        InstallerManager._child(root, "..", "outside")


def test_failed_setup_retry_keeps_failure_truthful_then_completes(tmp_path):
    manager = make_manager(tmp_path, FakeRunner(), supply=FakeSupply(fail_source_once=True))
    manager.start()
    try:
        created = manager.setup("indextts2", setup_request("indextts2", tmp_path / "managed"))
        failed = wait_job(manager, created.id)
        assert failed.status == InstallStatus.failed
        assert "fixture network failure" in failed.error
        assert not (tmp_path / "managed" / "installations" / "indextts2").exists()
        assert not (tmp_path / "managed" / ".installer-tmp" / created.id).exists()
        manager.action(created.id, "retry")
        completed = wait_job(manager, created.id)
        assert completed.status == InstallStatus.completed, completed.error
    finally:
        manager.close()


def test_model_download_pause_and_resume(tmp_path):
    runner = PausableModelRunner()
    manager = make_manager(tmp_path, runner)
    manager.start()
    try:
        setup = manager.setup("voxcpm", setup_request("voxcpm", tmp_path / "managed"))
        assert wait_job(manager, setup.id).status == InstallStatus.completed
        model = manager.install_model("voxcpm", ModelInstallRequest(
            installRoot=str(tmp_path / "managed"), modelId="voxcpm2-official", acceptLicense=True
        ))
        deadline = time.time() + 3
        while time.time() < deadline and manager.get_job(model.id).status != InstallStatus.running:
            time.sleep(0.01)
        manager.action(model.id, "pause")
        paused = wait_job(manager, model.id, states=(InstallStatus.paused,))
        assert paused.status == InstallStatus.paused
        manager.action(model.id, "resume")
        completed = wait_job(manager, model.id)
        assert completed.status == InstallStatus.completed, completed.error
        assert runner.download_calls == 2
        model_command = next(command for command, _, _ in runner.commands if "download" in command)
        assert "huggingface-hub[cli,hf_xet]==1.24.0" in model_command
    finally:
        manager.close()


def test_installer_http_contract(tmp_path):
    installer = make_manager(tmp_path, FakeRunner())
    adapters = {name: MockAdapter(name) for name in ("indextts2", "voxcpm", "gpt_sovits")}
    app = create_app(adapters=adapters, data_dir=tmp_path / "api-state", mock_mode=True,
                     installer_manager=installer)
    with TestClient(app) as client:
        catalog = client.get("/api/installer/catalog")
        assert catalog.status_code == 200 and len(catalog.json()) == 3
        response = client.post("/api/installations/indextts2/setup", json={
            "installRoot": str(tmp_path / "managed"), "acceptLicense": True, "device": "CPU",
            "acceptPythonLicense": True, "acceptedToolLicenses": ["uv"],
        })
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        deadline = time.time() + 5
        while time.time() < deadline:
            payload = client.get(f"/api/downloads/{job_id}").json()
            if payload["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        assert payload["status"] == "completed", payload
        installations = client.get("/api/installations", params={"installRoot": str(tmp_path / "managed")})
        assert installations.status_code == 200
        assert next(item for item in installations.json() if item["engine"] == "indextts2")["installed"]

        tools = client.get("/api/installer/tools", params={"installRoot": str(tmp_path / "managed")})
        assert tools.status_code == 200
        assert next(item for item in tools.json() if item["id"] == "uv")["installed"]


def test_tool_repair_is_explicit_and_does_not_touch_models(tmp_path):
    supply = FakeSupply()
    manager = make_manager(tmp_path, supply=supply)
    manager.start()
    try:
        root = tmp_path / "managed"
        with pytest.raises(ValueError, match="明确接受"):
            manager.repair_tool("ffmpeg", ToolRepairRequest(installRoot=str(root)))
        created = manager.repair_tool("ffmpeg", ToolRepairRequest(
            installRoot=str(root), acceptLicense=True,
        ))
        done = wait_job(manager, created.id)
        assert done.status == InstallStatus.completed, done.error
        assert supply.tool_status(root, "ffmpeg")["installed"]
        assert not (root / "models").exists()
    finally:
        manager.close()
