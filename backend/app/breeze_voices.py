"""Breeze assets live outside directories scanned by v1 Studio releases."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import now_iso
from .workspace import atomic_write_json


class BreezeVoiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    parameters: dict[str, Any]
    source: str = Field(default="", max_length=2000)
    license: str = Field(default="", max_length=2000)
    consent: str | bool = ""

    @model_validator(mode="after")
    def reference_pair(self):
        if not str(self.parameters.get("ref_audio_path", "")).strip() or not str(self.parameters.get("ref_text", "")).strip():
            raise ValueError("Breeze 声音资料需要参考音频和准确参考文字")
        return self


class BreezeVoice(BreezeVoiceCreate):
    id: str
    engine: Literal["breeze"] = "breeze"
    revision: int = 1
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class BreezeVoiceStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, voice_id):
        if not re.fullmatch(r"breeze-[0-9a-f]{32}", voice_id):
            raise KeyError("Breeze 声音资料不存在")
        return self.root / f"{voice_id}.json"

    def get(self, voice_id):
        with self._lock:
            target = self._path(voice_id)
            if not target.is_file():
                raise KeyError("Breeze 声音资料不存在")
            return BreezeVoice.model_validate_json(target.read_text(encoding="utf-8"))

    def list(self):
        with self._lock:
            return [BreezeVoice.model_validate_json(p.read_text(encoding="utf-8"))
                    for p in sorted(self.root.glob("breeze-*.json"))]

    def create(self, request: BreezeVoiceCreate):
        profile = BreezeVoice(id=f"breeze-{uuid.uuid4().hex}", **request.model_dump())
        with self._lock:
            atomic_write_json(self._path(profile.id), profile.model_dump(mode="json"))
        return profile

    def update(self, voice_id, request):
        with self._lock:
            old = self.get(voice_id)
            profile = BreezeVoice(id=old.id, revision=old.revision + 1, created_at=old.created_at,
                                  updated_at=now_iso(), **request.model_dump())
            atomic_write_json(self._path(profile.id), profile.model_dump(mode="json"))
        return profile

    def delete(self, voice_id):
        with self._lock:
            self.get(voice_id)
            self._path(voice_id).unlink()


class CompositeVoiceStore:
    def __init__(self, legacy, breeze):
        self.legacy, self.breeze = legacy, breeze

    def get(self, voice_id):
        return (self.breeze if voice_id.startswith("breeze-") else self.legacy).get(voice_id)
