from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class EngineError(RuntimeError):
    pass


class EngineAdapter(ABC):
    engine_id: str

    @abstractmethod
    def synthesize(self, text: str, output_path: Path, parameters: dict[str, Any]) -> None:
        """Generate one complete WAV segment or raise EngineError."""

    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Return a JSON-safe availability snapshot without loading model weights."""

    def close(self) -> None:
        pass

    def cancel_current(self) -> None:
        """Interrupt the inference currently owned by this adapter, if any."""
        pass
