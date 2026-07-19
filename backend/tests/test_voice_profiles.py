from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.main import create_app


def make_client(tmp_path):
    adapters = {name: MockAdapter(name) for name in ("indextts2", "voxcpm", "gpt_sovits")}
    return TestClient(create_app(adapters=adapters, data_dir=tmp_path / "data", mock_mode=True))


def test_voice_profiles_are_engine_specific_and_persistent(tmp_path):
    with make_client(tmp_path) as client:
        index = client.post("/api/voice-profiles", json={
            "name": "旁白姐姐",
            "engine": "indextts2",
            "parameters": {"spk_audio_prompt": str(tmp_path / "index.wav"), "emo_alpha": 0.6},
        })
        assert index.status_code == 201, index.text

        gpt = client.post("/api/voice-profiles", json={
            "name": "芙宁娜",
            "engine": "gpt_sovits",
            "parameters": {
                "gpt_weights_path": str(tmp_path / "voice.ckpt"),
                "sovits_weights_path": str(tmp_path / "voice.pth"),
                "ref_audio_path": str(tmp_path / "ref.wav"),
                "prompt_text": "这是一段参考文本。",
                "prompt_lang": "中文",
                "version": "v4",
            },
            "sourceModel": {"name": "社区模型", "sourceUrl": "https://example.invalid/model"},
        })
        assert gpt.status_code == 201, gpt.text
        profile_id = gpt.json()["id"]

        filtered = client.get("/api/voice-profiles", params={"engine": "gpt_sovits"}).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["name"] == "芙宁娜"
        assert filtered["items"][0]["sourceModel"]["name"] == "社区模型"

        updated = client.put(f"/api/voice-profiles/{profile_id}", json={"description": "中文角色声音"})
        assert updated.status_code == 200
        assert updated.json()["description"] == "中文角色声音"

    with make_client(tmp_path) as client:
        restored = client.get(f"/api/voice-profiles/{profile_id}")
        assert restored.status_code == 200
        assert restored.json()["parameters"]["sovits_weights_path"].endswith("voice.pth")


def test_voice_profile_validation_rejects_incomplete_gpt_pair(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post("/api/voice-profiles", json={
            "name": "缺少权重",
            "engine": "gpt_sovits",
            "parameters": {"ref_audio_path": "ref.wav", "prompt_text": "测试"},
        })
        assert response.status_code == 422


def test_voice_profile_delete_is_durable(tmp_path):
    with make_client(tmp_path) as client:
        created = client.post("/api/voice-profiles", json={
            "name": "Vox 角色",
            "engine": "voxcpm",
            "parameters": {"mode": "音色设计", "voice_instruction": "年轻清亮"},
        }).json()
        assert client.delete(f"/api/voice-profiles/{created['id']}").status_code == 204
        assert client.get(f"/api/voice-profiles/{created['id']}").status_code == 404
