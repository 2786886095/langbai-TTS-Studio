import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.breeze_voices import BreezeVoiceCreate, BreezeVoiceStore, CompositeVoiceStore
from app.service_runtime import DeviceCoordinator, ServiceInstance


def test_service_lease_prevents_two_servers_on_one_data_root(tmp_path):
    first, second = ServiceInstance(tmp_path), ServiceInstance(tmp_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="已有任务服务"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_local_security_and_desktop_same_origin(tmp_path):
    with TestClient(create_app(data_dir=tmp_path, mock_mode=True)) as client:
        assert client.get("/health", headers={"Host": "attacker.invalid"}).status_code == 403
        assert client.post("/api/jobs/v2", headers={"Origin": "null"}, json={}).status_code == 403
        assert client.post("/api/jobs/v2", headers={"Origin": "https://attacker.invalid"}, json={}).status_code == 403
        trusted = {"Origin": "http://127.0.0.1:5173"}
        assert client.post("/api/jobs/v2", headers=trusted, json={}).status_code == 403
        session = client.get("/api/session").json()
        trusted["X-Langbai-CSRF"] = session["csrfToken"]
        # Authentication passed: request is rejected by the schema, not security.
        assert client.post("/api/jobs/v2", headers=trusted, json={}).status_code == 422
        assert client.get("/health").json()["protocolVersion"] == 2
        assert client.get("/longform", follow_redirects=False).headers["location"].startswith("/studio/?longform=1")


def test_breeze_profiles_never_enter_v1_directory(tmp_path):
    with TestClient(create_app(data_dir=tmp_path, mock_mode=True)) as client:
        payload = {"name": "本地参考", "parameters": {"ref_audio_path": "F:/voice.wav", "ref_text": "参考文字"},
                   "source": "本地测试", "consent": True}
        result = client.post("/api/breeze/voices", json=payload)
        assert result.status_code == 201, result.text
        voice_id = result.json()["id"]
        assert not list((tmp_path / "voice-profiles").glob("*.json"))
        assert client.get("/api/voice-profiles").json()["items"] == []
        payload["name"] = "修改后的本地参考"
        assert client.put(f"/api/breeze/voices/{voice_id}", json=payload).json()["revision"] == 2
        assert client.get("/api/breeze/voices").json()["items"][0]["engine"] == "breeze"
        assert client.delete(f"/api/breeze/voices/{voice_id}").status_code == 204
        assert client.get("/api/breeze/voices").json()["items"] == []


def test_shared_device_lease_serializes_and_can_interrupt_wait():
    class Adapter:
        def __init__(self): self.closes = 0
        def close(self): self.closes += 1
    adapters = {name: Adapter() for name in ("breeze", "gpt_sovits")}
    device = DeviceCoordinator(adapters)
    device.reserve("legacy:x", "gpt_sovits")
    stopped, outcome = threading.Event(), []
    def waiter():
        try:
            device.reserve("longform:y", "breeze", stop_event=stopped)
        except InterruptedError:
            outcome.append("interrupted")
    thread = threading.Thread(target=waiter)
    thread.start()
    stopped.set()
    thread.join(2)
    assert outcome == ["interrupted"]
    assert device.snapshot()["id"] == "legacy:x"
    device.release("wrong-owner")
    assert device.snapshot()["id"] == "legacy:x"
    device.release("legacy:x")
    device.reserve("longform:z", "breeze")
    assert adapters["gpt_sovits"].closes == 1
    device.release("longform:z")
