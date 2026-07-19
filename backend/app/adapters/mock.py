from __future__ import annotations

import math
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Any

from .base import EngineAdapter


class MockAdapter(EngineAdapter):
    """Fast deterministic adapter used by API and recovery tests."""

    def __init__(self, engine_id: str, *, fail_text: str | None = None):
        self.engine_id = engine_id
        self.fail_text = fail_text
        self._failed_once: set[str] = set()
        self._lock = threading.Lock()

    def synthesize(self, text: str, output_path: Path, parameters: dict[str, Any]) -> None:
        delay_ms = int(parameters.get("mock_segment_delay_ms", 0))
        if delay_ms:
            time.sleep(delay_ms / 1000)
        fail_once = parameters.get("mock_fail_segment_once")
        segment_index = parameters.get("_segment_index")
        should_fail = (fail_once is True) or (
            isinstance(fail_once, int) and not isinstance(fail_once, bool) and fail_once == segment_index
        ) or (isinstance(fail_once, str) and fail_once in text)
        if should_fail:
            key = f"segment:{segment_index}" if isinstance(fail_once, (bool, int)) else f"text:{fail_once}"
            with self._lock:
                if key not in self._failed_once:
                    self._failed_once.add(key)
                    raise RuntimeError("mock one-shot segment failure")
        if self.fail_text and self.fail_text in text:
            raise RuntimeError(f"mock failure requested by text: {self.fail_text}")
        rate = int(parameters.get("mock_sample_rate", 22_050))
        duration = max(0.08, min(0.5, len(text) * 0.008))
        frames = int(rate * duration)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            payload = bytearray()
            for index in range(frames):
                value = int(2500 * math.sin(2 * math.pi * 220 * index / rate))
                payload.extend(struct.pack("<h", value))
            handle.writeframes(payload)

    def status(self) -> dict[str, Any]:
        return {"id": self.engine_id, "available": True, "state": "mock", "detail": "测试替身已启用"}
