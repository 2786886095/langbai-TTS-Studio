from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import LongAudioOptions, now_iso
from .parameters import ENGINE_INFO


PROJECT_SCHEMA_VERSION = 1
SETTINGS_SCHEMA_VERSION = 1
_RESOURCE_ID = re.compile(r"^[0-9a-f]{32}$")


class WorkspaceError(RuntimeError):
    pass


class WorkspaceNotFound(WorkspaceError):
    pass


class WorkspaceConflict(WorkspaceError):
    pass


class UnsupportedSchema(WorkspaceError):
    pass


def _resource_id(value: str) -> str:
    if not _RESOURCE_ID.fullmatch(value):
        raise ValueError("无效资源 ID")
    return value


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Durably write a JSON file without exposing a partially-written destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class ProjectCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    engine: str
    text: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict, alias="params")
    long_audio: LongAudioOptions = Field(default_factory=LongAudioOptions, alias="longAudio")

    @field_validator("engine")
    @classmethod
    def known_engine(cls, value: str) -> str:
        if value not in ENGINE_INFO:
            raise ValueError(f"不支持的引擎: {value}")
        return value


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    engine: str | None = None
    text: str | None = None
    parameters: dict[str, Any] | None = Field(default=None, alias="params")
    long_audio: LongAudioOptions | None = Field(default=None, alias="longAudio")

    @field_validator("engine")
    @classmethod
    def known_engine(cls, value: str | None) -> str | None:
        if value is not None and value not in ENGINE_INFO:
            raise ValueError(f"不支持的引擎: {value}")
        return value


class ProjectCopyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)


class Project(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(default=PROJECT_SCHEMA_VERSION, alias="schemaVersion")
    id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    engine: str
    text: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict, alias="params")
    long_audio: LongAudioOptions = Field(default_factory=LongAudioOptions, alias="longAudio")
    source_project_id: str | None = Field(default=None, alias="sourceProjectId")
    created_at: str = Field(default_factory=now_iso, alias="createdAt")
    updated_at: str = Field(default_factory=now_iso, alias="updatedAt")

    @field_validator("id", "source_project_id")
    @classmethod
    def valid_resource_id(cls, value: str | None) -> str | None:
        if value is not None:
            _resource_id(value)
        return value

    @field_validator("engine")
    @classmethod
    def known_engine(cls, value: str) -> str:
        if value not in ENGINE_INFO:
            raise ValueError(f"不支持的引擎: {value}")
        return value


class ProjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, project_id: str) -> Path:
        return self.root / f"{_resource_id(project_id)}.json"

    @staticmethod
    def _migrate(payload: dict[str, Any], source: Path) -> dict[str, Any]:
        version = payload.get("schemaVersion", payload.get("schema_version", 0))
        if version == 0:
            migrated = dict(payload)
            migrated["schemaVersion"] = 1
            if "parameters" in migrated and "params" not in migrated:
                migrated["params"] = migrated.pop("parameters")
            if "long_audio" in migrated and "longAudio" not in migrated:
                migrated["longAudio"] = migrated.pop("long_audio")
            return migrated
        if version != PROJECT_SCHEMA_VERSION:
            raise UnsupportedSchema(
                f"项目 {source.name} 使用不受支持的 schemaVersion={version}；当前版本={PROJECT_SCHEMA_VERSION}"
            )
        return payload

    def _load_path(self, path: Path) -> Project:
        resolved = path.resolve(strict=False)
        if path.is_symlink() or resolved.parent != self.root:
            raise WorkspaceError(f"项目文件越过项目目录: {path.name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            version = payload.get("schemaVersion", payload.get("schema_version", 0))
            migrated = self._migrate(payload, path)
            project = Project.model_validate(migrated)
            if version == 0:
                atomic_write_json(path, project.model_dump(mode="json", by_alias=True))
        except UnsupportedSchema:
            raise
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise WorkspaceError(f"项目文件损坏: {path.name}: {exc}") from exc
        if project.id != path.stem:
            raise WorkspaceError(f"项目 ID 与文件名不一致: {path.name}")
        return project

    def create(self, request: ProjectCreate, *, source_project_id: str | None = None) -> Project:
        with self._lock:
            project = Project(
                id=uuid.uuid4().hex,
                name=request.name,
                description=request.description,
                engine=request.engine,
                text=request.text,
                params=request.parameters,
                longAudio=request.long_audio,
                sourceProjectId=source_project_id,
            )
            atomic_write_json(self._path(project.id), project.model_dump(mode="json", by_alias=True))
            return project

    def get(self, project_id: str) -> Project:
        path = self._path(project_id)
        with self._lock:
            if not path.is_file():
                raise WorkspaceNotFound("项目不存在")
            return self._load_path(path)

    def list(self) -> list[Project]:
        with self._lock:
            projects = [self._load_path(path) for path in self.root.glob("*.json")]
        return sorted(projects, key=lambda project: project.updated_at, reverse=True)

    def update(self, project_id: str, request: ProjectUpdate) -> Project:
        with self._lock:
            current = self.get(project_id)
            changes = request.model_dump(exclude_unset=True, by_alias=False)
            changes = {key: value for key, value in changes.items() if value is not None}
            updated = current.model_copy(update={**changes, "updated_at": now_iso()})
            updated = Project.model_validate(updated.model_dump())
            atomic_write_json(self._path(project_id), updated.model_dump(mode="json", by_alias=True))
            return updated

    def copy(self, project_id: str, request: ProjectCopyRequest) -> Project:
        current = self.get(project_id)
        return self.create(ProjectCreate(
            name=request.name or f"{current.name} - 副本",
            description=current.description,
            engine=current.engine,
            text=current.text,
            params=current.parameters,
            longAudio=current.long_audio,
        ), source_project_id=current.id)

    def delete(self, project_id: str) -> None:
        path = self._path(project_id)
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError as exc:
                raise WorkspaceNotFound("项目不存在") from exc


class GlobalSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(default=SETTINGS_SCHEMA_VERSION, alias="schemaVersion")
    revision: int = Field(default=0, ge=0)
    theme: Literal["light", "dark", "system"] = "system"
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    default_engine: str = Field(default="indextts2", alias="defaultEngine")
    output_directory: str | None = Field(default=None, alias="outputDirectory")
    auto_reveal_output: bool = Field(default=False, alias="autoRevealOutput")
    update_channel: Literal["stable", "beta"] = Field(default="stable", alias="updateChannel")
    updated_at: str = Field(default_factory=now_iso, alias="updatedAt")

    @field_validator("default_engine")
    @classmethod
    def known_engine(cls, value: str) -> str:
        if value not in ENGINE_INFO:
            raise ValueError(f"不支持的引擎: {value}")
        return value

    @field_validator("output_directory")
    @classmethod
    def absolute_output_directory(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("outputDirectory 必须是绝对本地路径")
        resolved = path.resolve(strict=False)
        if resolved == Path(resolved.anchor):
            raise ValueError("outputDirectory 不能是磁盘根目录")
        return str(resolved)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=0)
    theme: Literal["light", "dark", "system"] | None = None
    language: Literal["zh-CN", "en-US"] | None = None
    default_engine: str | None = Field(default=None, alias="defaultEngine")
    output_directory: str | None = Field(default=None, alias="outputDirectory")
    auto_reveal_output: bool | None = Field(default=None, alias="autoRevealOutput")
    update_channel: Literal["stable", "beta"] | None = Field(default=None, alias="updateChannel")

    @field_validator("default_engine")
    @classmethod
    def known_engine(cls, value: str | None) -> str | None:
        if value is not None and value not in ENGINE_INFO:
            raise ValueError(f"不支持的引擎: {value}")
        return value

    @field_validator("output_directory")
    @classmethod
    def absolute_output_directory(cls, value: str | None) -> str | None:
        return GlobalSettings.absolute_output_directory(value)


class SettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _migrate(payload: dict[str, Any]) -> dict[str, Any]:
        version = payload.get("schemaVersion", payload.get("schema_version", 0))
        if version == 0:
            migrated = dict(payload)
            migrated["schemaVersion"] = 1
            aliases = {
                "default_engine": "defaultEngine", "output_directory": "outputDirectory",
                "auto_reveal_output": "autoRevealOutput", "update_channel": "updateChannel",
                "updated_at": "updatedAt",
            }
            for old, new in aliases.items():
                if old in migrated and new not in migrated:
                    migrated[new] = migrated.pop(old)
            return migrated
        if version != SETTINGS_SCHEMA_VERSION:
            raise UnsupportedSchema(
                f"设置使用不受支持的 schemaVersion={version}；当前版本={SETTINGS_SCHEMA_VERSION}"
            )
        return payload

    def get(self) -> GlobalSettings:
        with self._lock:
            if not self.path.is_file():
                settings = GlobalSettings()
                atomic_write_json(self.path, settings.model_dump(mode="json", by_alias=True))
                return settings
            if self.path.is_symlink():
                raise WorkspaceError("全局设置文件不能是符号链接")
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                version = payload.get("schemaVersion", payload.get("schema_version", 0))
                settings = GlobalSettings.model_validate(self._migrate(payload))
                if version == 0:
                    atomic_write_json(self.path, settings.model_dump(mode="json", by_alias=True))
                return settings
            except UnsupportedSchema:
                raise
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise WorkspaceError(f"全局设置文件损坏: {exc}") from exc

    def update(self, request: SettingsPatch) -> GlobalSettings:
        with self._lock:
            current = self.get()
            if request.expected_revision is not None and request.expected_revision != current.revision:
                raise WorkspaceConflict(
                    f"设置已被其他窗口修改；期望 revision={request.expected_revision}，实际={current.revision}"
                )
            changes = request.model_dump(exclude_unset=True, by_alias=False)
            changes.pop("expected_revision", None)
            changes = {key: value for key, value in changes.items() if value is not None or key == "output_directory"}
            updated = current.model_copy(update={
                **changes, "revision": current.revision + 1, "updated_at": now_iso(),
            })
            updated = GlobalSettings.model_validate(updated.model_dump())
            atomic_write_json(self.path, updated.model_dump(mode="json", by_alias=True))
            return updated
