import io
from pathlib import Path
from types import SimpleNamespace

from app.adapters.subprocess_adapter import ENGINE_LOG_MAX_BYTES, SubprocessAdapter, _rotate_log, _tail_text_lines
from engine_runtime import detect_gpt_sovits_version, has_override, recommended_gpt_sovits_sample_steps, resolve_torch_device


def test_blank_gpt_device_uses_available_accelerator():
    cuda_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    cpu_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))

    assert resolve_torch_device("", cuda_torch) == "cuda"
    assert resolve_torch_device(None, cpu_torch) == "cpu"
    assert resolve_torch_device("cuda:1", cpu_torch) == "cuda:1"
    assert has_override("") is False
    assert has_override("   ") is False
    assert has_override(False) is True


def test_gpt_sovits_defaults_follow_loaded_or_weight_version():
    assert detect_gpt_sovits_version({"version": "v3"}, "v4") == "v4"
    assert detect_gpt_sovits_version({"version": "auto", "vits_weights_path": r"D:\models\v4\hutao.pth"}) == "v4"
    assert detect_gpt_sovits_version({"version": "auto", "t2s_weights_path": r"D:\models\v2ProPlus\voice.ckpt"}) == "v2ProPlus"
    assert detect_gpt_sovits_version({"version": "auto", "vits_weights_path": r"D:\models\v1\legacy.pth"}) == "v1"
    assert recommended_gpt_sovits_sample_steps("v3") == 32
    assert recommended_gpt_sovits_sample_steps("v4") == 8


def test_worker_protocol_accepts_framed_and_legacy_json_but_rejects_progress_noise():
    assert SubprocessAdapter._protocol_payload('@@LANGBAI_RPC@@{"ready":true}\n') == {"ready": True}
    assert SubprocessAdapter._protocol_payload('{"ready":true}\n') == {"ready": True}
    assert SubprocessAdapter._protocol_payload('emitting double-array: 2% |██|\r') is None


def test_worker_protocol_drains_legacy_stdout_noise(tmp_path: Path):
    class FakeStdout:
        def __init__(self):
            self.lines = iter([
                "emitting double-array: 2% |██|\n",
                '@@LANGBAI_RPC@@{"id":"request-1","ok":true}\n',
            ])

        def readline(self):
            return next(self.lines, "")

    class FakeProcess:
        stdout = FakeStdout()

        @staticmethod
        def poll():
            return None

    adapter = SubprocessAdapter("gpt_sovits", tmp_path / "python.exe", tmp_path / "engine", tmp_path / "logs")
    adapter._log_handle = io.StringIO()

    response = adapter._read_protocol_message(FakeProcess())

    assert response == {"id": "request-1", "ok": True}
    assert "emitting double-array" in adapter._log_handle.getvalue()


def test_engine_worker_reserves_private_rpc_pipe_and_redirects_native_stdout():
    source = (Path(__file__).resolve().parents[1] / "engine_worker.py").read_text(encoding="utf-8")
    assert 'PROTOCOL_PREFIX = "@@LANGBAI_RPC@@"' in source
    assert "os.dup(sys.stdout.fileno())" in source
    assert "os.dup2(sys.stderr.fileno(), sys.stdout.fileno())" in source


def test_large_runtime_log_tail_is_bounded_and_rotated(tmp_path: Path):
    log = tmp_path / "gpt_sovits.log"
    log.write_bytes(b"old\n" * (ENGINE_LOG_MAX_BYTES // 4 + 1))

    assert _tail_text_lines(log, 3) == ["old", "old", "old"]
    _rotate_log(log)

    assert not log.exists()
    assert log.with_suffix(".log.1").is_file()


def test_cancel_current_terminates_worker_without_waiting_for_adapter_lock(tmp_path: Path):
    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = 1

        def wait(self, timeout):
            assert timeout == 2
            return self.returncode

    adapter = SubprocessAdapter("indextts2", tmp_path / "python.exe", tmp_path / "engine", tmp_path / "logs")
    process = FakeProcess()
    adapter._process = process

    adapter.cancel_current()

    assert process.terminated is True
    assert adapter._process is None


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
