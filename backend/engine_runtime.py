from __future__ import annotations

import re
from typing import Any


def resolve_torch_device(value: Any, torch_module: Any = None) -> str:
    """Return a valid explicit device when an upstream YAML stores an empty value."""
    candidate = str(value or "").strip()
    if candidate:
        return candidate
    if torch_module is None:
        import torch as torch_module
    return "cuda" if torch_module.cuda.is_available() else "cpu"


def has_override(value: Any) -> bool:
    """False for omitted and blank-string optional overrides; False booleans remain valid."""
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def detect_gpt_sovits_version(parameters: dict[str, Any], loaded_version: Any = None) -> str:
    """Resolve the real GPT-SoVITS generation before applying versioned defaults."""
    known = {"v1", "v2", "v3", "v4", "v2Pro", "v2ProPlus"}
    loaded = str(loaded_version or "").strip()
    if loaded in known:
        return loaded
    selected = str(parameters.get("version") or "auto").strip()
    if selected in known:
        return selected
    paths = " ".join(str(parameters.get(key) or "") for key in ("t2s_weights_path", "vits_weights_path", "gpt_weights_path", "sovits_weights_path"))
    for pattern, version in (
        (r"(?:^|[\\/_-])v2proplus(?:[\\/_-]|$)", "v2ProPlus"),
        (r"(?:^|[\\/_-])v2pro(?:[\\/_-]|$)", "v2Pro"),
        (r"(?:^|[\\/_-])v4(?:[\\/_-]|$)", "v4"),
        (r"(?:^|[\\/_-])v3(?:[\\/_-]|$)", "v3"),
        (r"(?:^|[\\/_-])v2(?:[\\/_-]|$)", "v2"),
        (r"(?:^|[\\/_-])v1(?:[\\/_-]|$)", "v1"),
    ):
        if re.search(pattern, paths, flags=re.IGNORECASE):
            return version
    return "auto"


def recommended_gpt_sovits_sample_steps(version: str) -> int:
    """Official WebUI defaults: v3 uses 32 steps and v4 uses 8; older families ignore it."""
    return 8 if version == "v4" else 32
