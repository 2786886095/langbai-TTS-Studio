from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .models import InstallJob


class InstallJobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, job_id: str) -> Path:
        if not job_id.isalnum():
            raise ValueError("invalid installer job id")
        return self.root / f"{job_id}.json"

    def save(self, job: InstallJob) -> None:
        with self._lock:
            path = self._path(job.id)
            temp = path.with_suffix(".tmp")
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(job.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)

    def load(self, job_id: str) -> InstallJob | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        with self._lock, path.open("r", encoding="utf-8") as handle:
            return InstallJob.model_validate(json.load(handle))

    def list(self) -> list[InstallJob]:
        jobs = []
        for path in self.root.glob("*.json"):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    jobs.append(InstallJob.model_validate(json.load(handle)))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return sorted(jobs, key=lambda job: job.created_at, reverse=True)
