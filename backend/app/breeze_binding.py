"""Read-only bindings for an independently installed Breeze runtime.

Breeze data lives in v2; it must never enter the v1 engine-binding/voice scans.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SOURCE_COMMIT = "6eba901d1845353802652c708a4740fdfc5baa75"
MODEL_REVISION = "3e28c5151381a722f1d8661b4118c298caa77aa4"
KV_PATCH_SHA256 = "9b27b1fefc3d867abf9ac477f03e3a5ecc2c030a06fe4f18a4e56a3c1abe3ac6"
KV_PATCH_PATH = "models/stream_runtime/stream/kv_cache.py"
PROFILE = {
    "performance_mode": "accelerated", "cfg_scale": 1.0,
    "max_seq_len": 2048, "attention_implementation": "eager",
    "fast_all": None,
    "fast_flags": {"text_encoder": False, "backbone_prefill": False,
                   "backbone_decode": True, "depth_decoder": True, "codec": False},
    "depth_bucket_sizes": [1],
}


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def windows_to_wsl(path: str | Path) -> str:
    """Translate only absolute drive paths. Never interpolate paths in a shell."""
    value = str(path)
    parsed = PureWindowsPath(value)
    if not re.fullmatch(r"[A-Za-z]:", parsed.drive) or not parsed.root:
        raise ValueError("Breeze 路径必须是本机盘符绝对路径，不支持 UNC 或相对路径")
    if ".." in parsed.parts or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("Breeze 路径包含非法组件")
    return "/mnt/" + parsed.drive[0].lower() + "/" + "/".join(parsed.parts[1:])


class BreezeBindingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_path: str = Field(default=r"F:\AI\Breeze-TTS-2", alias="sourcePath")
    model_path: str | None = Field(default=None, alias="modelPath")
    distribution: str = "Ubuntu"
    python_path: str = Field(default="/home/langbai666/.venvs/breeze-tts-2/bin/python", alias="pythonPath")
    performance_mode: Literal["accelerated"] = Field(default="accelerated", alias="performanceMode")
    license_accepted: bool = Field(default=False, alias="licenseAccepted")
    confirm: bool = False

    @field_validator("distribution")
    @classmethod
    def safe_distribution(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
            raise ValueError("WSL 发行版名称无效")
        return value

    @field_validator("python_path")
    @classmethod
    def linux_python(cls, value: str) -> str:
        if not value.startswith("/") or any(c in value for c in "\x00\n\r"):
            raise ValueError("Python 必须为 Linux 绝对路径")
        return value


class BreezeBindingStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get(self) -> dict | None:
        if not self.path.is_file():
            return None
        result = json.loads(self.path.read_text(encoding="utf-8"))
        if result.get("schemaVersion") != 1:
            raise ValueError("不支持的 Breeze 绑定版本")
        BreezeBindingRequest.model_validate({k: v for k, v in result.items() if k not in {
            "schemaVersion", "updatedAt", "sourceCommit", "modelRevision", "patchSha256", "licenseVersion", "licenseSha256",
        }})
        return result

    def bind(self, request: BreezeBindingRequest) -> dict:
        if not request.confirm:
            raise ValueError("请确认只读绑定现有 Breeze 安装")
        if not request.license_accepted:
            raise ValueError("请先确认 Breeze TTS 2 模型许可：研究和非商业用途，商用需另行授权")
        source = Path(request.source_path).resolve()
        model = Path(request.model_path or source / "checkpoints" / "Breeze-TTS-2").resolve()
        if not Path(request.source_path).is_absolute():
            raise ValueError("Breeze 源码目录必须为绝对路径")
        missing = [str(p) for p in (source / "breeze_infer", source / KV_PATCH_PATH,
                                   model / "config.json", model / "audio_tokenizer" / "config.json",
                                   model / "LICENSE") if not p.exists()]
        if missing:
            raise ValueError("现有 Breeze 安装缺少文件：" + "；".join(missing))
        if file_sha256(source / KV_PATCH_PATH) != KV_PATCH_SHA256:
            raise ValueError("Breeze KV 修复与已验证版本不同，拒绝使用加速模式；不会自动修改安装")
        record = request.model_dump(mode="json", by_alias=True)
        record.update(sourcePath=str(source), modelPath=str(model), schemaVersion=1,
                      sourceCommit=SOURCE_COMMIT, modelRevision=MODEL_REVISION, patchSha256=KV_PATCH_SHA256,
                      licenseVersion="Breeze TTS 2 Model License v1.1 (2026-09-01)",
                      licenseSha256=file_sha256(model / "LICENSE"),
                      updatedAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(8):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    if attempt == 7:
                        raise
                    time.sleep(min(.05 * 2**attempt, .5))
        finally:
            temporary.unlink(missing_ok=True)
        return record

    def unbind(self) -> None:
        self.path.unlink(missing_ok=True)
