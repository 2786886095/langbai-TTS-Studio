from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from ..installer.catalog import ENGINE_TOOL_REQUIREMENTS, INSTALLER_CATALOG
from ..installer.integrity import MODEL_INTEGRITY_VERIFIER
from ..installer.supply import ManagedSupply
from ..bindings import EngineBindingStore
from .base import EngineAdapter, EngineError


MODULE_BACKEND_ROOT = Path(__file__).resolve().parents[2]
MANAGED_SUPPLY_VERIFIER = ManagedSupply()

EXTERNAL_ROOT = Path(os.getenv("LANGBAI_EXTERNAL_ROOT") or (Path.home() / "Documents" / "langbai-TTS-Studio" / "external-engines"))

DEFAULT_CONFIG = {
    "indextts2": {
        "project": EXTERNAL_ROOT / "IndexTTS2",
        "python": EXTERNAL_ROOT / "IndexTTS2" / ".venv" / "Scripts" / "python.exe",
    },
    "voxcpm": {
        "project": EXTERNAL_ROOT / "VoxCPM",
        "python": EXTERNAL_ROOT / "VoxCPM" / ".venv" / "Scripts" / "python.exe",
    },
    "gpt_sovits": {
        "project": EXTERNAL_ROOT / "GPT-SoVITS",
        "python": EXTERNAL_ROOT / "GPT-SoVITS" / "runtime" / "python.exe",
        "runtime_root": EXTERNAL_ROOT / "GPT-SoVITS",
    },
}


def _env_python(env_path: Path) -> Path:
    windows = env_path / "Scripts" / "python.exe"
    return windows if os.name == "nt" else env_path / "bin" / "python"


def _managed_bundle(install_root: Path, engine_id: str) -> dict[str, Any] | None:
    bundle = install_root / "installations" / engine_id
    source = bundle / "source"
    env_path = bundle / "env"
    python = _env_python(env_path)
    manifest = bundle / "installation.json"
    if not (source.is_dir() and python.is_file() and manifest.is_file()):
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expected = INSTALLER_CATALOG[engine_id]["revision"]
    item = INSTALLER_CATALOG[engine_id]
    if data.get("revision") != expected or data.get("source_archive_sha256") != item["sha256"]:
        return None
    if not MANAGED_SUPPLY_VERIFIER.source_valid(source, item):
        return None
    tool_dirs: list[Path] = []
    for tool_id in ENGINE_TOOL_REQUIREMENTS[engine_id]:
        tool_status = MANAGED_SUPPLY_VERIFIER.tool_status(install_root, tool_id)
        if not tool_status["installed"]:
            return None
        tool_dirs.extend(sorted({Path(path).parent for path in tool_status["executables"].values()}))
    return {"bundle": bundle, "source": source, "env": env_path, "python": python, "tool_dirs": tool_dirs}


def _managed_model(install_root: Path, engine_id: str) -> Path | None:
    for model in INSTALLER_CATALOG[engine_id]["models"]:
        path = install_root / "models" / engine_id / model["id"]
        manifest = path / "model-manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("revision") == model["revision"] and MODEL_INTEGRITY_VERIFIER.verify(path, model["revision"]):
            return path
    return None


def _first_existing(root: Path, candidates: list[str]) -> Path | None:
    for relative in candidates:
        candidate = root / relative
        if candidate.exists():
            return candidate
    return None


def _managed_parameter_defaults(engine_id: str, source: Path | None, model: Path | None) -> tuple[dict, list[str]]:
    if model is None:
        return {}, []
    if engine_id == "indextts2":
        return {"model_dir": str(model)}, []
    if engine_id == "voxcpm":
        return {"model_path": str(model), "local_files_only": True}, []
    assert engine_id == "gpt_sovits"
    defaults: dict[str, Any] = {}
    if source:
        config = source / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
        if config.is_file():
            defaults["tts_config_path"] = str(config)
    bert = _first_existing(model, [
        "chinese-roberta-wwm-ext-large",
        "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
    ])
    hubert = _first_existing(model, [
        "chinese-hubert-base",
        "GPT_SoVITS/pretrained_models/chinese-hubert-base",
    ])
    if bert:
        defaults["bert_base_path"] = str(bert)
    if hubert:
        defaults["cnhuhbert_base_path"] = str(hubert)
    # The official snapshot contains several model generations. Picking one pair
    # would silently choose a voice/version for the user, so these stay explicit.
    return defaults, ["t2s_weights_path", "vits_weights_path"]


