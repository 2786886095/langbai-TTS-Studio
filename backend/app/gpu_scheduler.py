from __future__ import annotations

import csv
import io
import os
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class GpuSnapshot:
    index: int
    name: str
    memory_total_mb: int
    memory_used_mb: int
    utilization_percent: int
    temperature_c: int


@dataclass(frozen=True)
class AdaptiveWorkerPlan:
    workers: int
    estimated_worker_mb: int
    memory_limit_mb: int
    detail: str


def read_nvidia_gpu_snapshot() -> GpuSnapshot | None:
    """Read the busiest CUDA GPU without importing torch into the desktop backend."""
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rows = list(csv.reader(io.StringIO(completed.stdout)))
        snapshots = [
            GpuSnapshot(
                index=int(row[0].strip()),
                name=row[1].strip(),
                memory_total_mb=int(row[2].strip()),
                memory_used_mb=int(row[3].strip()),
                utilization_percent=int(row[4].strip()),
                temperature_c=int(row[5].strip()),
            )
            for row in rows
            if len(row) >= 6
        ]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not snapshots:
        return None
    return max(snapshots, key=lambda item: (item.memory_used_mb, item.utilization_percent))


def plan_gpt_sovits_workers(
    before_warmup: GpuSnapshot | None,
    after_warmup: GpuSnapshot | None,
    *,
    role_count: int,
    pending_segments: int,
    memory_fraction: float = 0.90,
    hard_max_workers: int = 4,
    aggressive: bool = False,
) -> AdaptiveWorkerPlan:
    useful_limit = max(1, min(role_count, pending_segments, hard_max_workers))
    if useful_limit == 1:
        return AdaptiveWorkerPlan(1, 0, 0, "仅有一个可并行角色或分段")
    if after_warmup is None:
        return AdaptiveWorkerPlan(1, 0, 0, "未读取到 NVIDIA 显存状态，采用安全单 Worker")

    snapshot = after_warmup
    memory_limit_mb = int(snapshot.memory_total_mb * memory_fraction)
    observed_delta = 0
    if before_warmup is not None and before_warmup.index == snapshot.index:
        observed_delta = max(0, snapshot.memory_used_mb - before_warmup.memory_used_mb)

    # GPT-SoVITS generations and third-party weights vary considerably. Never
    # assume a tiny worker just because a model was already warm before sampling.
    estimated_worker_mb = max(3584, int(observed_delta * 1.15))
    reserve_mb = max(512, int(snapshot.memory_total_mb * 0.04))
    headroom_mb = max(0, memory_limit_mb - snapshot.memory_used_mb - reserve_mb)
    extra_workers = headroom_mb // estimated_worker_mb
    workers = min(useful_limit, 1 + extra_workers)

    # A thermally constrained or already saturated GPU gains little from another
    # CUDA context and is more likely to become less responsive.
    if snapshot.temperature_c >= 86 or (snapshot.utilization_percent >= 92 and not aggressive):
        workers = 1
    elif snapshot.temperature_c >= 82 or snapshot.utilization_percent >= 82:
        workers = min(workers, 2)

    # Aggressive mode deliberately probes a second isolated CUDA context on
    # 10GB+ cards. Runtime OOM handling in JobManager collapses back to one
    # worker and resumes unfinished segments, so this is a throughput attempt
    # rather than a promise that two full voice models will fit.
    if aggressive and snapshot.memory_total_mb >= 10_000 and snapshot.temperature_c < 86:
        workers = max(workers, min(2, useful_limit))

    override = os.getenv("LANGBAI_GPT_SOVITS_MAX_WORKERS")
    if override:
        try:
            workers = min(workers, max(1, int(override)))
        except ValueError:
            pass

    mode_label = "激进并发" if aggressive else "安全自适应"
    detail = (
        f"{snapshot.name} · 显存 {snapshot.memory_used_mb}/{snapshot.memory_total_mb} MB · "
        f"负载 {snapshot.utilization_percent}% · 温度 {snapshot.temperature_c}°C · "
        f"{mode_label} {int(memory_fraction * 100)}% 上限 · 安排 {workers} 个 Worker"
    )
    return AdaptiveWorkerPlan(workers, estimated_worker_mb, memory_limit_mb, detail)


def is_cuda_oom(error: str | BaseException | None) -> bool:
    text = str(error or "").lower()
    return any(token in text for token in (
        "cuda out of memory", "cuda error: out of memory", "cublas_status_alloc_failed",
        "cudnn_status_alloc_failed", "显存不足", "outofmemoryerror",
    ))


GpuSnapshotProvider = Callable[[], GpuSnapshot | None]
