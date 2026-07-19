from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import now_iso
from .parameters import ENGINE_INFO
from .workspace import WorkspaceError, WorkspaceNotFound, _resource_id, atomic_write_json


VOICE_PROFILE_SCHEMA_VERSION = 1


class VoiceProfileCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    engine: str
    description: str = Field(default="", max_length=2000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_model: dict[str, Any] | None = Field(default=None, alias="sourceModel")

    @field_validator("engine")
    @classmethod
    def known_engine(cls, value: str) -> str:
        if value not in ENGINE_INFO:
            raise ValueError(f"不支持的引擎: {value}")
        return value

    @model_validator(mode="after")
    def validate_voice_contract(self):
        values = self.parameters
        if self.engine == "indextts2":
            if not str(values.get("spk_audio_prompt") or "").strip():
                raise ValueError("IndexTTS2 角色声音必须选择音色参考音频")
        elif self.engine == "voxcpm":
            mode = str(values.get("mode") or "可控音色克隆")
            if mode in {"可控音色克隆", "极致克隆"} and not str(values.get("reference_wav_path") or "").strip():
                raise ValueError("当前 VoxCPM2 克隆模式必须选择音色参考音频")
            has_prompt_audio = bool(str(values.get("prompt_wav_path") or "").strip())
            has_prompt_text = bool(str(values.get("prompt_text") or "").strip())
            if has_prompt_audio != has_prompt_text:
                raise ValueError("VoxCPM2 续写提示音频与精确转写必须成对保存")
        else:
            required = {
                "gpt_weights_path": "GPT 权重",
                "sovits_weights_path": "SoVITS 权重",
                "ref_audio_path": "参考音频",
                "prompt_text": "参考文本",
            }
            missing = [label for key, label in required.items() if not str(values.get(key) or "").strip()]
            if missing:
                raise ValueError("GPT-SoVITS 角色声音缺少：" + "、".join(missing))
        return self


class VoiceProfileUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    parameters: dict[str, Any] | None = None
    source_model: dict[str, Any] | None = Field(default=None, alias="sourceModel")


class VoiceProfile(VoiceProfileCreate):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(default=VOICE_PROFILE_SCHEMA_VERSION, alias="schemaVersion")
    id: str
    created_at: str = Field(default_factory=now_iso, alias="createdAt")
    updated_at: str = Field(default_factory=now_iso, alias="updatedAt")

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _resource_id(value)


class VoiceProfileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, profile_id: str) -> Path:
        return self.root / f"{_resource_id(profile_id)}.json"

    def _load_path(self, path: Path) -> VoiceProfile:
        resolved = path.resolve(strict=False)
        if path.is_symlink() or resolved.parent != self.root:
            raise WorkspaceError(f"角色声音文件越过资料库目录: {path.name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            version = payload.get("schemaVersion", payload.get("schema_version", 0))
            if version not in {0, VOICE_PROFILE_SCHEMA_VERSION}:
                raise WorkspaceError(f"角色声音 {path.name} 使用不受支持的 schemaVersion={version}")
            if version == 0:
                payload = {**payload, "schemaVersion": VOICE_PROFILE_SCHEMA_VERSION}
            profile = VoiceProfile.model_validate(payload)
            if version == 0:
                atomic_write_json(path, profile.model_dump(mode="json", by_alias=True))
        except WorkspaceError:
            raise
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise WorkspaceError(f"角色声音文件损坏: {path.name}: {exc}") from exc
        if profile.id != path.stem:
            raise WorkspaceError(f"角色声音 ID 与文件名不一致: {path.name}")
        return profile

    def list(self, engine: str | None = None) -> list[VoiceProfile]:
        if engine is not None and engine not in ENGINE_INFO:
            raise ValueError(f"不支持的引擎: {engine}")
        with self._lock:
            profiles = [self._load_path(path) for path in self.root.glob("*.json")]
        filtered = [profile for profile in profiles if engine is None or profile.engine == engine]
        return sorted(filtered, key=lambda profile: profile.updated_at, reverse=True)

    def get(self, profile_id: str) -> VoiceProfile:
        path = self._path(profile_id)
        with self._lock:
            if not path.is_file():
                raise WorkspaceNotFound("角色声音不存在")
            return self._load_path(path)

    def create(self, request: VoiceProfileCreate) -> VoiceProfile:
        with self._lock:
            profile = VoiceProfile(
                id=uuid.uuid4().hex,
                name=request.name.strip(),
                engine=request.engine,
                description=request.description,
                parameters=request.parameters,
                sourceModel=request.source_model,
            )
            atomic_write_json(self._path(profile.id), profile.model_dump(mode="json", by_alias=True))
            return profile

    def update(self, profile_id: str, request: VoiceProfileUpdate) -> VoiceProfile:
        with self._lock:
            current = self.get(profile_id)
            changes = request.model_dump(exclude_unset=True, by_alias=False)
            changes = {key: value for key, value in changes.items() if value is not None}
            candidate = current.model_copy(update={**changes, "updated_at": now_iso()})
            # Re-run the engine-specific contract after merging partial edits.
            VoiceProfileCreate.model_validate({
                "name": candidate.name,
                "engine": candidate.engine,
                "description": candidate.description,
                "parameters": candidate.parameters,
                "sourceModel": candidate.source_model,
            })
            updated = VoiceProfile.model_validate(candidate.model_dump())
            atomic_write_json(self._path(profile_id), updated.model_dump(mode="json", by_alias=True))
            return updated

    def delete(self, profile_id: str) -> None:
        path = self._path(profile_id)
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError as exc:
                raise WorkspaceNotFound("角色声音不存在") from exc