class SubprocessAdapter(EngineAdapter):
    def __init__(
        self,
        engine_id: str,
        python_path: str | Path,
        project_path: str | Path,
        log_dir: str | Path,
        runtime_root: str | Path | None = None,
        *,
        parameter_defaults: dict[str, Any] | None = None,
        required_parameters: list[str] | None = None,
        managed: bool = False,
        backend_root: str | Path | None = None,
        managed_model_path: str | Path | None = None,
        managed_tool_paths: list[str | Path] | None = None,
    ):
        self.engine_id = engine_id
        self.python_path = Path(python_path)
        self.project_path = Path(project_path)
        self.runtime_root = Path(runtime_root) if runtime_root else self.project_path
        self.parameter_defaults = dict(parameter_defaults or {})
        self.required_parameters = list(required_parameters or [])
        self.managed = managed
        self.managed_model_path = Path(managed_model_path) if managed_model_path else None
        self.managed_tool_paths = [Path(path) for path in (managed_tool_paths or [])]
        self.backend_root = Path(backend_root or os.getenv("LANGBAI_BACKEND_ROOT") or MODULE_BACKEND_ROOT).resolve()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._process: subprocess.Popen | None = None
        self._log_handle = None
        self._lock = threading.RLock()

    def _system_missing(self) -> list[str]:
        missing = []
        worker_path = self.backend_root / "engine_worker.py"
        if not worker_path.is_file():
            missing.append(f"后端工作进程脚本不存在: {worker_path}")
        if not self.python_path.is_file():
            missing.append(f"Python 不存在: {self.python_path}")
        if not self.project_path.is_dir():
            missing.append(f"项目目录不存在: {self.project_path}")
        if not self.runtime_root.is_dir():
            missing.append(f"运行资产目录不存在: {self.runtime_root}")
        if self.engine_id == "indextts2":
            model_dir = Path(self.parameter_defaults.get("model_dir") or self.project_path / "checkpoints")
            for name in ("config.yaml", "gpt.pth", "s2mel.pth"):
                path = model_dir / name
                if not path.is_file():
                    missing.append(f"模型文件不存在: {path}")
        elif self.engine_id == "voxcpm":
            model_path = self.parameter_defaults.get("model_path")
            if model_path:
                config = Path(model_path) / "config.json"
                if not config.is_file():
                    missing.append(f"VoxCPM2 模型配置不存在: {config}")
            else:
                snapshots = self.project_path / "cache" / "huggingface" / "hub" / "models--openbmb--VoxCPM2" / "snapshots"
                if not any(snapshots.glob("*/config.json")):
                    missing.append(f"VoxCPM2 本地缓存不存在: {snapshots}")
        elif self.engine_id == "gpt_sovits":
            config = Path(self.parameter_defaults.get("tts_config_path") or self.runtime_root / "GPT_SoVITS" / "configs" / "tts_infer.yaml")
            if not config.is_file():
                missing.append(f"推理配置不存在: {config}")
        return missing

    def status(self) -> dict[str, Any]:
        missing = self._system_missing()
        running = self._process is not None and self._process.poll() is None
        configuration_required = bool(self.required_parameters)
        if running:
            state = "ready"
        elif missing:
            state = "unavailable"
        elif configuration_required:
            state = "configuration_required"
        else:
            state = "installed"
        if missing:
            detail = "；".join(missing)
        elif configuration_required:
            detail = "托管 GPT-SoVITS 已安装，但官方模型包含多个版本，必须明确选择 GPT 与 SoVITS 音色权重。"
        elif running:
            detail = "模型工作进程已加载"
        else:
            detail = "托管运行环境已识别，首次生成时加载模型" if self.managed else "运行环境存在，首次生成时加载模型"
        return {
            "id": self.engine_id,
            "available": not missing,
            "state": state,
            "detail": detail,
            "python_path": str(self.python_path),
            "project_path": str(self.project_path),
            "runtime_root": str(self.runtime_root),
            "backend_root": str(self.backend_root),
            "managed": self.managed,
            "managed_model_path": str(self.managed_model_path) if self.managed_model_path else None,
            "managed_tool_paths": [str(path) for path in self.managed_tool_paths],
            "parameter_defaults": self.parameter_defaults,
            "configuration_required": configuration_required,
            "required_parameters": self.required_parameters,
        }

    def _start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        missing = self._system_missing()
        if missing:
            raise EngineError("；".join(missing))
        self.close()
        env = os.environ.copy()
        for variable in (
            "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT",
            "UV_INTERNAL__PYTHONHOME", "__PYVENV_LAUNCHER__",
        ):
            env.pop(variable, None)
        env["LANGBAI_ENGINE"] = self.engine_id
        env["LANGBAI_PROJECT_PATH"] = str(self.project_path)
        env["LANGBAI_RUNTIME_ROOT"] = str(self.runtime_root)
        env["PYTHONUTF8"] = "1"
        if self.managed_tool_paths:
            env["PATH"] = os.pathsep.join([*(str(path) for path in self.managed_tool_paths), env.get("PATH", "")])
        if self.engine_id == "voxcpm":
            env.setdefault("HF_HOME", str(self.project_path / "cache" / "huggingface"))
            env.setdefault("MODELSCOPE_CACHE", str(self.project_path / "cache" / "modelscope"))
        self._log_handle = (self.log_dir / f"{self.engine_id}.log").open("a", encoding="utf-8")
        self._process = subprocess.Popen(
            [str(self.python_path), "-u", str(self.backend_root / "engine_worker.py")],
            cwd=str(self.project_path), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._log_handle, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ready = self._process.stdout.readline() if self._process.stdout else ""
        try:
            message = json.loads(ready)
        except json.JSONDecodeError as exc:
            raise EngineError(f"{self.engine_id} 工作进程启动失败，详见日志 {self.log_dir}: {ready!r}") from exc
        if not message.get("ready"):
            raise EngineError(message.get("error") or f"{self.engine_id} 工作进程未就绪")

    def synthesize(self, text: str, output_path: Path, parameters: dict[str, Any]) -> None:
        effective_parameters = dict(self.parameter_defaults)
        effective_parameters.update({key: value for key, value in parameters.items() if value is not None})
        missing_parameters = [name for name in self.required_parameters if effective_parameters.get(name) in (None, "")]
        if missing_parameters:
            raise EngineError(
                "GPT-SoVITS 托管安装无法自动判断用户希望使用的音色权重；请明确选择: "
                + ", ".join(missing_parameters)
            )
        request_id = uuid.uuid4().hex
        payload = {"id": request_id, "action": "synthesize", "text": text,
                   "output_path": str(output_path.resolve()), "parameters": effective_parameters}
        with self._lock:
            self._start()
            assert self._process and self._process.stdin and self._process.stdout
            try:
                self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
                line = self._process.stdout.readline()
            except (BrokenPipeError, OSError) as exc:
                self.close()
                raise EngineError(f"{self.engine_id} 工作进程意外退出") from exc
            if not line:
                code = self._process.poll()
                self.close()
                raise EngineError(f"{self.engine_id} 工作进程无响应（退出码 {code}），详见日志")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EngineError(f"{self.engine_id} 返回了无效响应: {line[:300]}") from exc
            if response.get("id") != request_id:
                raise EngineError(f"{self.engine_id} 响应序号不匹配")
            if not response.get("ok"):
                raise EngineError(response.get("error") or f"{self.engine_id} 生成失败")
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise EngineError(f"{self.engine_id} 未生成有效 WAV 文件")

    def close(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write('{"action":"shutdown"}\n')
                    process.stdin.flush()
                process.wait(timeout=3)
            except Exception:
                process.terminate()
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


def _build_resolved_adapter(engine_id: str, log_dir: Path, managed_root: Path, binding_store: EngineBindingStore | None = None) -> SubprocessAdapter:
    legacy = DEFAULT_CONFIG[engine_id]
    backend_root = Path(os.getenv("LANGBAI_BACKEND_ROOT") or MODULE_BACKEND_ROOT).resolve()
    prefix = f"LANGBAI_{engine_id.upper()}"
    bundle = _managed_bundle(managed_root, engine_id)
    managed_model = _managed_model(managed_root, engine_id)
    binding = binding_store.get(engine_id) if binding_store else None
    explicit_project = os.getenv(f"{prefix}_PROJECT")
    explicit_python = os.getenv(f"{prefix}_PYTHON")
    explicit_runtime = os.getenv(f"{prefix}_RUNTIME_ROOT")
    project_path = Path(explicit_project) if explicit_project else (Path(binding["sourcePath"]) if binding else (bundle["source"] if bundle else legacy["project"]))
    python_path = Path(explicit_python) if explicit_python else (Path(binding["pythonPath"]) if binding else (bundle["python"] if bundle else legacy["python"]))
    runtime_root = Path(explicit_runtime) if explicit_runtime else (
        Path(binding["runtimeRoot"]) if binding else (bundle["source"] if bundle else (Path(explicit_project) if explicit_project else legacy.get("runtime_root", legacy["project"])))
    )
    defaults, required = _managed_parameter_defaults(engine_id, bundle["source"] if bundle and not binding else None, managed_model if not binding else None)
    if binding and isinstance(binding.get("parameterDefaults"), dict):
        defaults.update(binding["parameterDefaults"])
    if not managed_model and engine_id == "voxcpm":
        legacy_cache = project_path / "cache" / "huggingface"
        if legacy_cache.is_dir():
            defaults.update({"cache_dir": str(legacy_cache), "local_files_only": True})
    env_default_names = {
        "indextts2": {"model_dir": f"{prefix}_MODEL_DIR"},
        "voxcpm": {"model_path": f"{prefix}_MODEL_PATH"},
        "gpt_sovits": {
            "tts_config_path": f"{prefix}_TTS_CONFIG",
            "t2s_weights_path": f"{prefix}_T2S_WEIGHTS",
            "vits_weights_path": f"{prefix}_VITS_WEIGHTS",
            "bert_base_path": f"{prefix}_BERT_PATH",
            "cnhuhbert_base_path": f"{prefix}_CNHUBERT_PATH",
        },
    }[engine_id]
    for parameter, env_name in env_default_names.items():
        if os.getenv(env_name):
            defaults[parameter] = os.environ[env_name]
            if parameter in required:
                required.remove(parameter)
    return SubprocessAdapter(
        engine_id, python_path, project_path, log_dir, runtime_root,
        parameter_defaults=defaults, required_parameters=required,
        managed=bool(bundle) and not binding, backend_root=backend_root, managed_model_path=managed_model if not binding else None,
        managed_tool_paths=bundle["tool_dirs"] if bundle and not binding else [],
    )


class AutoDetectAdapter(EngineAdapter):
    """Refreshes the delegate when a managed installation/model appears at runtime."""

    def __init__(self, engine_id: str, log_dir: str | Path, managed_root: str | Path, binding_store: EngineBindingStore | None = None):
        self.engine_id = engine_id
        self.log_dir = Path(log_dir)
        self.managed_root = Path(managed_root).resolve()
        self.binding_store = binding_store
        self._delegate: SubprocessAdapter | None = None
        self._signature: str | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _adapter_signature(adapter: SubprocessAdapter) -> str:
        return json.dumps({
            "python": str(adapter.python_path), "project": str(adapter.project_path),
            "runtime": str(adapter.runtime_root), "defaults": adapter.parameter_defaults,
            "required": adapter.required_parameters, "backend": str(adapter.backend_root),
            "tools": [str(path) for path in adapter.managed_tool_paths],
        }, sort_keys=True, ensure_ascii=False)

    def _current(self) -> SubprocessAdapter:
        with self._lock:
            candidate = _build_resolved_adapter(self.engine_id, self.log_dir, self.managed_root, self.binding_store)
            signature = self._adapter_signature(candidate)
            if self._delegate is not None and signature == self._signature:
                return self._delegate
            if self._delegate is not None:
                self._delegate.close()
            self._delegate = candidate
            self._signature = signature
            return candidate

    def status(self) -> dict[str, Any]:
        return self._current().status()

    def synthesize(self, text: str, output_path: Path, parameters: dict[str, Any]) -> None:
        return self._current().synthesize(text, output_path, parameters)

    def close(self) -> None:
        with self._lock:
            if self._delegate is not None:
                self._delegate.close()


def build_default_adapters(log_dir: str | Path, install_root: str | Path | None = None, binding_store: EngineBindingStore | None = None) -> dict[str, EngineAdapter]:
    default_root = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "langbai-TTS-Studio" / "managed"
    managed_root = Path(install_root or os.getenv("LANGBAI_INSTALL_ROOT") or default_root).resolve()
    return {engine_id: AutoDetectAdapter(engine_id, log_dir, managed_root, binding_store) for engine_id in DEFAULT_CONFIG}
