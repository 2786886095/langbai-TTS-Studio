from __future__ import annotations

import copy
import json
import sys
import time
from collections import namedtuple
from pathlib import Path

import pytest


Disk = namedtuple("Disk", "total used free")


@pytest.fixture()
def installer_types(project_root: Path):
    sys.path.insert(0, str(project_root / "backend"))
    from app.installer.catalog import ENGINE_TOOL_REQUIREMENTS, INSTALLER_CATALOG, MANAGED_TOOL_CATALOG
    from app.installer.manager import InstallConflictError, InstallerManager
    from app.installer.models import InstallRequest, InstallStatus, ModelInstallRequest
    from app.installer.runner import CommandResult, CommandStopped

    return {
        "catalog": INSTALLER_CATALOG,
        "tool_catalog": MANAGED_TOOL_CATALOG,
        "tool_requirements": ENGINE_TOOL_REQUIREMENTS,
        "conflict": InstallConflictError,
        "manager": InstallerManager,
        "install_request": InstallRequest,
        "model_request": ModelInstallRequest,
        "status": InstallStatus,
        "result": CommandResult,
        "stopped": CommandStopped,
    }


class RecoverableRunner:
    def __init__(self, catalog: dict, result_type, *, fail_dependency_once: bool = False):
        self.catalog = catalog
        self.result_type = result_type
        self.fail_dependency_once = fail_dependency_once
        self.clone_calls = 0
        self.download_calls = 0
        self.commands: list[list[str]] = []

    def run(self, argv, *, cwd, env, stop_reason, on_output):
        self.commands.append(list(argv))
        if stop_reason():
            raise RuntimeError(stop_reason())
        on_output("50% acceptance fixture")
        if "clone" in argv:
            self.clone_calls += 1
            destination = Path(argv[-1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / ".git").mkdir()
            (destination / "extra-req.txt").write_text("", encoding="utf-8")
            (destination / "requirements.txt").write_text("", encoding="utf-8")
        if "venv" in argv:
            environment = Path(argv[-1])
            python = environment / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"fixture interpreter")
        if "sync" in argv and self.fail_dependency_once:
            self.fail_dependency_once = False
            raise RuntimeError("fixture dependency failure")
        if "download" in argv and "--local-dir" in argv:
            self.download_calls += 1
            model_dir = Path(argv[argv.index("--local-dir") + 1])
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "weights.bin").write_bytes(b"fixture model weights")
        if "rev-parse" in argv:
            source_urls = {argument for command in self.commands for argument in command}
            revision = next(
                item["revision"] for item in self.catalog.values() if item["source_url"] in source_urls
            )
            return self.result_type(0, revision)
        return self.result_type(0, "")


