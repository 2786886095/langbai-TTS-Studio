from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from ..jobs import EventBroker
from ..models import now_iso
from .catalog import (
    ENGINE_TOOL_REQUIREMENTS,
    INSTALLER_CATALOG,
    MANAGED_TOOL_CATALOG,
    MANAGED_PYTHON_LICENSE,
    MODEL_DOWNLOAD_TOOL,
    OFFICIAL_MODEL_REPOS,
    OFFICIAL_SOURCE_URLS,
)
from .models import InstallJob, InstallRequest, InstallStatus, ModelInstallRequest, ToolRepairRequest
from .integrity import MODEL_INTEGRITY_VERIFIER, content_manifest
from .runner import CommandRunner, CommandStopped
from .storage import InstallJobStore
from .supply import ManagedSupply


class InstallConflictError(RuntimeError):
    pass


class InstallerManager:
    def __init__(
        self,
        data_dir: str | Path,
        *,
        default_install_root: str | Path,
        runner: Any | None = None,
        disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
        which: Callable[[str], str | None] | None = None,
        catalog: dict[str, dict] | None = None,
        supply: ManagedSupply | None = None,
    ):
        self.store = InstallJobStore(Path(data_dir) / "installer_jobs")
        self.default_install_root = Path(default_install_root)
        self.runner = runner or CommandRunner()
        self.disk_usage = disk_usage
        # Kept as a constructor compatibility hook for older test factories;
        # production installation deliberately never probes the system PATH.
        _ = which
        self.catalog = catalog or INSTALLER_CATALOG
        self.supply = supply or ManagedSupply()
        self.events = EventBroker()
        self._queue: list[str] = []
        self._condition = threading.Condition()
        self._stop_flags: dict[str, str] = {}
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, name="installer-worker", daemon=True)
        self._thread.start()
        for job in reversed(self.store.list()):
            if job.status in (InstallStatus.queued, InstallStatus.running):
                job.status = InstallStatus.queued
                job.phase = "recovered"
                job.message = "服务重启后恢复排队"
                job.updated_at = now_iso()
                self.store.save(job)
                self._enqueue(job.id)

    def close(self) -> None:
        with self._condition:
            self._queue.append("")
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=5)

    def catalog_payload(self) -> list[dict]:
        result = []
        for engine, item in self.catalog.items():
            payload = json.loads(json.dumps(item))
            payload["required_tools"] = [
                json.loads(json.dumps(MANAGED_TOOL_CATALOG[tool_id]))
                for tool_id in ENGINE_TOOL_REQUIREMENTS[engine]
            ]
            payload["required_runtime_licenses"] = [json.loads(json.dumps(MANAGED_PYTHON_LICENSE))]
            result.append(payload)
        return result

    def tools_payload(self, requested_root: str | None = None) -> list[dict]:
        root = self._root(requested_root)
        return [self.supply.tool_status(root, tool_id) for tool_id in MANAGED_TOOL_CATALOG]

    def _root(self, requested: str | None) -> Path:
        raw = Path(requested) if requested else self.default_install_root
        if not raw.is_absolute():
            raise ValueError("安装根目录必须是绝对路径")
        root = raw.resolve()
        if root.parent == root:
            raise ValueError("不允许把磁盘根目录作为安装根目录")
        if root.exists() and not root.is_dir():
            raise ValueError("安装根路径不是目录")
        return root

    @staticmethod
    def _child(root: Path, *parts: str) -> Path:
        target = root.joinpath(*parts).resolve()
        if root != target and root not in target.parents:
            raise ValueError("安装路径逃逸")
        return target

    def _existing_parent(self, path: Path) -> Path:
        current = path
        while not current.exists() and current.parent != current:
            current = current.parent
        return current

    def _check_disk(self, root: Path, required: int) -> None:
        usage = self.disk_usage(self._existing_parent(root))
        free = usage.free if hasattr(usage, "free") else usage[2]
        if free < required:
            raise ValueError(f"磁盘空间不足：需要约 {required} 字节，可用 {free} 字节")

    def setup(self, engine: str, request: InstallRequest) -> InstallJob:
        item = self._catalog_item(engine)
        if not request.accept_license:
            raise ValueError("必须明确接受官方代码许可证后才能安装")
        if not request.accept_python_license:
            raise ValueError("必须明确接受 Python Software Foundation License 后才能创建托管 Python 环境")
        if request.device not in {"CPU", "CU126", "CU128"}:
            raise ValueError("device 必须是 CPU、CU126 或 CU128")
        required_tools = ENGINE_TOOL_REQUIREMENTS[engine]
        unknown_acceptances = sorted(set(request.accepted_tool_licenses) - set(required_tools))
        if unknown_acceptances:
            raise ValueError("请求包含当前引擎不需要的工具许可证: " + ", ".join(unknown_acceptances))
        missing_acceptances = [tool_id for tool_id in required_tools if tool_id not in request.accepted_tool_licenses]
        if missing_acceptances:
            details = ", ".join(
                f"{tool_id} ({MANAGED_TOOL_CATALOG[tool_id]['license']})" for tool_id in missing_acceptances
            )
            raise ValueError("必须明确接受托管工具许可证后才能下载或修复: " + details)
        root = self._root(request.install_root)
        final = self._child(root, "installations", engine)
        if final.exists():
            detected = self.inspect_engine(engine, root)
            if detected["installed"]:
                return self._detected_job(item, root, final, request.device)
            if not (detected["source"]["installed"] and detected["environment"]["installed"]):
                raise InstallConflictError(f"已有目录但源码或环境不完整，拒绝覆盖: {final}")
        required = int(item["estimated_source_bytes"] + item["estimated_environment_bytes"])
        required += sum(MANAGED_TOOL_CATALOG[tool_id]["archive_bytes"] for tool_id in required_tools)
        self._check_disk(root, required)
        accepted_at = now_iso()
        job = InstallJob(
            id=uuid.uuid4().hex, kind="setup", engine=engine, install_root=str(root),
            source_url=item["source_url"], revision=item["revision"], expected_sha256=item.get("sha256"),
            code_license=item["code_license"], license_accepted_at=accepted_at,
            python_license_accepted_at=accepted_at,
            tool_license_acceptances={tool_id: accepted_at for tool_id in required_tools},
            bytes_total=required, checkpoints={
                "device": request.device,
                "repair_existing_installation": final.exists(),
            },
        )
        self.store.save(job)
        self._enqueue(job.id)
        self._emit(job)
        return job

    def repair_tool(self, tool_id: str, request: ToolRepairRequest) -> InstallJob:
        item = MANAGED_TOOL_CATALOG.get(tool_id)
        if item is None:
            raise ValueError("未知托管工具")
        if not request.accept_license:
            raise ValueError(f"必须明确接受 {item['name']} 的许可证 {item['license']}")
        root = self._root(request.install_root)
        self._check_disk(root, int(item["archive_bytes"] * 3))
        accepted_at = now_iso()
        current = self.supply.tool_status(root, tool_id)
        if current["installed"]:
            job = InstallJob(
                id=uuid.uuid4().hex, kind="tool", engine="managed_tools", tool_id=tool_id,
                status=InstallStatus.completed, phase="already_installed", progress=1.0,
                message="托管工具已通过完整性校验，无需修复", install_root=str(root),
                source_url=item["archive_url"], revision=item["version"], expected_sha256=item["sha256"],
                code_license=item["license"], license_accepted_at=accepted_at,
                tool_license_acceptances={tool_id: accepted_at}, completed_stages=["detected"],
            )
            self.store.save(job)
            return job
        job = InstallJob(
            id=uuid.uuid4().hex, kind="tool", engine="managed_tools", tool_id=tool_id,
            install_root=str(root), source_url=item["archive_url"], revision=item["version"],
            expected_sha256=item["sha256"], code_license=item["license"],
            license_accepted_at=accepted_at, tool_license_acceptances={tool_id: accepted_at},
            bytes_total=item["archive_bytes"],
        )
        self.store.save(job)
        self._enqueue(job.id)
        self._emit(job)
        return job

    def install_model(self, engine: str, request: ModelInstallRequest) -> InstallJob:
        item = self._catalog_item(engine)
        if not request.accept_license:
            raise ValueError("必须明确接受官方模型许可证后才能下载")
        root = self._root(request.install_root)
        if not self.inspect_engine(engine, root)["installed"]:
            raise ValueError("请先完成源码和环境安装")
        model = next((value for value in item["models"] if value["id"] == request.model_id), None)
        if model is None or model["repo_id"] not in OFFICIAL_MODEL_REPOS:
            raise ValueError("未知或非官方模型")
        final = self._child(root, "models", engine, model["id"])
        if final.exists():
            manifest = final / "model-manifest.json"
            if manifest.is_file() and self._verify_model_directory(final, model):
                return self._detected_model_job(item, model, root, final)
            raise InstallConflictError(f"已有模型目录未通过 revision/内容哈希校验，拒绝覆盖: {final}")
        self._check_disk(root, int(model["estimated_installed_bytes"]))
        source_url = f"https://huggingface.co/{model['repo_id']}"
        job = InstallJob(
            id=uuid.uuid4().hex, kind="model", engine=engine, model_id=model["id"],
            install_root=str(root), source_url=source_url, revision=model["revision"],
            expected_sha256=model.get("sha256"), code_license=item["code_license"],
            model_license=model["license"], license_accepted_at=now_iso(),
            bytes_total=model["estimated_download_bytes"],
        )
        self.store.save(job)
        self._enqueue(job.id)
        self._emit(job)
        return job

    def _detected_job(self, item: dict, root: Path, final: Path, device: str) -> InstallJob:
        accepted_at = now_iso()
        job = InstallJob(
            id=uuid.uuid4().hex, kind="setup", engine=item["id"], status=InstallStatus.completed,
            phase="already_installed", progress=1.0, message="检测到完整安装，未覆盖任何文件",
            install_root=str(root), source_url=item["source_url"], revision=item["revision"],
            expected_sha256=item.get("sha256"), code_license=item["code_license"],
            license_accepted_at=accepted_at, python_license_accepted_at=accepted_at,
            tool_license_acceptances={tool_id: accepted_at for tool_id in ENGINE_TOOL_REQUIREMENTS[item["id"]]},
            source_path=str(final / "source"), env_path=str(final / "env"),
            completed_stages=["detected"], checkpoints={"device": device},
        )
        self.store.save(job)
        return job

    def _detected_model_job(self, item: dict, model: dict, root: Path, final: Path) -> InstallJob:
        job = InstallJob(
            id=uuid.uuid4().hex, kind="model", engine=item["id"], model_id=model["id"],
            status=InstallStatus.completed, phase="already_installed", progress=1.0,
            message="检测到完整模型，未覆盖任何文件", install_root=str(root),
            source_url=f"https://huggingface.co/{model['repo_id']}", revision=model["revision"],
            expected_sha256=model.get("sha256"), code_license=item["code_license"],
            model_license=model["license"], license_accepted_at=now_iso(), model_path=str(final),
            completed_stages=["detected"],
        )
        self.store.save(job)
        return job

    def list_jobs(self) -> list[InstallJob]:
        return self.store.list()

    def get_job(self, job_id: str) -> InstallJob | None:
        try:
            return self.store.load(job_id)
        except ValueError:
            return None

    def inspect_all(self, requested_root: str | None = None) -> list[dict]:
        root = self._root(requested_root)
        return [self.inspect_engine(engine, root) for engine in self.catalog]

    def inspect_engine(self, engine: str, root: Path) -> dict:
        item = self._catalog_item(engine)
        final = self._child(root, "installations", engine)
        source = final / "source"
        env = final / "env"
        python = self._env_python(env)
        manifest = final / "installation.json"
        revision = None
        installed_source_sha256 = None
        if manifest.is_file():
            try:
                install_data = json.loads(manifest.read_text(encoding="utf-8"))
                revision = install_data.get("revision")
                installed_source_sha256 = install_data.get("source_archive_sha256")
            except (OSError, json.JSONDecodeError):
                pass
        models = []
        for model in item["models"]:
            path = self._child(root, "models", engine, model["id"])
            model_ok = False
            model_state = "missing"
            model_manifest = path / "model-manifest.json"
            if path.exists():
                model_ok = model_manifest.is_file() and self._verify_model_directory(path, model)
                model_state = "installed" if model_ok else "integrity_failed"
            models.append({"id": model["id"], "installed": model_ok, "state": model_state, "path": str(path)})
        source_ok = (
            source.is_dir() and manifest.is_file() and revision == item["revision"]
            and installed_source_sha256 == item["sha256"] and self.supply.source_valid(source, item)
        )
        environment_ok = python.is_file()
        tools = [self.supply.tool_status(root, tool_id) for tool_id in ENGINE_TOOL_REQUIREMENTS[engine]]
        tools_ok = all(tool["installed"] for tool in tools)
        overall = source_ok and environment_ok and tools_ok
        return {
            "engine": engine, "install_root": str(root), "installed": overall,
            "detected": final.exists(), "revision": revision, "expected_revision": item["revision"],
            "source_path": str(source), "env_path": str(env),
            "source": {"installed": source_ok, "state": "installed" if source_ok else ("incomplete" if source.exists() else "missing"), "path": str(source)},
            "environment": {"installed": environment_ok, "state": "installed" if environment_ok else ("incomplete" if env.exists() else "missing"), "path": str(env), "python_path": str(python)},
            "tools": tools,
            "models": models,
        }

    def action(self, job_id: str, action: str) -> InstallJob:
        try:
            job = self.store.load(job_id)
        except ValueError:
            job = None
        if job is None:
            raise KeyError(job_id)
        if action == "cancel":
            if job.status in (InstallStatus.completed, InstallStatus.cancelled):
                return job
            self._stop_flags[job_id] = "cancel"
            if job.status in (InstallStatus.queued, InstallStatus.paused, InstallStatus.failed):
                job.status = InstallStatus.cancelled
                job.phase = "cancelled"
                job.message = "用户已取消"
                job.updated_at = now_iso()
                self.store.save(job)
                self._emit(job)
            return job
        if action == "pause":
            if job.kind != "model":
                raise ValueError("源码与依赖安装不支持暂停，只支持取消后重试")
            if job.status not in (InstallStatus.queued, InstallStatus.running):
                raise ValueError("当前状态不能暂停")
            self._stop_flags[job_id] = "pause"
            if job.status == InstallStatus.queued:
                job.status = InstallStatus.paused
                job.phase = "paused"
                job.updated_at = now_iso()
                self.store.save(job)
                self._emit(job)
            return job
        if action in ("resume", "retry"):
            allowed = (InstallStatus.paused,) if action == "resume" else (InstallStatus.failed, InstallStatus.cancelled)
            if job.status not in allowed:
                raise ValueError("当前状态不能执行该操作")
            self._stop_flags.pop(job_id, None)
            job.status = InstallStatus.queued
            job.phase = action
            job.error = None
            job.message = "已重新排队"
            job.updated_at = now_iso()
            self.store.save(job)
            self._enqueue(job_id)
            self._emit(job)
            return job
        raise ValueError("未知操作")

    def _catalog_item(self, engine: str) -> dict:
        item = self.catalog.get(engine)
        if item is None:
            raise ValueError("未知引擎")
        if item["source_url"] not in OFFICIAL_SOURCE_URLS and self.catalog is INSTALLER_CATALOG:
            raise ValueError("非官方源码地址")
        return item

    def _enqueue(self, job_id: str) -> None:
        with self._condition:
            self._queue.append(job_id)
            self._condition.notify()

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._queue:
                    self._condition.wait()
                job_id = self._queue.pop(0)
            if not job_id:
                return
            job = self.store.load(job_id)
            if job is None or job.status != InstallStatus.queued:
                continue
            try:
                if job.kind == "setup":
                    self._run_setup(job)
                elif job.kind == "tool":
                    self._run_tool(job)
                else:
                    self._run_model(job)
            except CommandStopped as exc:
                job = self.store.load(job_id) or job
                self._cleanup_job_temp(job)
                job.status = InstallStatus.paused if exc.reason == "pause" else InstallStatus.cancelled
                job.phase = job.status.value
                job.message = "下载已暂停，可继续" if exc.reason == "pause" else "用户已取消"
                job.updated_at = now_iso()
                self.store.save(job)
                self._emit(job)
            except Exception as exc:
                job = self.store.load(job_id) or job
                self._cleanup_job_temp(job)
                job.status = InstallStatus.failed
                job.phase = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.message = "安装失败，未写入完成目录"
                job.updated_at = now_iso()
                self.store.save(job)
                self._emit(job)

    def _run_setup(self, job: InstallJob) -> None:
        item = self._catalog_item(job.engine)
        root = Path(job.install_root)
        final = self._child(root, "installations", job.engine)
        temp = self._child(root, ".installer-tmp", job.id)
        bundle = temp / "bundle"
        source = bundle / "source"
        env_path = bundle / "env"
        repair_existing = bool(job.checkpoints.get("repair_existing_installation"))
        if final.exists() and not repair_existing:
            raise InstallConflictError("完成目录在安装过程中出现，拒绝覆盖")

        self._set(job, status=InstallStatus.running, phase="managed_tools", progress=0.01,
                  message="校验并准备应用托管工具")
        required_tools = ENGINE_TOOL_REQUIREMENTS[job.engine]
        tool_downloaded: dict[str, int] = {}
        for position, tool_id in enumerate(required_tools):
            accepted_at = job.tool_license_acceptances.get(tool_id)
            if not accepted_at:
                raise RuntimeError(f"缺少托管工具 {tool_id} 的许可证接受记录")
            start = 0.02 + position * 0.08

            def tool_progress(downloaded: int, total: int | None, *, base: float = start) -> None:
                denominator = total or MANAGED_TOOL_CATALOG[tool_id]["archive_bytes"] or 1
                tool_downloaded[tool_id] = max(0, downloaded)
                job.bytes_downloaded = min(job.bytes_total or sum(tool_downloaded.values()), sum(tool_downloaded.values()))
                job.progress = max(job.progress, min(base + 0.07 * downloaded / denominator, base + 0.07))
                job.updated_at = now_iso()
                self.store.save(job)
                self._emit(job)

            self.supply.ensure_tool(
                root, tool_id, license_accepted_at=accepted_at, job_id=job.id,
                on_progress=tool_progress, stop_reason=lambda: self._stop_flags.get(job.id),
            )
            self._complete_stage(job, f"tool_{tool_id}_verified", start + 0.07)

        # A setup request doubles as an integrity repair when only a managed
        # tool was missing/corrupt.  The recognized engine bundle is never
        # rewritten in that path.
        if repair_existing:
            status = self.inspect_engine(job.engine, root)
            if not status["installed"]:
                raise RuntimeError("托管工具修复后安装仍未通过完整性检查")
            job.source_path = status["source_path"]
            job.env_path = status["env_path"]
            self._complete_job(job, "托管工具已修复；源码、环境和模型均未被覆盖")
            return

        if temp.exists():
            # Tool archives use the same job workspace but clean their own
            # subdirectories.  Only an incomplete bundle can remain here.
            shutil.rmtree(bundle, ignore_errors=True)
        source.parent.mkdir(parents=True, exist_ok=True)
        self._set(job, phase="source_download", progress=max(job.progress, 0.20),
                  message="下载并校验固定提交的官方源码归档")

        def source_progress(downloaded: int, total: int | None) -> None:
            denominator = total or item["estimated_source_bytes"] or 1
            job.progress = max(job.progress, min(0.20 + 0.16 * downloaded / denominator, 0.36))
            job.updated_at = now_iso()
            self.store.save(job)
            self._emit(job)

        self.supply.install_source(
            item, source, temp / "source-work", on_progress=source_progress,
            stop_reason=lambda: self._stop_flags.get(job.id),
        )
        self._complete_stage(job, "source_verified", 0.38)

        uv_status = self.supply.tool_status(root, "uv")
        if not uv_status["installed"]:
            raise RuntimeError("托管 uv 完整性校验失败")
        uv = uv_status["executables"]["uv.exe"]
        self._set(job, phase="environment", progress=0.40, message="创建独立 Python 环境")
        env_python = self._env_python(env_path)
        command_env = self._managed_uv_env(root)
        self._command(job, [uv, "venv", "--python", item["python"], str(env_path)], cwd=root, env=command_env)
        if not env_python.is_file():
            raise RuntimeError("独立 Python 环境创建后未找到解释器")
        self._complete_stage(job, "environment_created", 0.46)
        self._set(job, phase="dependencies", progress=0.48, message="按官方方式安装依赖")
        if job.engine == "indextts2":
            index_env = dict(command_env, UV_PROJECT_ENVIRONMENT=str(env_path))
            self._command(job, [uv, "sync", "--extra", "webui", "--frozen", "--no-install-project"], cwd=source, env=index_env)
        elif job.engine == "voxcpm":
            self._command(job, [uv, "pip", "install", "--python", str(env_python), str(source)], cwd=source, env=command_env)
        elif job.engine == "gpt_sovits":
            device = job.checkpoints.get("device", "CU128")
            torch_index = {"CPU": "cpu", "CU126": "cu126", "CU128": "cu128"}[device]
            self._command(job, [uv, "pip", "install", "--python", str(env_python), "torch", "torchcodec",
                                "--index-url", f"https://download.pytorch.org/whl/{torch_index}"], cwd=source, env=command_env)
            self._command(job, [uv, "pip", "install", "--python", str(env_python), "--no-deps", "-r", str(source / "extra-req.txt")], cwd=source, env=command_env)
            self._command(job, [uv, "pip", "install", "--python", str(env_python), "-r", str(source / "requirements.txt")], cwd=source, env=command_env)
        self._complete_stage(job, "dependencies_installed", 0.94)
        tool_manifest = {
            tool_id: self.supply.tool_status(root, tool_id)["path"] for tool_id in required_tools
        }
        install_manifest = {
            "engine": job.engine, "source_url": item["source_url"], "revision": item["revision"],
            "source_archive_sha256": item["sha256"],
            "code_license": item["code_license"], "code_license_url": item["code_license_url"],
            "license_accepted_at": job.license_accepted_at,
            "python_version": item["python"], "python_license": MANAGED_PYTHON_LICENSE["license"],
            "python_license_url": MANAGED_PYTHON_LICENSE["license_url"],
            "python_license_accepted_at": job.python_license_accepted_at,
            "managed_tools": tool_manifest,
            "source": "source", "environment": "env", "models": "../../models/" + job.engine,
            "installed_at": now_iso(),
        }
        (bundle / "installation.json").write_text(json.dumps(install_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._raise_if_stopped(job)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(bundle, final)
        job.source_path = str(final / "source")
        job.env_path = str(final / "env")
        self._complete_stage(job, "atomic_finalize", 1.0)
        self._complete_job(job, "源码和独立环境安装完成；未自动下载任何模型")
        self._cleanup_job_temp(job)

    def _run_tool(self, job: InstallJob) -> None:
        if not job.tool_id or job.tool_id not in MANAGED_TOOL_CATALOG:
            raise ValueError("未知托管工具")
        root = Path(job.install_root)
        accepted_at = job.tool_license_acceptances.get(job.tool_id)
        if not accepted_at:
            raise RuntimeError("缺少托管工具许可证接受记录")
        self._set(job, status=InstallStatus.running, phase="tool_download", progress=0.02,
                  message=f"下载并校验固定版本 {job.tool_id}")

        def progress(downloaded: int, total: int | None) -> None:
            denominator = total or job.bytes_total or 1
            job.bytes_downloaded = downloaded
            job.progress = min(0.92, 0.02 + 0.90 * downloaded / denominator)
            job.updated_at = now_iso()
            self.store.save(job)
            self._emit(job)

        self.supply.ensure_tool(
            root, job.tool_id, license_accepted_at=accepted_at, job_id=job.id,
            on_progress=progress, stop_reason=lambda: self._stop_flags.get(job.id),
        )
        self._complete_stage(job, "tool_verified", 1.0)
        self._complete_job(job, f"托管工具 {job.tool_id} 已安装并通过完整性校验")

    def _run_model(self, job: InstallJob) -> None:
        item = self._catalog_item(job.engine)
        model = next(value for value in item["models"] if value["id"] == job.model_id)
        root = Path(job.install_root)
        final = self._child(root, "models", job.engine, model["id"])
        temp = self._child(root, ".installer-tmp", job.id)
        stage = temp / "model"
        if final.exists():
            raise InstallConflictError("模型完成目录在下载过程中出现，拒绝覆盖")
        stage.mkdir(parents=True, exist_ok=True)
        uv_status = self.supply.tool_status(root, "uv")
        if not uv_status["installed"]:
            raise RuntimeError("托管 uv 缺失或完整性校验失败，请先修复 uv")
        uv = uv_status["executables"]["uv.exe"]
        self._set(job, status=InstallStatus.running, phase="model_download", progress=max(job.progress, 0.03), message="下载固定版本官方模型")
        self._command(job, [
            uv, "tool", "run", "--from", MODEL_DOWNLOAD_TOOL["package"], "hf", "download",
            model["repo_id"], "--revision", model["revision"], "--local-dir", str(stage),
        ], cwd=root, env=self._managed_uv_env(root))
        self._complete_stage(job, "model_downloaded", 0.92)
        self._raise_if_stopped(job)
        self._set(job, phase="model_verify", progress=0.94, message="生成模型内容校验清单")
        digest, total = self._content_manifest(stage)
        job.computed_sha256 = digest
        job.bytes_downloaded = total
        model_manifest = {
            "engine": job.engine, "model_id": model["id"], "provider": model["provider"],
            "repo_id": model["repo_id"], "revision": model["revision"],
            "content_manifest_sha256": digest, "installed_bytes": total,
            "license": model["license"], "license_url": model["license_url"],
            "license_accepted_at": job.license_accepted_at, "installed_at": now_iso(),
        }
        (stage / "model-manifest.json").write_text(json.dumps(model_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._raise_if_stopped(job)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, final)
        MODEL_INTEGRITY_VERIFIER.remember_verified(final, model["revision"], digest, total)
        job.model_path = str(final)
        self._complete_stage(job, "atomic_finalize", 1.0)
        self._complete_job(job, "模型下载并校验完成")
        self._cleanup_job_temp(job)

    def _command(self, job: InstallJob, argv: list[str], *, cwd: Path, env: dict[str, str] | None = None):
        def on_output(line: str) -> None:
            job.log_tail = (job.log_tail + [line])[-200:]
            percent = re.search(r"(?<!\d)(\d{1,3})(?:\.\d+)?%", line)
            if percent:
                raw = min(100, int(percent.group(1))) / 100
                if job.kind == "model":
                    job.progress = max(job.progress, 0.03 + 0.86 * raw)
                    if job.bytes_total:
                        job.bytes_downloaded = int(job.bytes_total * raw)
            job.updated_at = now_iso()
            self.store.save(job)
            self._emit(job)

        return self.runner.run(
            [str(value) for value in argv], cwd=cwd, env=env,
            stop_reason=lambda: self._stop_flags.get(job.id), on_output=on_output,
        )

    @staticmethod
    def _env_python(env_path: Path) -> Path:
        windows = env_path / "Scripts" / "python.exe"
        return windows if os.name == "nt" else env_path / "bin" / "python"

    @staticmethod
    def _managed_uv_env(root: Path) -> dict[str, str]:
        return {
            "UV_PYTHON_PREFERENCE": "only-managed",
            "UV_PYTHON_DOWNLOADS": "automatic",
            "UV_PYTHON_INSTALL_DIR": str(root / "tools" / "python"),
            "UV_CACHE_DIR": str(root / "cache" / "uv"),
            "UV_TOOL_DIR": str(root / "tools" / "uv-tools"),
            "UV_TOOL_BIN_DIR": str(root / "tools" / "uv-tool-bin"),
            "HF_HOME": str(root / "cache" / "huggingface"),
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "UV_NO_PROGRESS": "0",
        }

    def _set(self, job: InstallJob, **changes) -> None:
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = now_iso()
        self.store.save(job)
        self._emit(job)

    def _complete_stage(self, job: InstallJob, stage: str, progress: float) -> None:
        if stage not in job.completed_stages:
            job.completed_stages.append(stage)
        job.progress = progress
        job.checkpoints[stage] = now_iso()
        job.updated_at = now_iso()
        self.store.save(job)
        self._emit(job)

    def _complete_job(self, job: InstallJob, message: str) -> None:
        job.status = InstallStatus.completed
        job.phase = "completed"
        job.progress = 1.0
        job.message = message
        job.error = None
        job.updated_at = now_iso()
        self.store.save(job)
        self._emit(job)

    def _emit(self, job: InstallJob) -> None:
        self.events.publish({"type": "installer.progress", "job": job.model_dump(mode="json")})

    def _raise_if_stopped(self, job: InstallJob) -> None:
        reason = self._stop_flags.get(job.id)
        if reason:
            raise CommandStopped(reason)

    @staticmethod
    def _content_manifest(root: Path) -> tuple[str, int]:
        return content_manifest(root)

    def _verify_model_directory(self, path: Path, model: dict) -> bool:
        return MODEL_INTEGRITY_VERIFIER.verify(path, model["revision"])

    def _safe_remove(self, root: Path, target: Path) -> None:
        safe_temp_root = self._child(root, ".installer-tmp")
        resolved = target.resolve()
        if safe_temp_root != resolved and safe_temp_root not in resolved.parents:
            raise ValueError("拒绝清理安装临时目录之外的路径")
        if resolved.exists():
            shutil.rmtree(resolved)

    def _cleanup_job_temp(self, job: InstallJob) -> None:
        try:
            root = Path(job.install_root).resolve()
            target = self._child(root, ".installer-tmp", job.id)
            self._safe_remove(root, target)
        except (OSError, ValueError):
            # Cleanup failure must not overwrite the truthful installer result.
            return
