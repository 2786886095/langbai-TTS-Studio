"""Persistent, identity-checked Windows → WSL Breeze transport."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .base import EngineAdapter, EngineError
from ..breeze_binding import (BreezeBindingStore, KV_PATCH_PATH, KV_PATCH_SHA256, MODEL_REVISION,
                              PROFILE, SOURCE_COMMIT, file_sha256, windows_to_wsl)

PROTOCOL_PREFIX = "@@LANGBAI_BREEZE@@"


class BreezeGpuBusyError(EngineError):
    gpu_busy = True


class BreezeContextBudgetError(EngineError):
    budget_exceeded = True


def parse_message(line: str) -> dict | None:
    if not line.startswith(PROTOCOL_PREFIX):
        return None
    try:
        payload = json.loads(line[len(PROTOCOL_PREFIX):])
        return payload if isinstance(payload, dict) else None
    except ValueError:
        return None


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.15):
            return True
    except OSError:
        return False


class BreezeAdapter(EngineAdapter):
    engine_id = "breeze"

    def __init__(self, binding_store: BreezeBindingStore, staging_root: str | Path,
                 log_dir: str | Path, *, worker_path: str | Path | None = None,
                 popen_factory=None, run_command=None, wsl_executable: str | None = None):
        self.bindings = binding_store
        self.staging_root = Path(staging_root).resolve()
        self.log_dir = Path(log_dir).resolve()
        self.worker_path = Path(worker_path or Path(__file__).resolve().parents[2] / "breeze_worker.py").resolve()
        self.controller_path = self.worker_path.with_name("breeze_worker_ctl.py")
        self._popen = popen_factory or subprocess.Popen
        self._run = run_command or subprocess.run
        self.wsl_executable = wsl_executable or shutil.which("wsl.exe") or "wsl.exe"
        self._inference_lock, self._state_lock, self._write_lock = threading.Lock(), threading.RLock(), threading.Lock()
        self._process = None
        self._messages = queue.Queue()
        self._identity = None
        self._hello = None
        self._launch_config = None
        self._install_fingerprint = None
        self._model_warmed = False
        self._instance_token = None
        self._phase = "idle"
        self._codec_frames = 0
        self._last_heartbeat = 0.
        self._last_error = None
        self._exit_confirmed = True
        self._command: list[str] = []
        self._log_lines: list[str] = []
        self._last_used = 0.
        self._idle_timer = None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._registry = self.log_dir / "breeze-worker-instance.json"

    def _config(self) -> dict:
        config = self.bindings.get()
        if not config:
            raise EngineError("Breeze 尚未绑定；请在引擎页绑定已有的 WSL 安装")
        if not config.get("licenseAccepted"):
            raise EngineError("Breeze 模型许可尚未确认")
        if config.get("licenseSha256") and file_sha256(Path(config["modelPath"]) / "LICENSE") != config["licenseSha256"]:
            raise EngineError("Breeze 模型许可文件已变化，请重新查看并确认绑定")
        return config

    def status(self) -> dict[str, Any]:
        try:
            config = self._config()
            source = Path(config["sourcePath"])
            model = Path(config["modelPath"])
            if not (model / "config.json").is_file() or not (model / "audio_tokenizer" / "config.json").is_file():
                raise ValueError("Breeze 权重目录不完整")
            if file_sha256(source / KV_PATCH_PATH) != KV_PATCH_SHA256:
                raise ValueError("Breeze KV 修复 sha 不匹配；不会修改现有安装")
            if not shutil.which(self.wsl_executable) and not Path(self.wsl_executable).is_file():
                raise ValueError("未找到 wsl.exe，请安装 WSL 并配置 Ubuntu")
            return {"id": self.engine_id, "available": True, "state": "ready" if self._hello and self._process.poll() is None else "bound",
                    "detail": "accelerated · CFG=1 参考克隆；首次生成时校验 WSL 和权重",
                    "transport": "wsl", "distribution": config["distribution"],
                    "python_path": config["pythonPath"], "project_path": config["sourcePath"],
                    "performance_mode": "accelerated", "native_sample_rate": 24000,
                    "source_commit": SOURCE_COMMIT, "model_revision": MODEL_REVISION,
                    "patch_sha256": KV_PATCH_SHA256, "externalWebPortOpen": _port_open(7862),
                    "license": "research_noncommercial", "licenseVersion": config.get("licenseVersion")}
        except (ValueError, KeyError, OSError, EngineError) as exc:
            return {"id": self.engine_id, "available": False, "state": "unconfigured", "detail": str(exc),
                    "transport": "wsl", "performance_mode": "accelerated", "project_path": ""}

    def runtime_fingerprint(self) -> dict:
        config = self._config()
        source, model = Path(config["sourcePath"]), Path(config["modelPath"])
        # Include local file identities as well as pinned revisions: replacing a model file
        # invalidates cache reuse even before the next worker's full SHA verification.
        files = []
        for relative in ("config.json", "tokenizer.json", "model-00001-of-00002.safetensors",
                         "model-00002-of-00002.safetensors", "audio_tokenizer/model.safetensors"):
            path = model / relative
            stat = path.stat()
            files.append([relative, stat.st_size, stat.st_mtime_ns])
        sources = []
        for subdir in ("breeze_infer", "models"):
            for path in sorted((source / subdir).rglob("*.py")):
                sources.append([path.relative_to(source).as_posix(), file_sha256(path)])
        return {"engine": "breeze", "adapterVersion": 1, "workerProtocol": 1,
                "workerSha256": file_sha256(self.worker_path),
                "sourceCommit": SOURCE_COMMIT, "modelRevision": MODEL_REVISION,
                "patchSha256": file_sha256(source / KV_PATCH_PATH), "modelFiles": files,
                "sourceFiles": sources, "sourcePath": str(source), "modelPath": str(model),
                "pythonPath": config["pythonPath"], "distribution": config["distribution"], **PROFILE,
                "contextDependency": "none", "output": {"sampleRate": 24000, "channels": 1, "subtype": "PCM_16"}}

    def cache_identity(self, params: dict) -> dict:
        result = self.runtime_fingerprint()
        reference = params.get("ref_audio_path") or params.get("reference_audio") or params.get("ref_audio")
        if not reference:
            raise EngineError("Breeze 缺少参考音频")
        result["refAudioSha256"] = file_sha256(reference)
        result["refText"] = str(params.get("ref_text") or params.get("prompt_text") or "")
        return result

    def _log(self, line: str):
        with self._state_lock:
            self._log_lines.append(line.rstrip())
            del self._log_lines[:-500]
        path = self.log_dir / "breeze.log"
        try:
            if path.exists() and path.stat().st_size > 32 * 1024 * 1024:
                backup = path.with_suffix(".log.1")
                backup.unlink(missing_ok=True)
                path.replace(backup)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line.rstrip() + "\n")
        except OSError:
            pass

    def _reader(self, process, messages: queue.Queue):
        try:
            for line in process.stdout:
                message = parse_message(line)
                if message is None:
                    self._log(line)
                    continue
                with self._state_lock:
                    if process is not self._process:
                        continue
                    self._last_heartbeat = time.monotonic()
                    if message.get("phase"):
                        self._phase = message["phase"]
                    if "codecFrames" in message:
                        self._codec_frames = message["codecFrames"]
                    if message.get("event") == "identity":
                        self._identity = message["identity"]
                        self._save_registry()
                messages.put(message)
        finally:
            messages.put({"event": "pipe_closed"})

    def _stderr_reader(self, process):
        for line in process.stderr:
            self._log(line)

    def _save_registry(self):
        record = {"identity": self._identity, "instanceToken": self._instance_token,
                  "stagingRoot": str(self.staging_root), "windowsPid": self._process.pid,
                  "windowsStartedAt": self._windows_started_at, "command": self._command,
                  "binding": self._config(), "exitConfirmed": False}
        if self._hello:
            record["hello"] = self._hello
        self._write_registry(record)

    def _write_registry(self, record: dict):
        temporary = self._registry.with_name(f".{self._registry.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(5):
                try:
                    os.replace(temporary, self._registry)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(.1 * (attempt + 1))
        finally:
            temporary.unlink(missing_ok=True)

    def _controller(self, config: dict, identity: dict) -> dict:
        command = [self.wsl_executable, "--distribution", config["distribution"], "--exec",
                   config["pythonPath"], "-B", windows_to_wsl(self.controller_path), "terminate"]
        try:
            result = self._run(command, input=json.dumps(identity) + "\n", text=True,
                               capture_output=True, encoding="utf-8", timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode != 0:
                return {"stopped": False, "reason": result.stderr[-500:]}
            payload = json.loads(result.stdout.strip())
            if not isinstance(payload, dict) or type(payload.get("stopped")) is not bool:
                return {"stopped": False, "reason": "invalid_controller_evidence"}
            return payload
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return {"stopped": False, "reason": str(exc)}

    def recover_owned_worker(self) -> dict:
        """Reconcile a previous backend's registered Linux process before any GPU lease.

        There may be no Windows Popen in this process, even while the Linux
        process from the previous service is still holding all of its VRAM.
        This method never treats a missing Popen as exit evidence.
        """
        with self._state_lock:
            record = None
            try:
                if not self._registry.exists():
                    if self._identity and self._launch_config and not self._exit_confirmed:
                        result = self._controller(self._launch_config, self._identity)
                    else:
                        result = {"stopped": True, "reason": "no_registered_worker"}
                else:
                    record = json.loads(self._registry.read_text(encoding="utf-8"))
                    identity = record.get("identity") if isinstance(record, dict) else None
                    token = record.get("instanceToken") if isinstance(record, dict) else None
                    if (not isinstance(record, dict) or record.get("stagingRoot") != str(self.staging_root)
                            or not isinstance(identity, dict)
                            or any(identity.get(key) is None for key in ("pid", "start_time", "pgid", "sid", "boot_id", "cmdline_sha256", "exe", "uid", "token"))
                            or not isinstance(record.get("binding"), dict)
                            or not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token)
                            or token != identity.get("token")):
                        result = {"stopped": False, "reason": "ownership_unverifiable"}
                        record = None
                    elif record.get("exitConfirmed") is True:
                        result = {"stopped": True, "reason": "previously_confirmed"}
                    else:
                        # Use the old binding; a newly selected distro is not
                        # evidence that the old distro's GPU worker has exited.
                        self._identity = record["identity"]
                        self._instance_token = record["instanceToken"]
                        self._launch_config = record["binding"]
                        result = self._controller(record["binding"], record["identity"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result = {"stopped": False, "reason": "registry_or_control_unverifiable", "detail": str(exc)}
                record = None
            self._set_exit_result(result, record)
            return result

    def _set_exit_result(self, result: dict, record: dict | None = None) -> None:
        self._exit_confirmed = result.get("stopped") is True
        if self._exit_confirmed:
            self._hello = None
            self._model_warmed = False
            self._phase = "stopped"
            self._last_error = None
            if record is not None and record.get("exitConfirmed") is not True:
                record["exitConfirmed"] = True
                record["exitEvidence"] = result
                try:
                    self._write_registry(record)
                except OSError as exc:
                    # The process is proven stopped. Preserve the diagnostic;
                    # the next service will safely revalidate the old record.
                    self._last_error = "进程已确认退出，但实例记录未能更新：" + str(exc)
        else:
            self._phase = "stop_unconfirmed"
            self._last_error = "已请求停止，尚未确认退出：" + str(result.get("reason"))

    def _send(self, payload: dict):
        with self._write_lock:
            process = self._process
            if process is None or process.poll() is not None:
                raise EngineError("Breeze worker 已退出")
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()

    def _start(self):
        config = self._config()
        fingerprint = hashlib.sha256(json.dumps(self.runtime_fingerprint(), sort_keys=True,
                                                 ensure_ascii=False).encode("utf-8")).hexdigest()
        if self._hello is not None and (self._launch_config != config or self._install_fingerprint != fingerprint):
            self.close()
        with self._state_lock:
            if self._process is not None and self._process.poll() is None and self._hello is not None:
                return
            if not self._exit_confirmed and self._process is not None and self._process.poll() is not None and self._identity:
                self._exit_confirmed = bool(self._controller(self._config(), self._identity).get("stopped"))
            if not self._exit_confirmed and self._process is None:
                self.recover_owned_worker()
            if not self._exit_confirmed:
                raise EngineError("上一 Breeze worker 尚未确认退出；请先在诊断中释放它")
            recovered = self.recover_owned_worker()
            if not recovered.get("stopped"):
                raise EngineError("检测到无法确认归属的遗留 worker，不会自动结束它：" + str(recovered.get("reason")))
            self.staging_root.mkdir(parents=True, exist_ok=True)
            self._instance_token = uuid.uuid4().hex
            self._identity = self._hello = None
            self._model_warmed = False
            self._launch_config = config
            self._install_fingerprint = fingerprint
            self._messages = queue.Queue()
            self._command = [self.wsl_executable, "--distribution", config["distribution"], "--exec",
                             config["pythonPath"], "-B", "-u", windows_to_wsl(self.worker_path),
                             "--source", windows_to_wsl(config["sourcePath"]),
                             "--model", windows_to_wsl(config["modelPath"]),
                             "--task-root", windows_to_wsl(self.staging_root),
                             "--instance-token", self._instance_token]
            self._windows_started_at = time.time()
            self._process = self._popen(self._command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                       bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._exit_confirmed = False
            self._phase = "validating"
            self._last_heartbeat = time.monotonic()
            process = self._process
            threading.Thread(target=self._reader, args=(process, self._messages), daemon=True).start()
            threading.Thread(target=self._stderr_reader, args=(process,), daemon=True).start()
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            message = self._next_message()
            if message.get("event") == "hello":
                if (message.get("protocolVersion") != 1 or message.get("source_commit") != SOURCE_COMMIT
                        or message.get("model_revision") != MODEL_REVISION or message.get("patch_sha256") != KV_PATCH_SHA256
                        or message.get("performance_mode") != "accelerated" or message.get("fast_flags") != PROFILE["fast_flags"]
                        or message.get("depth_bucket_sizes") != [1] or not message.get("weights_verified")):
                    self.close()
                    raise EngineError("Breeze worker 握手与固定的加速配置不匹配")
                if not self._identity or self._identity.get("token") != self._instance_token:
                    self.close()
                    raise EngineError("Breeze worker 实例身份不匹配")
                self._hello = message
                self._save_registry()
                return
        self.close()
        raise EngineError("Breeze worker 初始化超过 15 分钟")

    def _next_message(self) -> dict:
        try:
            message = self._messages.get(timeout=1)
        except queue.Empty:
            if self._process is None or self._process.poll() is not None:
                raise EngineError("Breeze worker 意外退出；任务可在诊断后继续")
            if time.monotonic() - self._last_heartbeat > 60:
                raise EngineError("Breeze worker 心跳超时；尚未确认进程退出")
            return {}
        if message.get("event") in {"fatal", "pipe_closed"}:
            raise EngineError(message.get("error") or "Breeze worker 管道已关闭")
        return message

    def synthesize(self, text: str, output_path: Path, parameters: dict[str, Any]) -> dict:
        target = Path(output_path).absolute()
        if target.parent.resolve() != self.staging_root or target.exists():
            raise EngineError("Breeze 输出必须是 staging 根目录内尚不存在的唯一 attempt 文件")
        for item in (target, *target.parents):
            if item.is_symlink() or (getattr(item, "is_junction", lambda: False)()):
                raise EngineError("Breeze 输出路径不允许符号链接或目录联接")
        params = dict(parameters)
        reference = params.get("ref_audio_path") or params.get("reference_audio") or params.get("ref_audio")
        if not reference or not Path(reference).is_file():
            raise EngineError("Breeze 参考音频不存在")
        params["ref_audio_path"] = windows_to_wsl(Path(reference).resolve())
        params["ref_text"] = str(params.get("ref_text") or params.get("prompt_text") or "")
        params["performance_mode"] = "accelerated"
        with self._inference_lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            self._start()
            request_id = uuid.uuid4().hex
            self._send({"id": request_id, "op": "synthesize", "text": text,
                        "output": windows_to_wsl(target), "params": params})
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                message = self._next_message()
                if message.get("id") != request_id:
                    continue
                self._last_used = time.monotonic()
                self._idle_timer = threading.Timer(600, self._release_idle)
                self._idle_timer.daemon = True
                self._idle_timer.start()
                if not message.get("ok"):
                    self._last_error = message.get("error") or "未知 Breeze 错误"
                    if "显卡空闲显存不足" in self._last_error:
                        raise BreezeGpuBusyError(self._last_error)
                    if "上下文预算" in self._last_error:
                        raise BreezeContextBudgetError(self._last_error)
                    raise EngineError(self._last_error)
                metadata = message.get("metadata", {})
                self._model_warmed = True
                if not target.is_file() or metadata.get("audio_sha256") != file_sha256(target):
                    raise EngineError("Breeze 输出文件 sha 校验失败")
                metadata["path"] = str(target)
                return metadata
            self.cancel_current()
            raise EngineError("Breeze 单段超过 15 分钟，已请求停止")

    def _release_idle(self):
        if self._inference_lock.acquire(blocking=False):
            try:
                if time.monotonic() - self._last_used >= 599:
                    self.close()
            finally:
                self._inference_lock.release()

    def cancel_current(self) -> None:
        self.close()

    def close(self) -> None:
        with self._state_lock:
            process, identity = self._process, self._identity
            if process is None:
                self.recover_owned_worker()
                return
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            try:
                self._send({"op": "cancel"})
                self._send({"op": "shutdown"})
            except (OSError, EngineError):
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # A wsl.exe exit alone does not prove the Linux worker exited.
        result = self._controller(self._launch_config or self._config(), identity) if identity else {
            "stopped": False, "reason": "linux_identity_not_registered"}
        with self._state_lock:
            try:
                record = json.loads(self._registry.read_text(encoding="utf-8")) if self._registry.exists() else None
            except (ValueError, OSError):
                record = None
            self._set_exit_result(result, record)
        if self._exit_confirmed:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Only the Popen handle we own, after the Linux identity was confirmed stopped.
                process.terminate()

    def runtime_snapshot(self, lines: int = 160) -> dict:
        with self._state_lock:
            return {**self.status(), "running": self._process is not None and self._process.poll() is None,
                    "pid": self._process.pid if self._process else None, "identity": self._identity,
                    "instanceToken": self._instance_token, "phase": self._phase,
                    "codecFrames": self._codec_frames,
                    "exitConfirmed": self._exit_confirmed, "error": self._last_error,
                    "command": self._command, "cwd": "WSL", "logPath": str(self.log_dir / "breeze.log"),
                    "logLines": self._log_lines[-max(1, min(lines, 500)):],
                    "warmed": self._model_warmed}