class RecoverableSupply:
    def __init__(self, catalog: dict, tool_catalog: dict, stopped_type):
        self.catalog = catalog
        self.tool_catalog = tool_catalog
        self.stopped_type = stopped_type
        self.source_calls = 0

    def _tool_root(self, root: Path, tool_id: str) -> Path:
        item = self.tool_catalog[tool_id]
        return Path(root) / "tools" / tool_id / item["version"]

    def tool_status(self, root: Path, tool_id: str) -> dict:
        item = self.tool_catalog[tool_id]
        path = self._tool_root(root, tool_id)
        executables = {name: str(path / name) for name in item["executables"]}
        installed = all(Path(value).is_file() for value in executables.values())
        return {
            "id": tool_id, "name": item["name"], "version": item["version"],
            "installed": installed, "state": "installed" if installed else "missing",
            "path": str(path), "executables": executables, "archiveUrl": item["archive_url"],
            "sha256": item["sha256"], "license": item["license"], "licenseUrl": item["license_url"],
        }

    def ensure_tool(self, root, tool_id, *, license_accepted_at, job_id, on_progress, stop_reason):
        reason = stop_reason()
        if reason:
            raise self.stopped_type(reason)
        item = self.tool_catalog[tool_id]
        path = self._tool_root(root, tool_id)
        for relative in item["executables"]:
            executable = path / relative
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"managed acceptance fixture")
        on_progress(item["archive_bytes"], item["archive_bytes"])
        return path / item["executables"][0]

    def install_source(self, item, source, work, *, on_progress, stop_reason):
        self.source_calls += 1
        reason = stop_reason()
        if reason:
            raise self.stopped_type(reason)
        source.mkdir(parents=True, exist_ok=True)
        (source / "extra-req.txt").write_text("", encoding="utf-8")
        (source / "requirements.txt").write_text("", encoding="utf-8")
        (source / "source-manifest.json").write_text(json.dumps({
            "revision": item["revision"], "archiveSha256": item["sha256"],
        }), encoding="utf-8")
        on_progress(item["estimated_source_bytes"], item["estimated_source_bytes"])

    @staticmethod
    def source_valid(source: Path, item: dict) -> bool:
        try:
            manifest = json.loads((source / "source-manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return manifest == {"revision": item["revision"], "archiveSha256": item["sha256"]}


def _manager(tmp_path: Path, types: dict, runner, *, free: int = 10**12):
    catalog = copy.deepcopy(types["catalog"])
    for engine in catalog.values():
        engine["estimated_source_bytes"] = 100
        engine["estimated_environment_bytes"] = 100
        for model in engine["models"]:
            model["estimated_download_bytes"] = 100
            model["estimated_installed_bytes"] = 100
    supply = RecoverableSupply(catalog, types["tool_catalog"], types["stopped"])
    return types["manager"](
        tmp_path / "state",
        default_install_root=tmp_path / "managed",
        runner=runner,
        disk_usage=lambda _: Disk(10**12, 0, free),
        catalog=catalog,
        supply=supply,
    )


def _setup_request(types: dict, engine: str, root: Path, **changes):
    payload = {
        "installRoot": str(root), "acceptLicense": True, "acceptPythonLicense": True,
        "acceptedToolLicenses": types["tool_requirements"][engine],
    }
    payload.update(changes)
    return types["install_request"](**payload)


def _wait(manager, job_id: str, statuses: set[str], timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job and job.status.value in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(f"Installer job did not reach {statuses}")


def test_environment_failure_cleans_partial_bundle_and_retry_redownloads_source(
    tmp_path: Path, installer_types: dict
) -> None:
    runner = RecoverableRunner(
        installer_types["catalog"], installer_types["result"], fail_dependency_once=True
    )
    manager = _manager(tmp_path, installer_types, runner)
    manager.start()
    try:
        job = manager.setup(
            "indextts2",
            _setup_request(installer_types, "indextts2", tmp_path / "managed", device="CPU"),
        )
        failed = _wait(manager, job.id, {"failed"})
        assert "source_verified" in failed.completed_stages
        assert manager.supply.source_calls == 1
        assert not (tmp_path / "managed" / ".installer-tmp" / job.id).exists()
        assert runner.clone_calls == 0
        manager.action(job.id, "retry")
        completed = _wait(manager, job.id, {"completed"})
        assert completed.status.value == "completed", completed.error
        assert manager.supply.source_calls == 2
        assert runner.clone_calls == 0, "Managed source installation must never invoke Git"
    finally:
        manager.close()


def test_setup_manifest_persists_license_url_and_separate_model_location(
    tmp_path: Path, installer_types: dict
) -> None:
    runner = RecoverableRunner(installer_types["catalog"], installer_types["result"])
    manager = _manager(tmp_path, installer_types, runner)
    manager.start()
    try:
        job = manager.setup(
            "voxcpm",
            _setup_request(installer_types, "voxcpm", tmp_path / "managed"),
        )
        completed = _wait(manager, job.id, {"completed"})
        manifest_path = Path(completed.source_path).parent / "installation.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["code_license"]
        assert manifest["code_license_url"]
        assert manifest["source_archive_sha256"] == installer_types["catalog"]["voxcpm"]["sha256"]
        assert manifest["python_license_accepted_at"]
        assert manifest["managed_tools"]["uv"]
        assert manifest["models"].startswith("../../models/")
        assert not (Path(completed.source_path).parent / "models").exists()
    finally:
        manager.close()


def test_fake_git_and_model_manifests_are_not_trusted_as_complete(
    tmp_path: Path, installer_types: dict
) -> None:
    runner = RecoverableRunner(installer_types["catalog"], installer_types["result"])
    manager = _manager(tmp_path, installer_types, runner)
    root = tmp_path / "managed"
    installation = root / "installations" / "voxcpm"
    git_dir = installation / "source" / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text(
        installer_types["catalog"]["voxcpm"]["revision"], encoding="utf-8"
    )
    interpreter = installation / "env" / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"fixture interpreter")
    (installation / "installation.json").write_text(
        json.dumps({"revision": installer_types["catalog"]["voxcpm"]["revision"]}),
        encoding="utf-8",
    )
    detected = manager.inspect_engine("voxcpm", root)
    assert not detected["installed"]
    assert not detected["source"]["installed"], ".git/HEAD is no longer an installer trust root"
    model = installer_types["catalog"]["voxcpm"]["models"][0]
    with pytest.raises(ValueError, match="先完成源码和环境安装"):
        manager.install_model(
            "voxcpm",
            installer_types["model_request"](
                installRoot=str(root), modelId=model["id"], acceptLicense=True
            ),
        )

    clean_root = tmp_path / "valid-engine"
    manager.start()
    try:
        setup = manager.setup("voxcpm", _setup_request(installer_types, "voxcpm", clean_root))
        assert _wait(manager, setup.id, {"completed"}).status.value == "completed"
        model_dir = clean_root / "models" / "voxcpm" / model["id"]
        model_dir.mkdir(parents=True)
        (model_dir / "weights.bin").write_bytes(b"tampered")
        (model_dir / "model-manifest.json").write_text(json.dumps({
            "engine": "voxcpm", "model_id": model["id"], "revision": model["revision"],
            "content_manifest_sha256": "0" * 64, "installed_bytes": 8,
        }), encoding="utf-8")
        with pytest.raises(installer_types["conflict"]):
            manager.install_model(
                "voxcpm",
                installer_types["model_request"](
                    installRoot=str(clean_root), modelId=model["id"], acceptLicense=True
                ),
            )
    finally:
        manager.close()


def test_disk_rejection_happens_before_install_root_is_created(
    tmp_path: Path, installer_types: dict
) -> None:
    runner = RecoverableRunner(installer_types["catalog"], installer_types["result"])
    manager = _manager(tmp_path, installer_types, runner, free=1)
    root = tmp_path / "not-created"
    with pytest.raises(ValueError, match="磁盘空间不足"):
        manager.setup(
            "gpt_sovits",
            _setup_request(installer_types, "gpt_sovits", root),
        )
    assert not root.exists()
    assert not runner.commands
