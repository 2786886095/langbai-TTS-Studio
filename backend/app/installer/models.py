from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models import now_iso


class InstallStatus(str, Enum):
    queued = "queued"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class InstallRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    install_root: str | None = Field(default=None, alias="installRoot")
    accept_license: bool = Field(default=False, alias="acceptLicense")
    accept_python_license: bool = Field(default=False, alias="acceptPythonLicense")
    accepted_tool_licenses: list[str] = Field(default_factory=list, alias="acceptedToolLicenses")
    device: str = "CU128"


class ModelInstallRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    install_root: str | None = Field(default=None, alias="installRoot")
    model_id: str = Field(alias="modelId")
    accept_license: bool = Field(default=False, alias="acceptLicense")


class ToolRepairRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    install_root: str | None = Field(default=None, alias="installRoot")
    accept_license: bool = Field(default=False, alias="acceptLicense")


class InstallJob(BaseModel):
    id: str
    kind: str
    engine: str
    model_id: str | None = None
    tool_id: str | None = None
    status: InstallStatus = InstallStatus.queued
    phase: str = "queued"
    progress: float = 0.0
    bytes_downloaded: int = 0
    bytes_total: int | None = None
    message: str = ""
    error: str | None = None
    install_root: str
    source_url: str
    revision: str
    expected_sha256: str | None = None
    computed_sha256: str | None = None
    code_license: str
    model_license: str | None = None
    license_accepted_at: str
    python_license_accepted_at: str | None = None
    tool_license_acceptances: dict[str, str] = Field(default_factory=dict)
    source_path: str | None = None
    env_path: str | None = None
    model_path: str | None = None
    completed_stages: list[str] = Field(default_factory=list)
    checkpoints: dict[str, Any] = Field(default_factory=dict)
    log_tail: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
