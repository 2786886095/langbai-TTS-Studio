from __future__ import annotations

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
