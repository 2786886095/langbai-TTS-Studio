from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import now_iso


ENGINE_IDS = {"indextts2", "voxcpm", "gpt_sovits"}


class EngineBindingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    source_path: str = Field(alias="sourcePath")
    python_path: str = Field(alias="pythonPath")
    runtime_root: str | None = Field(default=None, alias="runtimeRoot")
    parameter_defaults: dict[str, Any] = Field(default_factory=dict, alias="parameterDefaults")
    confirm: bool = False


class EngineDiscoveryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    roots: list[str] = Field(default_factory=list)
    max_depth: int = Field(default=2, alias="maxDepth", ge=0, le=3)


class EngineBindingStore:
    """Persists references to existing engines without moving or modifying them."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        bindings = raw.get("bindings", {}) if isinstance(raw, dict) else {}
        return bindings if isinstance(bindings, dict) else {}

    def list(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._read()))

    def get(self, engine: str) -> dict[str, Any] | None:
        return self.list().get(engine)

    def _write(self, bindings: dict[str, dict[str, Any]]) -> None:
        payload = {"schemaVersion": 1, "bindings": bindings}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def restore(self, engine: str, record: dict[str, Any] | None) -> None:
        with self._lock:
            bindings = self._read()
            if record is None:
                bindings.pop(engine, None)
            else:
                bindings[engine] = json.loads(json.dumps(record))
            self._write(bindings)

    @staticmethod
    def _absolute_existing(raw: str, *, directory: bool, label: str) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            raise ValueError(f"{label}必须是绝对路径")
        path = path.resolve()
        exists = path.is_dir() if directory else path.is_file()
        if not exists:
            raise ValueError(f"{label}不存在: {path}")
        return path

    @staticmethod
    def _validate_source(engine: str, source: Path, runtime: Path) -> None:
        markers = {
            "indextts2": [source / "indextts", source / "checkpoints"],
            "voxcpm": [source / "src", source / "pyproject.toml"],
            "gpt_sovits": [source / "GPT_SoVITS", runtime / "GPT_SoVITS" / "configs" / "tts_infer.yaml"],
        }[engine]
        missing = [str(path) for path in markers if not path.exists()]
        if missing:
            raise ValueError("本地程序缺少必要文件: " + "；".join(missing))

    def bind(self, engine: str, request: EngineBindingRequest) -> dict[str, Any]:
        if engine not in ENGINE_IDS:
            raise KeyError(engine)
        if not request.confirm:
            raise ValueError("必须明确确认绑定；软件不会移动或覆盖现有文件")
        source = self._absolute_existing(request.source_path, directory=True, label="项目源码目录")
        python = self._absolute_existing(request.python_path, directory=False, label="Python 可执行文件")
        runtime = self._absolute_existing(request.runtime_root or str(source), directory=True, label="运行资源目录")
        self._validate_source(engine, source, runtime)
        record = {
            "engine": engine,
            "sourcePath": str(source),
            "pythonPath": str(python),
            "runtimeRoot": str(runtime),
            "parameterDefaults": request.parameter_defaults,
            "origin": "bound",
            "updatedAt": now_iso(),
        }
        with self._lock:
            bindings = self._read()
            bindings[engine] = record
            self._write(bindings)
        return json.loads(json.dumps(record))

    def bind_detected(self, status: dict[str, Any]) -> dict[str, Any]:
        engine = str(status.get("id") or "")
        return self.bind(engine, EngineBindingRequest(
            sourcePath=str(status.get("project_path") or ""),
            pythonPath=str(status.get("python_path") or ""),
            runtimeRoot=str(status.get("runtime_root") or status.get("project_path") or ""),
            parameterDefaults=status.get("parameter_defaults") if isinstance(status.get("parameter_defaults"), dict) else {},
            confirm=True,
        ))

    @staticmethod
    def _candidate_directories(root: Path, max_depth: int) -> list[Path]:
        root = root.resolve()
        if not root.is_dir() or root.parent == root:
            raise ValueError(f"扫描目录无效: {root}")
        ignored = {".git", ".venv", "venv", "node_modules", "cache", "models", "checkpoints", "runtime-smoke"}
        found = [root]
        frontier = [(root, 0)]
        while frontier:
            current, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            try:
                children = [path for path in current.iterdir() if path.is_dir() and path.name.lower() not in ignored and not path.name.startswith(".")]
            except OSError:
                continue
            for child in children:
                found.append(child)
                frontier.append((child, depth + 1))
        return found

    def discover(self, roots: list[str], max_depth: int = 2) -> dict[str, EngineBindingRequest]:
        directories: list[Path] = []
        for raw in roots:
            path = Path(raw)
            if not path.is_absolute():
                raise ValueError("快速扫描目录必须是绝对路径")
            directories.extend(self._candidate_directories(path, max_depth))
        unique = list(dict.fromkeys(path.resolve() for path in directories))
        discovered: dict[str, EngineBindingRequest] = {}
        gpt_runtimes = [path for path in unique if (path / "runtime" / "python.exe").is_file() and (path / "GPT_SoVITS" / "configs" / "tts_infer.yaml").is_file()]
        for path in unique:
            if "indextts2" not in discovered and (path / "indextts").is_dir() and (path / "checkpoints").is_dir():
                python = next((candidate for candidate in (path / ".venv" / "Scripts" / "python.exe", path / "venv" / "Scripts" / "python.exe") if candidate.is_file()), None)
                if python:
                    discovered["indextts2"] = EngineBindingRequest(sourcePath=str(path), pythonPath=str(python), runtimeRoot=str(path), confirm=True)
            if "voxcpm" not in discovered and (path / "src").is_dir() and (path / "pyproject.toml").is_file():
                python = next((candidate for candidate in (path / "voxcpm_env" / "Scripts" / "python.exe", path / ".venv" / "Scripts" / "python.exe") if candidate.is_file()), None)
                if python:
                    discovered["voxcpm"] = EngineBindingRequest(sourcePath=str(path), pythonPath=str(python), runtimeRoot=str(path), confirm=True)
            if "gpt_sovits" not in discovered and (path / "GPT_SoVITS").is_dir():
                runtime = path if (path / "runtime" / "python.exe").is_file() and (path / "GPT_SoVITS" / "configs" / "tts_infer.yaml").is_file() else (gpt_runtimes[0] if gpt_runtimes else None)
                if runtime:
                    discovered["gpt_sovits"] = EngineBindingRequest(sourcePath=str(path), pythonPath=str(runtime / "runtime" / "python.exe"), runtimeRoot=str(runtime), confirm=True)
        return discovered
