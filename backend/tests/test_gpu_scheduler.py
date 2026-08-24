from app.gpu_scheduler import GpuSnapshot, is_cuda_oom, plan_gpt_sovits_workers


def snapshot(*, total=24_576, used=6_500, load=18, temperature=58):
    return GpuSnapshot(0, "Test GPU", total, used, load, temperature)


def test_adaptive_plan_scales_with_observed_vram_and_role_work():
    plan = plan_gpt_sovits_workers(
        snapshot(used=2_000), snapshot(used=6_500), role_count=4, pending_segments=30,
    )
    assert plan.workers == 3
    assert plan.estimated_worker_mb == 5_175
    assert plan.memory_limit_mb == int(24_576 * 0.9)


def test_adaptive_plan_protects_12gb_gpu_and_hot_or_busy_gpu():
    constrained = plan_gpt_sovits_workers(
        snapshot(total=12_288, used=7_000),
        snapshot(total=12_288, used=9_100),
        role_count=4,
        pending_segments=20,
    )
    assert constrained.workers == 1

    hot = plan_gpt_sovits_workers(
        snapshot(used=2_000), snapshot(used=6_000, temperature=87),
        role_count=4, pending_segments=20,
    )
    busy = plan_gpt_sovits_workers(
        snapshot(used=2_000), snapshot(used=6_000, load=95),
        role_count=4, pending_segments=20,
    )
    assert hot.workers == busy.workers == 1


def test_aggressive_plan_probes_two_workers_on_12gb_but_still_respects_temperature():
    before = snapshot(total=12_288, used=7_000)
    after = snapshot(total=12_288, used=9_100, load=95)
    aggressive = plan_gpt_sovits_workers(
        before, after, role_count=4, pending_segments=20,
        memory_fraction=0.98, aggressive=True,
    )
    hot = plan_gpt_sovits_workers(
        before, snapshot(total=12_288, used=9_100, temperature=87),
        role_count=4, pending_segments=20,
        memory_fraction=0.98, aggressive=True,
    )
    assert aggressive.workers == 2
    assert aggressive.memory_limit_mb == int(12_288 * 0.98)
    assert "激进并发" in aggressive.detail
    assert hot.workers == 1


def test_adaptive_plan_falls_back_without_nvidia_telemetry():
    plan = plan_gpt_sovits_workers(None, None, role_count=8, pending_segments=100)
    assert plan.workers == 1
    assert "安全单 Worker" in plan.detail


def test_cuda_oom_recognizes_common_upstream_errors():
    assert is_cuda_oom("torch.OutOfMemoryError: CUDA out of memory")
    assert is_cuda_oom("CUBLAS_STATUS_ALLOC_FAILED")
    assert not is_cuda_oom("reference audio missing")
