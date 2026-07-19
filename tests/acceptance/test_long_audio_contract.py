from __future__ import annotations

import time


TERMINAL = {"completed", "failed", "cancelled"}


def _create_job(
    client,
    *,
    engine: str,
    text: str,
    parameters: dict | None = None,
    long_audio: dict | None = None,
) -> dict:
    payload = {"engine": engine, "text": text, "parameters": parameters or {}}
    if long_audio is not None:
        payload["long_audio"] = long_audio
    response = client.post(
        "/api/jobs",
        json=payload,
    )
    assert response.status_code in {200, 201, 202}, response.text
    return response.json()


def _job_id(payload: dict) -> str:
    value = payload.get("id") or payload.get("job_id")
    assert value, payload
    return str(value)


def _wait_for_terminal(client, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last.get("status") in TERMINAL:
            return last
        time.sleep(0.05)
    raise AssertionError(f"Job did not finish in mock mode; last state={last!r}")


def _segments(job: dict) -> list[dict]:
    value = job.get("segments")
    assert isinstance(value, list), f"Job has no segment manifest: {job!r}"
    return value


def test_long_text_is_segmented_ordered_and_merged(api_client) -> None:
    text = (
        "第一段用于验证中文句号。第二段用于验证问号？第三段用于验证感叹号！"
        "This sentence checks English punctuation. "
        + "无标点长文本" * 80
    )
    created = _create_job(api_client, engine="voxcpm", text=text)
    job = _wait_for_terminal(api_client, _job_id(created))
    assert job["status"] == "completed", job
    segments = _segments(job)
    assert len(segments) >= 2
    assert [segment.get("index") for segment in segments] == list(range(len(segments)))
    assert all(segment.get("status") == "completed" for segment in segments)
    assert all(segment.get("output_path") for segment in segments)
    assert job.get("output_path"), job


def test_each_job_selects_exactly_one_engine(api_client) -> None:
    for selected in ("indextts2", "voxcpm", "gpt_sovits"):
        created = _create_job(api_client, engine=selected, text="每个任务只调用一个引擎。")
        job = _wait_for_terminal(api_client, _job_id(created))
        assert job.get("engine") == selected
        assert job.get("status") == "completed", job


def test_job_exposes_stable_segment_manifest(api_client) -> None:
    created = _create_job(
        api_client,
        engine="gpt_sovits",
        text="甲。乙。丙。丁。" + "稳定编号" * 100,
    )
    job = _wait_for_terminal(api_client, _job_id(created))
    segments = _segments(job)
    ids = [segment.get("id") for segment in segments]
    assert all(ids)
    assert len(ids) == len(set(ids))
    for segment in segments:
        assert isinstance(segment.get("text"), str) and segment["text"].strip()
        assert isinstance(segment.get("attempts"), int)
        assert segment["attempts"] >= 1


def test_cancel_is_distinct_from_failure(api_client) -> None:
    created = _create_job(
        api_client,
        engine="indextts2",
        text="取消测试。" + "需要足够多的模拟分段。" * 300,
        parameters={"mock_segment_delay_ms": 50},
    )
    job_id = _job_id(created)
    response = api_client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code in {200, 202, 409}, response.text
    job = _wait_for_terminal(api_client, job_id)
    assert job.get("status") == "cancelled", job


def test_failed_segment_can_be_retried_without_regenerating_successes(api_client) -> None:
    created = _create_job(
        api_client,
        engine="voxcpm",
        text=(
            "第一段先成功生成用于验证不被重做。"
            "第二段包含失败标记用于触发一次错误。"
            "第三段恢复后继续生成。" + "重试验证" * 40
        ),
        parameters={"mock_fail_segment_once": "失败标记"},
        long_audio={"max_chars": 24, "max_retries": 0},
    )
    job_id = _job_id(created)
    failed = _wait_for_terminal(api_client, job_id)
    assert failed.get("status") == "failed", failed
    before = {item["id"]: item for item in _segments(failed)}
    completed_before = {
        segment_id: (item.get("attempts"), item.get("output_path"))
        for segment_id, item in before.items()
        if item.get("status") == "completed"
    }
    failed_before = [segment_id for segment_id, item in before.items() if item.get("status") == "failed"]
    assert completed_before, before
    assert len(failed_before) == 1, before

    response = api_client.post(f"/api/jobs/{job_id}/retry")
    assert response.status_code in {200, 202}, response.text
    completed = _wait_for_terminal(api_client, job_id)
    assert completed.get("status") == "completed", completed
    after = {item["id"]: item for item in _segments(completed)}
    for segment_id, evidence in completed_before.items():
        assert (after[segment_id].get("attempts"), after[segment_id].get("output_path")) == evidence
    failed_id = failed_before[0]
    assert after[failed_id].get("status") == "completed"
    assert after[failed_id].get("attempts") == before[failed_id].get("attempts") + 1
