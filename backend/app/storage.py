from __future__ import annotations

import json
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Callable

from .models import JobManifest


class JobStore:
    def __init__(self, root: str | Path, output_directory: str | Path | Callable[[], str | Path] | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._output_directory = output_directory
        self._lock = threading.RLock()

    def output_dir(self) -> Path:
        raw = self._output_directory() if callable(self._output_directory) else self._output_directory
        path = Path(raw or (self.root.parent / "output")).resolve()
        if path == Path(path.anchor):
            raise ValueError("output directory cannot be a drive root")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def job_dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("invalid job id")
        path = (self.root / job_id).resolve()
        if self.root not in path.parents:
            raise ValueError("invalid job id")
        return path

    def create_dirs(self, job_id: str) -> tuple[Path, Path]:
        job_dir = self.job_dir(job_id)
        segment_dir = job_dir / "segments"
        segment_dir.mkdir(parents=True, exist_ok=True)
        return job_dir, segment_dir

    def save(self, job: JobManifest) -> None:
        with self._lock:
            job_dir, _ = self.create_dirs(job.id)
            destination = job_dir / "manifest.json"
            temp = destination.with_suffix(".json.tmp")
            data = job.model_dump(mode="json")
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, destination)

    def load(self, job_id: str) -> JobManifest | None:
        path = self.job_dir(job_id) / "manifest.json"
        if not path.is_file():
            return None
        with self._lock, path.open("r", encoding="utf-8") as handle:
            return JobManifest.model_validate(json.load(handle))

    def list(self) -> list[JobManifest]:
        jobs: list[JobManifest] = []
        with self._lock:
            for path in self.root.glob("*/manifest.json"):
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        jobs.append(JobManifest.model_validate(json.load(handle)))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        return jobs

    def delete(self, job_id: str) -> None:
        path = self.job_dir(job_id)
        with self._lock:
            if not path.is_dir():
                raise FileNotFoundError(job_id)
            shutil.rmtree(path)
