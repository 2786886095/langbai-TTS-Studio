from pathlib import Path

from app.adapters.subprocess_adapter import SubprocessAdapter


def test_engine_process_does_not_inherit_backend_python_home(monkeypatch, tmp_path: Path):
    captured = {}

    class FakeStdout:
        def readline(self):
            return '{"ready": true}\n'

    class FakeProcess:
        stdout = FakeStdout()
        stdin = None

        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        captured.update(kwargs["env"])
        return FakeProcess()

    python = tmp_path / "python.exe"
    project = tmp_path / "engine"
    python.touch()
    project.mkdir()
    checkpoints = project / "checkpoints"
    checkpoints.mkdir()
    for name in ("config.yaml", "gpt.pth", "s2mel.pth"):
        (checkpoints / name).touch()
    monkeypatch.setenv("PYTHONHOME", "backend-python-home")
    monkeypatch.setenv("UV_INTERNAL__PYTHONHOME", "backend-uv-home")
    monkeypatch.setenv("VIRTUAL_ENV", "backend-venv")
    monkeypatch.setattr("app.adapters.subprocess_adapter.subprocess.Popen", fake_popen)

    adapter = SubprocessAdapter("indextts2", python, project, tmp_path / "logs")
    adapter._start()

    assert "PYTHONHOME" not in captured
    assert "UV_INTERNAL__PYTHONHOME" not in captured
    assert "VIRTUAL_ENV" not in captured
    assert captured["LANGBAI_ENGINE"] == "indextts2"


def test_engine_worker_uses_packaged_backend_root(monkeypatch, tmp_path: Path):
    captured = {}

    class FakeStdout:
        def readline(self):
            return '{"ready": true}\n'

    class FakeProcess:
        stdout = FakeStdout()
        stdin = None

        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return FakeProcess()

    backend_root = tmp_path / "resources" / "backend"
    backend_root.mkdir(parents=True)
    worker = backend_root / "engine_worker.py"
    worker.write_text("# fixture", encoding="utf-8")
    python = tmp_path / "python.exe"
    project = tmp_path / "engine"
    python.touch()
    checkpoints = project / "checkpoints"
    checkpoints.mkdir(parents=True)
    for name in ("config.yaml", "gpt.pth", "s2mel.pth"):
        (checkpoints / name).touch()
    monkeypatch.setenv("LANGBAI_BACKEND_ROOT", str(backend_root))
    monkeypatch.setattr("app.adapters.subprocess_adapter.subprocess.Popen", fake_popen)

    adapter = SubprocessAdapter("indextts2", python, project, tmp_path / "logs")
    adapter._start()

    assert Path(captured["argv"][2]) == worker


def test_managed_ffmpeg_directory_is_prepended_to_worker_path(monkeypatch, tmp_path: Path):
    captured = {}

    class FakeStdout:
        def readline(self):
            return '{"ready": true}\n'

    class FakeProcess:
        stdout = FakeStdout()
        stdin = None

        def poll(self):
            return None

    def fake_popen(*_args, **kwargs):
        captured.update(kwargs["env"])
        return FakeProcess()

    backend_root = tmp_path / "backend"
    backend_root.mkdir()
    (backend_root / "engine_worker.py").write_text("# fixture", encoding="utf-8")
    python = tmp_path / "env" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    project = tmp_path / "GPT-SoVITS"
    config = project / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")
    ffmpeg_bin = tmp_path / "tools" / "ffmpeg" / "bin"
    ffmpeg_bin.mkdir(parents=True)
    monkeypatch.setattr("app.adapters.subprocess_adapter.subprocess.Popen", fake_popen)

    adapter = SubprocessAdapter(
        "gpt_sovits", python, project, tmp_path / "logs", backend_root=backend_root,
        managed=True, managed_tool_paths=[ffmpeg_bin],
        parameter_defaults={"tts_config_path": str(config)},
    )
    adapter._start()

    assert captured["PATH"].split(";")[0] == str(ffmpeg_bin)
