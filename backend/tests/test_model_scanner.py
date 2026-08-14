from pathlib import Path

import pytest

from app.model_scanner import scan_gpt_sovits_models


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    return path


def test_scanner_pairs_character_weights_and_reference_audio(tmp_path):
    model = tmp_path / "character-v2"
    gpt = touch(model / "GPT_weights_v2" / "Alice-e15.ckpt")
    sovits = touch(model / "SoVITS_weights_v2" / "Alice-s120.pth")
    reference = touch(model / "reference" / "Alice-你好，欢迎回来.wav")

    payload = scan_gpt_sovits_models([tmp_path])

    assert payload["gptWeights"] == 1
    assert payload["sovitsWeights"] == 1
    assert payload["audioFiles"] == 1
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["gptWeightsPath"] == str(gpt)
    assert item["sovitsWeightsPath"] == str(sovits)
    assert item["referenceAudio"] == str(reference)
    assert item["version"] == "v2"
    assert item["confidence"] >= 0.8


def test_scanner_ignores_pretrained_assets(tmp_path):
    touch(tmp_path / "pretrained_models" / "s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt")
    touch(tmp_path / "pretrained_models" / "s2G488k.pth")
    payload = scan_gpt_sovits_models([tmp_path])
    assert payload["scannedFiles"] == 0
    assert payload["items"] == []


def test_scanner_rejects_missing_roots(tmp_path):
    with pytest.raises(ValueError):
        scan_gpt_sovits_models([tmp_path / "does-not-exist"])
