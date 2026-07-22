from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class SegmentStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class LongAudioOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_chars: int = Field(default=180, alias="maxChars", ge=1, le=2000)
    silence_ms: int = Field(default=250, alias="silenceMs", ge=0, le=10_000)
    target_sample_rate: int = Field(default=44_100, alias="targetSampleRate", ge=8_000, le=192_000)
    keep_segments: bool = Field(default=True, alias="keepSegments")
    max_retries: int = Field(default=2, alias="maxRetries", ge=0, le=20)


class JobCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    engine: str
    text: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    long_audio: LongAudioOptions = Field(default_factory=LongAudioOptions, alias="longAudio")
    title: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_names(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            if "params" not in value and "parameters" in value:
                value["params"] = value["parameters"]
            if "longAudio" not in value and "long_audio" in value:
                value["longAudio"] = value["long_audio"]
        return value


class SegmentManifest(BaseModel):
    id: str = ""
    index: int
    text: str
    status: SegmentStatus = SegmentStatus.pending
    attempts: int = 0
    output_path: str | None = None
    error: str | None = None


class JobManifest(BaseModel):
    id: str
    engine: str
    title: str
    status: JobStatus = JobStatus.queued
    progress: float = 0.0
    text: str
    parameters: dict[str, Any]
    long_audio: LongAudioOptions
    segments: list[SegmentManifest]
    output_path: str | None = None
    output_directory: str | None = None
    session_id: str | None = None
    error: str | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @model_validator(mode="after")
    def fill_segment_ids(self):
        for segment in self.segments:
            if not segment.id:
                segment.id = f"{self.id}:{segment.index}"
        return self


class EngineStatus(BaseModel):
    id: str
    available: bool
    state: str
    detail: str | None = None
    python_path: str | None = None
    project_path: str
