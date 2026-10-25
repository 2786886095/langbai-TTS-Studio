from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SegmentOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    max_chars: int | None = Field(default=None, alias="maxChars", ge=10, le=1000)
    strategy: Literal["breeze_han_v1", "generic_v2", "paragraph"] | None = None


class OutputOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    format: Literal["WAV", "FLAC", "MP3"] = "WAV"
    sample_rate: Literal["native"] | int = Field(default="native", alias="sampleRate")
    silence_ms: int = Field(default=150, alias="silenceMs", ge=0, le=10000)
    keep_segments: bool = Field(default=True, alias="keepSegments")

    @model_validator(mode="after")
    def validate_rate(self):
        if self.sample_rate != "native" and not 8000 <= self.sample_rate <= 192000:
            raise ValueError("采样率必须为 native 或 8000–192000")
        return self


class LongformCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    text: str = Field(min_length=1, max_length=500000)
    title: str = Field(default="长文配音", max_length=200)
    engine: str
    params: dict[str, Any] = Field(default_factory=dict)
    voice_profile_id: str | None = Field(default=None, alias="voiceProfileId")
    segmentation: SegmentOptions = Field(default_factory=SegmentOptions)
    output: OutputOptions = Field(default_factory=OutputOptions)
    license_accepted: bool = Field(default=False, alias="licenseAccepted")


class PreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    text: str = Field(min_length=1, max_length=500000)
    engine: str = "breeze"
    segmentation: SegmentOptions = Field(default_factory=SegmentOptions)


class ActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    version: int | None = None
    mode: Literal["now", "after_segment"] = "after_segment"
    output: OutputOptions | None = None
    segment_ids: list[str] = Field(default_factory=list, alias="segmentIds")
    seed: int | None = None


class LongformConflict(ValueError):
    pass


class StorageBlocked(OSError):
    pass


class SegmentRejected(RuntimeError):
    pass
