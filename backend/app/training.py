from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .adapters.base import EngineAdapter


ACTIVE_TRAINING_STATES = {"running", "stopping"}
RESUMABLE_TRAINING_STATES = {"failed", "cancelled", "interrupted"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VoxTrainingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(default="VoxCPM2 角色训练", min_length=1, max_length=80)
    mode: Literal["lora", "sft"] = "lora"
    pretrained_path: str = Field(alias="pretrainedPath", min_length=1)
    train_manifest: str = Field(alias="trainManifest", min_length=1)
    val_manifest: str = Field(default="", alias="valManifest")
    output_dir: str = Field(alias="outputDir", min_length=1)
    batch_size: int = Field(default=2, alias="batchSize", ge=1, le=64)
    grad_accum_steps: int = Field(default=8, alias="gradAccumSteps", ge=1, le=256)
    max_steps: int = Field(default=1000, alias="maxSteps", ge=1, le=10_000_000)
    save_interval: int = Field(default=500, alias="saveInterval", ge=1, le=1_000_000)
    valid_interval: int = Field(default=500, alias="validInterval", ge=1, le=1_000_000)
    learning_rate: float | None = Field(default=None, alias="learningRate", gt=0, le=1)
    warmup_steps: int = Field(default=100, alias="warmupSteps", ge=0, le=1_000_000)
    num_workers: int = Field(default=4, alias="numWorkers", ge=0, le=64)
    max_batch_tokens: int = Field(default=8192, alias="maxBatchTokens", ge=0, le=1_000_000)
    max_grad_norm: float = Field(default=1.0, alias="maxGradNorm", ge=0, le=1000)
    lora_rank: int = Field(default=32, alias="loraRank", ge=1, le=1024)
    lora_alpha: int = Field(default=32, alias="loraAlpha", ge=1, le=4096)
    lora_dropout: float = Field(default=0.0, alias="loraDropout", ge=0, lt=1)
    enable_lm: bool = Field(default=True, alias="enableLm")
    enable_dit: bool = Field(default=True, alias="enableDit")
    enable_proj: bool = Field(default=False, alias="enableProj")

    @model_validator(mode="after")
    def intervals_fit_run(self):
        if self.save_interval > self.max_steps:
            self.save_interval = self.max_steps
        if self.valid_interval > self.max_steps:
            self.valid_interval = self.max_steps
        return self


class TrainingError(RuntimeError):
    pass


class TrainingManager:
    def __init__(self, root: str | Path, adapters: dict[str, EngineAdapter], worker_path: str | Path, *, mock_mode: bool = False,
                 gpt_worker_path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.records_dir = self.root / "records"
        self.runs_dir = self.root / "runs"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.adapters = adapters
        self.worker_path = Path(worker_path).resolve()
        self.gpt_worker_path = Path(gpt_worker_path or self.worker_path.with_name("gpt_workbench_worker.py")).resolve()
        self.mock_mode = mock_mode
        self._processes: dict[str, subprocess.Popen] = {}
        self._logs: dict[str, Any] = {}
        self._gpt_process: subprocess.Popen | None = None
        self._gpt_log: Any | None = None
        self._gpt_mock_running = False
        self._lock = threading.RLock()
        self._recover_interrupted()

    def _record_path(self, task_id: str) -> Path:
        return self.records_dir / f"{task_id}.json"

    def _load(self, task_id: str) -> dict[str, Any] | None:
        path = self._record_path(task_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _save(self, record: dict[str, Any]) -> None:
        path = self._record_path(record["id"])
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _recover_interrupted(self) -> None:
        for path in self.records_dir.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("status") in ACTIVE_TRAINING_STATES:
                record.update(status="interrupted", pid=None, endedAt=now_iso(), error="软件上次退出时训练仍在运行，可从最近检查点继续。")
                self._save(record)

    @staticmethod
    def _runtime(adapter: EngineAdapter) -> dict[str, Any]:
        return adapter.status()

    @staticmethod
    def _find_pretrained(project: Path, status: dict[str, Any]) -> Path | None:
        defaults = status.get("parameter_defaults") or {}
        candidates: list[Path] = []
        for raw in (defaults.get("model_path"), status.get("managed_model_path")):
            if raw:
                candidates.append(Path(raw))
        candidates.extend([
            project / "pretrained_models" / "VoxCPM2",
            project / "modelscope_cache" / "models" / "OpenBMB" / "VoxCPM2",
            project / "cache" / "modelscope" / "models" / "OpenBMB" / "VoxCPM2",
        ])
        for snapshots in (
            project / "cache" / "huggingface" / "hub" / "models--openbmb--VoxCPM2" / "snapshots",
            project / "modelscope_cache" / "models" / "openbmb" / "VoxCPM2" / "snapshots",
        ):
            if snapshots.is_dir():
                candidates.extend(sorted(snapshots.iterdir(), reverse=True))
        for candidate in candidates:
            if (candidate / "config.json").is_file():
                return candidate.resolve()
        return None

    def capabilities(self) -> dict[str, Any]:
        adapter = self.adapters.get("voxcpm")
        if adapter is None:
            return {"available": False, "detail": "VoxCPM2 引擎未配置", "modes": ["lora", "sft"]}
        status = self._runtime(adapter)
        project = Path(status.get("project_path") or "")
        python = Path(status.get("python_path") or "")
        script = project / "scripts" / "train_voxcpm_finetune.py"
        pretrained = self._find_pretrained(project, status) if project.is_dir() else None
        missing = []
        if not project.is_dir():
            missing.append(f"项目目录不存在：{project}")
        if not python.is_file() and not self.mock_mode:
            missing.append(f"Python 环境不存在：{python}")
        if not script.is_file() and not self.mock_mode:
            missing.append(f"官方训练脚本不存在：{script}")
        if pretrained is None:
            missing.append("尚未自动识别 VoxCPM2 基础模型，请在训练表单中手动选择。")
        return {
            "available": not any("不存在" in item for item in missing),
            "detail": "；".join(missing) if missing else "已检测到官方 VoxCPM2 LoRA 与全量 SFT 训练入口。",
            "modes": ["lora", "sft"],
            "projectPath": str(project),
            "pythonPath": str(python),
            "scriptPath": str(script),
            "pretrainedPath": str(pretrained) if pretrained else "",
            "defaultOutputDir": str((self.runs_dir / "voxcpm2").resolve()),
        }

    @staticmethod
    def _read_gpt_port(project: Path) -> int:
        config = project / "config.py"
        if config.is_file():
            try:
                match = re.search(r"^webui_port_main\s*=\s*(\d+)", config.read_text(encoding="utf-8", errors="ignore"), re.MULTILINE)
                if match:
                    return int(match.group(1))
            except OSError:
                pass
        return 9874

    def _gpt_assets(self) -> tuple[dict[str, Any], Path, Path, Path, int]:
        adapter = self.adapters.get("gpt_sovits")
        status = self._runtime(adapter) if adapter is not None else {}
        runtime_root = Path(status.get("runtime_root") or "")
        source_root = Path(status.get("project_path") or "")
        project = runtime_root if (runtime_root / "webui.py").is_file() else source_root
        python = Path(status.get("python_path") or "")
        script = project / "webui.py"
        return status, project, python, script, self._read_gpt_port(project)

    @staticmethod
    def _port_reachable(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.12):
                return True
        except OSError:
            return False

    def gpt_workbench_status(self, lines: int = 120) -> dict[str, Any]:
        status, project, python, script, port = self._gpt_assets()
        process = self._gpt_process
        running = self._gpt_mock_running or (process is not None and process.poll() is None)
        if process is not None and process.poll() is not None:
            running = False
            self._gpt_process = None
            if self._gpt_log:
                self._gpt_log.close()
                self._gpt_log = None
        missing: list[str] = []
        if not project.is_dir():
            missing.append(f"项目目录不存在：{project}")
        if not python.is_file() and not self.mock_mode:
            missing.append(f"Python 环境不存在：{python}")
        if not script.is_file() and not self.mock_mode:
            missing.append(f"官方训练入口不存在：{script}")
        log_path = self.root / "gpt-sovits-workbench.log"
        log_lines: list[str] = []
        if log_path.is_file():
            try:
                log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(20, min(lines, 300)):]
            except OSError:
                pass
        return {
            "available": not missing,
            "detail": "；".join(missing) if missing else "已检测到 GPT-SoVITS 官方数据处理、SoVITS 训练与 GPT 训练工作台。",
            "projectPath": str(project),
            "sourcePath": str(status.get("project_path") or project),
            "pythonPath": str(python),
            "scriptPath": str(script),
            "port": port,
            "url": f"http://127.0.0.1:{port}",
            "running": running,
            "reachable": running and (self.mock_mode or self._port_reachable(port)),
            "pid": process.pid if process is not None and process.poll() is None else (4343 if self._gpt_mock_running else None),
            "logPath": str(log_path),
            "logLines": log_lines,
        }

    def start_gpt_workbench(self) -> dict[str, Any]:
        with self._lock:
            current = self.gpt_workbench_status()
            if current["running"]:
                return current
            if not current["available"]:
                raise TrainingError(current["detail"])
            if self.mock_mode:
                self._gpt_mock_running = True
                return self.gpt_workbench_status()
            project = Path(current["projectPath"])
            python = Path(current["pythonPath"])
            if not self.gpt_worker_path.is_file():
                raise TrainingError(f"GPT-SoVITS 内嵌启动器不存在：{self.gpt_worker_path}")
            command = [str(python), "-I", str(self.gpt_worker_path), "--project", str(project), "--language", "zh_CN"]
            env = os.environ.copy()
            for variable in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "__PYVENV_LAUNCHER__"):
                env.pop(variable, None)
            env.update(PYTHONUTF8="1")
            log_path = Path(current["logPath"])
            log_handle = log_path.open("a", encoding="utf-8")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            try:
                process = subprocess.Popen(
                    command, cwd=project, env=env, stdin=subprocess.DEVNULL,
                    stdout=log_handle, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", creationflags=flags,
                )
            except Exception:
                log_handle.close()
                raise
            self._gpt_process = process
            self._gpt_log = log_handle
            threading.Thread(target=self._monitor_gpt_workbench, args=(process,), name="gpt-sovits-workbench", daemon=True).start()
            return self.gpt_workbench_status()

    def _monitor_gpt_workbench(self, process: subprocess.Popen) -> None:
        process.wait()
        with self._lock:
            if self._gpt_process is process:
                self._gpt_process = None
            if self._gpt_log:
                self._gpt_log.close()
                self._gpt_log = None

    def stop_gpt_workbench(self, *, wait: bool = False) -> dict[str, Any]:
        with self._lock:
            self._gpt_mock_running = False
            process = self._gpt_process
            if process is not None and process.poll() is None:
                thread = threading.Thread(target=self._stop_process, args=(process,), name="stop-gpt-sovits-workbench", daemon=True)
                thread.start()
                if wait:
                    thread.join(timeout=12)
            return self.gpt_workbench_status()

    @staticmethod
    def _validate_manifest(path: Path, project: Path) -> dict[str, int]:
        if not path.is_file():
            raise TrainingError(f"训练数据清单不存在：{path}")
        total = 0
        missing_audio = 0
        invalid = 0
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                total += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise TrainingError(f"训练清单第 {line_number} 行不是有效 JSON：{exc.msg}") from exc
                if not isinstance(row, dict) or not str(row.get("text") or "").strip() or not str(row.get("audio") or "").strip():
                    invalid += 1
                    continue
                audio = Path(str(row["audio"]))
                if not audio.is_absolute() and not (path.parent / audio).is_file() and not (project / audio).is_file():
                    missing_audio += 1
        if total == 0:
            raise TrainingError("训练数据清单为空")
        if invalid:
            raise TrainingError(f"训练清单中有 {invalid} 行缺少 audio 或 text 字段")
        if missing_audio:
            raise TrainingError(f"训练清单中有 {missing_audio} 个音频路径无法定位；相对路径应基于清单目录或 VoxCPM 项目目录")
        return {"samples": total, "missingAudio": missing_audio}

    def _build_config(self, request: VoxTrainingCreate, save_path: Path) -> dict[str, Any]:
        config: dict[str, Any] = {
            "pretrained_path": str(Path(request.pretrained_path).resolve()),
            "train_manifest": str(Path(request.train_manifest).resolve()),
            "val_manifest": str(Path(request.val_manifest).resolve()) if request.val_manifest else None,
            "sample_rate": 16000,
            "out_sample_rate": 48000,
            "batch_size": request.batch_size,
            "grad_accum_steps": request.grad_accum_steps,
            "num_workers": request.num_workers,
            "num_iters": request.max_steps,
            "log_interval": 10,
            "valid_interval": request.valid_interval,
            "save_interval": request.save_interval,
            "learning_rate": request.learning_rate or (0.0001 if request.mode == "lora" else 0.00001),
            "weight_decay": 0.01,
            "warmup_steps": request.warmup_steps,
            "max_steps": request.max_steps,
            "max_batch_tokens": request.max_batch_tokens,
            "max_grad_norm": request.max_grad_norm,
            "save_path": str(save_path),
            "tensorboard": str(save_path / "logs"),
            "lambdas": {"loss/diff": 1.0, "loss/stop": 1.0},
        }
        if request.mode == "lora":
            config["lora"] = {
                "enable_lm": request.enable_lm,
                "enable_dit": request.enable_dit,
                "enable_proj": request.enable_proj,
                "r": request.lora_rank,
                "alpha": request.lora_alpha,
                "dropout": request.lora_dropout,
            }
        return config

    def create(self, request: VoxTrainingCreate) -> dict[str, Any]:
        with self._lock:
            if any(item.get("status") in ACTIVE_TRAINING_STATES for item in self.list()):
                raise TrainingError("已有训练任务正在占用显卡，请先停止或等待完成")
            adapter = self.adapters.get("voxcpm")
            if adapter is None:
                raise TrainingError("VoxCPM2 引擎未配置")
            status = self._runtime(adapter)
            project = Path(status.get("project_path") or "").resolve()
            python = Path(status.get("python_path") or "").resolve()
            pretrained = Path(request.pretrained_path).resolve()
            train_manifest = Path(request.train_manifest).resolve()
            val_manifest = Path(request.val_manifest).resolve() if request.val_manifest else None
            if not project.is_dir():
                raise TrainingError(f"VoxCPM2 项目目录不存在：{project}")
            if not self.mock_mode and not python.is_file():
                raise TrainingError(f"VoxCPM2 Python 环境不存在：{python}")
            if not (pretrained / "config.json").is_file():
                raise TrainingError(f"基础模型目录缺少 config.json：{pretrained}")
            manifest_info = self._validate_manifest(train_manifest, project)
            if val_manifest:
                self._validate_manifest(val_manifest, project)

            task_id = uuid.uuid4().hex
            run_dir = self.runs_dir / task_id
            save_path = Path(request.output_dir).resolve() / task_id
            run_dir.mkdir(parents=True, exist_ok=False)
            save_path.mkdir(parents=True, exist_ok=True)
            config_path = run_dir / "voxcpm2-training.yaml"
            config_path.write_text(json.dumps(self._build_config(request, save_path), ensure_ascii=False, indent=2), encoding="utf-8")
            log_path = run_dir / "train.log"
            command = [str(python), "-u", str(self.worker_path), "--project", str(project), "--config", str(config_path)]
            record = {
                "id": task_id, "engine": "voxcpm", "name": request.name.strip(), "mode": request.mode,
                "status": "queued", "createdAt": now_iso(), "startedAt": None, "endedAt": None,
                "pid": None, "progress": 0.0, "currentStep": 0, "maxSteps": request.max_steps,
                "samples": manifest_info["samples"], "projectPath": str(project), "pythonPath": str(python),
                "pretrainedPath": str(pretrained), "trainManifest": str(train_manifest),
                "valManifest": str(val_manifest) if val_manifest else "", "outputDir": str(save_path),
                "configPath": str(config_path), "logPath": str(log_path), "command": command,
                "error": None,
            }
            self._save(record)
            self._launch(record)
            return self.snapshot(task_id)

    def _launch(self, record: dict[str, Any]) -> None:
        task_id = record["id"]
        if self.mock_mode:
            record.update(status="running", startedAt=now_iso(), pid=4242)
            Path(record["logPath"]).write_text("[mock] VoxCPM2 training started\n", encoding="utf-8")
            self._save(record)
            return
        env = os.environ.copy()
        for variable in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "__PYVENV_LAUNCHER__"):
            env.pop(variable, None)
        env.update(PYTHONUTF8="1", TOKENIZERS_PARALLELISM="false")
        project = Path(record["projectPath"])
        env.setdefault("HF_HOME", str(project / "cache" / "huggingface"))
        env.setdefault("MODELSCOPE_CACHE", str(project / "cache" / "modelscope"))
        log_handle = Path(record["logPath"]).open("a", encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = subprocess.Popen(
                record["command"], cwd=record["projectPath"], env=env,
                stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", creationflags=flags,
            )
        except Exception:
            log_handle.close()
            raise
        record.update(status="running", startedAt=record.get("startedAt") or now_iso(), endedAt=None, pid=process.pid, error=None)
        self._processes[task_id] = process
        self._logs[task_id] = log_handle
        self._save(record)
        threading.Thread(target=self._monitor, args=(task_id, process), name=f"training-{task_id[:8]}", daemon=True).start()

    def _monitor(self, task_id: str, process: subprocess.Popen) -> None:
        code = process.wait()
        with self._lock:
            handle = self._logs.pop(task_id, None)
            if handle:
                handle.close()
            self._processes.pop(task_id, None)
            record = self._load(task_id)
            if not record:
                return
            if record.get("status") == "stopping":
                record.update(status="cancelled", error=None)
            elif code == 0:
                record.update(status="completed", progress=1.0, currentStep=record.get("maxSteps", 0), error=None)
            else:
                record.update(status="failed", error=f"训练进程退出码：{code}")
            record.update(pid=None, endedAt=now_iso())
            self._save(record)

    @staticmethod
    def _log_progress(record: dict[str, Any], lines: list[str]) -> None:
        step = int(record.get("currentStep") or 0)
        patterns = (r"(?:step|iter(?:ation)?)\s*[:=#]?\s*(\d+)", r"(\d+)\s*/\s*(\d+)")
        for line in lines:
            for pattern in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    step = max(step, int(match.group(1)))
        maximum = max(1, int(record.get("maxSteps") or 1))
        record["currentStep"] = min(step, maximum)
        record["progress"] = min(1.0, step / maximum)

    def snapshot(self, task_id: str, lines: int = 240) -> dict[str, Any]:
        record = self._load(task_id)
        if record is None:
            raise TrainingError("训练任务不存在")
        log_lines: list[str] = []
        log_path = Path(record["logPath"])
        if log_path.is_file():
            try:
                log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(20, min(lines, 500)):]
            except OSError:
                pass
        self._log_progress(record, log_lines)
        if record.get("status") in ACTIVE_TRAINING_STATES:
            self._save(record)
        return {**record, "logLines": log_lines}

    def list(self) -> list[dict[str, Any]]:
        rows = []
        for path in self.records_dir.glob("*.json"):
            record = self._load(path.stem)
            if record:
                rows.append(record)
        rows.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
        return rows

    def stop(self, task_id: str, *, wait: bool = False) -> dict[str, Any]:
        with self._lock:
            record = self._load(task_id)
            if record is None:
                raise TrainingError("训练任务不存在")
            if record.get("status") not in ACTIVE_TRAINING_STATES:
                return self.snapshot(task_id)
            record["status"] = "stopping"
            self._save(record)
            if self.mock_mode:
                record.update(status="cancelled", pid=None, endedAt=now_iso())
                self._save(record)
                return self.snapshot(task_id)
            process = self._processes.get(task_id)
            if process is not None:
                thread = threading.Thread(target=self._stop_process, args=(process,), name=f"stop-training-{task_id[:8]}", daemon=True)
                thread.start()
                if wait:
                    thread.join(timeout=12)
            return self.snapshot(task_id)

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            process.wait(timeout=9)
        except Exception:
            if process.poll() is None:
                process.kill()

    def resume(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            if any(item.get("status") in ACTIVE_TRAINING_STATES for item in self.list()):
                raise TrainingError("已有训练任务正在运行")
            record = self._load(task_id)
            if record is None:
                raise TrainingError("训练任务不存在")
            if record.get("status") not in RESUMABLE_TRAINING_STATES:
                raise TrainingError("只有失败、已停止或被中断的训练可以继续")
            record.update(status="queued", endedAt=None, pid=None, error=None)
            self._save(record)
            self._launch(record)
            return self.snapshot(task_id)

    def active_summary(self) -> list[dict[str, Any]]:
        active = [
            {"id": item["id"], "name": item["name"], "mode": item["mode"], "status": item["status"], "pid": item.get("pid")}
            for item in self.list() if item.get("status") in ACTIVE_TRAINING_STATES
        ]
        workbench = self.gpt_workbench_status(lines=20)
        if workbench["running"]:
            active.append({"id": "gpt-sovits-workbench", "name": "GPT-SoVITS 官方训练工作台", "mode": "official_webui", "status": "running", "pid": workbench.get("pid")})
        return active

    def terminate_active(self, *, wait: bool = False) -> None:
        for item in self.active_summary():
            if item["id"] == "gpt-sovits-workbench":
                self.stop_gpt_workbench(wait=wait)
            else:
                self.stop(item["id"], wait=wait)

    def close(self) -> None:
        self.terminate_active(wait=True)
