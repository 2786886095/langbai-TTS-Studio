import json
import subprocess
import sys
from pathlib import Path

from app.adapters.base import EngineAdapter
from app.training import TrainingManager, VoxTrainingCreate


class TrainingAdapter(EngineAdapter):
    engine_id = "voxcpm"

    def __init__(self, project: Path, python: Path, pretrained: Path):
        self.project = project
        self.python = python
        self.pretrained = pretrained

    def synthesize(self, text, output_path, parameters):
        raise NotImplementedError

    def status(self):
        return {
            "id": "voxcpm", "available": True, "state": "installed", "detail": "fixture",
            "project_path": str(self.project), "python_path": str(self.python),
            "parameter_defaults": {"model_path": str(self.pretrained)},
            "managed_model_path": None,
        }


def fixture_runtime(tmp_path: Path):
    project = tmp_path / "VoxCPM"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "train_voxcpm_finetune.py").write_text("print('fixture')", encoding="utf-8")
    python = project / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    pretrained = project / "pretrained_models" / "VoxCPM2"
    pretrained.mkdir(parents=True)
    (pretrained / "config.json").write_text('{"architecture":"voxcpm2"}', encoding="utf-8")
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"fixture")
    manifest = tmp_path / "train.jsonl"
    manifest.write_text(json.dumps({"audio": str(audio), "text": "测试训练文本"}, ensure_ascii=False) + "\n", encoding="utf-8")
    return project, python, pretrained, manifest


def request(pretrained: Path, manifest: Path, output: Path, mode: str):
    return VoxTrainingCreate.model_validate({
        "name": f"fixture-{mode}", "mode": mode, "pretrainedPath": str(pretrained),
        "trainManifest": str(manifest), "outputDir": str(output), "maxSteps": 20,
        "saveInterval": 10, "validInterval": 10,
    })


def test_lora_and_sft_tasks_use_official_config_shape(tmp_path):
    project, python, pretrained, manifest = fixture_runtime(tmp_path)
    manager = TrainingManager(
        tmp_path / "state", {"voxcpm": TrainingAdapter(project, python, pretrained)},
        tmp_path / "training_worker.py", mock_mode=True,
    )

    capability = manager.capabilities()
    assert capability["available"] is True
    assert capability["pretrainedPath"] == str(pretrained.resolve())

    lora = manager.create(request(pretrained, manifest, tmp_path / "output", "lora"))
    assert lora["status"] == "running"
    lora_config = json.loads(Path(lora["configPath"]).read_text(encoding="utf-8"))
    assert lora_config["lora"]["r"] == 32
    assert lora_config["learning_rate"] == 0.0001
    assert manager.active_summary()[0]["id"] == lora["id"]

    stopped = manager.stop(lora["id"])
    assert stopped["status"] == "cancelled"
    resumed = manager.resume(lora["id"])
    assert resumed["status"] == "running"
    manager.stop(lora["id"])

    sft = manager.create(request(pretrained, manifest, tmp_path / "output", "sft"))
    sft_config = json.loads(Path(sft["configPath"]).read_text(encoding="utf-8"))
    assert "lora" not in sft_config
    assert sft_config["learning_rate"] == 0.00001
    manager.close()


def test_manifest_requires_audio_and_text(tmp_path):
    project, python, pretrained, manifest = fixture_runtime(tmp_path)
    manifest.write_text('{"audio":"voice.wav"}\n', encoding="utf-8")
    manager = TrainingManager(
        tmp_path / "state", {"voxcpm": TrainingAdapter(project, python, pretrained)},
        tmp_path / "training_worker.py", mock_mode=True,
    )
    try:
        manager.create(request(pretrained, manifest, tmp_path / "output", "lora"))
        raise AssertionError("invalid manifest should fail")
    except Exception as error:
        assert "audio 或 text" in str(error)


def test_gpt_workbench_worker_blocks_external_browser(tmp_path):
    project = tmp_path / "GPT-SoVITS"
    project.mkdir()
    (project / "webui.py").write_text(
        "import pathlib, sys, webbrowser\n"
        "pathlib.Path('browser-result.txt').write_text(str(webbrowser.open('http://127.0.0.1:9874')) + '|' + sys.argv[1], encoding='utf-8')\n",
        encoding="utf-8",
    )
    worker = Path(__file__).resolve().parents[1] / "gpt_workbench_worker.py"
    result = subprocess.run(
        [sys.executable, "-I", str(worker), "--project", str(project), "--language", "zh_CN"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert (project / "browser-result.txt").read_text(encoding="utf-8") == "False|zh_CN"
